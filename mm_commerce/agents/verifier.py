"""VERIFIER — BLOQUEAR on wrong type, under-spec, stock, unverified; bid_scope-aware."""
from __future__ import annotations

import json

from mm_commerce.agents.base import BaseAgent
from mm_commerce.config import get_settings
from mm_commerce.extractors.bid_scope import STATE_ITEM, STATE_TOTAL, STATE_UNKNOWN
from mm_commerce.matching import (
    COMM_DISPONIBLE,
    INTERNAL_VALIDATION_ERROR,
    TECH_NO_CUMPLE,
    TECH_NO_VER,
    VERIFIED_TECH,
    validate_evidence_consistency,
    AttrEvidence,
)
from mm_commerce.models import Offer, Opportunity, SupplierQuote, Tender
from mm_commerce.timing import ACTIONABLE_TIMING, TIMING_FECHA_NO_VERIFICADA, classify_timing


class VerifierAgent(BaseAgent):
    name = "verifier"

    def process(self, opp: Opportunity) -> dict:
        run = self.start_run(opp.id)
        blockers: list[str] = []
        warnings: list[str] = []
        settings = get_settings()

        if opp.external_id in settings.excluded_ids:
            blockers.append("HARD_SKIP_EXCLUDED_ID")
        if opp.skipped:
            blockers.append(f"SKIPPED:{opp.skip_reason or 'categoria'}")
        if opp.archived or opp.timing_state == "VENCIDA":
            blockers.append("VENCIDA")

        timing = classify_timing(opp.cierre_at or opp.opening_at)
        opp.timing_state = timing.state
        if timing.state == TIMING_FECHA_NO_VERIFICADA:
            blockers.append("FECHA_NO_VERIFICADA")
        elif timing.state not in ACTIONABLE_TIMING:
            blockers.append(f"TIMING:{timing.state}")

        tender = self.session.query(Tender).filter_by(opportunity_id=opp.id).one_or_none()
        offer = (
            self.session.query(Offer)
            .filter_by(opportunity_id=opp.id)
            .order_by(Offer.id.desc())
            .first()
        )
        quotes = self.session.query(SupplierQuote).filter_by(opportunity_id=opp.id).all()

        bid_scope: dict = {}
        if tender and tender.bid_scope_json:
            try:
                bid_scope = json.loads(tender.bid_scope_json)
            except Exception:
                bid_scope = {}
        scope_state = bid_scope.get("state") or STATE_UNKNOWN
        if scope_state == STATE_UNKNOWN or bid_scope.get("blocks_presentation"):
            blockers.append("BID_SCOPE_UNKNOWN")
            blockers.append("PRESENTACION_BLOQUEADA_HASTA_VERIFICAR_MODALIDAD")

        if tender is None:
            blockers.append("SIN_PLIEGO")
        elif not tender.items:
            blockers.append("SIN_ITEMS")

        if offer and offer.cost_total is not None and offer.precio_objetivo is not None:
            base = offer.total_cost if offer.total_cost is not None else offer.cost_total
            expected = round(base * offer.margin_multiplier, 2)
            if abs(expected - offer.precio_objetivo) > 0.05:
                blockers.append("PRECIO_INCONSISTENTE")

        if offer and offer.logistics_status == "PENDING":
            warnings.append("LOGISTICS_PENDING")
            if scope_state == STATE_TOTAL:
                blockers.append("LOGISTICS_PENDING")
            elif scope_state == STATE_ITEM:
                # item-level: logistics still required for any presentación
                blockers.append("LOGISTICS_PENDING")

        best_by_item: dict[int, SupplierQuote] = {}
        for q in sorted(quotes, key=lambda x: (-(x.match_pct or 0), 0 if x.unit_cost is not None else 1)):
            if q.tender_item_id is not None and q.tender_item_id not in best_by_item:
                best_by_item[q.tender_item_id] = q

        tech_ok = 0
        comm_ok = 0
        lines_total = len(tender.items) if tender else 0
        reasons: list[str] = []
        line_apto: dict[int, bool] = {}

        if tender and tender.items:
            for it in tender.items:
                q = best_by_item.get(it.id)
                if q is None:
                    blockers.append(f"SIN_QUOTE_R{it.line_no}")
                    reasons.append(f"R{it.line_no}:SIN_QUOTE")
                    line_apto[it.line_no] = False
                    continue
                tech = (q.technical_status or q.match_class or TECH_NO_VER).strip()
                comm = (q.commercial_status or "").strip()
                evid = {}
                try:
                    evid = json.loads(q.evidence_json or "{}")
                except Exception:
                    evid = {}

                # Evidence consistency gate
                ev_objs = []
                for e in evid.get("evidence") or []:
                    if not isinstance(e, dict):
                        continue
                    ev_objs.append(
                        AttrEvidence(
                            key=e.get("key", ""),
                            label=e.get("label", ""),
                            required=e.get("required", ""),
                            found=e.get("found", ""),
                            source_url=e.get("source_url", ""),
                            result=e.get("result", "NO_VERIFICADO"),
                            mandatory=bool(e.get("mandatory", True)),
                        )
                    )
                check = validate_evidence_consistency(
                    evidence=ev_objs,
                    hard_requirements=evid.get("hard_requirements") or [],
                    evidence_ok=evid.get("evidence_ok"),
                    evidence_total=evid.get("evidence_total"),
                )
                if check.get("validation_error") or evid.get("validation_error"):
                    blockers.append(f"{INTERNAL_VALIDATION_ERROR}_R{it.line_no}")
                    reasons.append(f"R{it.line_no}:{INTERNAL_VALIDATION_ERROR}")
                    line_apto[it.line_no] = False
                    continue

                line_tech_ok = False
                line_comm_ok = False

                if tech in (TECH_NO_CUMPLE, "NO CUMPLE") or q.verification == "NO CUMPLE":
                    if scope_state != STATE_ITEM:
                        blockers.append(f"NO_CUMPLE_R{it.line_no}")
                    reasons.append(f"R{it.line_no}:TECH={tech}")
                    for b in evid.get("blockers") or []:
                        bs = str(b)
                        if "PRODUCT_TYPE" in bs or "WRONG_PRODUCT" in bs or "CATEGORY" in bs:
                            if scope_state != STATE_ITEM:
                                blockers.append(f"WRONG_TYPE_R{it.line_no}")
                        if "LOWER_CAPACITY" in bs and scope_state != STATE_ITEM:
                            blockers.append(f"LOWER_CAPACITY_R{it.line_no}")
                    line_apto[it.line_no] = False
                    continue

                if tech not in VERIFIED_TECH or (q.match_pct or 0) < 90:
                    if scope_state != STATE_ITEM:
                        blockers.append(f"NO_VERIFICADO_R{it.line_no}")
                    reasons.append(f"R{it.line_no}:TECH={tech}")
                    line_apto[it.line_no] = False
                    continue

                tech_ok += 1
                line_tech_ok = True

                if q.unit_cost is None or comm != COMM_DISPONIBLE:
                    if scope_state != STATE_ITEM:
                        blockers.append(f"COMMERCIAL_R{it.line_no}:{comm or 'NO_DISP'}")
                    reasons.append(f"R{it.line_no}:COMM={comm or 'NO_DISP'}")
                    line_apto[it.line_no] = False
                    continue
                stock = str(q.stock_note or "")
                if stock.strip() in ("0", "OutOfStock"):
                    if scope_state != STATE_ITEM:
                        blockers.append(f"STOCK_INSUFICIENTE_R{it.line_no}")
                    reasons.append(f"R{it.line_no}:STOCK")
                    line_apto[it.line_no] = False
                    continue
                comm_ok += 1
                line_comm_ok = True
                line_apto[it.line_no] = bool(line_tech_ok and line_comm_ok)

            if scope_state == STATE_TOTAL:
                if lines_total and tech_ok < lines_total:
                    blockers.append("MATCHING_INCOMPLETO")
                    blockers.append(f"COBERTURA_TECNICA:{tech_ok}/{lines_total}")
                if lines_total and comm_ok < lines_total:
                    blockers.append(f"COBERTURA_COMERCIAL:{comm_ok}/{lines_total}")
            elif scope_state == STATE_ITEM:
                if tech_ok == 0:
                    blockers.append("NINGUN_RENGLON_TECH_OK")
                # Mark failing lines as informational blockers for report
                for ln, ok in line_apto.items():
                    if not ok:
                        warnings.append(f"ITEM_NO_APTO_R{ln}")
            else:
                blockers.append("MATCHING_INCOMPLETO")

        if offer and offer.status == "BLOQUEADO_MATCHING":
            if scope_state == STATE_TOTAL:
                blockers.append("OFERTA_BLOQUEADA_MATCHING")
        if offer and offer.economic_json:
            try:
                econ = json.loads(offer.economic_json)
                if econ.get("apto_para_cotizar") is False and scope_state == STATE_TOTAL:
                    blockers.append("NO_APTO_COTIZAR")
            except Exception:
                pass

        if opp.risk_level == "CRITICO":
            blockers.append("RIESGO_CRITICO")

        seen: set[str] = set()
        uniq = []
        for b in blockers:
            if b not in seen:
                seen.add(b)
                uniq.append(b)
        blockers = uniq

        if scope_state == STATE_TOTAL:
            apto = (
                lines_total > 0
                and tech_ok == lines_total
                and comm_ok == lines_total
                and "LOGISTICS_PENDING" not in blockers
                and INTERNAL_VALIDATION_ERROR not in "".join(blockers)
                and "BID_SCOPE_UNKNOWN" not in blockers
            )
        elif scope_state == STATE_ITEM:
            apto = (
                any(line_apto.values())
                and "LOGISTICS_PENDING" not in blockers
                and "BID_SCOPE_UNKNOWN" not in blockers
                and INTERNAL_VALIDATION_ERROR not in "".join(blockers)
            )
        else:
            apto = False

        if "LOGISTICS_PENDING" in blockers:
            apto = False
        if any(INTERNAL_VALIDATION_ERROR in b for b in blockers):
            apto = False

        if blockers or not apto:
            opp.approval_status = "BLOQUEADO"
            for b in blockers:
                self.finding(
                    run, b, opportunity_id=opp.id, severity="ERROR", code="BLOQUEAR", blocks=True
                )
            status = "BLOQUEADO"
        else:
            if opp.approval_status not in ("APROBADO", "RECHAZADO"):
                opp.approval_status = "PENDIENTE"
            status = "OK"

        self.finish_run(
            run,
            f"status={status}; scope={scope_state}; tech={tech_ok}/{lines_total}; "
            f"comm={comm_ok}/{lines_total}; blockers={blockers}",
        )
        opp.state = "VERIFIER"
        self.session.commit()
        return {
            "status": status,
            "blockers": blockers,
            "warnings": warnings,
            "bid_scope": scope_state,
            "modalidad": bid_scope.get("modalidad"),
            "line_apto": {str(k): v for k, v in line_apto.items()},
            "cobertura_tecnica": f"{tech_ok}/{lines_total}",
            "cobertura_comercial": f"{comm_ok}/{lines_total}",
            "cobertura": f"{tech_ok}/{lines_total}",
            "apto_para_cotizar": bool(apto and status == "OK"),
            "razon_bloqueo": "; ".join(reasons or blockers[:10]),
        }
