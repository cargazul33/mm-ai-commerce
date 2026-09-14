"""CLI digest — Telegram-style cards; only open timing."""
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from mm_commerce.telegram_bot import format_card, format_digest
from mm_commerce.models import init_db, get_session, Opportunity
from mm_commerce.config import get_settings
from mm_commerce.digest import build_actionable_digest
from click.testing import CliRunner
from mm_commerce.cli import main

BA = ZoneInfo("America/Argentina/Buenos_Aires")


def test_format_card_structure():
    card = format_card(
        {
            "id": "16813",
            "title": "Equipamiento informático",
            "fit_score": 85,
            "risk": "MEDIO",
            "organism": "MINISTERIO DE TRABAJO",
            "approval": "PENDIENTE",
            "state": "TELEGRAM",
            "publicacion": "NO VERIFICADA",
            "cierre": "21/09/2026 10:00 ART",
            "remaining": "7d restantes",
            "timing_state": "ABIERTA",
            "url": "https://codi.neuquen.gob.ar/x",
            "pliego_url": "https://codi.neuquen.gob.ar/pliego",
            "line_count": 3,
        }
    )
    assert "#16813" in card
    assert "FIT 85" in card
    assert "RISK" in card
    assert "publicación" in card
    assert "cierre" in card
    assert "restantes" in card
    assert "fuente" in card
    assert "pliego" in card
    assert "renglones" in card
    assert "APROBAR" in card
    assert "┌─" in card


def test_format_digest_empty():
    text = format_digest([])
    assert "digest vacío" in text.lower() or "sin oportunidades" in text.lower()


def test_digest_excludes_vencidas(tmp_path, monkeypatch):
    db = tmp_path / "d.db"
    url = f"sqlite:///{db}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("FIT_SCORE_ALERT_MIN", "50")
    get_settings.cache_clear()
    import mm_commerce.models as m

    m._engine = None
    m._SessionLocal = None
    init_db(url)
    session = get_session(url)
    future = (datetime.now(BA) + timedelta(days=5)).strftime("%d/%m/%y %H:%M ART")
    past = "07/09/26 09:00 ART"
    session.add(
        Opportunity(
            external_id="OPEN1",
            title="Adquisición de equipamientos informáticos",
            organism="MTDL",
            fit_score=90,
            risk_level="BAJO",
            state="TELEGRAM",
            skipped=False,
            archived=False,
            opening_at=future,
            cierre_at=future,
            url="https://example.com/o",
            pliego_url="https://example.com/p",
        )
    )
    session.add(
        Opportunity(
            external_id="DEAD1",
            title="COMPRA DE MATERIAL DIDACTICO",
            fit_score=99,
            skipped=False,
            archived=False,
            opening_at=past,
            cierre_at=past,
        )
    )
    session.commit()
    digest = build_actionable_digest(session, min_fit=50, limit=10)
    ids = [d["id"] for d in digest]
    assert "OPEN1" in ids
    assert "DEAD1" not in ids
    session.close()
    get_settings.cache_clear()


def test_digest_cli(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    db = tmp_path / "d.db"
    url = f"sqlite:///{db}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("USE_FIXTURES", "1")
    monkeypatch.setenv(
        "CODINEU_FIXTURE", str(root / "fixtures" / "codineu_sample.json")
    )
    monkeypatch.setenv("FIT_SCORE_ALERT_MIN", "50")
    get_settings.cache_clear()
    import mm_commerce.models as m

    m._engine = None
    m._SessionLocal = None
    init_db(url)
    session = get_session(url)
    future = (datetime.now(BA) + timedelta(days=3)).strftime("%d/%m/%y %H:%M ART")
    session.add(
        Opportunity(
            external_id="16589",
            title="Adquisición de equipamientos informáticos",
            organism="MTDL",
            fit_score=90,
            risk_level="BAJO",
            state="TELEGRAM",
            skipped=False,
            archived=False,
            opening_at=future,
            cierre_at=future,
            url="https://example.com/fuente",
            pliego_url="https://example.com/pliego",
            line_count=2,
        )
    )
    session.add(
        Opportunity(
            external_id="16514",
            title="should not appear if skipped",
            fit_score=99,
            skipped=True,
            skip_reason="HARD_SKIP",
        )
    )
    session.commit()
    session.close()

    runner = CliRunner()
    result = runner.invoke(main, ["digest", "--limit", "5", "--min-fit", "50"])
    assert result.exit_code == 0, result.output
    assert "#16589" in result.output
    assert "16514" not in result.output
    assert "cierre" in result.output
    assert "APROBAR" in result.output
    get_settings.cache_clear()
