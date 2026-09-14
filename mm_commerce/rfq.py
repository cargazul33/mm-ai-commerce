"""Assisted RFQ — draft only; NEVER auto-send until Mariano approves."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from mm_commerce.config import get_settings
from mm_commerce.matching import COMM_DISPONIBLE, TECH_EXACTO, VERIFIED_TECH
from mm_commerce.timing import now_ba


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
    url: str = ""
    missing: list[str] = field(default_factory=list)
    status: str = "DRAFT"  # DRAFT|QUEUED_SEND|SENT|DISCARDED
    created_at: str = ""

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
    return miss


def build_rfq_drafts_for_opportunity(session, opp) -> list[RfqDraft]:
    """When TECH EXACTO but commercial incomplete → draft RFQ (no send)."""
    from mm_commerce.models import Supplier, SupplierQuote, Tender

    tender = session.query(Tender).filter_by(opportunity_id=opp.id).one_or_none()
    if not tender:
        return []
    quotes = session.query(SupplierQuote).filter_by(opportunity_id=opp.id).all()
    best: dict[int, Any] = {}
    for q in sorted(quotes, key=lambda x: (-(x.match_pct or 0), 0 if x.unit_cost is not None else 1)):
        if q.tender_item_id is not None and q.tender_item_id not in best:
            best[q.tender_item_id] = q

    drafts: list[RfqDraft] = []
    ts = now_ba().strftime("%Y%m%d%H%M%S")
    for it in tender.items:
        q = best.get(it.id)
        if q is None:
            continue
        tech = (q.technical_status or q.match_class or "").strip()
        if tech not in VERIFIED_TECH and tech != TECH_EXACTO:
            continue
        if tech != TECH_EXACTO and tech not in VERIFIED_TECH:
            continue
        # Prefer EXACTO for RFQ assist
        if tech != TECH_EXACTO:
            continue
        comm = (q.commercial_status or "").strip()
        miss = _missing_commercial(q)
        if comm == COMM_DISPONIBLE and not miss:
            continue
        if not miss:
            miss = ["precio/stock/logística incompletos"]
        supplier = session.get(Supplier, q.supplier_id) if q.supplier_id else None
        proveedor = (supplier.name if supplier else "") or "PROVEEDOR_NO_IDENTIFICADO"
        contacto = (supplier.url if supplier and supplier.url else "") or (q.url or "consultar web proveedor")
        requisitos = (it.specs or it.product or "")[:500]
        mensaje = (
            f"Hola {proveedor},\n\n"
            f"Somos M&M — solicitamos cotización formal para licitación pública Neuquén #{opp.external_id}.\n"
            f"Producto: {q.product_label or it.product}\n"
            f"Requisitos técnicos: {requisitos}\n"
            f"Cantidad: {it.qty}\n"
            f"Necesitamos: precio unitario ARS (IVA discriminado), stock disponible, "
            f"plazo de entrega y costo de envío a Neuquén Capital.\n"
            f"URL referencia: {q.url or 'N/D'}\n\n"
            f"Quedamos a la espera. Gracias."
        )
        drafts.append(
            RfqDraft(
                rfq_id=f"rfq-{opp.external_id}-R{it.line_no}-{ts}",
                opportunity_id=str(opp.external_id),
                line_no=int(it.line_no),
                proveedor=proveedor,
                contacto=contacto,
                producto=(q.product_label or it.product)[:240],
                requisitos=requisitos,
                cantidad=float(it.qty or 1),
                mensaje=mensaje,
                url=q.url or "",
                missing=miss,
                status="DRAFT",
                created_at=now_ba().isoformat(timespec="seconds"),
            )
        )
    return drafts


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


def format_rfq_telegram(draft: RfqDraft) -> str:
    return "\n".join(
        [
            f"📝 RFQ ASISTIDO (NO ENVIADO) · {draft.rfq_id}",
            f"PROVEEDOR: {draft.proveedor}",
            f"CONTACTO: {draft.contacto}",
            f"PRODUCTO: {draft.producto}",
            f"REQUISITOS: {draft.requisitos[:300]}",
            f"CANTIDAD: {draft.cantidad}",
            f"FALTA: {', '.join(draft.missing)}",
            "",
            "MENSAJE:",
            draft.mensaje[:1500],
            "",
            "[ENVIAR RFQ]  [DESCARTAR]  ← stub hasta aprobación Mariano",
        ]
    )


def rfq_inline_keyboard(rfq_id: str) -> dict:
    """Telegram buttons — callback prepared; send is stubbed until approve."""
    return {
        "inline_keyboard": [
            [
                {"text": "ENVIAR RFQ", "callback_data": f"rfq_send:{rfq_id}"[:64]},
                {"text": "DESCARTAR", "callback_data": f"rfq_discard:{rfq_id}"[:64]},
            ]
        ]
    }


def handle_rfq_callback(data: str) -> dict[str, Any]:
    """Stub send — never emails; queues intent only."""
    if ":" not in data:
        return {"ok": False, "error": "BAD_RFQ_CALLBACK"}
    action, rfq_id = data.split(":", 1)
    action = action.strip().lower()
    rfq_id = rfq_id.strip()
    settings = get_settings()
    drafts_dir = settings.project_root / "offers_out"
    # Find draft file containing this rfq_id
    found = None
    path_hit = None
    for p in drafts_dir.glob("rfq_drafts_*.json"):
        try:
            items = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        for it in items:
            if it.get("rfq_id") == rfq_id:
                found = it
                path_hit = p
                break
        if found:
            break
    if not found:
        return {"ok": False, "error": "RFQ_NOT_FOUND", "rfq_id": rfq_id}

    if action in ("rfq_send", "enviar_rfq"):
        # STUB — do not email; mark queued for Mariano
        found["status"] = "QUEUED_SEND"
        found["send_note"] = "STUB: no email enviado — pendiente aprobación Mariano"
        result = {
            "ok": True,
            "action": "rfq_send_stub",
            "rfq_id": rfq_id,
            "status": "QUEUED_SEND",
            "warning": "NUNCA auto-send — stub until Mariano approves",
        }
    elif action in ("rfq_discard", "descartar"):
        found["status"] = "DISCARDED"
        result = {"ok": True, "action": "rfq_discard", "rfq_id": rfq_id, "status": "DISCARDED"}
    else:
        return {"ok": False, "error": "UNKNOWN_RFQ_ACTION", "action": action}

    if path_hit:
        items = json.loads(path_hit.read_text(encoding="utf-8"))
        for i, it in enumerate(items):
            if it.get("rfq_id") == rfq_id:
                items[i] = found
                break
        path_hit.write_text(json.dumps(items, indent=2, ensure_ascii=False), encoding="utf-8")
    return result
