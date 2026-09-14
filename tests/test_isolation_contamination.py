"""Cross-line isolation — R4 must never inherit R6 classification/evidence."""
from __future__ import annotations

from mm_commerce.isolation import (
    MatchSession,
    assert_no_cross_contamination,
    make_candidate_id,
)
from mm_commerce.matching import (
    TECH_EXACTO,
    TECH_NO_CUMPLE,
    TYPE_NAP_BOX,
    TYPE_NAP_SUPPORT,
    TYPE_SPLITTER,
    match_line_to_candidate,
)

SPLITTER_SPEC = (
    "REPUESTOS Y ACCESORIOS PARA EQUIPOS DE COMUNICACION; Elemento Unidad de "
    "Acoplamiento SPLITTER - Marca Sugerida: GLC - Especificacion Adicional: "
    "PLC SPLITTER GLC SM 1X16 FO-4075 CON CONECTOR SC/APC BALANCEADO"
)
NAP_SPEC = (
    "REPUESTOS Y ACCESORIOS PARA EQUIPOS DE COMUNICACION; Elemento caja Nap con Splitter 1x8 "
    "- Marca Equipo Glc - Modelo Equipo GLC-FDB-012-01 - Marca Sugerida: GLC - "
    "Especificacion Adicional: CAJA NAP INTERIOR FTTB 1X8 SC/APC modelo: GLC-FDB-012-01"
)


def test_r4_splitter_cannot_inherit_r6_soporte_nap():
    """Simulate sequential matching like sourcing — clear temp between items."""
    session = MatchSession()

    # --- R6 first (NAP box need, support candidate) ---
    session.begin_item(30, line_no=6)
    c6 = session.bind_candidate(
        tender_item_id=30,
        url="https://tienda.sawerin.com.ar/productos/soporte-nap",
        title="PowerFiber Soporte Fijación Cajas NAP EC/Preconnect | x2",
        line_no=6,
    )
    r6 = match_line_to_candidate(
        product="REPUESTOS Y ACCESORIOS PARA EQUIPOS DE COMUNICACION",
        specs=NAP_SPEC,
        brand="GLC",
        model="GLC-FDB-012-01",
        candidate_title="PowerFiber Soporte Fijación Cajas NAP EC/Preconnect | x2",
        candidate_text="Soporte de fijación para cajas NAP. Related: GLC-FDB-012-01",
        source_url="https://tienda.sawerin.com.ar/productos/soporte-nap",
        price=4176.9,
        stock="380",
        shipping_neuquen="envío nacional mencionado — cotizar a Neuquén",
        tender_item_id=30,
        candidate_id=c6.candidate_id,
        line_no=6,
    )
    session.evidence.put(c6, r6.evidence_matrix, meta=r6.to_dict())
    session.remember_url("https://tienda.sawerin.com.ar/productos/soporte-nap")
    assert r6.product_type_need == TYPE_NAP_BOX
    assert r6.product_type_found == TYPE_NAP_SUPPORT
    assert r6.technical_status == TECH_NO_CUMPLE
    session.end_item()  # clear temp

    # --- R4 splitter after R6 — must NOT show SOPORTE_NAP ---
    session.begin_item(28, line_no=4)
    c4 = session.bind_candidate(
        tender_item_id=28,
        url="https://dyrsistemas.com.ar/glc-plc-splitter-sm-1x16",
        title="GLC PLC SPLITTER SM 1X16 CON CONECTOR SC/APC - FO-4075",
        line_no=4,
    )
    r4 = match_line_to_candidate(
        product="REPUESTOS Y ACCESORIOS PARA EQUIPOS DE COMUNICACION",
        specs=SPLITTER_SPEC,
        brand="GLC",
        model="FO-4075",
        candidate_title="GLC PLC SPLITTER SM 1X16 CON CONECTOR SC/APC - FO-4075",
        candidate_text="PLC SPLITTER 1x16 FO-4075 SC/APC balanceado",
        source_url="https://dyrsistemas.com.ar/glc-plc-splitter-sm-1x16",
        tender_item_id=28,
        candidate_id=c4.candidate_id,
        line_no=4,
    )
    session.evidence.put(c4, r4.evidence_matrix, meta=r4.to_dict())
    assert r4.product_type_need == TYPE_SPLITTER
    assert r4.product_type_found == TYPE_SPLITTER
    assert r4.product_type_found != TYPE_NAP_SUPPORT
    assert r4.technical_status in (TECH_EXACTO, "EQUIVALENTE_PERMITIDO", "POSIBLE", "NO_VERIFICADO") or r4.match_pct >= 0
    # Must not inherit R6 type
    assert r4.product_type_found != r6.product_type_found or r4.product_type_need != r6.product_type_need
    assert r4.candidate_id != r6.candidate_id
    assert r4.tender_item_id == 28
    assert r4.evidence_matrix_key.startswith("28::")
    # Matrix rows keyed
    assert all(row.startswith("28::") for row in r4.evidence_matrix)
    session.end_item()

    errors = assert_no_cross_contamination(
        [
            {
                "tender_item_id": 30,
                "candidate_id": c6.candidate_id,
                "url": "https://tienda.sawerin.com.ar/productos/soporte-nap",
                "product_type_need": r6.product_type_need,
                "product_type_found": r6.product_type_found,
                "candidate_title": "PowerFiber Soporte Fijación Cajas NAP EC/Preconnect | x2",
                "evidence_json": r6.to_json(),
            },
            {
                "tender_item_id": 28,
                "candidate_id": c4.candidate_id,
                "url": "https://dyrsistemas.com.ar/glc-plc-splitter-sm-1x16",
                "product_type_need": r4.product_type_need,
                "product_type_found": r4.product_type_found,
                "candidate_title": "GLC PLC SPLITTER SM 1X16 CON CONECTOR SC/APC - FO-4075",
                "evidence_json": r4.to_json(),
            },
        ]
    )
    assert not any("INHERITED_NAP_SUPPORT" in e for e in errors)
    assert not any("SHARED_CANDIDATE_ID" in e for e in errors)
    assert not any("SHARED_URL" in e for e in errors)


def test_candidate_ids_unique_and_item_mandatory():
    a = make_candidate_id(tender_item_id=1, url="http://a", title="x")
    b = make_candidate_id(tender_item_id=2, url="http://a", title="x")
    assert a != b
    session = MatchSession()
    session.begin_item(1, line_no=1)
    c1 = session.bind_candidate(tender_item_id=1, url="http://a", title="x", line_no=1)
    session.end_item()
    session.begin_item(2, line_no=2)
    c2 = session.bind_candidate(tender_item_id=2, url="http://a", title="x", line_no=2)
    assert c1.candidate_id != c2.candidate_id


def test_evidence_not_shared_by_reference():
    session = MatchSession()
    session.begin_item(1, line_no=1)
    c1 = session.bind_candidate(tender_item_id=1, url="u1", title="t1", line_no=1)
    rows = ["a|b|c"]
    session.evidence.put(c1, rows)
    rows.append("MUTATED")
    got = session.evidence.get(c1)
    assert "MUTATED" not in got
