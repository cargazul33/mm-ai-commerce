"""VERIFIER — bloqueos independientes; BLOQUEAR en inconsistencias."""
from __future__ import annotations

from mm_commerce.agents.base import BaseAgent
from mm_commerce.config import get_settings
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

        tender = (
            self.session.query(Tender).filter_by(opportunity_id=opp.id).one_or_none()
        )
        offer = (
            self.session.query(Offer)
            .filter_by(opportunity_id=opp.id)
            .order_by(Offer.id.desc())
            .first()
        )
        quotes = (
            self.session.query(SupplierQuote).filter_by(opportunity_id=opp.id).all()
        )

        if tender is None:
            blockers.append("SIN_PLIEGO")
        elif not tender.items:
            blockers.append("SIN_ITEMS")

        if offer and offer.cost_total is not None and offer.precio_objetivo is not None:
            base = offer.total_cost if offer.total_cost is not None else offer.cost_total
            expected = round(base * offer.margin_multiplier, 2)
            if abs(expected - offer.precio_objetivo) > 0.05:
                blockers.append("PRECIO_INCONSISTENTE")
                self.finding(
                    run,
                    f"precio_objetivo {offer.precio_objetivo} != cost*mult {expected}",
                    opportunity_id=opp.id,
                    severity="ERROR",
                    code="PRECIO_INCONSISTENTE",
                    blocks=True,
                )

        if offer and offer.logistics_status == "PENDING":
            warnings.append("LOGISTICS_PENDING")

        # incomplete coverage: tender lines without priced quote
        if tender and tender.items:
            priced_ids = {
                q.tender_item_id
                for q in quotes
                if q.unit_cost is not None and q.tender_item_id is not None
            }
            missing = [it.line_no for it in tender.items if it.id not in priced_ids]
            if missing:
                warnings.append(f"RENGLONES_SIN_PRECIO:{missing}")
                self.finding(
                    run,
                    f"renglones sin precio verificado: {missing}",
                    opportunity_id=opp.id,
                    severity="WARN",
                    code="COBERTURA_INCOMPLETA",
                )

        for q in quotes:
            if q.unit_cost is not None and q.verification == "NO VERIFICADO" and not q.url:
                self.finding(
                    run,
                    f"costo sin URL y NO VERIFICADO: {q.product_label[:80]}",
                    opportunity_id=opp.id,
                    severity="WARN",
                    code="COSTO_DEBIL",
                )

        if opp.risk_level == "CRITICO":
            blockers.append("RIESGO_CRITICO")

        if blockers:
            opp.approval_status = "BLOQUEADO"
            for b in blockers:
                self.finding(
                    run,
                    b,
                    opportunity_id=opp.id,
                    severity="ERROR",
                    code="BLOQUEAR",
                    blocks=True,
                )
            status = "BLOQUEADO"
        else:
            if opp.approval_status not in ("APROBADO", "RECHAZADO"):
                opp.approval_status = "PENDIENTE"
            status = "OK"

        self.finish_run(
            run, f"status={status}; blockers={blockers}; warnings={warnings}"
        )
        opp.state = "VERIFIER"
        self.session.commit()
        return {"status": status, "blockers": blockers, "warnings": warnings}
