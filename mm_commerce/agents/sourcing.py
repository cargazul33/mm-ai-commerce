"""SOURCING — proveedores REALES; MATCH%; nunca inventa precios/stock."""
from __future__ import annotations

from mm_commerce.agents.base import BaseAgent
from mm_commerce.connectors.mercadolibre import search_public
from mm_commerce.connectors.real_suppliers import search_real_pages
from mm_commerce.models import Opportunity, Supplier, SupplierQuote, Tender
from mm_commerce.scoring import match_score
from mm_commerce.timing import now_ba


class SourcingAgent(BaseAgent):
    name = "sourcing"

    def process(self, opp: Opportunity) -> list[SupplierQuote]:
        run = self.start_run(opp.id)
        tender = (
            self.session.query(Tender).filter_by(opportunity_id=opp.id).one_or_none()
        )
        # clear prior quotes for re-run honesty
        for old in (
            self.session.query(SupplierQuote).filter_by(opportunity_id=opp.id).all()
        ):
            self.session.delete(old)
        self.session.flush()

        quotes: list[SupplierQuote] = []
        sources_used: list[str] = []

        items = list(tender.items) if tender else []
        if not items:
            queries = [opp.title[:80] or opp.rubros[:80] or "insumos oficina"]
            item_refs: list[tuple] = [(None, q, 1.0, q) for q in queries]
        else:
            item_refs = [
                (
                    it.id,
                    f"{it.brand} {it.model} {it.product} {it.specs}".strip(),
                    it.qty,
                    it.product,
                )
                for it in items
            ]

        for tender_item_id, query, qty, label in item_refs[:12]:
            # 1) real public product pages (preferred)
            results, src = search_real_pages(query, limit=3)
            sources_used.append(src)
            # 2) ML live (may be empty if blocked)
            ml_results, ml_src = search_public(query[:120], limit=2)
            sources_used.append(ml_src)
            results = results + ml_results

            if not results:
                # honest empty — NO VERIFICADO placeholder without price
                supplier = self._get_or_create_supplier(
                    "SIN_PROVEEDOR_VERIFICADO", source="none", url=""
                )
                q = SupplierQuote(
                    supplier_id=supplier.id,
                    opportunity_id=opp.id,
                    tender_item_id=tender_item_id,
                    product_label=label or query,
                    unit_cost=None,
                    currency="ARS",
                    qty=qty,
                    match_score=0,
                    match_pct=0,
                    verification="NO VERIFICADO",
                    url="",
                    notes="sin evidencia pública de precio/stock",
                    stock_note="NO VERIFICADO",
                    shipping_neuquen="NO VERIFICADO",
                    verified_at=now_ba().isoformat(timespec="seconds"),
                )
                self.session.add(q)
                quotes.append(q)
                continue

            for res in results:
                supplier = self._get_or_create_supplier(
                    res.get("seller") or "desconocido",
                    source=res.get("source", "unknown"),
                    url=res.get("url", ""),
                )
                price = res.get("price")
                verification = res.get("verification", "NO VERIFICADO")
                if price is None:
                    verification = "NO VERIFICADO"
                ms = match_score(query, res.get("title") or label or "")
                # boost match if model tokens present
                for tok in ("DS-P7001", "UPS3500", "FO-4075", "FDB-012", "WI-AP217", "9163E"):
                    if tok.lower() in query.lower() and tok.lower() in (
                        res.get("title") or ""
                    ).lower():
                        ms = max(ms, 85)
                q = SupplierQuote(
                    supplier_id=supplier.id,
                    opportunity_id=opp.id,
                    tender_item_id=tender_item_id,
                    product_label=res.get("title") or label or query,
                    unit_cost=float(price) if price is not None else None,
                    currency=res.get("currency") or "ARS",
                    qty=qty,
                    match_score=ms,
                    match_pct=ms,
                    verification=verification,
                    url=res.get("url") or "",
                    notes=res.get("notes") or src,
                    stock_note=str(res.get("stock") or "NO VERIFICADO"),
                    shipping_neuquen=str(
                        res.get("shipping_neuquen") or "NO VERIFICADO"
                    ),
                    verified_at=str(
                        res.get("verified_at")
                        or now_ba().isoformat(timespec="seconds")
                    ),
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
            if url and not s.url:
                s.url = url
            return s
        s = Supplier(name=name, source=source, url=url)
        self.session.add(s)
        self.session.flush()
        return s
