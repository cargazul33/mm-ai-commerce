"""VERIFIER — bloqueos independientes; BLOQUEAR en inconsistencias."""
from __future__ import annotations

from mm_commerce.agents.base import BaseAgent
from mm_commerce.config import get_settings
from mm_commerce.models import Offer, Opportunity, SupplierQuote, Tender


class VerifierAgent(BaseAgent):
    name = "verifier"

    def process(self, opp: Opportunity) -> dict:
        run = self.start_run(opp.id)
        blockers: list[str] = []
        settings = get_settings()

        if opp.external_id in settings.excluded_ids:
            blockers.append("HARD_SKIP_EXCLUDED_ID")

        if opp.skipped:
            blockers.append(f"SKIPPED:{opp.skip_reason or 'categoria'}")

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
            expected = round(offer.cost_total * offer.margin_multiplier, 2)
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

        # invented-price guard: offer lines with price but quote NO VERIFICADO and no url
        for q in quotes:
            if q.unit_cost is not None and q.verification == "NO VERIFICADO" and not q.url:
                # soft warning — not always block
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
            # keep PENDIENTE awaiting human
            if opp.approval_status not in ("APROBADO", "RECHAZADO"):
                opp.approval_status = "PENDIENTE"
            status = "OK"

        self.finish_run(run, f"status={status}; blockers={blockers}")
        opp.state = "VERIFIER"
        self.session.commit()
        return {"status": status, "blockers": blockers}
