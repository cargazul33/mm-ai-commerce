"""RISK — BAJO|MEDIO|ALTO|CRITICO."""
from __future__ import annotations

from mm_commerce.agents.base import BaseAgent
from mm_commerce.models import Offer, Opportunity, SupplierQuote


class RiskAgent(BaseAgent):
    name = "risk"

    def process(self, opp: Opportunity) -> str:
        run = self.start_run(opp.id)
        reasons: list[str] = []
        level = "BAJO"

        if opp.skipped:
            level = "CRITICO"
            reasons.append(f"skipped:{opp.skip_reason or 'flag'}")

        if opp.fit_score < 40:
            level = _max(level, "ALTO")
            reasons.append("fit_bajo")

        quotes = (
            self.session.query(SupplierQuote).filter_by(opportunity_id=opp.id).all()
        )
        priced = [q for q in quotes if q.unit_cost is not None]
        confirmed = [q for q in quotes if q.verification == "CONFIRMADO"]

        if not quotes:
            level = _max(level, "ALTO")
            reasons.append("sin_sourcing")
        elif not priced:
            level = _max(level, "MEDIO")
            reasons.append("sin_costos")
        if quotes and not confirmed:
            level = _max(level, "MEDIO")
            reasons.append("sin_confirmados")

        offer = (
            self.session.query(Offer)
            .filter_by(opportunity_id=opp.id)
            .order_by(Offer.id.desc())
            .first()
        )
        if offer and offer.tax_status == "PENDING":
            level = _max(level, "MEDIO")
            reasons.append("tax_pending")
        if offer and offer.logistics_status == "PENDING":
            level = _max(level, "MEDIO")
            reasons.append("logistics_pending")

        # short deadline heuristic (string only)
        if "hoy" in (opp.opening_at or "").lower():
            level = _max(level, "ALTO")
            reasons.append("apertura_cercana")

        opp.risk_level = level
        self.finding(
            run,
            f"risk={level}; " + ",".join(reasons) if reasons else f"risk={level}",
            opportunity_id=opp.id,
            severity="WARN" if level in ("ALTO", "CRITICO") else "INFO",
            code="RISK",
        )
        self.finish_run(run, f"level={level}; reasons={reasons}")
        opp.state = "RISK"
        self.session.commit()
        return level


_ORDER = {"BAJO": 0, "MEDIO": 1, "ALTO": 2, "CRITICO": 3}


def _max(a: str, b: str) -> str:
    return a if _ORDER.get(a, 0) >= _ORDER.get(b, 0) else b
