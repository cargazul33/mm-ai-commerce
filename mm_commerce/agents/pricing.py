"""PRICING — COST_TOTAL; PRECIO_OBJETIVO=COST×1.90; tax/logistics PENDING."""
from __future__ import annotations

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

        # pick best quote per tender_item (or overall) with known cost
        chosen: list[SupplierQuote] = []
        seen_items: set[int | None] = set()
        for q in quotes:
            key = q.tender_item_id
            if key in seen_items and key is not None:
                continue
            if q.unit_cost is None:
                continue
            chosen.append(q)
            seen_items.add(key)

        cost_total = None
        if chosen:
            cost_total = sum((q.unit_cost or 0) * (q.qty or 1) for q in chosen)

        precio = None if cost_total is None else round(cost_total * mult, 2)

        offer = Offer(
            opportunity_id=opp.id,
            cost_total=cost_total,
            precio_objetivo=precio,
            margin_multiplier=mult,
            tax_status="PENDING",
            logistics_status="PENDING",
            status="BORRADOR",
            notes=(
                "tax/logistics PENDING — no inventar tasas"
                if cost_total is not None
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

        # logistics stub
        self.session.add(
            LogisticsQuote(
                opportunity_id=opp.id,
                carrier="",
                amount=None,
                status="PENDING",
                notes="REQUIERE COTIZACIÓN REAL — no inventar flete",
            )
        )

        self.finish_run(
            run,
            f"cost_total={cost_total}; precio_objetivo={precio}; mult={mult}; "
            f"tax=PENDING; logistics=PENDING; lines={len(chosen)}",
        )
        opp.state = "PRICING"
        self.session.commit()
        return offer
