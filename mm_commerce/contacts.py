"""Discover REAL commercial contacts — never use product URL as CONTACTO."""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any
from urllib.parse import urljoin, urlparse

from mm_commerce.timing import now_ba

# Prefer sales-ish mailboxes
_SALES_LOCAL = frozenset(
    {
        "ventas",
        "venta",
        "sales",
        "comercial",
        "cotizaciones",
        "cotizacion",
        "presupuesto",
        "pedidos",
        "info",
        "contacto",
        "contact",
    }
)

_EMAIL_RE = re.compile(
    r"(?<![A-Z0-9._%+-])([A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,})(?![A-Z0-9._%+-])",
    re.I,
)
_WA_RE = re.compile(
    r"(?:wa\.me/|whatsapp\.com/send\?phone=|api\.whatsapp\.com/send\?phone=)(\+?\d{8,15})",
    re.I,
)
_PHONE_RE = re.compile(
    r"(?:\+54\s*9?\s*\d{2,4}[\s-]?\d{3,4}[\s-]?\d{3,4}"
    r"|(?:0?\d{2,4})[\s-]?\d{3,4}[\s-]?\d{3,4})",
)
_FORM_HREF_RE = re.compile(
    r'href=["\']([^"\']*(?:contacto|contactenos|contact|presupuesto|cotiz)[^"\']*)["\']',
    re.I,
)


@dataclass
class CommercialContact:
    razon_social: str = ""
    web: str = ""
    email: str = ""
    whatsapp: str = ""  # digits / wa.me target
    telefono: str = ""
    form_url: str = ""
    product_url: str = ""
    contacto: str = ""  # primary channel value shown as CONTACTO
    canal: str = ""  # WHATSAPP|EMAIL|FORM|PHONE|NONE
    source_urls: list[str] = field(default_factory=list)
    verified_at: str = ""
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _origin(url: str) -> str:
    p = urlparse(url)
    if not p.scheme or not p.netloc:
        return ""
    return f"{p.scheme}://{p.netloc}"


def _normalize_wa(digits: str) -> str:
    d = re.sub(r"\D", "", digits or "")
    if d.startswith("00"):
        d = d[2:]
    return d


def _is_product_path(url: str) -> bool:
    path = (urlparse(url).path or "").lower()
    bad = (
        "/producto",
        "/product",
        "/p/",
        "--det--",
        "/item",
        "/sku",
        "product_show",
        "/wi-tek-",
        "/glc-",
        "/ds-p",
    )
    return any(b in path for b in bad)


def _pick_email(emails: list[str]) -> str:
    if not emails:
        return ""
    ranked: list[tuple[int, str]] = []
    for e in emails:
        local = e.split("@", 1)[0].lower()
        score = 0
        if local in _SALES_LOCAL or any(local.startswith(s) for s in _SALES_LOCAL):
            score += 10
        if local.startswith("ventas") or local.startswith("sales"):
            score += 5
        if local in ("noreply", "no-reply", "donotreply"):
            score -= 20
        ranked.append((score, e))
    ranked.sort(key=lambda x: (-x[0], x[1]))
    return ranked[0][1]


def _extract_from_html(html: str, base: str) -> dict[str, Any]:
    emails = sorted({m.group(1).lower() for m in _EMAIL_RE.finditer(html)})
    # common obfuscations: name [at] domain [dot] com
    for m in re.finditer(
        r"([A-Z0-9._%+-]+)\s*(?:\[at\]|\(at\)|\s+at\s+)\s*([A-Z0-9.-]+)\s*(?:\[dot\]|\(dot\)|\s+dot\s+)\s*([A-Z]{2,})",
        html,
        re.I,
    ):
        emails.append(f"{m.group(1)}@{m.group(2)}.{m.group(3)}".lower())
    emails = sorted(set(emails))

    was = sorted({_normalize_wa(m.group(1)) for m in _WA_RE.finditer(html) if m.group(1)})
    phones: list[str] = []
    for m in _PHONE_RE.finditer(html):
        raw = m.group(0).strip()
        digits = re.sub(r"\D", "", raw)
        if len(digits) >= 8:
            phones.append(raw)
    # prefer tel: links
    for m in re.finditer(r'tel:([+\d\s\-()]+)', html, re.I):
        phones.insert(0, m.group(1).strip())

    forms: list[str] = []
    for m in _FORM_HREF_RE.finditer(html):
        href = m.group(1).strip()
        if href.startswith("#") or href.lower().startswith("javascript:"):
            continue
        abs_u = urljoin(base, href)
        if not _is_product_path(abs_u):
            forms.append(abs_u)

    title = ""
    tm = re.search(r"<title[^>]*>([^<]+)</title>", html, re.I)
    if tm:
        title = re.sub(r"\s+", " ", tm.group(1)).strip()

    return {
        "emails": emails,
        "whatsapps": was,
        "phones": phones,
        "forms": forms,
        "title": title,
    }


def _candidate_pages(product_or_web: str) -> list[str]:
    origin = _origin(product_or_web)
    if not origin:
        return []
    paths = [
        "/",
        "/contacto",
        "/contactenos",
        "/contactenos.php",
        "/contacto.php",
        "/contact",
        "/contact-us",
        "/empresa",
        "/about",
    ]
    out = [urljoin(origin, p) for p in paths]
    # also try origin without www flip
    return list(dict.fromkeys(out))


def discover_commercial_contact(
    *,
    product_url: str = "",
    web: str = "",
    razon_social_hint: str = "",
    fetch_html=None,
    timeout: float = 20.0,
) -> CommercialContact:
    """Order: WhatsApp → sales email → commercial form → phone. Never product URL."""
    seed = web or _origin(product_url) or product_url
    origin = _origin(seed) or _origin(product_url)
    contact = CommercialContact(
        razon_social=razon_social_hint or "",
        web=origin or web or "",
        product_url=product_url or "",
        verified_at=now_ba().isoformat(timespec="seconds"),
    )
    if not origin:
        contact.canal = "NONE"
        contact.contacto = "CONTACTO_NO_VERIFICADO"
        contact.notes = "sin web/origen para buscar contacto comercial"
        return contact

    def _default_fetch(url: str) -> str:
        import httpx

        r = httpx.get(
            url,
            timeout=timeout,
            follow_redirects=True,
            headers={"User-Agent": "Mozilla/5.0 (compatible; MM-Commerce-RFQ/1.0)"},
        )
        if r.status_code >= 400:
            return ""
        return r.text or ""

    fetch = fetch_html or _default_fetch
    emails: list[str] = []
    was: list[str] = []
    phones: list[str] = []
    forms: list[str] = []
    titles: list[str] = []

    for page in _candidate_pages(origin):
        try:
            html = fetch(page) or ""
        except Exception:
            html = ""
        if not html:
            continue
        contact.source_urls.append(page)
        got = _extract_from_html(html, page)
        emails.extend(got["emails"])
        was.extend(got["whatsapps"])
        phones.extend(got["phones"])
        forms.extend(got["forms"])
        if got["title"]:
            titles.append(got["title"])

    emails = sorted(set(emails))
    was = list(dict.fromkeys(was))
    phones = list(dict.fromkeys(phones))
    forms = list(dict.fromkeys(forms))

    contact.email = _pick_email(emails)
    contact.whatsapp = was[0] if was else ""
    contact.telefono = phones[0] if phones else ""
    contact.form_url = forms[0] if forms else ""
    def _looks_like_domain(name: str) -> bool:
        n = (name or "").lower().strip()
        return "." in n and " " not in n

    title_name = ""
    for t in titles:
        name = re.split(r"[|\-–—]", t)[0].strip()
        if name and len(name) < 80 and not _looks_like_domain(name):
            title_name = name
            break
    if title_name and (
        not contact.razon_social or _looks_like_domain(contact.razon_social)
    ):
        contact.razon_social = title_name
    if not contact.razon_social and origin:
        contact.razon_social = urlparse(origin).netloc.replace("www.", "")

    if contact.whatsapp:
        contact.canal = "WHATSAPP"
        contact.contacto = f"https://wa.me/{contact.whatsapp}"
    elif contact.email:
        contact.canal = "EMAIL"
        contact.contacto = contact.email
    elif contact.form_url:
        contact.canal = "FORM"
        contact.contacto = contact.form_url
    elif contact.telefono:
        contact.canal = "PHONE"
        contact.contacto = contact.telefono
    else:
        contact.canal = "NONE"
        contact.contacto = "CONTACTO_NO_VERIFICADO"
        contact.notes = "no se halló WhatsApp/email/form/teléfono comercial"

    # Safety: never leave product URL as contacto
    if contact.contacto == product_url or (
        product_url and contact.contacto.startswith(product_url.split("?")[0])
    ):
        contact.contacto = "CONTACTO_NO_VERIFICADO"
        contact.canal = "NONE"
        contact.notes = "bloqueado: product URL no es CONTACTO válido"
    return contact


def persist_supplier_contact(supplier, contact: CommercialContact) -> None:
    """Store contact fields on Supplier (columns + notes JSON backup)."""
    supplier.razon_social = contact.razon_social or supplier.razon_social or supplier.name
    supplier.web = contact.web or supplier.web or ""
    supplier.email = contact.email or supplier.email or ""
    supplier.whatsapp = contact.whatsapp or supplier.whatsapp or ""
    supplier.telefono = contact.telefono or supplier.telefono or ""
    supplier.contact_verified_at = contact.verified_at
    # Keep product listing URL separate from commercial web if url was product
    if contact.web and (not supplier.url or _is_product_path(supplier.url)):
        supplier.url = contact.web
    blob = {}
    try:
        blob = json.loads(supplier.notes or "{}")
        if not isinstance(blob, dict):
            blob = {"legacy": supplier.notes}
    except Exception:
        blob = {"legacy": supplier.notes or ""}
    blob["commercial_contact"] = contact.to_dict()
    supplier.notes = json.dumps(blob, ensure_ascii=False)
