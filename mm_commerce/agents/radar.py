"""RADAR — CODINEU, dedupe, FIT SCORE, timing BA, archive VENCIDAS, skip 16514."""
from __future__ import annotations

import json

from mm_commerce.agents.base import BaseAgent
from mm_commerce.config import get_settings
from mm_commerce.connectors.codineu import fetch_public_list, has_login_credentials
from mm_commerce.models import Opportunity, Tender, TenderItem
from mm_commerce.scoring import fit_score, is_avoided, text_blob
from mm_commerce.timing import (
    TIMING_FECHA_NO_VERIFICADA,
    TIMING_VENCIDA,
    classify_timing,
    format_cierre_display,
)


def _line_products(session, opp: Opportunity) -> list[str]:
    tender = session.query(Tender).filter_by(opportunity_id=opp.id).one_or_none()
    if not tender:
        return []
    return [it.product for it in tender.items if (it.product or "").strip()]


def apply_timing_and_archive(opp: Opportunity, cierre_raw: str) -> str:
    """Set timing_state; auto-archive VENCIDA. Returns timing state."""
    info = classify_timing(cierre_raw)
    opp.timing_state = info.state
    opp.cierre_at = format_cierre_display(info.cierre, cierre_raw)
    if info.state == TIMING_VENCIDA:
        opp.archived = True
        opp.state = "ARCHIVADA"
        opp.skipped = True
        opp.skip_reason = "VENCIDA"
        if opp.approval_status == "PENDIENTE":
            opp.approval_status = "BLOQUEADO"
    elif info.state == TIMING_FECHA_NO_VERIFICADA:
        # Do not recommend bidding; keep out of actionable digest
        if not opp.archived:
            opp.skip_reason = (
                opp.skip_reason or "FECHA_NO_VERIFICADA"
            )
    return info.state


def archive_expired(session) -> dict:
    """Reclassify all opps; archive those with cierre already past (BA tz)."""
    archived = 0
    unchecked = 0
    open_n = 0
    rows = session.query(Opportunity).all()
    for opp in rows:
        cierre = opp.cierre_at or opp.opening_at
        prev = opp.archived
        state = apply_timing_and_archive(opp, cierre)
        if state == TIMING_VENCIDA and not prev:
            archived += 1
        elif state == TIMING_VENCIDA:
            archived += 0  # already
            if opp.archived:
                pass
        elif state == TIMING_FECHA_NO_VERIFICADA:
            unchecked += 1
        else:
            # reopen only if previously archived solely for expiry and now open
            if opp.archived and opp.skip_reason == "VENCIDA":
                opp.archived = False
                opp.skipped = False
                opp.skip_reason = ""
                if opp.state == "ARCHIVADA":
                    opp.state = "RADAR"
                if opp.approval_status == "BLOQUEADO":
                    opp.approval_status = "PENDIENTE"
            open_n += 1
    # recount archived after pass
    total_archived = (
        session.query(Opportunity).filter(Opportunity.archived.is_(True)).count()
    )
    session.commit()
    return {
        "scanned": len(rows),
        "newly_logic": archived,
        "total_archived": total_archived,
        "fecha_no_verificada": unchecked,
        "open_or_cierra_hoy": open_n,
    }


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
        archived_now = 0

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

            title = (raw.get("titulo") or raw.get("title") or "").strip()
            rubros = raw.get("rubros") or ""
            organism = raw.get("organismo") or raw.get("organism") or ""
            modality = raw.get("modalidad") or raw.get("modality") or ""
            apertura = raw.get("apertura") or ""
            cierre_raw = (
                raw.get("cierre")
                or raw.get("cierre_formal")
                or apertura
                or ""
            )
            publicacion = raw.get("publicacion") or ""
            blob = text_blob(title, rubros, organism, modality)

            existing = (
                self.session.query(Opportunity)
                .filter_by(external_id=ext_id)
                .one_or_none()
            )
            line_items = _line_products(self.session, existing) if existing else []
            score, reason = fit_score(
                title, rubros, organism, modality, line_items=line_items or None
            )
            skip = False
            skip_reason = ""
            if is_avoided(blob):
                skip = True
                skip_reason = "categoría_evitada"
                score = min(score, 10)

            pliego_url = raw.get("pliego_url") or raw.get("url") or ""

            if existing:
                existing.title = title
                existing.organism = organism
                existing.rubros = rubros
                existing.modality = modality
                existing.opening_at = apertura or existing.opening_at
                existing.publicacion_at = publicacion or existing.publicacion_at
                existing.url = raw.get("url") or existing.url
                existing.pliego_url = pliego_url or existing.pliego_url
                existing.fit_score = score
                existing.raw_json = json.dumps(raw, ensure_ascii=False)
                if not existing.archived:
                    existing.skipped = skip
                    existing.skip_reason = skip_reason
                tstate = apply_timing_and_archive(existing, cierre_raw)
                if tstate == TIMING_VENCIDA:
                    archived_now += 1
                if existing.state == "RADAR" or not existing.state:
                    if not existing.archived:
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
                    opening_at=apertura,
                    publicacion_at=publicacion,
                    url=raw.get("url") or "",
                    pliego_url=pliego_url,
                    fit_score=score,
                    state="RADAR",
                    skipped=skip,
                    skip_reason=skip_reason,
                    raw_json=json.dumps(raw, ensure_ascii=False),
                )
                self.session.add(opp)
                self.session.flush()
                tstate = apply_timing_and_archive(opp, cierre_raw)
                if tstate == TIMING_VENCIDA:
                    archived_now += 1
                created += 1

        # Also re-scan DB for any stale rows not in this feed
        extra = archive_expired(self.session)

        summary = (
            f"source={source_note}; created={created}; updated={updated}; "
            f"skipped_hard={skipped}; archived_pass={archived_now}; "
            f"login_env={'OK' if creds else 'REQUIERE CREDENCIALES'}; "
            f"total_seen={len(items)}; db_archived={extra.get('total_archived')}"
        )
        self.finish_run(run, summary)
        self.session.commit()
        return {
            "created": created,
            "updated": updated,
            "skipped_hard": skipped,
            "archived_in_feed": archived_now,
            "archive_scan": extra,
            "source": source_note,
            "login": "OK" if creds else "REQUIERE CREDENCIALES",
            "total": len(items),
            "use_fixtures": settings.use_fixtures,
        }
