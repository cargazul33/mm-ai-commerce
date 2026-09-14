"""Matching / bid report for Telegram + offers_out."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from mm_commerce.config import get_settings
from mm_commerce.extractors.bid_scope import MODALIDAD_LABEL, STATE_ITEM, STATE_TOTAL, STATE_UNKNOWN
from mm_commerce.matching import (
    INTERNAL_VALIDATION_ERROR,
    TECH_EXACTO,
    VERIFIED_TECH,
    validate_evidence_consistency,
)
from mm_commerce.models import Offer, Opportunity, Supplier, SupplierQuote, Tender
from mm_commerce.rfq import build_rfq_drafts_for_opportunity, format_rfq_telegram, persist_rfq_drafts


def _money(v: Any) -> str:
    if v is None:
        return "N/D"
    try:
        return f"${float(v):,.2f}"
    except (TypeError, ValueError):
        return "N/D"


def _result_label(result: str) -> str:
    if result == "CUMPLE":
        return "PASS"
    if result == "NO_CUMPLE":
        return "FAIL"
    return "UNKNOWN"


def build_matching_report(session: Session, opp: Opportunity) -> dict[str, Any]:
    tender = session.query(Tender).filter_by(opportunity_id=opp.id).one_or_none()
    offer = (
        session.query(Offer)
        .filter_by(opportunity_id=opp.id)
        .order_by(Offer.id.desc())
        .first()
    )
    quotes = session.query(SupplierQuote).filter_by(opportunity_id=opp.id).all()
    best: dict[int, SupplierQuote] = {}
    for q in sorted(
        quotes,
        key=lambda x: (
            0 if (x.technical_status or "") in VERIFIED_TECH else 1,
            0 if x.unit_cost is not None else 1,
            -(x.match_pct or 0),
        ),
    ):
        if q.tender_item_id is not None and q.tender_item_id not in best:
            best[q.tender_item_id] = q

    bid_scope = {}
    if tender and tender.bid_scope_json:
        try:
            bid_scope = json.loads(tender.bid_scope_json)
        except Exception:
            bid_scope = {}
    state = bid_scope.get("state") or STATE_UNKNOWN
    modalidad = bid_scope.get("modalidad") or MODALIDAD_LABEL.get(state, "NO VERIFICADA")

    econ = {}
    if offer and offer.economic_json:
        try:
            econ = json.loads(offer.economic_json)
        except Exception:
            econ = {}

    rows = []
    line_results = []
    blockers: list[str] = []
    ganancia_total = 0.0
    capital = 0.0
    ganancia_known = True
    mult = (offer.margin_multiplier if offer else None) or 1.9

    items = list(tender.items) if tender else []
    for it in items:
        q = best.get(it.id)
        evid = {}
        if q and q.evidence_json:
            try:
                evid = json.loads(q.evidence_json)
            except Exception:
                evid = {}
        evidence_list = evid.get("evidence") or []
        # rebuild AttrEvidence-like for validation via dicts
        from mm_commerce.matching import AttrEvidence

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
        hard = evid.get("hard_requirements") or []
        check = validate_evidence_consistency(
            evidence=ev_objs,
            hard_requirements=hard,
            evidence_ok=evid.get("evidence_ok"),
            evidence_total=evid.get("evidence_total"),
        )
        if check.get("validation_error"):
            blockers.append(f"R{it.line_no}:{INTERNAL_VALIDATION_ERROR}")

        mand_ev = [e for e in ev_objs if e.mandatory]
        hard_bits = []
        for e in mand_ev:
            hard_bits.append(f"{e.label}:{_result_label(e.result)}")
        # listed count must match fraction
        total = int(check["hard_requirements_total"])
        ok = int(check["pass_count"])
        tech = (q.technical_status if q else "") or (evid.get("technical_status") or "NO_VERIFICADO")
        comm = (q.commercial_status if q else "") or (evid.get("commercial_status") or "PRECIO_NO_VERIFICADO")
        supplier_name = "SIN_PROVEEDOR_VERIFICADO"
        if q and q.supplier_id:
            s = session.get(Supplier, q.supplier_id)
            supplier_name = (s.name if s else "") or supplier_name
        price = q.unit_cost if q else None
        label = (q.product_label if q else it.product) or ""
        need_t = evid.get("product_type_need") or ""
        found_t = evid.get("product_type_found") or ""
        match_pct = q.match_pct if q else 0

        # Per-line APTO under item-level vs total
        line_tech_ok = tech in VERIFIED_TECH and (match_pct or 0) >= 90
        line_comm_ok = (
            price is not None
            and comm == "DISPONIBLE"
            and str(q.stock_note or "").strip() not in ("0", "OutOfStock")
        ) if q else False
        if state == STATE_ITEM:
            apto_line = bool(line_tech_ok and line_comm_ok)
        elif state == STATE_TOTAL:
            apto_line = bool(line_tech_ok and line_comm_ok)  # individual readiness
        else:
            apto_line = False

        ganancia = None
        if line_tech_ok and price is not None:
            line_cost = float(price) * float(it.qty or 1)
            line_price = line_cost * float(mult)
            ganancia = round(line_price - line_cost, 2)
            if line_comm_ok:
                ganancia_total += ganancia
                capital += line_cost
            else:
                ganancia_known = False
        else:
            ganancia_known = False

        rows.append(
            [
                it.line_no,
                tech,
                comm,
                match_pct,
                f"{ok}/{total}",
                supplier_name,
                _money(price),
                label[:80],
                need_t,
                found_t,
                [f"{e.key}:{e.result}" for e in mand_ev],
                "SÍ" if apto_line else "NO",
                ganancia,
                " / ".join(hard_bits),
                check.get("validation_error"),
            ]
        )
        line_results.append(
            {
                "line_no": it.line_no,
                "apto": "SÍ" if apto_line else "NO",
                "tech": tech,
                "commercial": comm,
                "ganancia_potencial": ganancia,
                "evidence": f"{ok}/{total}",
                "hard_requirements": " / ".join(hard_bits),
                "validation_error": check.get("validation_error"),
                "supplier": supplier_name,
                "unit_cost": price,
            }
        )

    # Global apto
    if state == STATE_UNKNOWN or bid_scope.get("blocks_presentation"):
        apto_global = "NO"
        blockers.append("BID_SCOPE_NO_VERIFICADA")
    elif state == STATE_TOTAL:
        apto_global = "SÍ" if line_results and all(r["apto"] == "SÍ" for r in line_results) else "NO"
    elif state == STATE_ITEM:
        apto_global = "SÍ" if any(r["apto"] == "SÍ" for r in line_results) else "NO"
    else:
        apto_global = "NO"

    if any(r.get("validation_error") for r in line_results):
        apto_global = "NO"
        blockers.append(INTERNAL_VALIDATION_ERROR)

    rfq_drafts = build_rfq_drafts_for_opportunity(session, opp)
    rfq_path = None
    if rfq_drafts:
        rfq_path = str(persist_rfq_drafts(rfq_drafts, opportunity_id=str(opp.external_id)))

    report = {
        "opportunity_id": opp.external_id,
        "title": opp.title,
        "fit": opp.fit_score,
        "risk": opp.risk_level,
        "approval": opp.approval_status,
        "modalidad": modalidad,
        "bid_scope": bid_scope,
        "rows": rows,
        "line_results": line_results,
        "ganancia_total_posible": round(ganancia_total, 2) if ganancia_known or ganancia_total else None,
        "capital_necesario": round(capital, 2) if capital else None,
        "blockers": blockers
        + list(econ.get("pending_lines") and [] or [])
        + ([econ.get("note")] if econ.get("note") else []),
        "econ": econ,
        "apto_global": apto_global,
        "rfq_drafts": [d.to_dict() for d in rfq_drafts],
        "rfq_path": rfq_path,
        "multiplier": mult,
    }
    return report


def format_matching_report_text(report: dict[str, Any]) -> str:
    ext = report.get("opportunity_id")
    lines = [
        f"🔧 M&M MATCHING HARD #{ext} (TECH ≠ COMMERCIAL)",
        f"{(report.get('title') or '')[:110]}",
        f"FIT {report.get('fit')} · RISK {report.get('risk')} · {report.get('approval')}",
        f"MODALIDAD: {report.get('modalidad')}",
        "",
    ]
    for row in report.get("rows") or []:
        (
            ren,
            tech,
            comm,
            match_pct,
            ev,
            prov,
            precio,
            label,
            tn,
            tf,
            _keys,
            apto,
            gan,
            hard_bits,
            val_err,
        ) = row
        lines.append(f"R{ren} Hard requirements / {hard_bits}")
        lines.append(
            f"   TECH {tech} / COMM {comm} / MATCH {match_pct}% / EVIDENCIA {ev}"
            + (f" / ⚠ {val_err}" if val_err else "")
        )
        lines.append(f"   {prov} · {precio} · {label}")
        lines.append(f"   tipo {tn}→{tf} · APTO {apto} · GANANCIA {_money(gan)}")
    lines.append("")
    lines.append(f"MODALIDAD DE OFERTA: {report.get('modalidad')}")
    lines.append("RESULTADO POR RENGLÓN:")
    for lr in report.get("line_results") or []:
        lines.append(
            f"  R{lr['line_no']}: APTO {lr['apto']} · ganancia {_money(lr.get('ganancia_potencial'))}"
        )
    lines.append(
        f"GANANCIA TOTAL POSIBLE: {_money(report.get('ganancia_total_posible'))}"
    )
    lines.append(f"CAPITAL NECESARIO: {_money(report.get('capital_necesario'))}")
    blocks = report.get("blockers") or []
    lines.append(f"BLOQUEOS: {'; '.join(str(b) for b in blocks[:12]) or '—'}")
    lines.append(f"APTO GLOBAL: {report.get('apto_global')}")
    if report.get("rfq_drafts"):
        lines.append("")
        lines.append(f"RFQ ASISTIDOS: {len(report['rfq_drafts'])} borrador(es) (no enviados)")
        for d in report["rfq_drafts"][:5]:
            lines.append(format_rfq_telegram_short(d))
    return "\n".join(lines)


def format_rfq_telegram_short(d: dict) -> str:
    return (
        f"  · R{d.get('line_no')} {d.get('proveedor')}: falta {', '.join(d.get('missing') or [])}"
        f" [{d.get('rfq_id')}]"
    )


def write_matching_report(session: Session, opp: Opportunity) -> tuple[Path, Path, dict]:
    report = build_matching_report(session, opp)
    settings = get_settings()
    out = settings.project_root / "offers_out"
    out.mkdir(parents=True, exist_ok=True)
    jp = out / f"matching_report_{opp.external_id}.json"
    tp = out / f"matching_report_{opp.external_id}.txt"
    # JSON-safe rows
    safe = dict(report)
    jp.write_text(json.dumps(safe, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
    tp.write_text(format_matching_report_text(report), encoding="utf-8")
    return jp, tp, report
