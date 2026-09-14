"""MercadoLibre búsqueda pública + fallback allowlist stub."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import httpx

from mm_commerce.config import get_settings

ML_SEARCH = "https://api.mercadolibre.com/sites/MLA/search"
UA = "MMAICommerce/0.1"


def load_allowlist() -> list[dict[str, Any]]:
    s = get_settings()
    path = s.project_root / "fixtures" / "suppliers_allowlist.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def search_public(query: str, limit: int = 5, timeout: float = 12.0) -> tuple[list[dict[str, Any]], str]:
    """Búsqueda pública MLA. Live (USE_FIXTURES=0): sin stubs de precio inventado."""
    from mm_commerce.timing import now_ba

    settings = get_settings()
    verified_at = now_ba().isoformat(timespec="seconds")
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            r = client.get(
                ML_SEARCH,
                params={"q": query, "limit": limit},
                headers={"User-Agent": UA},
            )
            if r.status_code != 200:
                if settings.use_fixtures:
                    return _stub_matches(query), f"ml_http_{r.status_code}_stub"
                return [], f"ml_http_{r.status_code}_no_stub"
            data = r.json()
            results = []
            for item in data.get("results", [])[:limit]:
                avail = item.get("available_quantity")
                results.append(
                    {
                        "title": item.get("title", ""),
                        "price": item.get("price"),
                        "currency": item.get("currency_id", "ARS"),
                        "url": item.get("permalink", ""),
                        "seller": (item.get("seller") or {}).get("nickname", "mercadolibre"),
                        "verification": "PROBABLE",
                        "source": "mercadolibre",
                        "stock": str(avail) if avail is not None else "NO VERIFICADO",
                        "shipping_neuquen": "NO VERIFICADO",
                        "verified_at": verified_at,
                    }
                )
            if not results:
                if settings.use_fixtures:
                    return _stub_matches(query), "ml_empty_stub"
                return [], "ml_empty"
            return results, "mercadolibre_live"
    except Exception as exc:  # noqa: BLE001
        if settings.use_fixtures:
            return _stub_matches(query), f"ml_error:{type(exc).__name__}_stub"
        return [], f"ml_error:{type(exc).__name__}"


def _stub_matches(query: str) -> list[dict[str, Any]]:
    allow = load_allowlist()
    q = query.lower()
    out = []
    for row in allow:
        label = f"{row.get('name','')} {row.get('product','')} {row.get('specs','')}".lower()
        if any(tok in label for tok in q.split() if len(tok) > 3) or not q:
            price = row.get("unit_cost")
            out.append(
                {
                    "title": row.get("product", row.get("name", "")),
                    "price": price,  # may be None — never invent
                    "currency": row.get("currency", "ARS"),
                    "url": row.get("url", ""),
                    "seller": row.get("name", "allowlist"),
                    "verification": row.get("verification", "NO VERIFICADO"),
                    "source": "allowlist_stub",
                    "notes": row.get("notes", ""),
                }
            )
        if len(out) >= 5:
            break
    if not out and allow:
        # return first few as NO VERIFICADO references without forcing match
        for row in allow[:2]:
            out.append(
                {
                    "title": row.get("product", ""),
                    "price": row.get("unit_cost"),
                    "currency": row.get("currency", "ARS"),
                    "url": row.get("url", ""),
                    "seller": row.get("name", "allowlist"),
                    "verification": "NO VERIFICADO",
                    "source": "allowlist_stub",
                    "notes": "sin_match_fuerte",
                }
            )
    return out
