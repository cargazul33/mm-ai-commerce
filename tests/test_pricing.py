from pathlib import Path

import pytest

from mm_commerce.models import (
    init_db,
    get_session,
    Opportunity,
    Tender,
    TenderItem,
    Supplier,
    SupplierQuote,
)
from mm_commerce.agents.pricing import PricingAgent
from mm_commerce.config import get_settings


@pytest.fixture()
def session(tmp_path, monkeypatch):
    db = tmp_path / "p.db"
    url = f"sqlite:///{db}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("MARGIN_MULTIPLIER", "1.90")
    get_settings.cache_clear()
    import mm_commerce.models as m

    m._engine = None
    m._SessionLocal = None
    init_db(url)
    s = get_session(url)
    yield s
    s.close()
    get_settings.cache_clear()


def test_precio_objetivo_multiplier(session):
    opp = Opportunity(external_id="99901", title="test", fit_score=80, state="SOURCING")
    session.add(opp)
    session.flush()
    sup = Supplier(name="S1")
    session.add(sup)
    session.flush()
    session.add(
        SupplierQuote(
            supplier_id=sup.id,
            opportunity_id=opp.id,
            product_label="Notebook",
            unit_cost=1000.0,
            qty=2,
            match_score=90,
            verification="PROBABLE",
            url="https://example.local/x",
        )
    )
    session.commit()
    offer = PricingAgent(session).process(opp)
    assert offer.cost_total == 2000.0
    assert offer.precio_objetivo == 3800.0
    assert offer.tax_status == "PENDING"
    assert offer.logistics_status == "PENDING"


def test_no_invent_without_cost(session):
    opp = Opportunity(external_id="99902", title="test2", fit_score=70, state="SOURCING")
    session.add(opp)
    session.commit()
    offer = PricingAgent(session).process(opp)
    assert offer.cost_total is None
    assert offer.precio_objetivo is None
