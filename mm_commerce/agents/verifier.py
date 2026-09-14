"""VERIFIER — BLOQUEAR on wrong type, under-spec, stock, unverified; split TECH/COMMERCIAL."""
from __future__ import annotations

import json

from mm_commerce.agents.base import BaseAgent
from mm_commerce.config import get_settings
from mm_commerce.matching import (
    COMM_DISPONIBLE,
    TECH_NO_CUMPLE,
    TECH_NO_VER,
    VERIFIED_TECH,
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
            # logistics required for APROBAR / full coverage
            blockers.append("LOGISTICS_PENDING")

        best_by_item: dict[int, SupplierQuote] = {}
        for q in sorted(quotes, key=lambda x: (-(x.match_pct or 0), 0 if x.unit_cost is not None else 1)):
            if q.tender_item_id is not None and q.tender_item_id not in best_by_item:
                best_by_item[q.tender_item_id] = q

        tech_ok = 0
        comm_ok = 0
        lines_total = len(tender.items) if tender else 0
        reasons: list[str] = []

        if tender and tender.items:
            for it in tender.items:
                q = best_by_item.get(it.id)
                if q is None:
                    blockers.append(f"SIN_QUOTE_R{it.line_no}")
                    reasons.append(f"R{it.line_no}:SIN_QUOTE")
                    continue
                tech = (q.technical_status or q.match_class or TECH_NO_VER).strip()
                comm = (q.commercial_status or "").strip()
                evid = {}
                try:
                    evid = json.loads(q.evidence_json or "{}")
                except Exception:
                    evid = {}

                if tech in (TECH_NO_CUMPLE, "NO CUMPLE") or q.verification == "NO CUMPLE":
                    blockers.append(f"NO_CUMPLE_R{it.line_no}")
                    reasons.append(f"R{it.line_no}:TECH={tech}")
                    for b in evid.get("blockers") or []:
                        bs = str(b)
                        if "PRODUCT_TYPE" in bs or "WRONG_PRODUCT" in bs or "CATEGORY" in bs:
                            blockers.append(f"WRONG_TYPE_R{it.line_no}")
                        if "LOWER_CAPACITY" in bs:
                            blockers.append(f"LOWER_CAPACITY_R{it.line_no}")
                        if "NAP_SUPPORT" in bs:
                            blockers.append(f"WRONG_PRODUCT_R{it.line_no}")
                    continue

                if tech not in VERIFIED_TECH or (q.match_pct or 0) < 90:
                    blockers.append(f"NO_VERIFICADO_R{it.line_no}")
                    reasons.append(f"R{it.line_no}:TECH={tech}")
                    continue

                tech_ok += 1

                if q.unit_cost is None or comm != COMM_DISPONIBLE:
                    blockers.append(f"COMMERCIAL_R{it.line_no}:{comm or 'NO_DISP'}")
                    reasons.append(f"R{it.line_no}:COMM={comm or 'NO_DISP'}")
                    continue
                stock = str(q.stock_note or "")
                if stock.strip() in ("0", "OutOfStock"):
                    blockers.append(f"STOCK_INSUFICIENTE_R{it.line_no}")
                    reasons.append(f"R{it.line_no}:STOCK")
                    continue
                comm_ok += 1

            if lines_total and tech_ok < lines_total:
                blockers.append("MATCHING_INCOMPLETO")
                blockers.append(f"COBERTURA_TECNICA:{tech_ok}/{lines_total}")
            if lines_total and comm_ok < lines_total:
                blockers.append(f"COBERTURA_COMERCIAL:{comm_ok}/{lines_total}")

        if offer and offer.status == "BLOQUEADO_MATCHING":
            blockers.append("OFERTA_BLOQUEADA_MATCHING")
        if offer and offer.economic_json:
            try:
                econ = json.loads(offer.economic_json)
                if econ.get("apto_para_cotizar") is False:
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

        # APROBAR disabled until 100% tech + commercial + logistics
        apto = (
            lines_total > 0
            and tech_ok == lines_total
            and comm_ok == lines_total
            and "LOGISTICS_PENDING" not in blockers
            and not any(b.startswith("NO_CUMPLE") or b.startswith("NO_VERIFICADO") or b.startswith("WRONG_") for b in blockers)
        )
        # logistics always pending today → always block APROBAR (honest)
        if "LOGISTICS_PENDING" in blockers:
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
            f"status={status}; tech={tech_ok}/{lines_total}; comm={comm_ok}/{lines_total}; "
            f"blockers={blockers}",
        )
        opp.state = "VERIFIER"
        self.session.commit()
        return {
            "status": status,
            "blockers": blockers,
            "warnings": warnings,
            "cobertura_tecnica": f"{tech_ok}/{lines_total}",
            "cobertura_comercial": f"{comm_ok}/{lines_total}",
            "cobertura": f"{tech_ok}/{lines_total}",
            "apto_para_cotizar": bool(apto and status == "OK"),
            "razon_bloqueo": "; ".join(reasons or blockers[:10]),
        }
