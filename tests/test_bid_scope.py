"""BID_SCOPE parser + item-level approval logic."""
from __future__ import annotations

from pathlib import Path

import pytest

from mm_commerce.extractors.bid_scope import (
    STATE_ITEM,
    STATE_TOTAL,
    STATE_UNKNOWN,
    parse_bid_scope,
)
from mm_commerce.models import (
    Opportunity,
    Offer,
    Supplier,
    SupplierQuote,
    Tender,
    TenderItem,
    get_session,
    init_db,
)
from mm_commerce.agents.verifier import VerifierAgent
from mm_commerce.config import get_settings
import json

ROOT = Path(__file__).resolve().parents[1]
PLIEGO = ROOT / "data" / "pliegos" / "16813" / "pliego.txt"


def test_16813_bid_scope_total_required():
    text = PLIEGO.read_text(encoding="utf-8")
    scope = parse_bid_scope(text, line_count=7)
    assert scope.cotizar_por_renglon is True
    assert scope.lines_to_quote == 7
    assert scope.cotizar_parcialmente is False
    assert scope.oferta_total_obligatoria is True
    assert scope.state == STATE_TOTAL
    assert scope.blocks_presentation is False
    assert scope.citations
    d = scope.to_dict()
    assert d["modalidad"] == "TOTAL"
    assert d["oferta_total_obligatoria_txt"] == "SÍ"
    assert d["cotizar_parcialmente_txt"] == "NO"


def test_unknown_blocks_presentation():
    scope = parse_bid_scope("texto sin cláusulas de cotización", line_count=3)
    assert scope.state == STATE_UNKNOWN
    assert scope.blocks_presentation is True


def test_item_level_when_adjudicacion_por_renglon():
    text = (
        "Se aceptarán ofertas parciales. La adjudicación se efectuará por renglón. "
        "Se deberá consignar precio unitario de cada renglón."
    )
    scope = parse_bid_scope(text, line_count=7)
    assert scope.state == STATE_ITEM
    assert scope.cotizar_parcialmente is True
    assert scope.adjudicacion_por_renglon is True


@pytest.fixture()
def session(tmp_path, monkeypatch):
    db = tmp_path / "bid.db"
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


def _seed_two_lines(session, *, scope_state: str):
    opp = Opportunity(
        external_id="16813t",
        title="test",
        fit_score=90,
        state="PRICING",
        risk_level="MEDIO",
        cierre_at="2026-09-21T10:00:00-03:00",
        timing_state="ABIERTA",
    )
    session.add(opp)
    session.flush()
    t = Tender(opportunity_id=opp.id, process_id="16813t")
    if scope_state == STATE_ITEM:
        t.bid_scope_json = json.dumps(
            {
                "state": STATE_ITEM,
                "modalidad": "POR RENGLÓN",
                "blocks_presentation": False,
            }
        )
    elif scope_state == STATE_TOTAL:
        t.bid_scope_json = json.dumps(
            {
                "state": STATE_TOTAL,
                "modalidad": "TOTAL",
                "blocks_presentation": False,
                "lines_to_quote": 2,
            }
        )
    else:
        t.bid_scope_json = json.dumps(
            {"state": STATE_UNKNOWN, "modalidad": "NO VERIFICADA", "blocks_presentation": True}
        )
    session.add(t)
    session.flush()
    i1 = TenderItem(tender_id=t.id, line_no=1, product="OLT", qty=1, specs="OLT GPON")
    i2 = TenderItem(tender_id=t.id, line_no=2, product="UPS", qty=1, specs="UPS 3000VA")
    session.add_all([i1, i2])
    session.flush()
    sup = Supplier(name="TestSup", source="test", url="https://example.com")
    session.add(sup)
    session.flush()
    # R1 EXACTO + DISPONIBLE
    session.add(
        SupplierQuote(
            supplier_id=sup.id,
            opportunity_id=opp.id,
            tender_item_id=i1.id,
            product_label="OLT OK",
            unit_cost=1000,
            qty=1,
            match_pct=100,
            match_class="EXACTO",
            technical_status="EXACTO",
            commercial_status="DISPONIBLE",
            stock_note="1",
            shipping_neuquen="OK",
            evidence_json=json.dumps(
                {
                    "evidence": [
                        {
                            "key": "product_type",
                            "label": "PRODUCT_TYPE",
                            "required": "OLT",
                            "found": "OLT",
                            "source_url": "",
                            "result": "CUMPLE",
                            "mandatory": True,
                        }
                    ],
                    "hard_requirements": [{"key": "product_type", "mandatory": True}],
                    "evidence_ok": 1,
                    "evidence_total": 1,
                }
            ),
            verification="PROBABLE",
        )
    )
    # R2 NO_CUMPLE
    session.add(
        SupplierQuote(
            supplier_id=sup.id,
            opportunity_id=opp.id,
            tender_item_id=i2.id,
            product_label="UPS bad",
            unit_cost=500,
            qty=1,
            match_pct=15,
            match_class="NO_CUMPLE",
            technical_status="NO_CUMPLE",
            commercial_status="DISPONIBLE",
            stock_note="1",
            shipping_neuquen="OK",
            evidence_json=json.dumps(
                {
                    "evidence": [
                        {
                            "key": "product_type",
                            "label": "PRODUCT_TYPE",
                            "required": "UPS",
                            "found": "UPS",
                            "source_url": "",
                            "result": "CUMPLE",
                            "mandatory": True,
                        },
                        {
                            "key": "capacity_va",
                            "label": "potencia_VA",
                            "required": "3000",
                            "found": "2500",
                            "source_url": "",
                            "result": "NO_CUMPLE",
                            "mandatory": True,
                        },
                    ],
                    "hard_requirements": [
                        {"key": "product_type", "mandatory": True},
                        {"key": "capacity_va", "mandatory": True},
                    ],
                    "evidence_ok": 1,
                    "evidence_total": 2,
                    "blockers": ["LOWER_CAPACITY"],
                }
            ),
            verification="NO CUMPLE",
        )
    )
    session.add(
        Offer(
            opportunity_id=opp.id,
            logistics_status="OK",
            cost_total=1000,
            precio_objetivo=1900,
            margin_multiplier=1.9,
            total_cost=1000,
            economic_json=json.dumps({"apto_para_cotizar": True}),
            status="BORRADOR",
        )
    )
    session.commit()
    return opp


def test_item_level_r1_apto_even_if_r2_no_cumple(session):
    opp = _seed_two_lines(session, scope_state=STATE_ITEM)
    # logistics OK already
    res = VerifierAgent(session).process(opp)
    assert res["bid_scope"] == STATE_ITEM
    assert res["line_apto"].get("1") is True
    assert res["line_apto"].get("2") is False
    # Under item-level, global can be OK if at least one line apto and logistics ok
    assert res["status"] == "OK"
    assert res["apto_para_cotizar"] is True


def test_total_required_blocks_when_r2_fails(session):
    opp = _seed_two_lines(session, scope_state=STATE_TOTAL)
    res = VerifierAgent(session).process(opp)
    assert res["bid_scope"] == STATE_TOTAL
    assert res["status"] == "BLOQUEADO"
    assert any("NO_CUMPLE_R2" in b or "COBERTURA" in b for b in res["blockers"])
