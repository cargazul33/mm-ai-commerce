import pytest
from pathlib import Path

from mm_commerce.models import init_db, get_session, Opportunity, Offer
from mm_commerce.agents.verifier import VerifierAgent
from mm_commerce.agents.bid import BidAgent
from mm_commerce.config import get_settings


@pytest.fixture()
def session(tmp_path, monkeypatch):
    db = tmp_path / "v.db"
    url = f"sqlite:///{db}"
    monkeypatch.setenv("DATABASE_URL", url)
    get_settings.cache_clear()
    import mm_commerce.models as m

    m._engine = None
    m._SessionLocal = None
    init_db(url)
    s = get_session(url)
    yield s
    s.close()
    get_settings.cache_clear()


def test_verifier_blocks_inconsistent_price(session):
    opp = Opportunity(
        external_id="888",
        title="x",
        fit_score=80,
        state="RISK",
        risk_level="BAJO",
    )
    session.add(opp)
    session.flush()
    session.add(
        Offer(
            opportunity_id=opp.id,
            cost_total=1000,
            precio_objetivo=9999,  # inconsistent
            margin_multiplier=1.9,
            tax_status="PENDING",
            logistics_status="PENDING",
        )
    )
    # need tender to avoid SIN_PLIEGO — add minimal
    from mm_commerce.models import Tender, TenderItem

    t = Tender(opportunity_id=opp.id, process_id="888")
    session.add(t)
    session.flush()
    session.add(TenderItem(tender_id=t.id, product="item", qty=1))
    session.commit()
    res = VerifierAgent(session).process(opp)
    assert res["status"] == "BLOQUEADO"
    assert "PRECIO_INCONSISTENTE" in res["blockers"]
    assert opp.approval_status == "BLOQUEADO"


def test_bid_never_autosubmit(session, tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{tmp_path/'b.db'}")
    # reuse session DB already
    opp = Opportunity(
        external_id="777",
        title="Notebooks",
        fit_score=90,
        state="VERIFIER",
        approval_status="PENDIENTE",
        risk_level="MEDIO",
    )
    session.add(opp)
    session.flush()
    session.add(
        Offer(
            opportunity_id=opp.id,
            cost_total=100,
            precio_objetivo=190,
            margin_multiplier=1.9,
        )
    )
    session.commit()
    res = BidAgent(session).process(opp)
    assert res["auto_submit"] is False
    assert res["status"] == "OK"
    assert Path(res["path"]).exists()
