"""SOURCING — proveedores REALES; hard MATCH%; nunca inventa precios/stock."""
from __future__ import annotations

import json

from mm_commerce.agents.base import BaseAgent
from mm_commerce.connectors.mercadolibre import search_public
from mm_commerce.connectors.real_suppliers import search_real_pages
from mm_commerce.matching import (
    TECH_NO_CUMPLE,
    TECH_NO_VER,
    CLASS_NO_CUMPLE,
    CLASS_NO_VER,
    full_spec_blob,
    match_line_to_candidate,
)
from mm_commerce.models import Opportunity, Supplier, SupplierQuote, Tender
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
        used_urls: set[str] = set()  # never reuse same URL across line items

        items = list(tender.items) if tender else []
        if not items:
            item_refs = [
                {
                    "tender_item_id": None,
                    "query": opp.title[:80] or opp.rubros[:80] or "insumos oficina",
                    "qty": 1.0,
                    "product": opp.title[:80] or "insumos",
                    "specs": "",
                    "brand": "",
                    "model": "",
                }
            ]
        else:
            item_refs = []
            for it in items:
                full = full_spec_blob(it.product, it.specs, it.brand, it.model)
                item_refs.append(
                    {
                        "tender_item_id": it.id,
                        "query": full,  # FULL pliego — no truncate
                        "qty": it.qty,
                        "product": it.product,
                        "specs": it.specs or full,
                        "brand": it.brand or "",
                        "model": it.model or "",
                    }
                )

        for ref in item_refs[:12]:
            query = ref["query"]
            label = ref["product"]
            qty = ref["qty"]
            tender_item_id = ref["tender_item_id"]

            results, src = search_real_pages(query, limit=4)
            sources_used.append(src)
            ml_results, ml_src = search_public(query[:120], limit=2)
            sources_used.append(ml_src)
            results = results + ml_results

            # Drop URLs already used by another line item
            filtered = []
            for res in results:
                url = (res.get("url") or "").strip()
                if url and url in used_urls:
                    continue
                filtered.append(res)
            results = filtered

            if not results:
                supplier = self._get_or_create_supplier(
                    "SIN_PROVEEDOR_VERIFICADO", source="none", url=""
                )
                empty = match_line_to_candidate(
                    product=ref["product"],
                    specs=ref["specs"],
                    brand=ref["brand"],
                    model=ref["model"],
                    candidate_title="",
                    candidate_text="",
                    source_url="",
                )
                q = SupplierQuote(
                    supplier_id=supplier.id,
                    opportunity_id=opp.id,
                    tender_item_id=tender_item_id,
                    product_label=label or query[:200],
                    unit_cost=None,
                    currency="ARS",
                    qty=qty,
                    match_score=0,
                    match_pct=0,
                    match_class=TECH_NO_VER,
                    technical_status=TECH_NO_VER,
                    commercial_status="PRECIO_NO_VERIFICADO",
                    evidence_json=empty.to_json(),
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

            line_best: SupplierQuote | None = None
            for res in results:
                url = (res.get("url") or "").strip()
                page_text = " ".join(
                    str(x)
                    for x in (
                        res.get("title"),
                        res.get("notes"),
                        res.get("raw_text"),
                        res.get("description"),
                    )
                    if x
                )
                mres = match_line_to_candidate(
                    product=ref["product"],
                    specs=ref["specs"],
                    brand=ref["brand"],
                    model=ref["model"],
                    candidate_title=res.get("title") or "",
                    candidate_text=page_text,
                    source_url=url,
                    price=res.get("price"),
                    stock=str(res.get("stock") or ""),
                    shipping_neuquen=str(res.get("shipping_neuquen") or ""),
                    qty_needed=float(qty or 1),
                )
                supplier = self._get_or_create_supplier(
                    res.get("seller") or "desconocido",
                    source=res.get("source", "unknown"),
                    url=url,
                )
                price = res.get("price")
                verification = res.get("verification", "NO VERIFICADO")
                if price is None:
                    verification = "NO VERIFICADO"
                # Hard class overrides soft verification labels
                tech = mres.technical_status or mres.match_class
                if tech in (TECH_NO_CUMPLE, "NO CUMPLE", CLASS_NO_CUMPLE):
                    verification = "NO CUMPLE"
                elif tech in (TECH_NO_VER, "NO VERIFICADO", CLASS_NO_VER):
                    verification = "NO VERIFICADO"
                elif tech in ("EXACTO", "EQUIVALENTE_PERMITIDO", "EQUIVALENTE PERMITIDO") and price is not None:
                    verification = res.get("verification") or "PROBABLE"
                else:
                    verification = verification or tech

                q = SupplierQuote(
                    supplier_id=supplier.id,
                    opportunity_id=opp.id,
                    tender_item_id=tender_item_id,
                    product_label=res.get("title") or label or query[:200],
                    unit_cost=float(price) if price is not None else None,
                    currency=res.get("currency") or "ARS",
                    qty=qty,
                    match_score=mres.match_pct,
                    match_pct=mres.match_pct,
                    match_class=tech,
                    technical_status=tech,
                    commercial_status=mres.commercial_status,
                    evidence_json=mres.to_json(),
                    verification=verification,
                    url=url,
                    notes=(res.get("notes") or src) + f" | class={mres.match_class}",
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
                if line_best is None or q.match_pct > line_best.match_pct:
                    line_best = q

            # Reserve the best usable URL for this line so other lines cannot reuse it
            if line_best and line_best.url:
                # Only lock URL if it is not a hard NO CUMPLE / wrong category
                if line_best.match_class not in (TECH_NO_CUMPLE, CLASS_NO_CUMPLE, "NO CUMPLE"):
                    used_urls.add(line_best.url)
                else:
                    # still prevent accidental reuse of known-bad cross matches
                    used_urls.add(line_best.url)

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
