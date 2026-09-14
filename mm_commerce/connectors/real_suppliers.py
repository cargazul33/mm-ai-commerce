"""Real supplier page fetch — parse public prices; never invent."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from mm_commerce.timing import now_ba

UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
)

PRICE_RE = re.compile(
    r"(?:\$|ARS)\s*([\d]{1,3}(?:[.\s]\d{3})*(?:,\d{2})?|\d+(?:[.,]\d{2})?)",
    re.I,
)
LD_JSON_RE = re.compile(
    r'<script[^>]*type=["\']application/ld\+json["\'][^>]*>(.*?)</script>',
    re.I | re.S,
)


def _parse_ars(raw: str) -> float | None:
    s = raw.strip().replace(" ", "")
    if "," in s and "." in s:
        # 1.149.700,00
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        parts = s.split(",")
        if len(parts[-1]) == 2:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
        # 1149700 or 1.149.700
        if s.count(".") >= 1 and len(s.split(".")[-1]) == 3:
            s = s.replace(".", "")
    try:
        return float(s)
    except ValueError:
        return None


def _from_ld_json(html: str) -> list[dict[str, Any]]:
    out = []
    for m in LD_JSON_RE.finditer(html or ""):
        raw = m.group(1).strip()
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        nodes = data if isinstance(data, list) else [data]
        # also unwrap @graph
        expanded = []
        for n in nodes:
            if isinstance(n, dict) and "@graph" in n:
                expanded.extend(n["@graph"])
            else:
                expanded.append(n)
        for n in expanded:
            if not isinstance(n, dict):
                continue
            typ = n.get("@type")
            types = typ if isinstance(typ, list) else [typ]
            if "Product" not in types and n.get("mainEntity", {}).get("@type") != "Product":
                prod = n.get("mainEntity") if isinstance(n.get("mainEntity"), dict) else n
            else:
                prod = n
            if not isinstance(prod, dict):
                continue
            ptypes = prod.get("@type")
            ptypes = ptypes if isinstance(ptypes, list) else [ptypes]
            if "Product" not in ptypes:
                continue
            offer = prod.get("offers") or {}
            if isinstance(offer, list):
                offer = offer[0] if offer else {}
            price = offer.get("price")
            avail = str(offer.get("availability") or "")
            stock = "NO VERIFICADO"
            if "InStock" in avail:
                inv = offer.get("inventoryLevel") or {}
                if isinstance(inv, dict) and inv.get("value") is not None:
                    stock = str(inv.get("value"))
                else:
                    stock = "InStock"
            elif "OutOfStock" in avail:
                stock = "0"
            try:
                price_f = float(price) if price is not None else None
            except (TypeError, ValueError):
                price_f = None
            if price_f == 0:
                price_f = None
            out.append(
                {
                    "title": prod.get("name") or "",
                    "price": price_f,
                    "currency": offer.get("priceCurrency") or "ARS",
                    "stock": stock,
                    "seller": (offer.get("seller") or {}).get("name")
                    if isinstance(offer.get("seller"), dict)
                    else "",
                }
            )
    return out


def fetch_product_page(url: str, *, timeout: float = 25.0) -> dict[str, Any]:
    """Fetch a public product URL and extract price/stock evidence."""
    verified_at = now_ba().isoformat(timespec="seconds")
    try:
        with httpx.Client(
            timeout=timeout, follow_redirects=True, headers={"User-Agent": UA}
        ) as client:
            r = client.get(url)
            if r.status_code != 200:
                return {
                    "ok": False,
                    "url": url,
                    "error": f"http_{r.status_code}",
                    "verified_at": verified_at,
                }
            html = r.text
            ld = _from_ld_json(html)
            price = None
            title = ""
            stock = "NO VERIFICADO"
            seller = ""
            if ld:
                best = next((x for x in ld if x.get("price")), ld[0])
                price = best.get("price")
                title = best.get("title") or ""
                stock = best.get("stock") or stock
                seller = best.get("seller") or ""
            if price is None:
                # transfer/cash hints first
                for m in re.finditer(
                    r"(?:transferencia|efectivo|especial)[^\$]{0,40}\$\s*([\d\.\,]+)",
                    html,
                    re.I,
                ):
                    price = _parse_ars(m.group(1))
                    if price and price > 100:
                        break
            if price is None:
                for m in PRICE_RE.finditer(html):
                    cand = _parse_ars(m.group(1))
                    if cand and cand >= 1000:
                        price = cand
                        break
            if "sin stock" in html.lower() or "out of stock" in html.lower():
                if stock == "NO VERIFICADO":
                    stock = "0"
            elif "disponible" in html.lower() and stock == "NO VERIFICADO":
                stock = "disponible (texto página)"
            # shipping to Neuquén: only if page mentions envío a todo el país / Neuquén
            ship = "NO VERIFICADO"
            low = html.lower()
            if "neuqu" in low and ("envío" in low or "envio" in low):
                ship = "menciona envío / Neuquén en página — cotizar"
            elif "todo el país" in low or "todo el pais" in low or "oca" in low:
                ship = "envío nacional mencionado — cotizar a Neuquén"
            return {
                "ok": True,
                "url": str(r.url),
                "title": title,
                "price": price,
                "currency": "ARS",
                "stock": stock,
                "seller": seller,
                "shipping_neuquen": ship,
                "verified_at": verified_at,
                "verification": "PROBABLE" if price is not None else "NO VERIFICADO",
            }
    except Exception as exc:  # noqa: BLE001
        return {
            "ok": False,
            "url": url,
            "error": f"{type(exc).__name__}: {exc}",
            "verified_at": verified_at,
            "verification": "NO VERIFICADO",
        }


# Curated seed URLs for known CODINEU 16813 line models (public pages).
# Used only as search seeds — prices always re-fetched live.
SEED_URLS_BY_KEYWORD: list[tuple[tuple[str, ...], list[str], str]] = [
    (
        ("ds-p7001", "p7001", "deltastream", "olt"),
        [
            "https://katech.com.ar/producto/ds-p7001-04-olt-tp-link-gpon-4-puertos2p-10g1p-giga-uplink/",
            "https://proxion.com.ar/producto/olt-4p-tp-link-ds-p7001-04-fibra-optica/",
        ],
        "Katech/Proxion",
    ),
    (
        ("ups3500", "atomlux", "ups 3500", "estabilizador"),
        [
            "https://depot.com.ar/productos/ups-estabilizador-de-tension-atomlux-ups3500-3500va-220v-ca-negro/",
        ],
        "Computers Depot",
    ),
    (
        ("fo-4075", "splitter", "1x16", "sc/apc"),
        [
            "https://dyrsistemas.com.ar/glc-plc-splitter-sm-1x16-con-conector-scapc-fo-4075-4368",
        ],
        "DYRSistemas",
    ),
    (
        ("fdb-012", "glc-fdb", "caja nap", "1x8"),
        [
            "https://tienda.sawerin.com.ar/productos/glc-fdb-012-01-caja-interior-ftth-fttb-1x8-sc-apc/",
        ],
        "Sawerin Networks",
    ),
]


def find_seed_urls(query: str) -> list[str]:
    q = query.lower()
    urls: list[str] = []
    for keys, ulist, _ in SEED_URLS_BY_KEYWORD:
        if any(k in q for k in keys):
            urls.extend(ulist)
    return list(dict.fromkeys(urls))


def search_real_pages(query: str, limit: int = 3) -> tuple[list[dict[str, Any]], str]:
    """Fetch curated public product pages matching query."""
    urls = find_seed_urls(query)[:limit]
    if not urls:
        return [], "no_seed_urls"
    results = []
    for url in urls:
        page = fetch_product_page(url)
        if not page.get("ok"):
            results.append(
                {
                    "title": query,
                    "price": None,
                    "currency": "ARS",
                    "url": url,
                    "seller": url.split("/")[2] if "://" in url else "unknown",
                    "verification": "NO VERIFICADO",
                    "source": "real_page_fetch_fail",
                    "stock": "NO VERIFICADO",
                    "shipping_neuquen": "NO VERIFICADO",
                    "verified_at": page.get("verified_at"),
                    "notes": page.get("error", ""),
                }
            )
            continue
        seller = page.get("seller") or (url.split("/")[2] if "://" in url else "")
        results.append(
            {
                "title": page.get("title") or query,
                "price": page.get("price"),
                "currency": page.get("currency") or "ARS",
                "url": page.get("url") or url,
                "seller": seller,
                "verification": page.get("verification") or "NO VERIFICADO",
                "source": "real_product_page",
                "stock": page.get("stock") or "NO VERIFICADO",
                "shipping_neuquen": page.get("shipping_neuquen") or "NO VERIFICADO",
                "verified_at": page.get("verified_at"),
                "notes": "fetched_live",
            }
        )
    return results, "real_product_pages"
