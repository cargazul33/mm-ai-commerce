"""RADAR — CODINEU, dedupe, FIT SCORE, skip 16514 forever."""
from __future__ import annotations

import json

from mm_commerce.agents.base import BaseAgent
from mm_commerce.config import get_settings
from mm_commerce.connectors.codineu import fetch_public_list, has_login_credentials
from mm_commerce.models import Opportunity
from mm_commerce.scoring import fit_score, is_avoided, text_blob


class RadarAgent(BaseAgent):
    name = "radar"

    def run_once(self) -> dict:
        run = self.start_run()
        settings = get_settings()
        items, source_note = fetch_public_list()
        creds = has_login_credentials()
        created = 0
        updated = 0
        skipped = 0

        for raw in items:
            ext_id = str(raw.get("id") or raw.get("process_id") or "").strip()
            if not ext_id:
                continue
            if ext_id in settings.excluded_ids:
                skipped += 1
                self.finding(
                    run,
                    f"HARD SKIP proceso {ext_id} (excluido forever)",
                    severity="WARN",
                    code="HARD_SKIP",
                )
                continue

            title = raw.get("titulo") or raw.get("title") or ""
            rubros = raw.get("rubros") or ""
            organism = raw.get("organismo") or raw.get("organism") or ""
            modality = raw.get("modalidad") or raw.get("modality") or ""
            blob = text_blob(title, rubros, organism, modality)
            score, reason = fit_score(title, rubros, organism, modality)
            skip = False
            skip_reason = ""
            if is_avoided(blob):
                skip = True
                skip_reason = "categoría_evitada"
                score = min(score, 10)

            existing = (
                self.session.query(Opportunity)
                .filter_by(external_id=ext_id)
                .one_or_none()
            )
            if existing:
                existing.title = title
                existing.organism = organism
                existing.rubros = rubros
                existing.modality = modality
                existing.opening_at = raw.get("apertura") or existing.opening_at
                existing.url = raw.get("url") or existing.url
                existing.fit_score = score
                existing.skipped = skip
                existing.skip_reason = skip_reason
                existing.raw_json = json.dumps(raw, ensure_ascii=False)
                if existing.state == "RADAR" or not existing.state:
                    existing.state = "RADAR"
                updated += 1
            else:
                opp = Opportunity(
                    external_id=ext_id,
                    source="codineu",
                    title=title,
                    organism=organism,
                    rubros=rubros,
                    modality=modality,
                    opening_at=raw.get("apertura") or "",
                    url=raw.get("url") or "",
                    fit_score=score,
                    state="RADAR",
                    skipped=skip,
                    skip_reason=skip_reason,
                    raw_json=json.dumps(raw, ensure_ascii=False),
                )
                self.session.add(opp)
                created += 1

        summary = (
            f"source={source_note}; created={created}; updated={updated}; "
            f"skipped_hard={skipped}; login_env={'OK' if creds else 'REQUIERE CREDENCIALES'}; "
            f"total_seen={len(items)}"
        )
        self.finish_run(run, summary)
        self.session.commit()
        return {
            "created": created,
            "updated": updated,
            "skipped_hard": skipped,
            "source": source_note,
            "login": "OK" if creds else "REQUIERE CREDENCIALES",
            "total": len(items),
        }
