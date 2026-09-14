"""SOURCING — proveedores; MATCH SCORE; nunca inventa precios."""
from __future__ import annotations

from mm_commerce.agents.base import BaseAgent
from mm_commerce.connectors.mercadolibre import search_public
from mm_commerce.models import Opportunity, Supplier, SupplierQuote, Tender
from mm_commerce.scoring import match_score


class SourcingAgent(BaseAgent):
    name = "sourcing"

    def process(self, opp: Opportunity) -> list[SupplierQuote]:
        run = self.start_run(opp.id)
        tender = (
            self.session.query(Tender).filter_by(opportunity_id=opp.id).one_or_none()
        )
        quotes: list[SupplierQuote] = []
        sources_used: list[str] = []

        items = list(tender.items) if tender else []
        if not items:
            queries = [opp.title[:80] or opp.rubros[:80] or "insumos oficina"]
            item_refs: list[tuple] = [(None, q, 1.0) for q in queries]
        else:
            item_refs = [
                (it.id, f"{it.brand} {it.model} {it.product}".strip(), it.qty)
                for it in items
            ]

        for tender_item_id, query, qty in item_refs[:10]:
            results, src = search_public(query, limit=3)
            sources_used.append(src)
            for res in results:
                supplier = self._get_or_create_supplier(
                    res.get("seller") or "desconocido",
                    source=res.get("source", "unknown"),
                    url=res.get("url", ""),
                )
                price = res.get("price")
                # never invent: keep None if missing
                verification = res.get("verification", "NO VERIFICADO")
                if price is None:
                    verification = "NO VERIFICADO"
                ms = match_score(query, res.get("title") or "")
                q = SupplierQuote(
                    supplier_id=supplier.id,
                    opportunity_id=opp.id,
                    tender_item_id=tender_item_id,
                    product_label=res.get("title") or query,
                    unit_cost=float(price) if price is not None else None,
                    currency=res.get("currency") or "ARS",
                    qty=qty,
                    match_score=ms,
                    verification=verification,
                    url=res.get("url") or "",
                    notes=res.get("notes") or src,
                )
                self.session.add(q)
                quotes.append(q)

        self.finish_run(
            run,
            f"quotes={len(quotes)}; sources={','.join(sorted(set(sources_used)))}",
        )
        opp.state = "SOURCING"
        self.session.commit()
        return quotes

    def _get_or_create_supplier(self, name: str, source: str, url: str) -> Supplier:
        name = (name or "desconocido")[:255]
        s = self.session.query(Supplier).filter_by(name=name).one_or_none()
        if s:
            return s
        s = Supplier(name=name, source=source, url=url)
        self.session.add(s)
        self.session.flush()
        return s
