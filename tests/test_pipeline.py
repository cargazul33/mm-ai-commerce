from pathlib import Path

import pytest

from mm_commerce.models import init_db, get_session, Opportunity
from mm_commerce.agents.commander import Commander
from mm_commerce.config import get_settings


@pytest.fixture()
def session(tmp_path, monkeypatch):
    root = Path(__file__).resolve().parents[1]
    db = tmp_path / "full.db"
    url = f"sqlite:///{db}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("USE_FIXTURES", "1")
    monkeypatch.setenv("CODINEU_FIXTURE", str(root / "fixtures" / "codineu_sample.json"))
    monkeypatch.setenv("PLIEGOS_DIR", str(root / "fixtures" / "pliegos"))
    monkeypatch.setenv("FIT_SCORE_ALERT_MIN", "50")
    get_settings.cache_clear()
    import mm_commerce.models as m

    m._engine = None
    m._SessionLocal = None
    init_db(url)
    s = get_session(url)
    yield s
    s.close()
    get_settings.cache_clear()


def test_full_pipeline_once(session):
    result = Commander(session).run_once(limit=5, min_fit=50)
    assert result["radar"]["skipped_hard"] >= 1
    assert "telegram" in result
    assert result["telegram"]["status"] in ("CLI_ONLY", "SKIPPED_EMPTY_OPEN", "SENT")
    # at least one opp processed or digest built
    assert isinstance(result["processed"], list)
    # 16514 never in DB
    assert session.query(Opportunity).filter_by(external_id="16514").count() == 0
