"""Download official CODINEU attachments (pliegos) — never invent docs."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urljoin

import httpx

from mm_commerce.config import get_settings
from mm_commerce.connectors.codineu import DETAIL_BASE, UA

GRID3_RE = re.compile(
    r"""name\s*=\s*["']Grid3ContainerDataV["']\s+value\s*=\s*(['"])(.*?)\1""",
    re.I | re.S,
)
DOWNLOAD_RE = re.compile(
    r"com\.portallicitaciones\.adescargaradj\?[A-Za-z0-9_\-]+",
)


def parse_attachment_meta(html: str) -> list[dict[str, str]]:
    """Extract attachment filenames + download servlet paths from detail HTML."""
    from html import unescape
    import json

    out: list[dict[str, str]] = []
    m = GRID3_RE.search(html or "")
    names: list[str] = []
    if m:
        try:
            grid = json.loads(unescape(m.group(2)))
            for row in grid:
                if isinstance(row, list) and len(row) >= 2:
                    names.append(str(row[1]))
        except json.JSONDecodeError:
            pass
    links = list(dict.fromkeys(DOWNLOAD_RE.findall(html or "")))
    for i, link in enumerate(links):
        fname = names[i] if i < len(names) else f"adjunto_{i+1}.bin"
        out.append({"filename": unquote(fname.replace("+", " ")), "servlet": link})
    return out


def download_opportunity_docs(
    detail_url: str,
    process_id: str,
    *,
    timeout: float = 60.0,
) -> dict[str, Any]:
    """
    Fetch detail page → download all adescargaradj files into data/pliegos/{id}/.
    Returns {ok, dir, files:[{path,bytes,url,name}], error?}.
    """
    settings = get_settings()
    out_dir = settings.project_root / "data" / "pliegos" / str(process_id)
    out_dir.mkdir(parents=True, exist_ok=True)
    files: list[dict[str, Any]] = []
    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=True, verify=False
        ) as client:
            r = client.get(detail_url, headers={"User-Agent": UA})
            if r.status_code != 200 or not r.text:
                return {
                    "ok": False,
                    "error": f"detail_http_{r.status_code}",
                    "dir": str(out_dir),
                    "files": [],
                }
            metas = parse_attachment_meta(r.text)
            if not metas:
                # still save HTML for audit
                html_path = out_dir / "detalle.html"
                html_path.write_text(r.text, encoding="utf-8", errors="replace")
                return {
                    "ok": False,
                    "error": "SIN_ADJUNTOS_EN_DETALLE",
                    "dir": str(out_dir),
                    "files": [{"path": str(html_path), "name": "detalle.html"}],
                    "detail_html": str(html_path),
                }
            for meta in metas:
                url = urljoin(DETAIL_BASE, meta["servlet"])
                dr = client.get(url, headers={"User-Agent": UA})
                name = meta["filename"]
                cd = dr.headers.get("content-disposition") or ""
                m = re.search(
                    r"filename\*?=(?:UTF-8'')?\"?([^\";]+)\"?", cd, re.I
                )
                if m:
                    name = unquote(m.group(1).replace("+", " "))
                # sanitize
                safe = re.sub(r"[^\w.\- %áéíóúÁÉÍÓÚñÑ#]+", "_", name)[:180]
                path = out_dir / safe
                path.write_bytes(dr.content)
                files.append(
                    {
                        "path": str(path),
                        "name": safe,
                        "bytes": len(dr.content),
                        "url": url,
                        "content_type": dr.headers.get("content-type", ""),
                    }
                )
            return {"ok": True, "dir": str(out_dir), "files": files}
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "error": f"{type(exc).__name__}: {exc}",
            "dir": str(out_dir),
            "files": files,
        }
