"""Real supplier page fetch — parse public prices; never invent."""
from __future__ import annotations

import json
import re
from typing import Any

import httpx

from mm_commerce.matching import infer_category
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
        s = s.replace(".", "").replace(",", ".")
    elif "," in s:
        parts = s.split(",")
        if len(parts[-1]) == 2:
            s = s.replace(".", "").replace(",", ".")
        else:
            s = s.replace(",", "")
    else:
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
            desc = prod.get("description") or ""
            out.append(
                {
                    "title": prod.get("name") or "",
                    "description": desc if isinstance(desc, str) else "",
                    "price": price_f,
                    "currency": offer.get("priceCurrency") or "ARS",
                    "stock": stock,
                    "seller": (offer.get("seller") or {}).get("name")
                    if isinstance(offer.get("seller"), dict)
                    else "",
                }
            )
    return out


def _strip_html(html: str) -> str:
    text = re.sub(r"(?is)<script[^>]*>.*?</script>", " ", html or "")
    text = re.sub(r"(?is)<style[^>]*>.*?</style>", " ", text)
    text = re.sub(r"(?is)<[^>]+>", " ", text)
    text = re.sub(r"&\w+;", " ", text)
    return re.sub(r"\s+", " ", text).strip()[:8000]


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
            description = ""
            stock = "NO VERIFICADO"
            seller = ""
            if ld:
                best = next((x for x in ld if x.get("price")), ld[0])
                price = best.get("price")
                title = best.get("title") or ""
                description = best.get("description") or ""
                stock = best.get("stock") or stock
                seller = best.get("seller") or ""
            # Prefer <title> / h1 if ld title empty or suspicious
            if not title:
                m = re.search(r"<h1[^>]*>(.*?)</h1>", html, re.I | re.S)
                if m:
                    title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
                else:
                    m = re.search(r"<title[^>]*>(.*?)</title>", html, re.I | re.S)
                    if m:
                        title = re.sub(r"<[^>]+>", "", m.group(1)).strip()
            raw_text = _strip_html(html)
            if price is None:
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
                "description": description,
                "raw_text": raw_text,
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


# Curated seed URLs — matched by CATEGORY + strong model tokens (not loose keywords).
# Prevents splitter page matching ODF, or NAP support matching NAP box.
SEED_CATALOG: list[dict[str, Any]] = [
    {
        "categories": {"olt_gpon"},
        "require_any": ("ds-p7001", "p7001", "deltastream"),
        "urls": [
            "https://katech.com.ar/producto/ds-p7001-04-olt-tp-link-gpon-4-puertos2p-10g1p-giga-uplink/",
            "https://proxion.com.ar/producto/olt-4p-tp-link-ds-p7001-04-fibra-optica/",
        ],
        "seller": "Katech/Proxion",
    },
    {
        "categories": {"ups_estabilizador"},
        "require_any": ("ups3500", "ups 3500", "3000 va", "3000va", "atomlux"),
        "urls": [
            "https://depot.com.ar/productos/ups-estabilizador-de-tension-atomlux-ups3500-3500va-220v-ca-negro/",
        ],
        "seller": "Computers Depot",
    },
    {
        "categories": {"odf_caja_empalme"},
        "require_any": ("odf", "caja de empalme", "12 puertos"),
        "forbid": ("splitter", "1x16", "fo-4075", "caja nap", "soporte"),
        "urls": [
            # Public ODF listings are scarce; leave empty → NO VERIFICADO rather than wrong category
        ],
        "seller": "",
    },
    {
        "categories": {"splitter_plc"},
        "require_any": ("fo-4075", "1x16", "splitter", "plc"),
        "forbid": ("odf", "caja de empalme", "caja nap", "fdb"),
        "urls": [
            "https://dyrsistemas.com.ar/glc-plc-splitter-sm-1x16-con-conector-scapc-fo-4075-4368",
        ],
        "seller": "DYRSistemas",
    },
    {
        "categories": {"caja_nap"},
        "require_any": ("fdb-012", "glc-fdb", "caja nap", "1x8"),
        "forbid": ("soporte", "fijación", "fijacion", "mount"),
        "urls": [
            "https://tienda.sawerin.com.ar/productos/glc-fdb-012-01-caja-interior-ftth-fttb-1x8-sc-apc/",
        ],
        "seller": "Sawerin Networks",
    },
    {
        "categories": {"access_point_indoor"},
        "require_any": ("wi-ap217", "ap217", "access point interior", "wi-tek"),
        "urls": [],
        "seller": "",
    },
    {
        "categories": {"access_point_outdoor"},
        "require_any": ("9163e", "meraki", "catalyst 9163", "wifi 6e outdoor"),
        "urls": [],
        "seller": "",
    },
]


def find_seed_urls(query: str) -> list[str]:
    q = (query or "").lower()
    cat = infer_category(query)
    urls: list[str] = []
    for entry in SEED_CATALOG:
        cats = entry.get("categories") or set()
        if cat not in cats and cat != "unknown":
            continue
        forbid = entry.get("forbid") or ()
        if any(f in q for f in forbid):
            # If query itself is the forbidden category, skip this seed
            # (e.g. ODF query must not get splitter seeds — handled by category)
            pass
        req = entry.get("require_any") or ()
        if req and not any(k in q for k in req):
            # category matched but weak model tokens — still allow if category exact
            if cat not in cats:
                continue
        urls.extend(entry.get("urls") or [])
    # Fallback: if nothing by category, try strong model tokens only (no cross-category)
    if not urls:
        for entry in SEED_CATALOG:
            req = entry.get("require_any") or ()
            forbid = entry.get("forbid") or ()
            if any(f in q for f in forbid) and cat not in (entry.get("categories") or set()):
                continue
            if any(k in q for k in req):
                # Only if inferred category matches or unknown
                if cat == "unknown" or cat in (entry.get("categories") or set()):
                    urls.extend(entry.get("urls") or [])
    return list(dict.fromkeys(urls))


def search_real_pages(query: str, limit: int = 3) -> tuple[list[dict[str, Any]], str]:
    """Fetch curated public product pages matching query (category-aware)."""
    urls = find_seed_urls(query)[:limit]
    if not urls:
        return [], "no_seed_urls"
    results = []
    for url in urls:
        page = fetch_product_page(url)
        if not page.get("ok"):
            results.append(
                {
                    "title": query[:200],
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
                    "raw_text": "",
                    "description": "",
                }
            )
            continue
        seller = page.get("seller") or (url.split("/")[2] if "://" in url else "")
        results.append(
            {
                "title": page.get("title") or query[:200],
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
                "raw_text": page.get("raw_text") or "",
                "description": page.get("description") or "",
            }
        )
    return results, "real_product_pages"
