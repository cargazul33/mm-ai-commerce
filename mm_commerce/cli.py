"""CLI — python -m mm_commerce run|radar|digest|init-db."""
from __future__ import annotations

import json
import sys

import click

from mm_commerce.config import get_settings
from mm_commerce.models import init_db, get_session
from mm_commerce.agents.commander import Commander
from mm_commerce.agents.radar import RadarAgent
from mm_commerce.telegram_bot import format_digest


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


@main.command("run")
@click.option("--once", is_flag=True, default=True)
@click.option("--limit", default=10, show_default=True)
@click.option("--min-fit", default=None, type=int)
def run_cmd(once: bool, limit: int, min_fit: int | None) -> None:
    init_db()
    session = get_session()
    try:
        result = Commander(session).run_once(limit=limit, min_fit=min_fit)
        # compact print
        out = {
            "radar": result["radar"],
            "processed": result["processed"],
            "telegram": result["telegram"],
            "digest": result["digest"],
        }
        click.echo(json.dumps(out, ensure_ascii=False, indent=2, default=str))
    finally:
        session.close()


@main.command("digest")
def digest_cmd() -> None:
    init_db()
    session = get_session()
    try:
        from mm_commerce.models import Opportunity
        from mm_commerce.config import get_settings

        s = get_settings()
        rows = (
            session.query(Opportunity)
            .filter(Opportunity.skipped.is_(False))
            .filter(Opportunity.fit_score >= s.fit_score_alert_min)
            .order_by(Opportunity.fit_score.desc())
            .limit(20)
            .all()
        )
        digest = [
            {
                "id": o.external_id,
                "title": o.title[:120],
                "fit_score": o.fit_score,
                "risk": o.risk_level,
                "state": o.state,
                "approval": o.approval_status,
                "organism": o.organism,
            }
            for o in rows
        ]
        click.echo(format_digest(digest))
    finally:
        session.close()


if __name__ == "__main__":
    main()
