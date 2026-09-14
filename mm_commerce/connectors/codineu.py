"""CODINEU — parseo GeneXus HTML público + fixtures. Sin bypass de auth."""
from __future__ import annotations

import json
import logging
import re
from html import unescape
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

import httpx

from mm_commerce.config import HARD_SKIP_IDS, get_settings

log = logging.getLogger(__name__)

LIST_URL = (
    "https://codi.neuquen.gob.ar/PortalLicitaciones/servlet/"
    "com.portallicitaciones.wwlicitacion"
)
DETAIL_BASE = "https://codi.neuquen.gob.ar/PortalLicitaciones/servlet/"
UA = "Mozilla/5.0 (compatible; MMAICommerce/0.1; +https://github.com/cargazul33/mm-ai-commerce)"

GRID_V_RE = re.compile(
    r"""name\s*=\s*["']GridContainerDataV["']\s+value\s*=\s*(['"])(.*?)\1""",
    re.I | re.S,
)
DETAIL_LINK_RE = re.compile(
    r"com\.portallicitaciones\.wpdetalle\?[A-Za-z0-9_\-]+",
)


def has_login_credentials() -> bool:
    s = get_settings()
    path = Path(s.codineu_login_env)
    if not path.exists():
        return False
    text = path.read_text(encoding="utf-8", errors="replace")
    return any(
        line.strip() and not line.strip().startswith("#") and "=" in line
        for line in text.splitlines()
    )


def load_fixture(path: str | None = None) -> list[dict[str, Any]]:
    s = get_settings()
    p = Path(path or s.codineu_fixture)
    if not p.exists():
        alt = s.project_root / "fixtures" / "codineu_sample.json"
        p = alt
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    if isinstance(data, list):
        return data
    if isinstance(data, dict):
        for key in ("items", "licitaciones", "data", "results"):
            if isinstance(data.get(key), list):
                return data[key]
    return []


def _unique_detail_links(html: str) -> list[str]:
    seen: set[str] = set()
    out: list[str] = []
    for m in DETAIL_LINK_RE.finditer(html):
        link = m.group(0)
        if link not in seen:
            seen.add(link)
            out.append(urljoin(DETAIL_BASE, link))
    return out


def _cell(row: list[Any], idx: int, default: str = "") -> str:
    if idx >= len(row) or row[idx] is None:
        return default
    return str(row[idx]).replace("\r\n", "\n").replace("\r", "\n").strip()


def row_to_item(row: list[Any], detail_url: str = "") -> dict[str, Any] | None:
    """Mapea un vector GridContainerDataV → dict canónico. None si inválido."""
    if not isinstance(row, list) or len(row) < 12:
        return None
    ext_id = _cell(row, 0)
    if not ext_id.isdigit():
        return None
    titulo = _cell(row, 8) or _cell(row, 7).split("\n")[0]
    rubros = _cell(row, 18)
    if not rubros and "\n" in _cell(row, 7):
        parts = _cell(row, 7).split("\n", 1)
        if len(parts) > 1:
            rubros = parts[1].strip()
    organismo = _cell(row, 17)
    if not organismo and "\n" in _cell(row, 4):
        organismo = _cell(row, 4).split("\n")[-1].strip()
    modalidad = _cell(row, 5) or _cell(row, 4).split("\n")[0]
    return {
        "id": ext_id,
        "numero": _cell(row, 6),
        "modalidad": modalidad,
        "titulo": titulo,
        "apertura": _cell(row, 9),
        "acto": _cell(row, 10),
        "estado_cod": _cell(row, 11),
        "organismo": organismo,
        "rubros": rubros,
        "url": detail_url
        or f"{DETAIL_BASE}com.portallicitaciones.wwlicitacion#{ext_id}",
    }


def parse_genexus_html(html: str) -> list[dict[str, Any]]:
    """Parsea GridContainerDataV (GeneXus WorkWith) → lista de licitaciones."""
    if not html:
        return []
    m = GRID_V_RE.search(html)
    if not m:
        log.warning("CODINEU HTML: GridContainerDataV no encontrado")
        return []
    raw = unescape(m.group(2))
    try:
        grid = json.loads(raw)
    except json.JSONDecodeError as exc:
        log.warning("CODINEU HTML: JSON GridContainerDataV inválido: %s", exc)
        return []
    if not isinstance(grid, list):
        return []

    links = _unique_detail_links(html)
    items: list[dict[str, Any]] = []
    for i, row in enumerate(grid):
        if not isinstance(row, list):
            continue
        url = links[i] if i < len(links) else ""
        item = row_to_item(row, url)
        if item:
            items.append(item)
    return items


def parse_html_file(path: str | Path) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    return parse_genexus_html(p.read_text(encoding="utf-8", errors="replace"))


def filter_hard_skips(
    items: list[dict[str, Any]], excluded: frozenset[str] | None = None
) -> tuple[list[dict[str, Any]], int]:
    """Hard-skip 16514 (y otros excluded). No persistir — solo filtrar."""
    banned = excluded if excluded is not None else HARD_SKIP_IDS
    s = get_settings()
    banned = frozenset(banned) | s.excluded_ids
    kept: list[dict[str, Any]] = []
    skipped = 0
    for it in items:
        ext = str(it.get("id") or it.get("process_id") or "").strip()
        if ext in banned:
            skipped += 1
            continue
        kept.append(it)
    return kept, skipped


def fetch_public_list(timeout: float = 25.0) -> tuple[list[dict[str, Any]], str]:
    """
    Intento honesto de página pública GeneXus.
    Si falla o no parsea → fixtures.
    Retorna (items, source_note). Hard-skips NO se eliminan aquí
    (Radar cuenta skipped_hard); el parseo sí incluye todos los ids vistos.
    """
    s = get_settings()
    if s.use_fixtures:
        items = load_fixture()
        return items, f"fixture:{s.codineu_fixture}"

    # Optional local HTML snapshot (dev / offline live parse)
    html_candidates = [
        Path("/workspace/codineu-list.html"),
        s.project_root / "fixtures" / "html" / "wwlicitacion_sample.html",
    ]

    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=True, verify=False
        ) as client:
            r = client.get(LIST_URL, headers={"User-Agent": UA})
            if r.status_code == 200 and r.text:
                parsed = parse_genexus_html(r.text)
                if parsed:
                    return parsed, "live_genexus_html"
                # live ok but empty parse → try local snapshot then fixture
                for cand in html_candidates:
                    if cand.exists():
                        parsed = parse_html_file(cand)
                        if parsed:
                            return parsed, f"live_http_200_parse_empty_snapshot:{cand}"
                items = load_fixture()
                return items, "live_http_200_parse_empty_fallback_fixture"
            items = load_fixture()
            return items, f"live_http_{r.status_code}_fallback_fixture"
    except Exception as exc:  # noqa: BLE001
        for cand in html_candidates:
            if cand.exists():
                parsed = parse_html_file(cand)
                if parsed:
                    return parsed, f"live_error:{type(exc).__name__}_snapshot:{cand.name}"
        items = load_fixture()
        return items, f"live_error:{type(exc).__name__}_fallback_fixture"


def find_pliego_docs(process_id: str) -> list[Path]:
    s = get_settings()
    candidates = [
        Path(s.pliegos_dir) / str(process_id),
        s.project_root / "fixtures" / "pliegos" / str(process_id),
        Path("/workspace/pliegos") / str(process_id),
    ]
    base = next((c for c in candidates if c.exists()), candidates[0])
    if not base.exists():
        return []
    docs: list[Path] = []
    for p in sorted(base.iterdir()):
        if p.is_file() and p.suffix.lower() in {
            ".pdf",
            ".txt",
            ".html",
            ".htm",
            ".doc",
            ".docx",
        }:
            docs.append(p)
    return docs


def read_pliego_text(process_id: str, max_chars: int = 80000) -> tuple[str, str]:
    """Lee texto de pliego local si existe. Nunca inventa."""
    docs = find_pliego_docs(process_id)
    if not docs:
        return "", "SIN_DOCUMENTO"
    # Prefer .txt then pdf
    preferred = sorted(
        docs, key=lambda p: (0 if p.suffix.lower() == ".txt" else 1, p.name)
    )
    path = preferred[0]
    if path.suffix.lower() == ".pdf":
        import subprocess

        try:
            r = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                capture_output=True,
                timeout=45,
                check=False,
            )
            if r.returncode == 0 and r.stdout:
                text = r.stdout.decode("utf-8", errors="replace")[:max_chars]
                if text.strip():
                    return text, str(path)
            return "", f"PDF_SIN_TEXTO:{path}"
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return "", f"PDF_SIN_TEXTO:{path}"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:max_chars]
        return text, str(path)
    except Exception:  # noqa: BLE001
        return "", f"READ_ERROR:{path}"
