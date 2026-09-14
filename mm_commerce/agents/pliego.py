"""PLIEGO — ítems estructurados; NO inventa; marca NO VERIFICADO."""
from __future__ import annotations

from mm_commerce.agents.base import BaseAgent
from mm_commerce.connectors.codineu import find_pliego_docs, read_pliego_text
from mm_commerce.extractors.pliego_lines import extract_line_items
from mm_commerce.models import Opportunity, Tender, TenderItem


class PliegoAgent(BaseAgent):
    name = "pliego"

    def process(self, opp: Opportunity) -> Tender:
        run = self.start_run(opp.id)
        docs = find_pliego_docs(opp.external_id)
        text, doc_path = read_pliego_text(opp.external_id)

        tender = (
            self.session.query(Tender).filter_by(opportunity_id=opp.id).one_or_none()
        )
        if tender is None:
            tender = Tender(opportunity_id=opp.id, process_id=opp.external_id)
            self.session.add(tender)
            self.session.flush()

        tender.doc_path = doc_path or (str(docs[0]) if docs else "")
        tender.deadlines = opp.opening_at
        tender.delivery_notes = "NO VERIFICADO" if not text else "ver pliego"
        tender.notes = ""

        # clear prior items for re-extract
        for old in list(tender.items):
            self.session.delete(old)
        self.session.flush()

        items = extract_line_items(text) if text else []
        if not text:
            tender.extraction_status = "SIN_DOCUMENTO"
            tender.notes = "sin_documento; no_inventar"
        elif not items:
            tender.extraction_status = "SIN_LINEAS"
            tender.notes = "sin_lineas_confiables_en_documento; no_inventar"
        else:
            tender.extraction_status = "NO VERIFICADO"
            patterns = sorted({it.get("source_pattern", "") for it in items})
            tender.notes = f"extracted={len(items)}; patterns={','.join(patterns)}"

        for it in items:
            self.session.add(
                TenderItem(
                    tender_id=tender.id,
                    line_no=it["line_no"],
                    product=it["product"],
                    qty=it["qty"],
                    unit=it["unit"],
                    brand=it.get("brand", ""),
                    model=it.get("model", ""),
                    specs=it.get("specs", ""),
                    verification=it.get("verification", "NO VERIFICADO"),
                )
            )

        self.finish_run(
            run,
            f"items={len(items)}; doc={tender.doc_path or 'NINGUNO'}; "
            f"status={tender.extraction_status}",
        )
        opp.state = "PLIEGO"
        self.session.commit()
        return tender
