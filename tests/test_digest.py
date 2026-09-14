"""CLI digest — Telegram-style cards."""
from pathlib import Path

from mm_commerce.telegram_bot import format_card, format_digest
from mm_commerce.models import init_db, get_session, Opportunity
from mm_commerce.config import get_settings
from click.testing import CliRunner
from mm_commerce.cli import main


def test_format_card_structure():
    card = format_card(
        {
            "id": "16589",
            "title": "Equipamiento informático",
            "fit_score": 85,
            "risk": "MEDIO",
            "organism": "MINISTERIO DE TRABAJO",
            "approval": "PENDIENTE",
            "state": "TELEGRAM",
            "apertura": "07/09/26 10:00 ART",
        }
    )
    assert "#16589" in card
    assert "FIT 85" in card
    assert "APROBAR" in card
    assert "┌─" in card


def test_format_digest_empty():
    text = format_digest([])
    assert "digest vacío" in text.lower() or "sin oportunidades" in text.lower()


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
    session.add(
        Opportunity(
            external_id="16589",
            title="Adquisición de equipamientos informáticos",
            organism="MTDL",
            fit_score=90,
            risk_level="BAJO",
            state="TELEGRAM",
            skipped=False,
            opening_at="07/09/26 10:00 ART",
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
    assert "APROBAR" in result.output
    get_settings.cache_clear()
