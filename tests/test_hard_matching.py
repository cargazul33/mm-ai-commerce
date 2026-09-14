"""Hard attribute matching — permanent bug cases + product-type identity."""
from __future__ import annotations

from mm_commerce.matching import (
    TECH_EXACTO,
    TECH_EQUIV,
    TECH_NO_CUMPLE,
    TECH_NO_VER,
    TYPE_AP_INDOOR,
    TYPE_NAP_BOX,
    TYPE_NAP_SUPPORT,
    TYPE_ODF,
    TYPE_ROUTER,
    TYPE_SPLITTER,
    infer_product_type,
    match_line_to_candidate,
)

UPS_SPEC = (
    "ESTABILIZADOR DE TENSION; Tipo UPS - Potencia 3000 Va - Tensión De Entrada 220 Vac "
    "- Accesorio Sin - Tensión De Salida 220 Vac - Marca Sugerida: Atomlux - "
    "Especificacion Adicional: Atomlux-modelo: UPS3500@"
)
ODF_SPEC = (
    "CAJA DE EMPALME PARA FIBRA OPTICA; Tipo ODF para fusiones múltiples de fibras ópticas "
    "- Marca Sugerida: ninguna - Especificacion Adicional: ODF 12 puertos con acopladores SC/APC"
)
NAP_SPEC = (
    "REPUESTOS Y ACCESORIOS PARA EQUIPOS DE COMUNICACION; Elemento caja Nap con Splitter 1x8 "
    "- Marca Equipo Glc - Modelo Equipo GLC-FDB-012-01 - Marca Sugerida: GLC - "
    "Especificacion Adicional: CAJA NAP INTERIOR FTTB 1X8 SC/APC modelo: GLC-FDB-012-01"
)
AP_SPEC = (
    "ROUTER; Tipo Access point interior 2.4 GHz, 5 GHz inalámbrico - Velocidad 573-4800 Mbps "
    "- Marca Sugerida: Wi-Tek - Especificacion Adicional: (WI-AP217-Lite)"
)


def test_ups_2500_vs_3000_is_no_cumple():
    res = match_line_to_candidate(
        product="ESTABILIZADOR DE TENSION",
        specs=UPS_SPEC,
        brand="Atomlux",
        model="UPS3500",
        candidate_title="Estabilizador De Tensión Ups Atomlux 2500va Ca 220v Negro",
        candidate_text="UPS 2500 VA 220V Atomlux",
        source_url="https://example.com/ups-2500",
    )
    assert res.technical_status == TECH_NO_CUMPLE
    assert res.match_pct < 100
    assert any(e.key == "capacity_va" and "NO_CUMPLE" in e.result for e in res.evidence)
    assert any("LOWER_CAPACITY" in b for b in res.blockers)
    # commercial separate
    assert res.commercial_status  # set independently


def test_splitter_vs_odf_max_20_not_exacto():
    res = match_line_to_candidate(
        product="CAJA DE EMPALME PARA FIBRA OPTICA",
        specs=ODF_SPEC,
        candidate_title="GLC PLC Splitter SM 1x16 con conector SC/APC FO-4075",
        candidate_text="PLC SPLITTER 1x16 FO-4075 SC/APC balanceado",
        source_url="https://dyrsistemas.com.ar/glc-plc-splitter",
    )
    assert res.technical_status == TECH_NO_CUMPLE
    assert res.match_pct <= 20
    assert res.match_class != TECH_EXACTO
    assert infer_product_type(ODF_SPEC) == TYPE_ODF
    assert infer_product_type("GLC PLC Splitter SM 1x16 FO-4075") == TYPE_SPLITTER
    assert any(e.key == "product_type" and e.result == "NO_CUMPLE" for e in res.evidence)


def test_nap_support_vs_nap_box_not_100():
    """CAJA NAP ≠ SOPORTE CAJA NAP — even if page body mentions FDB."""
    res = match_line_to_candidate(
        product="REPUESTOS Y ACCESORIOS PARA EQUIPOS DE COMUNICACION",
        specs=NAP_SPEC,
        brand="GLC",
        model="GLC-FDB-012-01",
        candidate_title="PowerFiber Soporte Fijación Cajas NAP EC/Preconnect | x2",
        candidate_text=(
            "Soporte de fijación para cajas NAP. Related: GLC-FDB-012-01 Caja interior "
            "FTTH/FTTB 1x8 SC/APC con splitter"
        ),
        source_url="https://tienda.sawerin.com.ar/productos/glc-fdb-012-01",
        price=4176.9,
        stock="380",
        shipping_neuquen="envío nacional mencionado — cotizar a Neuquén",
    )
    assert res.match_pct <= 20
    assert res.technical_status == TECH_NO_CUMPLE
    assert res.technical_status != TECH_EXACTO
    assert res.product_type_found == TYPE_NAP_SUPPORT
    assert res.product_type_need == TYPE_NAP_BOX
    # commercial may look fine — must NOT upgrade technical
    assert res.commercial_status != ""  # separated


def test_ap_vs_generic_router_not_exacto():
    res = match_line_to_candidate(
        product="ROUTER",
        specs=AP_SPEC,
        brand="Wi-Tek",
        model="WI-AP217-Lite",
        candidate_title="Router WiFi genérico 300Mbps 2 antenas",
        candidate_text="Router inalámbrico doméstico, no access point empresarial",
        source_url="https://example.com/router",
    )
    assert res.technical_status in (TECH_NO_CUMPLE, TECH_NO_VER, "POSIBLE")
    assert res.match_pct < 100
    assert infer_product_type(AP_SPEC) == TYPE_AP_INDOOR
    # generic router title
    assert infer_product_type(
        "Router WiFi genérico 300Mbps", title_hint="Router WiFi genérico 300Mbps"
    ) == TYPE_ROUTER


def test_correct_ups3500_can_be_exacto():
    res = match_line_to_candidate(
        product="ESTABILIZADOR DE TENSION",
        specs=UPS_SPEC,
        brand="Atomlux",
        model="UPS3500",
        candidate_title="UPS Estabilizador Atomlux UPS3500 3500VA 220V CA Negro",
        candidate_text="Atomlux UPS3500 3500 VA 220V estabilizador de tension",
        source_url="https://depot.com.ar/productos/ups-atomlux-ups3500",
        price=500000,
        stock="5",
        shipping_neuquen="envío nacional mencionado — cotizar a Neuquén",
    )
    assert res.technical_status in (TECH_EXACTO, TECH_EQUIV)
    assert res.match_pct >= 95
    assert not any(e.result == "NO_CUMPLE" for e in res.evidence if e.mandatory)


def test_correct_nap_box_exacto():
    res = match_line_to_candidate(
        product="REPUESTOS Y ACCESORIOS PARA EQUIPOS DE COMUNICACION",
        specs=NAP_SPEC,
        brand="GLC",
        model="GLC-FDB-012-01",
        candidate_title="GLC-FDB-012-01 Caja NAP interior FTTH/FTTB 1x8 SC/APC",
        candidate_text="Caja NAP interior FTTB 1x8 SC/APC modelo GLC-FDB-012-01 con splitter",
        source_url="https://tienda.sawerin.com.ar/productos/glc-fdb-012-01-real",
        price=8000,
        stock="10",
        shipping_neuquen="envío nacional mencionado — cotizar a Neuquén",
    )
    assert res.technical_status in (TECH_EXACTO, TECH_EQUIV)
    assert res.match_pct >= 90
    assert res.product_type_found == TYPE_NAP_BOX


def test_evidence_matrix_present():
    res = match_line_to_candidate(
        product="ESTABILIZADOR DE TENSION",
        specs=UPS_SPEC,
        candidate_title="UPS 2500va",
        candidate_text="2500 VA",
        source_url="https://x.test/u",
    )
    assert res.evidence_matrix
    assert all("|" in row for row in res.evidence_matrix)
    assert res.evidence_total >= 1


def test_keyword_only_cannot_be_exacto_100():
    """Shared keywords NAP/fibra/soporte must not yield EXACTO without type+attrs."""
    res = match_line_to_candidate(
        product="REPUESTOS",
        specs=NAP_SPEC,
        candidate_title="Kit fibra óptica NAP soporte splitter accesorios",
        candidate_text="fibra nap splitter soporte caja accesorios red",
        source_url="https://x.test/kw",
    )
    assert res.technical_status != TECH_EXACTO or res.match_pct < 100
    # With support in title → NO_CUMPLE
    assert res.match_pct <= 20 or res.technical_status != TECH_EXACTO
