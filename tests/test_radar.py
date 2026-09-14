import os
from pathlib import Path

import pytest

from mm_commerce.models import init_db, get_session, Opportunity, Base, get_engine
from mm_commerce.agents.radar import RadarAgent
from mm_commerce.config import get_settings


@pytest.fixture()
def session(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    url = f"sqlite:///{db}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv(
        "CODINEU_FIXTURE",
        str(Path(__file__).resolve().parents[1] / "fixtures" / "codineu_sample.json"),
    )
    monkeypatch.setenv("USE_FIXTURES", "1")
    get_settings.cache_clear()
    # reset engine
    import mm_commerce.models as m

    m._engine = None
    m._SessionLocal = None
    init_db(url)
    s = get_session(url)
    yield s
    s.close()
    get_settings.cache_clear()


def test_radar_skips_16514(session):
    stats = RadarAgent(session).run_once()
    assert stats["skipped_hard"] >= 1
    row = session.query(Opportunity).filter_by(external_id="16514").one_or_none()
    assert row is None  # never persisted
    assert stats["created"] + stats["updated"] >= 1
