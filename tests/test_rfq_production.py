"""Production RFQ — hard-req gate, real contact, no auto-send, reply ingest."""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from mm_commerce.contacts import discover_commercial_contact
from mm_commerce.models import (
    Opportunity,
    Supplier,
    SupplierQuote,
    Tender,
    TenderItem,
    get_session,
    init_db,
)
from mm_commerce.rfq import (
    RFQ_TEMPLATE,
    build_rfq_drafts_for_opportunity,
    build_rfq_message,
    handle_rfq_callback,
    ingest_supplier_reply,
    parse_supplier_reply,
    persist_rfq_drafts,
    prepare_email_draft,
    prepare_whatsapp_link,
    rfq_inline_keyboard,
    verify_hard_requirements_100,
)


BIOSEGUR_HOME = """
<html><title>Biosegur | Seguridad</title>
<a href="https://wa.me/5492235249544">WhatsApp</a>
<a href="tel:+542234750351">+54 223 4750351</a>
<a href="contactenos.php">Contacto</a>
info@biosegur.com.ar ventas@biosegur.com.ar
</html>
"""
BIOSEGUR_CONTACT = """
<html><title>Contacto Biosegur</title>
<a href="https://wa.me/5492235249544">WA</a>
ventas@biosegur.com.ar
</html>
"""


def _fetch_biosegur(url: str) -> str:
    u = url.lower()
    if "contacto" in u or "contactenos" in u:
        return BIOSEGUR_CONTACT
    if "biosegur" in u:
        return BIOSEGUR_HOME
    return ""


@pytest.fixture()
def session(tmp_path, monkeypatch):
    db = tmp_path / "t.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db}")
    # reset engine
    import mm_commerce.models as m
    import mm_commerce.config as c

    c.get_settings.cache_clear()
    m._engine = None
    m._SessionLocal = None
    init_db(f"sqlite:///{db}")
    # project_root offers_out under tmp
    settings = c.get_settings()
    monkeypatch.setattr(settings, "project_root", tmp_path)
    (tmp_path / "offers_out").mkdir(exist_ok=True)
    s = get_session(f"sqlite:///{db}")
    yield s
    s.close()


def _seed_exacto(session, *, hard_ok: bool = True, stock="NO VERIFICADO", qty=2.0):
    opp = Opportunity(
        external_id="16813",
        title="CODINEU test",
        state="SOURCING",
        fit_score=80,
        timing_state="ABIERTA",
    )
    session.add(opp)
    session.flush()
    tender = Tender(opportunity_id=opp.id, process_id="16813", bid_scope_json='{"state":"ITEM_LEVEL_ALLOWED"}')
    session.add(tender)
    session.flush()
    item = TenderItem(
        tender_id=tender.id,
        line_no=5,
        product="ROUTER",
        qty=qty,
        brand="Wi-Tek",
        model="WI-AP217-Lite",
        specs="Access point interior WI-AP217-Lite",
        verification="VERIFICADO",
    )
    session.add(item)
    session.flush()
    sup = Supplier(
        name="www.biosegur.com.ar",
        url="https://www.biosegur.com.ar/wi-tek-wi-ap217-lite-access-point--det--P2360",
        source="web",
    )
    session.add(sup)
    session.flush()
    results = ["CUMPLE"] * 8 if hard_ok else ["CUMPLE"] * 7 + ["NO_VERIFICADO"]
    keys = [
        "product_type",
        "brand",
        "model",
        "access_point",
        "indoor",
        "not_generic_router",
        "bands",
        "speed",
    ]
    evidence = [
        {
            "key": k,
            "label": k,
            "required": k,
            "found": "x",
            "source_url": "https://www.biosegur.com.ar/prod",
            "result": results[i],
            "mandatory": True,
        }
        for i, k in enumerate(keys)
    ]
    ok = sum(1 for r in results if r == "CUMPLE")
    evid = {
        "evidence": evidence,
        "hard_requirements": keys,
        "evidence_ok": ok,
        "evidence_total": 8,
        "technical_status": "EXACTO",
        "commercial_status": "STOCK_NO_VERIFICADO",
        "brand": "Wi-Tek",
        "model": "WI-AP217-Lite",
    }
    q = SupplierQuote(
        supplier_id=sup.id,
        opportunity_id=opp.id,
        tender_item_id=item.id,
        product_label="Wi-Tek Wi-Ap217-Lite Access Point",
        unit_cost=170319.6,
        qty=qty,
        match_score=100,
        match_pct=100,
        technical_status="EXACTO",
        commercial_status="STOCK_NO_VERIFICADO",
        stock_note=stock,
        shipping_neuquen="NO VERIFICADO",
        url="https://www.biosegur.com.ar/wi-tek-wi-ap217-lite-access-point--det--P2360",
        evidence_json=json.dumps(evid),
        verification="VERIFICADO",
    )
    session.add(q)
    session.commit()
    return opp, q, sup


def test_template_contains_mm_insumos():
    msg = build_rfq_message(
        producto="AP",
        marca_modelo="Wi-Tek WI-AP217-Lite",
        cantidad=2,
        hard_requirements="- indoor: PASS",
        modelo_spec="WI-AP217-Lite",
    )
    assert "Somos M&M Insumos, de Neuquén Capital." in msg
    assert "Precio unitario + IVA" in msg
    assert "Costo de envío a Neuquén Capital" in msg
    assert "Muchas gracias.\nM&M Insumos" in msg
    assert "{producto}" not in RFQ_TEMPLATE or "Producto: {producto}" in RFQ_TEMPLATE


def test_contact_prefers_whatsapp_never_product_url():
    product = "https://www.biosegur.com.ar/wi-tek-wi-ap217-lite-access-point--det--P2360"
    c = discover_commercial_contact(
        product_url=product,
        web="https://www.biosegur.com.ar/",
        razon_social_hint="Biosegur",
        fetch_html=_fetch_biosegur,
    )
    assert c.canal == "WHATSAPP"
    assert c.whatsapp == "5492235249544"
    assert c.contacto == "https://wa.me/5492235249544"
    assert c.contacto != product
    assert "producto" not in c.contacto.lower()
    assert c.email in ("ventas@biosegur.com.ar", "info@biosegur.com.ar")
    assert c.verified_at  # BA iso


def test_hard_req_blocks_rfq_file(session, tmp_path):
    from mm_commerce.config import get_settings

    opp, q, _ = _seed_exacto(session, hard_ok=False)
    drafts = build_rfq_drafts_for_opportunity(
        session, opp, line_nos={5}, fetch_html=_fetch_biosegur
    )
    assert drafts == []
    bp = get_settings().project_root / "offers_out" / "rfq_blocked_16813.json"
    assert bp.exists()
    blocked = json.loads(bp.read_text())
    assert blocked[0]["blocked"] is True
    assert "HARD_REQUIREMENTS" in blocked[0]["block_reason"]


def test_r5_flow_generates_rfq_with_real_contact(session):
    opp, q, sup = _seed_exacto(session, hard_ok=True)
    hard = verify_hard_requirements_100(q)
    assert hard["ok"] is True
    assert hard["summary"] == "8/8"
    drafts = build_rfq_drafts_for_opportunity(
        session, opp, line_nos={5}, fetch_html=_fetch_biosegur
    )
    assert len(drafts) == 1
    d = drafts[0]
    assert d.line_no == 5
    assert d.cantidad == 2.0
    assert d.canal == "WHATSAPP"
    assert d.contacto.startswith("https://wa.me/")
    assert d.url != d.contacto
    assert "M&M Insumos" in d.mensaje
    assert d.hard_requirements_ok is True
    assert len(d.evidence_matrix) == 8
    assert "stock" in d.missing
    path = persist_rfq_drafts(drafts, opportunity_id="16813")
    assert path.exists()
    # supplier persisted contact
    session.refresh(sup)
    assert sup.whatsapp == "5492235249544"
    assert sup.contact_verified_at


def test_buttons_prepare_only_never_send(session):
    opp, q, _ = _seed_exacto(session, hard_ok=True)
    drafts = build_rfq_drafts_for_opportunity(
        session, opp, line_nos={5}, fetch_html=_fetch_biosegur
    )
    persist_rfq_drafts(drafts, opportunity_id="16813")
    rid = drafts[0].rfq_id
    # email present on draft → ENVIAR EMAIL available
    kb = rfq_inline_keyboard(rid, email=drafts[0].email, draft=drafts[0].to_dict())
    labels = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert "ENVIAR WHATSAPP" in labels
    assert "COPIAR" in labels and "DESCARTAR" in labels
    if drafts[0].email and "@" in drafts[0].email:
        assert "ENVIAR EMAIL" in labels
    else:
        assert "ENVIAR EMAIL" not in labels
    # NULL email disables button
    kb_off = rfq_inline_keyboard(rid, email=None)
    assert "ENVIAR EMAIL" not in [b["text"] for row in kb_off["inline_keyboard"] for b in row]

    wa = handle_rfq_callback(f"rfq_wa:{rid}")
    assert wa["ok"] and wa["status"] == "PREPARED_WA"
    assert wa["prepared"]["auto_sent"] is False
    assert "wa.me" in wa["prepared"]["link"]

    em = handle_rfq_callback(f"rfq_email:{rid}")
    assert em["ok"] and em["status"] == "PREPARED_EMAIL"
    assert em["prepared"]["auto_sent"] is False

    cp = handle_rfq_callback(f"rfq_copy:{rid}")
    assert cp["ok"] and "M&M Insumos" in cp["detail"]

    # discard
    drafts2 = build_rfq_drafts_for_opportunity(
        session, opp, line_nos={5}, fetch_html=_fetch_biosegur
    )
    persist_rfq_drafts(drafts2, opportunity_id="16813")
    rid2 = drafts2[0].rfq_id
    dc = handle_rfq_callback(f"rfq_discard:{rid2}")
    assert dc["status"] == "DISCARDED"


def test_parse_and_ingest_reply_updates_commercial(session):
    opp, q, _ = _seed_exacto(session, hard_ok=True)
    drafts = build_rfq_drafts_for_opportunity(
        session, opp, line_nos={5}, fetch_html=_fetch_biosegur
    )
    persist_rfq_drafts(drafts, opportunity_id="16813")
    rid = drafts[0].rfq_id
    text = (
        "Precio unitario: $185000 + IVA\n"
        "Stock disponible: 5\n"
        "Plazo de despacho: 72 horas\n"
        "Envío a Neuquén Capital: $25000\n"
        "Vigencia de la oferta: 10 días\n"
    )
    parsed = parse_supplier_reply(text)
    assert parsed["unit_price"] == 185000.0
    assert parsed["stock"] == "5"
    assert "72" in parsed["lead_time"]
    assert parsed["shipping_amount"] == 25000.0

    result = ingest_supplier_reply(session, rfq_id=rid, text=text, recalculate=True)
    assert result["ok"]
    assert result["commercial_status"] == "DISPONIBLE"
    session.refresh(q)
    assert q.unit_cost == 185000.0
    assert q.stock_note == "5"
    assert q.commercial_status == "DISPONIBLE"
    evid = json.loads(q.evidence_json)
    assert evid["rfq_reply"]["parsed"]["validity"]
    assert result["pricing"] is not None
