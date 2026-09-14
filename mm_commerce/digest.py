"""Digest accionable — solo ABIERTA/CIERRA_HOY; nunca VENCIDA ni FECHA_NO_VERIFICADA."""
from __future__ import annotations

from sqlalchemy.orm import Session

from mm_commerce.config import get_settings
from mm_commerce.models import Offer, Opportunity, Tender
from mm_commerce.timing import (
    ACTIONABLE_TIMING,
    TIMING_FECHA_NO_VERIFICADA,
    classify_timing,
    format_cierre_display,
)


def _utilidad(session: Session, opp: Opportunity) -> float | None:
    if opp.utilidad_estimada is not None:
        return opp.utilidad_estimada
    offer = (
        session.query(Offer)
        .filter_by(opportunity_id=opp.id)
        .order_by(Offer.id.desc())
        .first()
    )
    if (
        offer
        and offer.precio_objetivo is not None
        and offer.cost_total is not None
    ):
        u = float(offer.precio_objetivo) - float(offer.cost_total)
        opp.utilidad_estimada = u
        return u
    return None


def _line_count(session: Session, opp: Opportunity) -> int:
    if opp.line_count:
        return opp.line_count
    tender = session.query(Tender).filter_by(opportunity_id=opp.id).one_or_none()
    if tender:
        n = len(tender.items)
        opp.line_count = n
        return n
    return 0


def opportunity_to_card(session: Session, opp: Opportunity) -> dict:
    cierre_raw = opp.cierre_at or opp.opening_at
    timing = classify_timing(cierre_raw)
    utilidad = _utilidad(session, opp)
    lines = _line_count(session, opp)
    return {
        "id": opp.external_id,
        "db_id": opp.id,
        "title": (opp.title or "")[:160],
        "fit_score": opp.fit_score,
        "risk": opp.risk_level or "—",
        "state": opp.state,
        "approval": opp.approval_status,
        "organism": opp.organism,
        "apertura": opp.opening_at,
        "publicacion": opp.publicacion_at or "NO VERIFICADA",
        "cierre": format_cierre_display(timing.cierre, cierre_raw),
        "cierre_raw": cierre_raw,
        "timing_state": timing.state,
        "remaining": timing.remaining_label,
        "hours_left": timing.hours_left,
        "url": opp.url or "",
        "pliego_url": opp.pliego_url or opp.url or "",
        "line_count": lines,
        "utilidad": utilidad,
        "rubros": (opp.rubros or "")[:120],
    }


def build_actionable_digest(
    session: Session,
    *,
    min_fit: int | None = None,
    limit: int = 20,
) -> list[dict]:
    """
    Solo oportunidades ABIERTA | CIERRA_HOY, no archivadas, no skip hard,
    FECHA_NO_VERIFICADA excluida. Sort: utilidad desc, FIT desc, cierre asc.
    """
    settings = get_settings()
    threshold = settings.fit_score_alert_min if min_fit is None else min_fit

    rows = (
        session.query(Opportunity)
        .filter(Opportunity.archived.is_(False))
        .filter(Opportunity.skipped.is_(False))
        .filter(Opportunity.fit_score >= threshold)
        .filter(Opportunity.approval_status.in_(["PENDIENTE", ""]))
        .all()
    )

    cards: list[dict] = []
    for opp in rows:
        cierre_raw = opp.cierre_at or opp.opening_at
        timing = classify_timing(cierre_raw)
        opp.timing_state = timing.state
        if timing.state == TIMING_FECHA_NO_VERIFICADA:
            continue
        if timing.state not in ACTIONABLE_TIMING:
            continue
        cards.append(opportunity_to_card(session, opp))

    def sort_key(c: dict):
        util = c.get("utilidad")
        # known utilidad first (desc), unknown last
        util_key = (0, -(util or 0)) if util is not None else (1, 0)
        hours = c.get("hours_left")
        deadline_key = hours if hours is not None else 10**9
        return (util_key[0], util_key[1], -int(c.get("fit_score") or 0), deadline_key)

    cards.sort(key=sort_key)
    session.commit()
    return cards[:limit]
