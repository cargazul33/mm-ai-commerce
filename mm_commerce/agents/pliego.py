"""PLIEGO — descarga oficial + ítems; NO inventa."""
from __future__ import annotations

from pathlib import Path

from mm_commerce.agents.base import BaseAgent
from mm_commerce.connectors.codineu import find_pliego_docs, read_pliego_text
from mm_commerce.connectors.pliego_download import download_opportunity_docs
from mm_commerce.extractors.pliego_lines import extract_line_items
from mm_commerce.models import Opportunity, Tender, TenderItem
from mm_commerce.config import get_settings


class PliegoAgent(BaseAgent):
    name = "pliego"

    def process(self, opp: Opportunity) -> Tender:
        run = self.start_run(opp.id)
        settings = get_settings()

        # Live: download ALL official attachments into data/pliegos/{id}
        dl_info = {}
        if opp.url and not settings.use_fixtures:
            dl_info = download_opportunity_docs(opp.url, opp.external_id)
            if dl_info.get("ok") and dl_info.get("files"):
                # point pliegos_dir search at downloaded folder via symlink/copy already there
                dest = Path(dl_info["dir"])
                # also mirror under fixtures path expected by find_pliego_docs
                mirror = settings.project_root / "data" / "pliegos" / opp.external_id
                mirror.mkdir(parents=True, exist_ok=True)
                opp.pliego_url = dl_info["files"][0].get("url") or opp.url

        docs = find_pliego_docs(opp.external_id)
        # Prefer freshly downloaded dir
        local_dir = settings.project_root / "data" / "pliegos" / opp.external_id
        if local_dir.exists():
            docs = sorted(
                [
                    p
                    for p in local_dir.iterdir()
                    if p.is_file()
                    and p.suffix.lower() in {".pdf", ".txt", ".html", ".htm", ".doc", ".docx"}
                ]
            ) or docs

        text, doc_path = "", ""
        if docs:
            # Prefer pdf via pdftotext
            preferred = sorted(
                docs, key=lambda p: (0 if p.suffix.lower() == ".pdf" else 1, p.name)
            )
            path = preferred[0]
            if path.suffix.lower() == ".pdf":
                import subprocess

                try:
                    r = subprocess.run(
                        ["pdftotext", "-layout", str(path), "-"],
                        capture_output=True,
                        timeout=60,
                        check=False,
                    )
                    if r.returncode == 0 and r.stdout:
                        text = r.stdout.decode("utf-8", errors="replace")[:120000]
                        doc_path = str(path)
                except (FileNotFoundError, subprocess.TimeoutExpired):
                    text, doc_path = "", f"PDF_SIN_TEXTO:{path}"
            else:
                text = path.read_text(encoding="utf-8", errors="replace")[:120000]
                doc_path = str(path)
        if not text:
            text, doc_path = read_pliego_text(opp.external_id)

        tender = (
            self.session.query(Tender).filter_by(opportunity_id=opp.id).one_or_none()
        )
        if tender is None:
            tender = Tender(opportunity_id=opp.id, process_id=opp.external_id)
            self.session.add(tender)
            self.session.flush()

        tender.doc_path = doc_path or (str(docs[0]) if docs else "")
        tender.deadlines = opp.cierre_at or opp.opening_at
        tender.delivery_notes = "NO VERIFICADO" if not text else "ver pliego"
        tender.notes = ""

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
            if dl_info:
                tender.notes += f"; download_ok={dl_info.get('ok')}; files={len(dl_info.get('files') or [])}"

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

        opp.line_count = len(items)
        if tender.doc_path and str(tender.doc_path).startswith("http"):
            opp.pliego_url = tender.doc_path
        elif not opp.pliego_url:
            opp.pliego_url = opp.url or ""

        self.finish_run(
            run,
            f"items={len(items)}; doc={tender.doc_path or 'NINGUNO'}; "
            f"status={tender.extraction_status}; dl={dl_info.get('ok')}",
        )
        opp.state = "PLIEGO"
        self.session.commit()
        return tender
