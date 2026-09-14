"""Authoritative technical specs — manufacturer / official datasheet / distributor.

Ecommerce pages are OK for price/stock/shipping only. Technical evidence must
prefer higher-tier sources (no weak keyword inference from seller titles).
"""
from __future__ import annotations

from typing import Any

# Curated from official Wi-Tek datasheet / authorized distributor (By Demes).
# Sources checked 2026-09-14.
AUTHORITATIVE_BY_MODEL: dict[str, dict[str, Any]] = {
    "WI-AP217-LITE": {
        "model": "WI-AP217-Lite",
        "brand": "Wi-Tek",
        "tier": "official_distributor",
        "source_url": "https://bydemes.com/en/products/networking/wireless/access-points/WITEK-0167",
        "manufacturer_url": "https://www.wireless-tek.com/product_show.php?id=352",
        "datasheet_url": "https://www.wireless-tek.com/files_down.php?id=316",
        "title": "Ceiling Mount WiFi 5 Access Point — WI-AP217-Lite V2",
        "text": (
            "WI-AP217-Lite V2 Ceiling Mount WiFi 5 Access Point. "
            "Installation environment: Indoor. Ceiling mount / wall mounting. "
            "Operating bands: 2.4 GHz and 5 GHz. Dual band. "
            "300Mbps at 2.4GHz + 867Mbps at 5GHz (wireless speed up to 1200Mbps). "
            "Access Point Wi-Tek WI-AP217-Lite. IEEE 802.11a/b/g/n/ac. "
            "2x Gigabit Ethernet ports. 802.3af PoE."
        ),
    },
    "WI-AP217-LITE V2": {
        "model": "WI-AP217-Lite",
        "brand": "Wi-Tek",
        "tier": "official_distributor",
        "source_url": "https://bydemes.com/en/products/networking/wireless/access-points/WITEK-0167",
        "title": "Ceiling Mount WiFi 5 Access Point — WI-AP217-Lite V2",
        "text": (
            "WI-AP217-Lite V2 Ceiling Mount WiFi 5 Access Point. "
            "Installation environment: Indoor. Ceiling mount. "
            "Operating bands: 2.4 GHz and 5 GHz. "
            "300Mbps at 2.4GHz + 867Mbps at 5GHz up to 1200Mbps. "
            "Access Point Wi-Tek WI-AP217-Lite."
        ),
    },
}


def _norm_model(model: str) -> str:
    return (
        (model or "")
        .upper()
        .replace(" ", "")
        .replace("_", "-")
        .strip("()[]")
    )


def lookup_authoritative_specs(*, brand: str = "", model: str = "") -> dict[str, Any] | None:
    key = _norm_model(model)
    if not key:
        return None
    # strip parentheses leftovers
    key = key.replace("(", "").replace(")", "")
    if key in AUTHORITATIVE_BY_MODEL:
        return dict(AUTHORITATIVE_BY_MODEL[key])
    # try without V2 suffix variants
    for k, v in AUTHORITATIVE_BY_MODEL.items():
        if key.startswith(k) or k.startswith(key):
            return dict(v)
    # brand+model loose
    blob = f"{brand} {model}".upper()
    if "AP217" in blob and "LITE" in blob:
        return dict(AUTHORITATIVE_BY_MODEL["WI-AP217-LITE"])
    return None


def merge_tech_candidate_text(
    *,
    brand: str = "",
    model: str = "",
    ecommerce_title: str = "",
    ecommerce_text: str = "",
) -> tuple[str, str, str, dict[str, Any] | None]:
    """Return (title, text, tech_source_url, auth_meta).

    Technical matching prefers authoritative distributor/manufacturer text.
    Ecommerce title/text may still contribute model identity tokens.
    """
    auth = lookup_authoritative_specs(brand=brand, model=model)
    if not auth:
        title = ecommerce_title or ""
        text = f"{ecommerce_title} {ecommerce_text}".strip()
        return title, text, "", None
    title = auth.get("title") or ecommerce_title or auth.get("model") or ""
    # Auth text first; keep ecommerce model/title tokens for identity only
    text = f"{auth.get('text', '')} {ecommerce_title} {model} {brand}".strip()
    return title, text, str(auth.get("source_url") or ""), auth
