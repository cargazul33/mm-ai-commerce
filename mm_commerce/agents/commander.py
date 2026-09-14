"""COMMANDER — orquesta state machine; digest solo ABIERTAS."""
from __future__ import annotations

from mm_commerce.agents.base import BaseAgent
from mm_commerce.agents.bid import BidAgent
from mm_commerce.agents.pricing import PricingAgent
from mm_commerce.agents.pliego import PliegoAgent
from mm_commerce.agents.radar import RadarAgent, archive_expired
from mm_commerce.agents.risk import RiskAgent
from mm_commerce.agents.sourcing import SourcingAgent
from mm_commerce.agents.verifier import VerifierAgent
from mm_commerce.config import get_settings
from mm_commerce.digest import build_actionable_digest
from mm_commerce.models import Opportunity
from mm_commerce.scoring import fit_score
from mm_commerce.telegram_bot import notify_high_fit
from mm_commerce.timing import ACTIONABLE_TIMING, classify_timing

STATES = (
    "RADAR",
    "PLIEGO",
    "SOURCING",
    "PRICING",
    "RISK",
    "VERIFIER",
    "BID",
    "TELEGRAM",
    "DONE",
    "ARCHIVADA",
)


class Commander(BaseAgent):
    name = "commander"

    def run_once(self, *, limit: int = 15, min_fit: int | None = None) -> dict:
        run = self.start_run()
        settings = get_settings()
        min_fit = settings.fit_score_alert_min if min_fit is None else min_fit

        archive_stats = archive_expired(self.session)
        radar = RadarAgent(self.session)
        radar_stats = radar.run_once()

        # actionable open opps only
        candidates = (
            self.session.query(Opportunity)
            .filter(Opportunity.archived.is_(False))
            .filter(Opportunity.skipped.is_(False))
            .filter(Opportunity.fit_score >= min_fit)
            .all()
        )
        opps = []
        for opp in candidates:
            t = classify_timing(opp.cierre_at or opp.opening_at)
            opp.timing_state = t.state
            if t.state in ACTIONABLE_TIMING:
                opps.append(opp)

        # sort like digest: utilidad, FIT, soonest
        def sk(o: Opportunity):
            u = o.utilidad_estimada
            uk = (0, -(u or 0)) if u is not None else (1, 0)
            hours = classify_timing(o.cierre_at or o.opening_at).hours_left
            return (uk[0], uk[1], -o.fit_score, hours if hours is not None else 1e9)

        opps.sort(key=sk)
        opps = opps[:limit]

        results = []
        for opp in opps:
            results.append(self._pipeline_one(opp))

        digest = build_actionable_digest(self.session, min_fit=min_fit, limit=20)
        tg = notify_high_fit(self.session, digest)

        summary = (
            f"radar={radar_stats}; archived={archive_stats}; "
            f"processed={len(results)}; telegram={tg.get('status')}; "
            f"digest_n={len(digest)}"
        )
        self.finish_run(run, summary)
        self.session.commit()
        return {
            "radar": radar_stats,
            "archive": archive_stats,
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

        timing = classify_timing(opp.cierre_at or opp.opening_at)
        if timing.state not in ACTIONABLE_TIMING:
            return {
                "id": opp.external_id,
                "status": f"SKIP_TIMING:{timing.state}",
            }

        out: dict = {"id": opp.external_id, "fit": opp.fit_score, "steps": []}
        try:
            PliegoAgent(self.session).process(opp)
            out["steps"].append("PLIEGO")
            # Recalc FIT with concrete line items
            lines = []
            if opp.tender:
                lines = [f"{it.product} {it.specs}" for it in opp.tender.items]
                opp.line_count = len(lines)
            score, _ = fit_score(
                opp.title,
                opp.rubros,
                opp.organism,
                opp.modality,
                line_items=lines or None,
            )
            opp.fit_score = score
            out["fit"] = score

            SourcingAgent(self.session).process(opp)
            out["steps"].append("SOURCING")
            PricingAgent(self.session).process(opp)
            out["steps"].append("PRICING")
            # utilidad from latest offer
            if opp.offers:
                off = max(opp.offers, key=lambda x: x.id)
                if off.precio_objetivo is not None and off.cost_total is not None:
                    opp.utilidad_estimada = float(off.precio_objetivo) - float(
                        off.cost_total
                    )
            risk = RiskAgent(self.session).process(opp)
            out["steps"].append(f"RISK:{risk}")
            ver = VerifierAgent(self.session).process(opp)
            out["steps"].append(f"VERIFIER:{ver['status']}")
            bid = BidAgent(self.session).process(opp)
            out["steps"].append(f"BID:{bid.get('status')}")
            if (
                opp.approval_status != "BLOQUEADO"
                and opp.fit_score >= settings.fit_score_alert_min
            ):
                opp.state = "TELEGRAM"
            else:
                opp.state = "DONE" if opp.approval_status == "BLOQUEADO" else opp.state
            self.session.commit()
            out["approval"] = opp.approval_status
            out["risk"] = opp.risk_level
            out["state"] = opp.state
            out["timing"] = opp.timing_state
        except Exception as exc:  # noqa: BLE001
            out["error"] = f"{type(exc).__name__}: {exc}"
            self.session.rollback()
        return out
