"""PRICING — separates technical coverage from commercial readiness.

Full cotizable offer ONLY when every mandatory line has:
  TECHNICAL in {EXACTO, EQUIVALENTE_PERMITIDO}
  AND COMMERCIAL == DISPONIBLE (price+stock+envío verified)
"""
from __future__ import annotations

import json

from mm_commerce.agents.base import BaseAgent
from mm_commerce.config import get_settings
from mm_commerce.matching import (
    COMM_DISPONIBLE,
    TECH_EXACTO,
    TECH_EQUIV,
    TECH_NO_CUMPLE,
    TECH_NO_VER,
    VERIFIED_TECH,
)
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

        best_by_item: dict[int | None, SupplierQuote] = {}
        ranked = sorted(
            quotes,
            key=lambda q: (
                0 if (q.technical_status or q.match_class or "") in VERIFIED_TECH else 1,
                0 if q.unit_cost is not None else 1,
                -(q.match_pct or q.match_score or 0),
            ),
        )
        for q in ranked:
            key = q.tender_item_id
            if key in best_by_item and key is not None:
                continue
            best_by_item[key] = q

        lines_total = len(tender.items) if tender else 0
        tech_ok_lines: list[SupplierQuote] = []
        commercial_ok_lines: list[SupplierQuote] = []
        pending: list[dict] = []

        if tender and tender.items:
            for it in tender.items:
                q = best_by_item.get(it.id)
                tech = (q.technical_status if q else "") or (q.match_class if q else "") or TECH_NO_VER
                comm = (q.commercial_status if q else "") or "PRECIO_NO_VERIFICADO"
                if tech in (TECH_NO_CUMPLE, "NO CUMPLE") or tech in (TECH_NO_VER, "NO VERIFICADO", "POSIBLE"):
                    pending.append(
                        {
                            "line_no": it.line_no,
                            "tech": tech,
                            "commercial": comm,
                            "reason": f"TECH:{tech}",
                        }
                    )
                    continue
                if tech in VERIFIED_TECH and (q.match_pct or 0) >= 90:
                    tech_ok_lines.append(q)
                    if (
                        q.unit_cost is not None
                        and comm == COMM_DISPONIBLE
                    ):
                        commercial_ok_lines.append(q)
                    else:
                        pending.append(
                            {
                                "line_no": it.line_no,
                                "tech": tech,
                                "commercial": comm,
                                "reason": f"COMMERCIAL:{comm}",
                            }
                        )
                else:
                    pending.append(
                        {
                            "line_no": it.line_no,
                            "tech": tech,
                            "commercial": comm,
                            "reason": "TECH_WEAK",
                        }
                    )
        else:
            pending.append({"line_no": 0, "reason": "SIN_ITEMS"})

        cobertura_tech = len(tech_ok_lines)
        cobertura_comm = len(commercial_ok_lines)
        costo_confirmado = (
            round(sum((q.unit_cost or 0) * (q.qty or 1) for q in commercial_ok_lines), 2)
            if commercial_ok_lines
            else 0.0
        )
        bid_scope = {}
        if tender and getattr(tender, "bid_scope_json", None):
            try:
                bid_scope = json.loads(tender.bid_scope_json or "{}")
            except Exception:
                bid_scope = {}
        scope_state = bid_scope.get("state") or "UNKNOWN"
        # TOTAL_REQUIRED → need all lines; ITEM_LEVEL → any ready line is cotizable
        if scope_state == "ITEM_LEVEL_ALLOWED":
            full_ok = (
                tender is not None
                and lines_total > 0
                and cobertura_comm >= 1
            )
        elif scope_state == "TOTAL_REQUIRED":
            full_ok = (
                tender is not None
                and lines_total > 0
                and cobertura_tech == lines_total
                and cobertura_comm == lines_total
                and not pending
            )
        else:
            # Legacy / missing scope: keep TOTAL_REQUIRED economics (verifier still blocks UNKNOWN)
            full_ok = (
                tender is not None
                and lines_total > 0
                and cobertura_tech == lines_total
                and cobertura_comm == lines_total
                and not pending
            )

        merchandise = costo_confirmado if (full_ok or (scope_state == "ITEM_LEVEL_ALLOWED" and cobertura_comm >= 1)) else None
        if scope_state == "ITEM_LEVEL_ALLOWED" and cobertura_comm >= 1:
            # partial offer economics on ready lines only
            pass
        logistics = None
        logistics_status = "PENDING"
        total = merchandise
        precio = None if total is None else round(total * mult, 2)
        utilidad = None if (precio is None or total is None) else round(precio - total, 2)
        margen = None
        if precio and total and precio > 0:
            margen = round(100.0 * (precio - total) / precio, 2)

        note = (
            f"COBERTURA_TECNICA {cobertura_tech}/{lines_total}; "
            f"COBERTURA_COMERCIAL {cobertura_comm}/{lines_total}; "
            f"scope={scope_state}; apto={'SI' if full_ok else 'NO'}"
        )

        offer = Offer(
            opportunity_id=opp.id,
            cost_total=merchandise,
            precio_objetivo=precio,
            margin_multiplier=mult,
            tax_status="PENDING",
            logistics_status=logistics_status,
            status="BORRADOR" if full_ok else "BLOQUEADO_MATCHING",
            merchandise_cost=merchandise,
            logistics_cost=logistics,
            other_costs=None,
            total_cost=total,
            utilidad=utilidad,
            margen_pct=margen,
            capital_requerido=total,
            economic_json=json.dumps(
                {
                    "cobertura_tecnica": f"{cobertura_tech}/{lines_total}",
                    "cobertura_comercial": f"{cobertura_comm}/{lines_total}",
                    "cobertura": f"{cobertura_tech}/{lines_total}",
                    "costo_confirmado": costo_confirmado,
                    "costo_verificado": costo_confirmado,
                    "costo_faltante": None if full_ok else "N/D (líneas sin evidencia comercial completa)",
                    "costo_pendiente": None if full_ok else "N/D (líneas sin evidencia comercial completa)",
                    "apto_para_cotizar": full_ok,
                    "pending_lines": pending,
                    "merchandise": merchandise,
                    "logistics_status": logistics_status,
                    "precio_objetivo": precio,
                    "multiplier": mult,
                    "note": note,
                },
                ensure_ascii=False,
            ),
            notes=note,
        )
        self.session.add(offer)
        self.session.flush()

        if tender and tender.items:
            for it in tender.items:
                q = best_by_item.get(it.id)
                tech = (q.technical_status if q else "") or (q.match_class if q else TECH_NO_VER)
                item_ready = q is not None and q in commercial_ok_lines and (
                    full_ok or scope_state == "ITEM_LEVEL_ALLOWED"
                )
                if item_ready:
                    self.session.add(
                        OfferItem(
                            offer_id=offer.id,
                            tender_item_id=it.id,
                            description=q.product_label,
                            qty=q.qty,
                            unit_cost=q.unit_cost,
                            unit_price=round(q.unit_cost * mult, 2),
                            verification=tech,
                        )
                    )
                else:
                    self.session.add(
                        OfferItem(
                            offer_id=offer.id,
                            tender_item_id=it.id,
                            description=(q.product_label if q else it.product),
                            qty=it.qty,
                            unit_cost=None,
                            unit_price=None,
                            verification=tech or TECH_NO_VER,
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
        opp.utilidad_estimada = utilidad
        self.finish_run(
            run,
            f"tech={cobertura_tech}/{lines_total}; comm={cobertura_comm}/{lines_total}; apto={full_ok}",
        )
        opp.state = "PRICING"
        self.session.commit()
        return offer
