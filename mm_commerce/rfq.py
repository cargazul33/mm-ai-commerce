"""Production RFQ Agent — draft only; NEVER auto-send until Mariano approves.

For TECHNICAL EXACTO candidates:
1. Verify 100% HARD_REQUIREMENTS (block RFQ if not)
2. Attach technical evidence_matrix
3. Find REAL commercial contact: WhatsApp → sales email → form → phone
   NEVER use product URL as CONTACTO
4. Store razón social, web, email, WhatsApp/teléfono, product URL, verified_at (BA)

Telegram buttons prepare/copy only:
[ENVIAR WHATSAPP] [ENVIAR EMAIL] [COPIAR] [DESCARTAR]
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any
from urllib.parse import quote

from mm_commerce.config import get_settings
from mm_commerce.contacts import CommercialContact, discover_commercial_contact, persist_supplier_contact
from mm_commerce.matching import (
    AttrEvidence,
    COMM_DISPONIBLE,
    COMM_ENVIO_NO_VER,
    COMM_PRECIO_NO_VER,
    COMM_SIN_STOCK,
    COMM_STOCK_INSUF,
    COMM_STOCK_NO_VER,
    TECH_EXACTO,
    validate_evidence_consistency,
)
from mm_commerce.timing import now_ba

RFQ_TEMPLATE = """Hola,

Somos M&M Insumos, de Neuquén Capital.

Estamos cotizando una contratación y necesitamos presupuesto para:

Producto: {producto}
Marca/modelo requerido: {marca_modelo}
Cantidad: {cantidad}

Especificaciones:
{hard_requirements}

Solicitamos por favor informar:

• Precio unitario + IVA
• Precio final
• Stock disponible
• Plazo de despacho
• Costo de envío a Neuquén Capital
• Forma de pago
• Vigencia de la oferta
• Garantía

Por favor confirmar también que el producto cotizado corresponde exactamente a:
{modelo_spec}

Muchas gracias.
M&M Insumos"""


@dataclass
class RfqDraft:
    rfq_id: str
    opportunity_id: str
    line_no: int
    proveedor: str
    contacto: str
    producto: str
    requisitos: str
    cantidad: float
    mensaje: str
    url: str = ""  # product URL (reference only — NEVER CONTACTO)
    missing: list[str] = field(default_factory=list)
    status: str = "DRAFT"  # DRAFT|PREPARED_WA|PREPARED_EMAIL|COPIED|QUEUED_SEND|SENT|DISCARDED|BLOCKED
    created_at: str = ""
    # production fields
    blocked: bool = False
    block_reason: str = ""
    hard_requirements_ok: bool = False
    evidence_matrix: list[dict[str, Any]] = field(default_factory=list)
    evidence_summary: str = ""
    marca_modelo: str = ""
    canal: str = ""
    razon_social: str = ""
    web: str = ""
    email: str = ""
    whatsapp: str = ""
    telefono: str = ""
    form_url: str = ""
    contact_verified_at: str = ""
    quote_id: int | None = None
    technical_status: str = ""
    commercial_status: str = ""
    prepared: dict[str, Any] = field(default_factory=dict)
    send_note: str = "NUNCA auto-send — requiere aprobación explícita de Mariano"

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _missing_commercial(q) -> list[str]:
    miss: list[str] = []
    if q.unit_cost is None:
        miss.append("precio")
    stock = str(q.stock_note or "")
    if stock.strip() in ("", "NO VERIFICADO", "0", "OutOfStock"):
        miss.append("stock")
    ship = str(q.shipping_neuquen or "")
    if ship.strip() in ("", "NO VERIFICADO"):
        miss.append("logística/envío Neuquén")
    qty = q.qty
    if qty is None or qty <= 0:
        miss.append("cantidad")
    # stock vs required qty
    needed = float(q.qty or 0)
    if needed > 0 and stock.strip().isdigit() and int(stock.strip()) < needed:
        if "stock" not in miss:
            miss.append("stock")
    return miss


def _load_evidence(q) -> tuple[list[AttrEvidence], list[dict], dict]:
    evid: dict = {}
    if q and q.evidence_json:
        try:
            evid = json.loads(q.evidence_json or "{}")
        except Exception:
            evid = {}
    evidence_list = evid.get("evidence") or []
    ev_objs = [
        AttrEvidence(
            key=e.get("key", ""),
            label=e.get("label", ""),
            required=e.get("required", ""),
            found=e.get("found", ""),
            source_url=e.get("source_url", ""),
            result=e.get("result", "NO_VERIFICADO"),
            mandatory=bool(e.get("mandatory", True)),
        )
        for e in evidence_list
        if isinstance(e, dict)
    ]
    return ev_objs, evidence_list, evid


def verify_hard_requirements_100(q) -> dict[str, Any]:
    """Return ok=True only if ALL mandatory hard attrs are CUMPLE (100%)."""
    ev_objs, evidence_list, evid = _load_evidence(q)
    hard = evid.get("hard_requirements") or []
    check = validate_evidence_consistency(
        evidence=ev_objs,
        hard_requirements=hard,
        evidence_ok=evid.get("evidence_ok"),
        evidence_total=evid.get("evidence_total"),
    )
    mand = [e for e in ev_objs if e.mandatory]
    fails = [e for e in mand if e.result != "CUMPLE"]
    total = int(check.get("hard_requirements_total") or len(mand))
    ok_n = int(check.get("pass_count") or 0)
    consistent = bool(check.get("consistent"))
    all_pass = total > 0 and ok_n == total and not fails and consistent
    matrix = [
        {
            "ATTR": e.key or e.label,
            "REQUIRED": e.required,
            "FOUND": e.found,
            "SOURCE": e.source_url,
            "RESULT": e.result,
        }
        for e in mand
    ]
    return {
        "ok": all_pass,
        "pass_count": ok_n,
        "total": total,
        "fails": [{"key": e.key, "result": e.result} for e in fails],
        "validation_error": check.get("validation_error"),
        "evidence_matrix": matrix,
        "summary": f"{ok_n}/{total}",
        "raw_evidence": evidence_list,
        "evid": evid,
    }


def _marca_modelo_from(it, evid: dict, q) -> str:
    brand = (it.brand or "").strip()
    model = (it.model or "").strip()
    if not brand:
        brand = str(evid.get("brand") or "").strip()
    if not model:
        model = str(evid.get("model") or "").strip()
    # fall back to hard req found values
    for e in evid.get("evidence") or []:
        if not isinstance(e, dict):
            continue
        k = (e.get("key") or "").lower()
        if k in ("brand", "marca") and not brand:
            brand = str(e.get("found") or e.get("required") or "")
        if k in ("model", "modelo") and not model:
            model = str(e.get("found") or e.get("required") or "")
    label = (q.product_label or it.product or "").strip()
    if brand and model:
        return f"{brand} {model}".strip()
    if model:
        return model
    if brand:
        return brand
    return label[:120]


def _format_hard_specs(matrix: list[dict], requisitos: str) -> str:
    lines = []
    for row in matrix:
        lines.append(
            f"- {row.get('ATTR')}: requerido={row.get('REQUIRED')} | "
            f"encontrado={row.get('FOUND')} | {row.get('RESULT')}"
        )
    if lines:
        return "\n".join(lines)
    return (requisitos or "N/D")[:800]


def build_rfq_message(
    *,
    producto: str,
    marca_modelo: str,
    cantidad: float,
    hard_requirements: str,
    modelo_spec: str,
) -> str:
    return RFQ_TEMPLATE.format(
        producto=producto,
        marca_modelo=marca_modelo,
        cantidad=cantidad,
        hard_requirements=hard_requirements,
        modelo_spec=modelo_spec,
    )


def build_rfq_drafts_for_opportunity(
    session,
    opp,
    *,
    line_nos: set[int] | None = None,
    fetch_html=None,
    discover: bool = True,
) -> list[RfqDraft]:
    """When TECH EXACTO + 100% hard reqs + commercial incomplete → draft RFQ (no send)."""
    from mm_commerce.models import Supplier, SupplierQuote, Tender

    tender = session.query(Tender).filter_by(opportunity_id=opp.id).one_or_none()
    if not tender:
        return []
    quotes = session.query(SupplierQuote).filter_by(opportunity_id=opp.id).all()
    best: dict[int, Any] = {}
    for q in sorted(
        quotes,
        key=lambda x: (
            0 if (x.technical_status or "") == TECH_EXACTO else 1,
            -(x.match_pct or 0),
            0 if x.unit_cost is not None else 1,
        ),
    ):
        if q.tender_item_id is not None and q.tender_item_id not in best:
            best[q.tender_item_id] = q

    drafts: list[RfqDraft] = []
    blocked_log: list[RfqDraft] = []
    ts = now_ba().strftime("%Y%m%d%H%M%S")
    for it in tender.items:
        if line_nos is not None and int(it.line_no) not in line_nos:
            continue
        q = best.get(it.id)
        if q is None:
            continue
        tech = (q.technical_status or q.match_class or "").strip()
        if tech != TECH_EXACTO:
            continue
        miss = _missing_commercial(q)
        comm = (q.commercial_status or "").strip()
        if comm == COMM_DISPONIBLE and not miss:
            continue
        if not miss:
            miss = ["precio/stock/logística incompletos"]

        hard = verify_hard_requirements_100(q)
        supplier = session.get(Supplier, q.supplier_id) if q.supplier_id else None
        proveedor = (supplier.name if supplier else "") or "PROVEEDOR_NO_IDENTIFICADO"
        product_url = q.url or ""
        evid = hard["evid"]
        marca_modelo = _marca_modelo_from(it, evid, q)
        hard_text = _format_hard_specs(hard["evidence_matrix"], it.specs or it.product or "")
        modelo_spec = marca_modelo or (q.product_label or it.product or "")[:200]
        mensaje = build_rfq_message(
            producto=(q.product_label or it.product or "")[:240],
            marca_modelo=marca_modelo,
            cantidad=float(it.qty or 1),
            hard_requirements=hard_text,
            modelo_spec=modelo_spec,
        )

        contact = CommercialContact(
            razon_social=getattr(supplier, "razon_social", "") or proveedor,
            web=getattr(supplier, "web", "") or "",
            email=getattr(supplier, "email", "") or "",
            whatsapp=getattr(supplier, "whatsapp", "") or "",
            telefono=getattr(supplier, "telefono", "") or "",
            product_url=product_url,
            contacto="CONTACTO_NO_VERIFICADO",
            canal="NONE",
            verified_at=now_ba().isoformat(timespec="seconds"),
        )
        if discover:
            contact = discover_commercial_contact(
                product_url=product_url,
                web=(getattr(supplier, "web", "") or "")
                or (getattr(supplier, "url", "") if supplier and not _url_looks_product(getattr(supplier, "url", "")) else "")
                or "",
                razon_social_hint=proveedor,
                fetch_html=fetch_html,
            )
            if supplier is not None:
                persist_supplier_contact(supplier, contact)
                session.commit()

        rfq_id = f"rfq-{opp.external_id}-R{it.line_no}-{ts}"
        base = RfqDraft(
            rfq_id=rfq_id,
            opportunity_id=str(opp.external_id),
            line_no=int(it.line_no),
            proveedor=contact.razon_social or proveedor,
            contacto=contact.contacto,
            producto=(q.product_label or it.product)[:240],
            requisitos=(it.specs or it.product or "")[:500],
            cantidad=float(it.qty or 1),
            mensaje=mensaje,
            url=product_url,
            missing=miss,
            status="DRAFT",
            created_at=now_ba().isoformat(timespec="seconds"),
            hard_requirements_ok=bool(hard["ok"]),
            evidence_matrix=hard["evidence_matrix"],
            evidence_summary=hard["summary"],
            marca_modelo=marca_modelo,
            canal=contact.canal,
            razon_social=contact.razon_social or proveedor,
            web=contact.web,
            email=contact.email,
            whatsapp=contact.whatsapp,
            telefono=contact.telefono,
            form_url=contact.form_url,
            contact_verified_at=contact.verified_at,
            quote_id=q.id,
            technical_status=tech,
            commercial_status=comm,
        )

        if not hard["ok"]:
            base.blocked = True
            base.status = "BLOCKED"
            fails = ", ".join(f"{f['key']}={f['result']}" for f in hard["fails"]) or "incomplete"
            base.block_reason = (
                f"HARD_REQUIREMENTS no 100% ({hard['summary']}); fails=[{fails}]"
                + (f"; {hard['validation_error']}" if hard.get("validation_error") else "")
            )
            blocked_log.append(base)
            continue

        if contact.canal == "NONE" or contact.contacto in ("", "CONTACTO_NO_VERIFICADO"):
            base.blocked = True
            base.status = "BLOCKED"
            base.block_reason = "CONTACTO comercial real no encontrado (WhatsApp/email/form/tel)"
            blocked_log.append(base)
            continue

        # Final guard: CONTACTO must not be product URL
        if product_url and base.contacto.rstrip("/") == product_url.rstrip("/"):
            base.blocked = True
            base.status = "BLOCKED"
            base.block_reason = "CONTACTO inválido: product URL prohibida"
            blocked_log.append(base)
            continue

        drafts.append(base)

    # Persist blocked for audit alongside drafts
    if blocked_log:
        _persist_blocked(blocked_log, opportunity_id=str(opp.external_id))
    return drafts


def _url_looks_product(url: str) -> bool:
    from mm_commerce.contacts import _is_product_path

    return bool(url) and _is_product_path(url)


def _persist_blocked(drafts: list[RfqDraft], *, opportunity_id: str) -> Path:
    settings = get_settings()
    out_dir = settings.project_root / "offers_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"rfq_blocked_{opportunity_id}.json"
    path.write_text(
        json.dumps([d.to_dict() for d in drafts], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def persist_rfq_drafts(drafts: list[RfqDraft], *, opportunity_id: str) -> Path:
    settings = get_settings()
    out_dir = settings.project_root / "offers_out"
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"rfq_drafts_{opportunity_id}.json"
    path.write_text(
        json.dumps([d.to_dict() for d in drafts], indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return path


def load_rfq_draft(rfq_id: str) -> tuple[dict | None, Path | None]:
    settings = get_settings()
    drafts_dir = settings.project_root / "offers_out"
    for p in list(drafts_dir.glob("rfq_drafts_*.json")) + list(
        drafts_dir.glob("rfq_blocked_*.json")
    ):
        try:
            items = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for it in items:
            if it.get("rfq_id") == rfq_id:
                return it, p
    return None, None


def save_rfq_draft(found: dict, path: Path) -> None:
    items = json.loads(path.read_text(encoding="utf-8"))
    for i, it in enumerate(items):
        if it.get("rfq_id") == found.get("rfq_id"):
            items[i] = found
            break
    path.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")


def format_rfq_telegram(draft: RfqDraft | dict) -> str:
    d = draft.to_dict() if isinstance(draft, RfqDraft) else draft
    matrix = d.get("evidence_matrix") or []
    ev_lines = []
    for row in matrix[:12]:
        ev_lines.append(
            f"  · {row.get('ATTR')}: {row.get('RESULT')} "
            f"(req={row.get('REQUIRED')}; found={row.get('FOUND')})"
        )
    ev_block = "\n".join(ev_lines) if ev_lines else "  (sin matriz)"
    status = d.get("status") or "DRAFT"
    blocked = d.get("blocked")
    head = "📝 RFQ ASISTIDO (NO ENVIADO)"
    if blocked or status == "BLOCKED":
        head = "🚫 RFQ BLOQUEADO"
    email_ok = bool((d.get("email") or "").strip() and "@" in (d.get("email") or ""))
    btns = "[ENVIAR WHATSAPP] "
    btns += "[ENVIAR EMAIL] " if email_ok else "[EMAIL DESHABILITADO] "
    btns += "[COPIAR] [DESCARTAR]"
    return "\n".join(
        [
            f"{head} · {d.get('rfq_id')}",
            f"PROVEEDOR / RAZÓN SOCIAL: {d.get('razon_social') or d.get('proveedor')}",
            f"WEB: {d.get('web') or '—'}",
            f"CONTACTO ({d.get('canal') or '?'}): {d.get('contacto')}",
            f"EMAIL: {d.get('email') or '—'} · WA: {d.get('whatsapp') or '—'} · TEL: {d.get('telefono') or '—'}",
            f"PRODUCTO: {d.get('producto')}",
            f"MARCA/MODELO: {d.get('marca_modelo') or '—'}",
            f"CANTIDAD: {d.get('cantidad')}",
            f"TECH: {d.get('technical_status')} · COMM: {d.get('commercial_status')}",
            f"HARD_REQ: {d.get('evidence_summary')} · OK={d.get('hard_requirements_ok')}",
            f"FALTA: {', '.join(d.get('missing') or [])}",
            f"PRODUCT URL (ref): {d.get('url') or '—'}",
            f"verified_at: {d.get('contact_verified_at') or d.get('created_at')}",
            "",
            "EVIDENCE_MATRIX:",
            ev_block,
            "",
            "MENSAJE:",
            (d.get("mensaje") or "")[:1800],
            "",
            btns,
            "⚠ Ningún botón envía sin aprobación explícita de Mariano",
            (f"BLOQUEO: {d.get('block_reason')}" if d.get("block_reason") else ""),
        ]
    ).strip()


def rfq_inline_keyboard(
    rfq_id: str,
    *,
    email: str | None = None,
    whatsapp: str | None = None,
    draft: dict | None = None,
) -> dict:
    """Telegram buttons — prepare/copy only; NEVER auto-send.

    ENVIAR EMAIL is omitted/disabled when email is NULL/empty.
    """
    rid = rfq_id[:48]
    d = draft or {}
    em = (email if email is not None else d.get("email") or "").strip()
    row1 = [{"text": "ENVIAR WHATSAPP", "callback_data": f"rfq_wa:{rid}"}]
    if em and "@" in em:
        row1.append({"text": "ENVIAR EMAIL", "callback_data": f"rfq_email:{rid}"})
    return {
        "inline_keyboard": [
            row1,
            [
                {"text": "COPIAR", "callback_data": f"rfq_copy:{rid}"},
                {"text": "DESCARTAR", "callback_data": f"rfq_discard:{rid}"},
            ],
        ]
    }


def prepare_whatsapp_link(draft: dict) -> dict[str, Any]:
    wa = (draft.get("whatsapp") or "").strip()
    if not wa:
        # try parse from contacto
        m = re.search(r"wa\.me/(\d+)", draft.get("contacto") or "")
        wa = m.group(1) if m else ""
    if not wa:
        return {"ok": False, "error": "NO_WHATSAPP"}
    text = draft.get("mensaje") or ""
    link = f"https://wa.me/{wa}?text={quote(text)}"
    return {
        "ok": True,
        "channel": "WHATSAPP",
        "link": link,
        "whatsapp": wa,
        "auto_sent": False,
        "note": "PREPARED only — Mariano must open/send manually",
    }


def prepare_email_draft(draft: dict) -> dict[str, Any]:
    email = (draft.get("email") or "").strip()
    if not email or "@" not in email:
        return {"ok": False, "error": "NO_EMAIL"}
    subject = (
        f"Solicitud de cotización — M&M Insumos — "
        f"{draft.get('producto', '')[:60]} x{draft.get('cantidad')}"
    )
    body = draft.get("mensaje") or ""
    mailto = f"mailto:{email}?subject={quote(subject)}&body={quote(body)}"
    return {
        "ok": True,
        "channel": "EMAIL",
        "mailto": mailto,
        "email": email,
        "subject": subject,
        "body": body,
        "auto_sent": False,
        "note": "PREPARED only — Mariano must send manually",
    }


def handle_rfq_callback(data: str) -> dict[str, Any]:
    """Callbacks prepare/copy/discard — NEVER send WhatsApp/email automatically."""
    if ":" not in data:
        return {"ok": False, "error": "BAD_RFQ_CALLBACK"}
    action, rfq_id = data.split(":", 1)
    action = action.strip().lower()
    rfq_id = rfq_id.strip()
    found, path_hit = load_rfq_draft(rfq_id)
    # tolerate truncated callback ids
    if not found and len(rfq_id) >= 12:
        settings = get_settings()
        for p in (settings.project_root / "offers_out").glob("rfq_drafts_*.json"):
            try:
                items = json.loads(p.read_text(encoding="utf-8"))
            except Exception:
                continue
            for it in items:
                if str(it.get("rfq_id", "")).startswith(rfq_id) or rfq_id in str(
                    it.get("rfq_id", "")
                ):
                    found, path_hit = it, p
                    rfq_id = it["rfq_id"]
                    break
            if found:
                break
    if not found:
        return {"ok": False, "error": "RFQ_NOT_FOUND", "rfq_id": rfq_id}
    if found.get("blocked") or found.get("status") == "BLOCKED":
        return {
            "ok": False,
            "error": "RFQ_BLOCKED",
            "rfq_id": rfq_id,
            "block_reason": found.get("block_reason"),
        }

    if action in ("rfq_wa", "rfq_whatsapp", "enviar_whatsapp"):
        prep = prepare_whatsapp_link(found)
        if not prep.get("ok"):
            return {"ok": False, "error": prep.get("error"), "rfq_id": rfq_id}
        found["status"] = "PREPARED_WA"
        found["prepared"] = prep
        found["send_note"] = "STUB: WhatsApp NO enviado — pendiente Mariano"
        result = {
            "ok": True,
            "action": "rfq_wa_prepare",
            "rfq_id": rfq_id,
            "status": "PREPARED_WA",
            "prepared": prep,
            "detail": (
                f"WhatsApp PREPARADO (no enviado)\n"
                f"Número: {prep.get('whatsapp')}\n"
                f"Link: {prep.get('link')[:500]}\n"
                f"Abrí el link manualmente para enviar."
            ),
            "warning": "NUNCA auto-send — stub until Mariano approves",
        }
    elif action in ("rfq_email", "enviar_email"):
        prep = prepare_email_draft(found)
        if not prep.get("ok"):
            return {"ok": False, "error": prep.get("error"), "rfq_id": rfq_id}
        found["status"] = "PREPARED_EMAIL"
        found["prepared"] = prep
        found["send_note"] = "STUB: Email NO enviado — pendiente Mariano"
        result = {
            "ok": True,
            "action": "rfq_email_prepare",
            "rfq_id": rfq_id,
            "status": "PREPARED_EMAIL",
            "prepared": {k: v for k, v in prep.items() if k != "body"},
            "detail": (
                f"Email PREPARADO (no enviado)\n"
                f"Para: {prep.get('email')}\n"
                f"Asunto: {prep.get('subject')}\n"
                f"mailto: {prep.get('mailto')[:400]}\n"
                f"Copiá/abrí mailto manualmente para enviar."
            ),
            "warning": "NUNCA auto-send — stub until Mariano approves",
        }
    elif action in ("rfq_copy", "copiar"):
        found["status"] = "COPIED"
        found["prepared"] = {"channel": "COPY", "text": found.get("mensaje"), "auto_sent": False}
        result = {
            "ok": True,
            "action": "rfq_copy",
            "rfq_id": rfq_id,
            "status": "COPIED",
            "detail": found.get("mensaje") or "",
            "warning": "Texto listo para copiar — no se envió nada",
        }
    elif action in ("rfq_discard", "descartar"):
        found["status"] = "DISCARDED"
        result = {"ok": True, "action": "rfq_discard", "rfq_id": rfq_id, "status": "DISCARDED"}
    elif action in ("rfq_send", "enviar_rfq"):
        # Legacy button — still NEVER sends
        found["status"] = "QUEUED_SEND"
        found["send_note"] = "STUB: no email/WhatsApp enviado — pendiente aprobación Mariano"
        result = {
            "ok": True,
            "action": "rfq_send_stub",
            "rfq_id": rfq_id,
            "status": "QUEUED_SEND",
            "warning": "NUNCA auto-send — stub until Mariano approves",
        }
    else:
        return {"ok": False, "error": "UNKNOWN_RFQ_ACTION", "action": action}

    if path_hit:
        save_rfq_draft(found, path_hit)
    return result


# --- Supplier reply ingest ----------------------------------------------------

_PRICE_RE = re.compile(
    r"(?:precio(?:\s*unitario)?|unitario|p\.?\s*u\.?)\s*[:=]?\s*\$?\s*"
    r"([\d]{1,3}(?:\.\d{3})+(?:,\d{2})?|[\d]{1,3}(?:\s\d{3})+(?:,\d{2})?|\d+(?:[.,]\d{1,2})?)",
    re.I,
)
_MONEY_TOKEN_RE = re.compile(
    r"\$\s*([\d]{1,3}(?:\.\d{3})+(?:,\d{2})?|[\d]{1,3}(?:\s\d{3})+(?:,\d{2})?|\d+(?:[.,]\d{1,2})?)"
)
_STOCK_RE = re.compile(
    r"(?:stock|disponible|disponibilidad|cantidad disponible)\s*[:=]?\s*(\d+|sin\s*stock|agotado|inmediato)",
    re.I,
)
_LEAD_RE = re.compile(
    r"(?:plazo|despacho|entrega|lead(?:\s*time)?)\s*[:=]?\s*([^\n,]{2,60})",
    re.I,
)
_SHIP_RE = re.compile(
    r"(?:env[ií]o|flete|shipping|log[ií]stica)[^\n]{0,60}?\$?\s*"
    r"([\d]{1,3}(?:\.\d{3})+(?:,\d{2})?|[\d]{1,3}(?:\s\d{3})+(?:,\d{2})?|\d+(?:[.,]\d{1,2})?)",
    re.I,
)
_VALID_RE = re.compile(
    r"(?:vigencia|validez|válida|valida)\s*(?:de\s*la\s*oferta|oferta|cotizaci[oó]n)?\s*[:=]?\s*([^\n,]{2,60})",
    re.I,
)


def _parse_money(raw: str | None) -> float | None:
    if not raw:
        return None
    s = raw.strip()
    # ARS: 1.234.567,89 or 1 234 567,89 → remove thousand sep, comma decimal
    if re.search(r"\d\.\d{3}", s) or " " in s:
        s2 = s.replace(" ", "").replace(".", "").replace(",", ".")
    elif "," in s and "." not in s:
        # 185000,50
        s2 = s.replace(",", ".")
    else:
        # plain 185000 or 185000.50
        s2 = s.replace(" ", "")
    try:
        return float(s2)
    except ValueError:
        return None


def parse_supplier_reply(text: str) -> dict[str, Any]:
    """Extract price, stock, lead time, shipping, validity from pasted reply."""
    t = text or ""
    price = None
    m = _PRICE_RE.search(t)
    if m:
        price = _parse_money(m.group(1))
    # fallback: first $ amount
    if price is None:
        m2 = _MONEY_TOKEN_RE.search(t)
        if m2:
            price = _parse_money(m2.group(1))

    stock_note = ""
    sm = _STOCK_RE.search(t)
    if sm:
        stock_note = sm.group(1).strip()
        if re.search(r"sin\s*stock|agotado", stock_note, re.I):
            stock_note = "0"

    lead = ""
    lm = _LEAD_RE.search(t)
    if lm:
        lead = lm.group(1).strip()

    shipping = ""
    ship_amt = None
    hm = _SHIP_RE.search(t)
    if hm:
        ship_amt = _parse_money(hm.group(1))
        shipping = str(ship_amt) if ship_amt is not None else hm.group(1).strip()
    else:
        # textual shipping without amount
        hm2 = re.search(
            r"(?:env[ií]o|flete|shipping)[^\n]{0,40}?:?\s*([^\n$]{2,40})",
            t,
            re.I,
        )
        if hm2:
            shipping = hm2.group(1).strip()

    validity = ""
    vm = _VALID_RE.search(t)
    if vm:
        validity = vm.group(1).strip()

    return {
        "unit_price": price,
        "stock": stock_note,
        "lead_time": lead,
        "shipping": shipping,
        "shipping_amount": ship_amt,
        "validity": validity,
        "raw_excerpt": t[:2000],
        "parsed_at": now_ba().isoformat(timespec="seconds"),
    }


def _infer_commercial_status(parsed: dict, *, needed_qty: float) -> str:
    from mm_commerce.matching import classify_commercial

    return classify_commercial(
        price=parsed.get("unit_price"),
        stock=str(parsed.get("stock") or ""),
        shipping_neuquen=str(parsed.get("shipping") or ""),
        qty_needed=float(needed_qty or 1),
    )


def ingest_supplier_reply(
    session,
    *,
    rfq_id: str | None = None,
    quote_id: int | None = None,
    opportunity_id: str | None = None,
    line_no: int | None = None,
    text: str,
    recalculate: bool = True,
) -> dict[str, Any]:
    """Parse reply → update quote COMMERCIAL_STATUS + evidence + optional pricing."""
    from mm_commerce.models import Opportunity, SupplierQuote, Tender

    parsed = parse_supplier_reply(text)
    draft = None
    if rfq_id:
        draft, path = load_rfq_draft(rfq_id)
        if draft:
            quote_id = quote_id or draft.get("quote_id")
            opportunity_id = opportunity_id or draft.get("opportunity_id")
            line_no = line_no or draft.get("line_no")

    q = None
    if quote_id:
        q = session.get(SupplierQuote, int(quote_id))
    elif opportunity_id and line_no is not None:
        opp = session.query(Opportunity).filter_by(external_id=str(opportunity_id)).one_or_none()
        if opp:
            tender = session.query(Tender).filter_by(opportunity_id=opp.id).one_or_none()
            item = None
            if tender:
                item = next((i for i in tender.items if int(i.line_no) == int(line_no)), None)
            if item:
                q = (
                    session.query(SupplierQuote)
                    .filter_by(opportunity_id=opp.id, tender_item_id=item.id)
                    .order_by(SupplierQuote.match_pct.desc())
                    .first()
                )

    if q is None:
        return {"ok": False, "error": "QUOTE_NOT_FOUND", "parsed": parsed}

    needed = float(q.qty or (draft or {}).get("cantidad") or 1)
    if parsed.get("unit_price") is not None:
        q.unit_cost = float(parsed["unit_price"])
    if parsed.get("stock"):
        q.stock_note = str(parsed["stock"])
    if parsed.get("shipping"):
        q.shipping_neuquen = str(parsed["shipping"])
    q.verified_at = now_ba().isoformat(timespec="seconds")
    comm = _infer_commercial_status(parsed, needed_qty=needed)
    q.commercial_status = comm

    evid = {}
    try:
        evid = json.loads(q.evidence_json or "{}")
    except Exception:
        evid = {}
    evid["rfq_reply"] = {
        "parsed": parsed,
        "rfq_id": rfq_id,
        "ingested_at": now_ba().isoformat(timespec="seconds"),
        "lead_time": parsed.get("lead_time"),
        "validity": parsed.get("validity"),
        "raw": parsed.get("raw_excerpt"),
    }
    evid["commercial_status"] = comm
    q.evidence_json = json.dumps(evid, ensure_ascii=False)
    session.commit()

    pricing_result = None
    if recalculate:
        from mm_commerce.agents.pricing import PricingAgent
        from mm_commerce.models import Opportunity as Opp

        opp = session.get(Opp, q.opportunity_id)
        if opp:
            offer = PricingAgent(session).process(opp)
            pricing_result = {
                "offer_id": offer.id,
                "merchandise_cost": offer.merchandise_cost,
                "total_cost": offer.total_cost,
                "precio_objetivo": offer.precio_objetivo,
                "utilidad": offer.utilidad,
                "margen_pct": offer.margen_pct,
                "status": offer.status,
            }

    if draft and rfq_id:
        draft["status"] = "REPLY_INGESTED"
        draft["commercial_status"] = comm
        draft["reply_parsed"] = parsed
        draft["reply_ingested_at"] = now_ba().isoformat(timespec="seconds")
        _, path = load_rfq_draft(rfq_id)
        if path:
            save_rfq_draft(draft, path)

    # persist reply artifact
    settings = get_settings()
    out = settings.project_root / "offers_out"
    out.mkdir(parents=True, exist_ok=True)
    reply_path = out / f"rfq_reply_{rfq_id or q.id}.json"
    reply_path.write_text(
        json.dumps(
            {
                "rfq_id": rfq_id,
                "quote_id": q.id,
                "parsed": parsed,
                "commercial_status": comm,
                "pricing": pricing_result,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    return {
        "ok": True,
        "quote_id": q.id,
        "commercial_status": comm,
        "parsed": parsed,
        "pricing": pricing_result,
        "reply_path": str(reply_path),
    }


def send_rfq_draft_card(
    draft: RfqDraft | dict,
    *,
    chat_id: str | None = None,
) -> dict[str, Any]:
    """Push one RFQ draft card to Telegram (draft only — never supplier send)."""
    from mm_commerce.telegram_bot import send_telegram_text

    d = draft.to_dict() if isinstance(draft, RfqDraft) else draft
    text = format_rfq_telegram(d)
    kb = None if d.get("blocked") else rfq_inline_keyboard(d["rfq_id"], email=d.get("email"), draft=d)
    # allow override chat id via temporary settings mutation is avoided —
    # send_telegram_text uses settings; callers should set TELEGRAM_USER_ID
    return send_telegram_text(text, reply_markup=kb, chat_id=chat_id)


def run_rfq_for_line(
    session,
    *,
    opportunity_id: str,
    line_no: int,
    fetch_html=None,
    send_telegram: bool = False,
    chat_id: str | None = None,
) -> dict[str, Any]:
    """Build (and optionally Telegram-push) RFQ for one EXACTO line."""
    from mm_commerce.models import Opportunity

    opp = session.query(Opportunity).filter_by(external_id=str(opportunity_id)).one_or_none()
    if not opp:
        return {"ok": False, "error": "OPP_NOT_FOUND"}
    drafts = build_rfq_drafts_for_opportunity(
        session, opp, line_nos={int(line_no)}, fetch_html=fetch_html
    )
    path = persist_rfq_drafts(drafts, opportunity_id=str(opportunity_id)) if drafts else None
    # also load blocked for this line
    settings = get_settings()
    blocked_path = settings.project_root / "offers_out" / f"rfq_blocked_{opportunity_id}.json"
    blocked = []
    if blocked_path.exists():
        try:
            blocked = [
                b
                for b in json.loads(blocked_path.read_text(encoding="utf-8"))
                if int(b.get("line_no") or 0) == int(line_no)
            ]
        except Exception:
            blocked = []
    tg = None
    target = drafts[0] if drafts else (blocked[0] if blocked else None)
    if send_telegram and target:
        tg = send_rfq_draft_card(target if isinstance(target, RfqDraft) else target, chat_id=chat_id)
    return {
        "ok": True,
        "opportunity_id": opportunity_id,
        "line_no": line_no,
        "drafts": [d.to_dict() for d in drafts],
        "blocked": blocked,
        "path": str(path) if path else None,
        "telegram": tg,
        "rfq_generated": bool(drafts),
        "contacto": (drafts[0].contacto if drafts else None),
        "canal": (drafts[0].canal if drafts else None),
    }
