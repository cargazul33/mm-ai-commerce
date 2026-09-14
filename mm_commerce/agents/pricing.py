"""PRICING — merchandise + logistics + costs → ×1.90 → utilidad/margen/capital."""
from __future__ import annotations

import json

from mm_commerce.agents.base import BaseAgent
from mm_commerce.config import get_settings
from mm_commerce.models import (
    LogisticsQuote,
    Offer,
    OfferItem,
    Opportunity,
    SupplierQuote,
    Tender,
)


class PricingAgent(BaseAgent):
    name = "pricing"

    def process(self, opp: Opportunity) -> Offer:
        run = self.start_run(opp.id)
        settings = get_settings()
        mult = settings.margin_multiplier

        quotes = (
            self.session.query(SupplierQuote)
            .filter_by(opportunity_id=opp.id)
            .order_by(SupplierQuote.match_score.desc())
            .all()
        )
        tender = (
            self.session.query(Tender).filter_by(opportunity_id=opp.id).one_or_none()
        )

        chosen: list[SupplierQuote] = []
        seen_items: set[int | None] = set()
        ranked = sorted(
            quotes,
            key=lambda q: (
                0 if q.verification == "PROBABLE" else 1,
                0 if q.unit_cost is not None else 1,
                -q.match_score,
            ),
        )
        for q in ranked:
            key = q.tender_item_id
            if key in seen_items and key is not None:
                continue
            if q.unit_cost is None:
                continue
            if q.verification == "NO VERIFICADO" and not q.url:
                continue
            if (q.match_pct or q.match_score or 0) < 40:
                continue  # weak evidence — do not use in economic base
            chosen.append(q)
            seen_items.add(key)

        merchandise = None
        if chosen:
            merchandise = round(
                sum((q.unit_cost or 0) * (q.qty or 1) for q in chosen), 2
            )

        logistics = None
        logistics_status = "PENDING"
        other_costs = None

        total = None
        if merchandise is not None:
            if logistics is None:
                total = merchandise
            else:
                total = round(merchandise + (logistics or 0) + (other_costs or 0), 2)

        precio = None if total is None else round(total * mult, 2)
        utilidad = None if (precio is None or total is None) else round(precio - total, 2)
        margen = None
        if precio and total and precio > 0:
            margen = round(100.0 * (precio - total) / precio, 2)
        capital = total

        offer = Offer(
            opportunity_id=opp.id,
            cost_total=merchandise,
            precio_objetivo=precio,
            margin_multiplier=mult,
            tax_status="PENDING",
            logistics_status=logistics_status,
            status="BORRADOR",
            merchandise_cost=merchandise,
            logistics_cost=logistics,
            other_costs=other_costs,
            total_cost=total,
            utilidad=utilidad,
            margen_pct=margen,
            capital_requerido=capital,
            economic_json=json.dumps(
                {
                    "merchandise": merchandise,
                    "logistics": logistics,
                    "logistics_status": logistics_status,
                    "other_costs": other_costs,
                    "total_cost": total,
                    "precio_objetivo": precio,
                    "multiplier": mult,
                    "utilidad": utilidad,
                    "margen_pct": margen,
                    "capital_requerido": capital,
                    "lines_priced": len(chosen),
                    "lines_total": len(tender.items) if tender else 0,
                    "note": "logistics PENDING — no inventar flete a Neuquén",
                },
                ensure_ascii=False,
            ),
            notes=(
                "tax/logistics PENDING — no inventar tasas/flete"
                if merchandise is not None
                else "sin costos verificados — no inventar"
            ),
        )
        self.session.add(offer)
        self.session.flush()

        if chosen:
            for q in chosen:
                unit_price = (
                    None if q.unit_cost is None else round(q.unit_cost * mult, 2)
                )
                self.session.add(
                    OfferItem(
                        offer_id=offer.id,
                        tender_item_id=q.tender_item_id,
                        description=q.product_label,
                        qty=q.qty,
                        unit_cost=q.unit_cost,
                        unit_price=unit_price,
                        verification=q.verification,
                    )
                )
        elif tender:
            for it in tender.items:
                self.session.add(
                    OfferItem(
                        offer_id=offer.id,
                        tender_item_id=it.id,
                        description=it.product,
                        qty=it.qty,
                        unit_cost=None,
                        unit_price=None,
                        verification="NO VERIFICADO",
                    )
                )

        self.session.add(
            LogisticsQuote(
                opportunity_id=opp.id,
                carrier="",
                amount=None,
                status="PENDING",
                notes="REQUIERE COTIZACIÓN REAL a Neuquén — no inventar flete",
            )
        )

        if utilidad is not None:
            opp.utilidad_estimada = utilidad

        self.finish_run(
            run,
            f"merch={merchandise}; total={total}; precio={precio}; "
            f"utilidad={utilidad}; margen%={margen}; capital={capital}; "
            f"lines={len(chosen)}",
        )
        opp.state = "PRICING"
        self.session.commit()
        return offer
