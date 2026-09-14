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
from mm_commerce.matching import COMM_DISPONIBLE, TECH_EXACTO, TECH_NO_CUMPLE


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
    t = Tender(opportunity_id=opp.id, process_id="99901")
    session.add(t)
    session.flush()
    it = TenderItem(tender_id=t.id, line_no=1, product="Notebook", qty=2)
    session.add(it)
    session.flush()
    sup = Supplier(name="S1")
    session.add(sup)
    session.flush()
    session.add(
        SupplierQuote(
            supplier_id=sup.id,
            opportunity_id=opp.id,
            tender_item_id=it.id,
            product_label="Notebook",
            unit_cost=1000.0,
            qty=2,
            match_score=100,
            match_pct=100,
            match_class=TECH_EXACTO,
            technical_status=TECH_EXACTO,
            commercial_status=COMM_DISPONIBLE,
            verification="PROBABLE",
            url="https://example.local/x",
            stock_note="InStock",
            shipping_neuquen="envío nacional mencionado — cotizar a Neuquén",
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


def test_no_full_offer_when_no_cumple(session):
    opp = Opportunity(external_id="99903", title="ups", fit_score=80, state="SOURCING")
    session.add(opp)
    session.flush()
    t = Tender(opportunity_id=opp.id, process_id="99903")
    session.add(t)
    session.flush()
    it = TenderItem(tender_id=t.id, line_no=1, product="UPS 3000VA", qty=1)
    session.add(it)
    session.flush()
    sup = Supplier(name="S2")
    session.add(sup)
    session.flush()
    session.add(
        SupplierQuote(
            supplier_id=sup.id,
            opportunity_id=opp.id,
            tender_item_id=it.id,
            product_label="UPS 2500VA",
            unit_cost=1000.0,
            qty=1,
            match_score=15,
            match_pct=15,
            match_class=TECH_NO_CUMPLE,
            technical_status=TECH_NO_CUMPLE,
            commercial_status="PRECIO_NO_VERIFICADO",
            verification="NO CUMPLE",
            url="https://example.local/bad",
        )
    )
    session.commit()
    offer = PricingAgent(session).process(opp)
    assert offer.cost_total is None
    assert offer.precio_objetivo is None
    assert offer.status == "BLOQUEADO_MATCHING"
