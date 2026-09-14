"""CODINEU — fuentes públicas + fixtures. Sin bypass de auth."""
from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import httpx

from mm_commerce.config import get_settings

LIST_URL = "https://codi.neuquen.gob.ar/PortalLicitaciones/servlet/com.portallicitaciones.wwlicitacion"
UA = "Mozilla/5.0 (compatible; MMAICommerce/0.1; +https://github.com/cargazul33/mm-ai-commerce)"


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
    # fallback to packaged fixtures
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


def fetch_public_list(timeout: float = 25.0) -> tuple[list[dict[str, Any]], str]:
    """
    Intento honesto de página pública. Si falla → fixtures.
    Retorna (items, source_note).
    """
    s = get_settings()
    if s.use_fixtures:
        items = load_fixture()
        return items, f"fixture:{s.codineu_fixture}"

    try:
        with httpx.Client(timeout=timeout, follow_redirects=True, verify=False) as client:
            r = client.get(LIST_URL, headers={"User-Agent": UA})
            if r.status_code != 200:
                items = load_fixture()
                return items, f"live_http_{r.status_code}_fallback_fixture"
            # Portal GeneXus: parseo completo es frágil; usamos fixture + nota
            items = load_fixture()
            return items, "live_ok_but_parse_uses_fixture_snapshot"
    except Exception as exc:  # noqa: BLE001
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
        if p.is_file() and p.suffix.lower() in {".pdf", ".txt", ".html", ".htm", ".doc", ".docx"}:
            docs.append(p)
    return docs


def read_pliego_text(process_id: str, max_chars: int = 50000) -> tuple[str, str]:
    """Lee texto de pliego local si existe. Nunca inventa."""
    docs = find_pliego_docs(process_id)
    if not docs:
        return "", "SIN_DOCUMENTO"
    # Prefer .txt
    preferred = sorted(docs, key=lambda p: (0 if p.suffix == ".txt" else 1, p.name))
    path = preferred[0]
    if path.suffix.lower() == ".pdf":
        # try pdftotext if available
        import subprocess

        try:
            r = subprocess.run(
                ["pdftotext", "-layout", str(path), "-"],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if r.returncode == 0 and r.stdout:
                text = r.stdout.decode("utf-8", errors="replace")[:max_chars]
                return text, str(path)
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return "", f"PDF_SIN_TEXTO:{path}"
    try:
        text = path.read_text(encoding="utf-8", errors="replace")[:max_chars]
        return text, str(path)
    except Exception:  # noqa: BLE001
        return "", f"READ_ERROR:{path}"
