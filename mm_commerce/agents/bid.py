"""BID — prepara paquete de oferta ONLY; NUNCA auto-submit."""
from __future__ import annotations

import json
from pathlib import Path

from mm_commerce.agents.base import BaseAgent
from mm_commerce.config import get_settings
from mm_commerce.models import Offer, Opportunity, Tender


class BidAgent(BaseAgent):
    name = "bid"

    def process(self, opp: Opportunity) -> dict:
        run = self.start_run(opp.id)
        settings = get_settings()

        if opp.approval_status == "BLOQUEADO":
            self.finish_run(run, "no_package:BLOQUEADO", status="SKIP")
            self.session.commit()
            return {"status": "SKIP", "reason": "BLOQUEADO"}

        # NEVER purchase/pay/present without approval
        if opp.approval_status != "APROBADO":
            note = "paquete_borrador_solo; requiere APROBADO humano para presentar"
        else:
            note = "aprobado_humano; listo_para_presentación_manual"

        offer = (
            self.session.query(Offer)
            .filter_by(opportunity_id=opp.id)
            .order_by(Offer.id.desc())
            .first()
        )
        tender = (
            self.session.query(Tender).filter_by(opportunity_id=opp.id).one_or_none()
        )

        out_dir = settings.project_root / "offers_out"
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"offer_{opp.external_id}.json"

        econ = {}
        if offer and offer.economic_json:
            try:
                econ = json.loads(offer.economic_json)
            except Exception:
                econ = {}
        package = {
            "opportunity_id": opp.external_id,
            "title": opp.title,
            "organism": opp.organism,
            "fit_score": opp.fit_score,
            "risk_level": opp.risk_level,
            "approval_status": opp.approval_status,
            "auto_submit": False,
            "warning": "NUNCA auto-submit — solo preparación de paquete",
            "cost_total": offer.cost_total if offer else None,
            "precio_objetivo": offer.precio_objetivo if offer else None,
            "margin_multiplier": offer.margin_multiplier if offer else settings.margin_multiplier,
            "tax_status": offer.tax_status if offer else "PENDING",
            "logistics_status": offer.logistics_status if offer else "PENDING",
            "cobertura": econ.get("cobertura"),
            "costo_verificado": econ.get("costo_verificado"),
            "costo_pendiente": econ.get("costo_pendiente"),
            "apto_para_cotizar": econ.get("apto_para_cotizar"),
            "items": [
                {
                    "description": oi.description,
                    "qty": oi.qty,
                    "unit_cost": oi.unit_cost,
                    "unit_price": oi.unit_price,
                    "verification": oi.verification,
                }
                for oi in (offer.items if offer else [])
            ],
            "tender_doc": tender.doc_path if tender else "",
            "note": note,
        }
        path.write_text(json.dumps(package, indent=2, ensure_ascii=False), encoding="utf-8")

        if offer:
            offer.package_path = str(path)
            offer.status = "PAQUETE_LISTO"
            offer.notes = (offer.notes or "") + f" | {note}"

        self.finish_run(run, f"package={path}; auto_submit=False; approval={opp.approval_status}")
        opp.state = "BID"
        self.session.commit()
        return {"status": "OK", "path": str(path), "auto_submit": False}
