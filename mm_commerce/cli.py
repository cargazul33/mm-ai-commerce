"""CLI — python -m mm_commerce run|radar|digest|archive|telegram-poll|init-db."""
from __future__ import annotations

import json
import sys

import click

from mm_commerce.config import get_settings
from mm_commerce.models import init_db, get_session
from mm_commerce.agents.commander import Commander
from mm_commerce.agents.radar import RadarAgent, archive_expired
from mm_commerce.digest import build_actionable_digest
from mm_commerce.telegram_bot import (
    format_digest,
    notify_high_fit,
    poll_callbacks_once,
    simulate_callback,
)


@click.group()
@click.version_option(package_name="mm-commerce")
def main() -> None:
    """M&M AI Commerce — pipeline B2B Radar→Telegram."""
    pass


@main.command("init-db")
def init_db_cmd() -> None:
    init_db()
    click.echo(f"DB lista: {get_settings().database_url}")


@main.command("radar")
@click.option("--once", is_flag=True, default=True, help="Una pasada")
def radar_cmd(once: bool) -> None:
    init_db()
    session = get_session()
    try:
        stats = RadarAgent(session).run_once()
        click.echo(json.dumps(stats, ensure_ascii=False, indent=2))
    finally:
        session.close()


@main.command("archive-expired")
def archive_cmd() -> None:
    """Archiva todas las VENCIDAS (cierre < now BA)."""
    init_db()
    session = get_session()
    try:
        stats = archive_expired(session)
        click.echo(json.dumps(stats, ensure_ascii=False, indent=2))
    finally:
        session.close()


@main.command("run")
@click.option("--once", is_flag=True, default=True)
@click.option("--limit", default=10, show_default=True)
@click.option("--min-fit", default=None, type=int)
@click.option("--send/--no-send", default=True, help="Enviar digest a Telegram")
def run_cmd(once: bool, limit: int, min_fit: int | None, send: bool) -> None:
    init_db()
    session = get_session()
    try:
        result = Commander(session).run_once(limit=limit, min_fit=min_fit)
        if not send:
            result["telegram"] = {"status": "SEND_DISABLED"}
        out = {
            "radar": result["radar"],
            "archive": result.get("archive"),
            "processed": result["processed"],
            "telegram": result["telegram"],
            "digest": result["digest"],
        }
        click.echo(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    finally:
        session.close()


@main.command("digest")
@click.option("--limit", default=10, show_default=True, help="Top N oportunidades")
@click.option("--min-fit", default=None, type=int, help="Umbral FIT (default settings)")
@click.option("--json-out", "json_out", is_flag=True, help="Salida JSON cruda")
@click.option("--send", is_flag=True, help="Enviar a Telegram (solo ABIERTAS)")
def digest_cmd(limit: int, min_fit: int | None, json_out: bool, send: bool) -> None:
    """Telegram-style cards — solo ABIERTA/CIERRA_HOY."""
    init_db()
    session = get_session()
    try:
        digest = build_actionable_digest(session, min_fit=min_fit, limit=limit)
        if send:
            tg = notify_high_fit(session, digest)
            click.echo(json.dumps({"telegram": tg, "digest": digest}, ensure_ascii=False, indent=2, default=str))
        elif json_out:
            click.echo(json.dumps(digest, ensure_ascii=False, indent=2))
        else:
            click.echo(format_digest(digest))
    finally:
        session.close()


@main.command("telegram-poll")
@click.option("--timeout", default=5, show_default=True, help="Segundos getUpdates")
def telegram_poll_cmd(timeout: int) -> None:
    """Procesa callbacks APROBAR/RECHAZAR/VER (allowlist)."""
    init_db()
    session = get_session()
    try:
        result = poll_callbacks_once(session, timeout_sec=timeout)
        click.echo(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    finally:
        session.close()


@main.command("telegram-simulate")
@click.argument("external_id")
@click.option("--action", type=click.Choice(["approve", "reject", "view"]), default="approve")
def telegram_simulate_cmd(external_id: str, action: str) -> None:
    """Simula callback allowlist → actualiza DB."""
    init_db()
    session = get_session()
    try:
        result = simulate_callback(session, external_id=external_id, action=action)
        click.echo(json.dumps(result, ensure_ascii=False, indent=2))
    finally:
        session.close()


@main.command("circuit")
@click.option("--id", "external_id", required=True, help="CODINEU process id ABIERTA")
@click.option("--send/--no-send", default=True)
@click.option("--min-fit", default=0, show_default=True)
def circuit_cmd(external_id: str, send: bool, min_fit: int) -> None:
    """Primer circuito económico real sobre UNA oportunidad ABIERTA."""
    from mm_commerce.agents.bid import BidAgent
    from mm_commerce.agents.pricing import PricingAgent
    from mm_commerce.agents.pliego import PliegoAgent
    from mm_commerce.agents.radar import RadarAgent, archive_expired
    from mm_commerce.agents.risk import RiskAgent
    from mm_commerce.agents.sourcing import SourcingAgent
    from mm_commerce.agents.verifier import VerifierAgent
    from mm_commerce.digest import opportunity_to_card
    from mm_commerce.models import Opportunity
    from mm_commerce.scoring import fit_score
    from mm_commerce.telegram_bot import (
        format_card,
        notify_high_fit,
        simulate_callback,
        build_full_detail,
    )
    from mm_commerce.timing import ACTIONABLE_TIMING, classify_timing

    init_db()
    session = get_session()
    try:
        archive_expired(session)
        RadarAgent(session).run_once()
        opp = (
            session.query(Opportunity)
            .filter_by(external_id=str(external_id))
            .one_or_none()
        )
        if not opp:
            click.echo(json.dumps({"error": "OPP_NOT_FOUND", "id": external_id}))
            return
        timing = classify_timing(opp.cierre_at or opp.opening_at)
        opp.timing_state = timing.state
        if timing.state not in ACTIONABLE_TIMING:
            click.echo(
                json.dumps(
                    {"error": "NOT_ACTIONABLE", "timing": timing.state, "id": external_id},
                    ensure_ascii=False,
                )
            )
            return

        PliegoAgent(session).process(opp)
        session.refresh(opp)
        lines = []
        if opp.tender:
            lines = [f"{it.product} {it.specs}" for it in opp.tender.items]
            opp.line_count = len(lines)
        score, reason = fit_score(
            opp.title, opp.rubros, opp.organism, opp.modality, line_items=lines or None
        )
        opp.fit_score = score
        session.commit()

        SourcingAgent(session).process(opp)
        PricingAgent(session).process(opp)
        risk = RiskAgent(session).process(opp)
        ver = VerifierAgent(session).process(opp)
        bid = BidAgent(session).process(opp)
        session.refresh(opp)
        if opp.approval_status != "BLOQUEADO":
            opp.state = "TELEGRAM"
        session.commit()

        card = opportunity_to_card(session, opp)
        detail = build_full_detail(session, opp)
        tg = {"status": "SEND_DISABLED"}
        if send:
            tg = notify_high_fit(session, [card])
            sim_view = simulate_callback(session, external_id=opp.external_id, action="view")
            # NEVER auto-approve while matching/verifier blocked
            if opp.approval_status == "BLOQUEADO" or (ver or {}).get("status") == "BLOQUEADO":
                sim_approve = {
                    "ok": False,
                    "error": "SKIP_APPROVE_WHILE_BLOCKED",
                    "status": opp.approval_status,
                }
            else:
                sim_approve = simulate_callback(
                    session, external_id=opp.external_id, action="approve"
                )
            session.refresh(opp)
            persisted = opp.approval_status
            from mm_commerce.telegram_bot import poll_callbacks_once

            poll = poll_callbacks_once(session, timeout_sec=2)
        else:
            sim_view = sim_approve = poll = None
            persisted = opp.approval_status

        out = {
            "id": opp.external_id,
            "title": opp.title,
            "timing": opp.timing_state,
            "cierre": opp.cierre_at or opp.opening_at,
            "fit": opp.fit_score,
            "fit_reason": reason,
            "line_count": opp.line_count,
            "risk": risk,
            "verifier": ver,
            "bid": bid,
            "telegram": tg,
            "approval_persisted": persisted,
            "simulate_view_ok": bool((sim_view or {}).get("ok")),
            "simulate_approve": sim_approve,
            "poll": poll,
            "card": card,
            "detail_preview": (detail or "")[:1200],
        }
        # attach economic from latest offer
        if opp.offers:
            off = max(opp.offers, key=lambda x: x.id)
            out["economic"] = {
                "merchandise": off.merchandise_cost,
                "logistics": off.logistics_cost,
                "logistics_status": off.logistics_status,
                "total_cost": off.total_cost,
                "precio_objetivo": off.precio_objetivo,
                "utilidad": off.utilidad,
                "margen_pct": off.margen_pct,
                "capital": off.capital_requerido,
            }
        click.echo(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    finally:
        session.close()


if __name__ == "__main__":
    main()
