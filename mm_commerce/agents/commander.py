"""COMMANDER — orquesta state machine por oportunidad."""
from __future__ import annotations

from mm_commerce.agents.base import BaseAgent
from mm_commerce.agents.bid import BidAgent
from mm_commerce.agents.pricing import PricingAgent
from mm_commerce.agents.pliego import PliegoAgent
from mm_commerce.agents.radar import RadarAgent
from mm_commerce.agents.risk import RiskAgent
from mm_commerce.agents.sourcing import SourcingAgent
from mm_commerce.agents.verifier import VerifierAgent
from mm_commerce.config import get_settings
from mm_commerce.models import Opportunity
from mm_commerce.telegram_bot import notify_high_fit

# RADAR → PLIEGO → SOURCING → PRICING → RISK → VERIFIER → TELEGRAM (via bid+notify)
STATES = ("RADAR", "PLIEGO", "SOURCING", "PRICING", "RISK", "VERIFIER", "BID", "TELEGRAM", "DONE")


class Commander(BaseAgent):
    name = "commander"

    def run_once(self, *, limit: int = 15, min_fit: int | None = None) -> dict:
        run = self.start_run()
        settings = get_settings()
        min_fit = settings.fit_score_alert_min if min_fit is None else min_fit

        radar = RadarAgent(self.session)
        radar_stats = radar.run_once()

        # select actionable opps
        q = (
            self.session.query(Opportunity)
            .filter(Opportunity.skipped.is_(False))
            .filter(Opportunity.fit_score >= min_fit)
            .order_by(Opportunity.fit_score.desc())
        )
        opps = q.limit(limit).all()

        results = []
        for opp in opps:
            results.append(self._pipeline_one(opp))

        # digest / telegram for high-fit pending
        digest = self._build_digest(min_fit=min_fit)
        tg = notify_high_fit(self.session, digest)

        summary = (
            f"radar={radar_stats}; processed={len(results)}; "
            f"telegram={tg.get('status')}; digest_n={len(digest)}"
        )
        self.finish_run(run, summary)
        self.session.commit()
        return {
            "radar": radar_stats,
            "processed": results,
            "digest": digest,
            "telegram": tg,
        }

    def _pipeline_one(self, opp: Opportunity) -> dict:
        settings = get_settings()
        if opp.external_id in settings.excluded_ids:
            opp.skipped = True
            opp.skip_reason = "HARD_SKIP"
            opp.approval_status = "BLOQUEADO"
            self.session.commit()
            return {"id": opp.external_id, "status": "HARD_SKIP"}

        out: dict = {"id": opp.external_id, "fit": opp.fit_score, "steps": []}
        try:
            PliegoAgent(self.session).process(opp)
            out["steps"].append("PLIEGO")
            SourcingAgent(self.session).process(opp)
            out["steps"].append("SOURCING")
            PricingAgent(self.session).process(opp)
            out["steps"].append("PRICING")
            risk = RiskAgent(self.session).process(opp)
            out["steps"].append(f"RISK:{risk}")
            ver = VerifierAgent(self.session).process(opp)
            out["steps"].append(f"VERIFIER:{ver['status']}")
            bid = BidAgent(self.session).process(opp)
            out["steps"].append(f"BID:{bid.get('status')}")
            if opp.approval_status != "BLOQUEADO" and opp.fit_score >= settings.fit_score_alert_min:
                opp.state = "TELEGRAM"
            else:
                opp.state = "DONE" if opp.approval_status == "BLOQUEADO" else opp.state
            self.session.commit()
            out["approval"] = opp.approval_status
            out["risk"] = opp.risk_level
            out["state"] = opp.state
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"
            self.session.rollback()
        return out

    def _build_digest(self, min_fit: int) -> list[dict]:
        rows = (
            self.session.query(Opportunity)
            .filter(Opportunity.skipped.is_(False))
            .filter(Opportunity.fit_score >= min_fit)
            .filter(Opportunity.approval_status.in_(["PENDIENTE", ""]))
            .order_by(Opportunity.fit_score.desc())
            .limit(20)
            .all()
        )
        digest = []
        for o in rows:
            digest.append(
                {
                    "id": o.external_id,
                    "title": o.title[:120],
                    "fit_score": o.fit_score,
                    "risk": o.risk_level,
                    "state": o.state,
                    "approval": o.approval_status,
                    "organism": o.organism,
                }
            )
        return digest
