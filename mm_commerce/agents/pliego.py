"""PLIEGO — ítems estructurados; NO inventa; marca NO VERIFICADO."""
from __future__ import annotations

import re

from mm_commerce.agents.base import BaseAgent
from mm_commerce.connectors.codineu import find_pliego_docs, read_pliego_text
from mm_commerce.models import Opportunity, Tender, TenderItem

# Patrones determinísticos — extracción, no invención
LINE_PATTERNS = [
    re.compile(
        r"(?P<qty>\d+[.,]?\d*)\s*(?P<unit>u\.?|unidades?|kits?|cajas?|resmas?|jg|juegos?)?\s*"
        r"(?:de\s+)?(?P<product>[A-Za-zÁÉÍÓÚáéíóúñÑ0-9 /\-]{8,120})",
        re.I,
    ),
]
ITEM_KEYWORDS = (
    "notebook",
    "computadora",
    "impresora",
    "monitor",
    "toner",
    "cartucho",
    "silla",
    "escritorio",
    "resma",
    "carpeta",
    "router",
    "switch",
    "cable",
    "teclado",
    "mouse",
    "ups",
    "disco",
    "memoria",
    "proyector",
)


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
        tender.extraction_status = "NO VERIFICADO"
        tender.notes = ""

        # clear prior items for re-extract
        for old in list(tender.items):
            self.session.delete(old)
        self.session.flush()

        items = self._extract_items(text, opp)
        if not items:
            # fallback: one placeholder from title — marked NO VERIFICADO
            items = [
                {
                    "line_no": 1,
                    "product": (opp.title[:180] or "ítem no extraído"),
                    "qty": 1.0,
                    "unit": "u",
                    "brand": "",
                    "model": "",
                    "specs": opp.rubros[:300],
                    "verification": "NO VERIFICADO",
                }
            ]
            tender.notes = "sin_lineas_en_documento; proxy_titulo"

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
            f"items={len(items)}; doc={tender.doc_path or 'NINGUNO'}; status=NO VERIFICADO",
        )
        opp.state = "PLIEGO"
        self.session.commit()
        return tender

    def _extract_items(self, text: str, opp: Opportunity) -> list[dict]:
        if not text:
            return []
        found: list[dict] = []
        lines = text.splitlines()
        line_no = 0
        for raw in lines:
            line = raw.strip()
            if len(line) < 8:
                continue
            low = line.lower()
            if not any(k in low for k in ITEM_KEYWORDS):
                # also accept numbered list-ish lines with qty
                if not re.search(r"\b\d+\b", line):
                    continue
                if not any(
                    k in low
                    for k in ("adquis", "insumo", "elemento", "material", "equip")
                ):
                    continue
            m = LINE_PATTERNS[0].search(line)
            line_no += 1
            if m:
                qty_s = (m.group("qty") or "1").replace(",", ".")
                try:
                    qty = float(qty_s)
                except ValueError:
                    qty = 1.0
                product = (m.group("product") or line)[:200].strip()
                unit = (m.group("unit") or "u")[:16]
            else:
                qty = 1.0
                product = line[:200]
                unit = "u"
            brand = ""
            model = ""
            bm = re.search(
                r"\b(HP|Dell|Lenovo|Epson|Brother|Cisco|Samsung|LG|Acer|Canon|Logitech)\b",
                line,
                re.I,
            )
            if bm:
                brand = bm.group(1)
            found.append(
                {
                    "line_no": line_no,
                    "product": product,
                    "qty": qty,
                    "unit": unit,
                    "brand": brand,
                    "model": model,
                    "specs": line[:400],
                    "verification": "NO VERIFICADO",
                }
            )
            if line_no >= 30:
                break
        return found
