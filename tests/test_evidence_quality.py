"""Evidence quality regression — no weak inferences (R5 WI-AP217-Lite)."""
from __future__ import annotations

from mm_commerce.matching import (
    COMM_DISPONIBLE,
    COMM_STOCK_INSUF,
    COMM_STOCK_NO_VER,
    TECH_EXACTO,
    classify_commercial,
    match_line_to_candidate,
)
from mm_commerce.rfq import rfq_inline_keyboard

AP_SPEC = (
    "ROUTER; Tipo Access point interior 2.4 GHz, 5 GHz inalámbrico - Velocidad 573-4800 Mbps "
    "- Marca Sugerida: Wi-Tek - Especificacion Adicional: (WI-AP217-Lite)"
)


def _weak_seller():
    return match_line_to_candidate(
        product="ROUTER",
        specs=AP_SPEC,
        brand="Wi-Tek",
        model="WI-AP217-Lite",
        candidate_title="Wi-Tek WI-AP217-Lite Access Point",
        candidate_text="Wi-Tek WI-AP217-Lite Access Point 2.4 mbps",
        source_url="https://seller.example/ap217-lite",
        price=170319.6,
        stock="NO VERIFICADO",
        shipping_neuquen="NO VERIFICADO",
        qty_needed=2,
    )


def test_lite_is_not_indoor():
    """lite != indoor — must be UNKNOWN unless ficha says indoor/interior/ceiling."""
    res = _weak_seller()
    indoor = next(e for e in res.evidence if e.key == "indoor")
    assert indoor.result != "CUMPLE"
    assert indoor.result == "NO_VERIFICADO"
    assert "lite" not in (indoor.found or "").lower() or indoor.result != "CUMPLE"
    assert indoor.validation_type == "NORMALIZED_TEXT"


def test_bands_24_alone_not_dual():
    """2.4 alone != 2.4+5 — MULTI_VALUE needs BOTH."""
    res = _weak_seller()
    bands = next(e for e in res.evidence if e.key == "bands")
    assert bands.result != "CUMPLE"
    assert bands.validation_type == "MULTI_VALUE"
    assert bands.result in ("NO_CUMPLE", "NO_VERIFICADO")


def test_mbps_without_number_not_speed_range():
    """'mbps' without numeric throughput != speed RANGE evidence."""
    res = match_line_to_candidate(
        product="ROUTER",
        specs=AP_SPEC,
        brand="Wi-Tek",
        model="WI-AP217-Lite",
        candidate_title="Access Point indoor interior ceiling mount",
        candidate_text="Access Point indoor interior 2.4 GHz and 5 GHz mbps wifi",
        source_url="https://example.com/x",
        price=1,
        stock="NO VERIFICADO",
        qty_needed=2,
    )
    speed = next(e for e in res.evidence if e.key == "speed")
    assert speed.result != "CUMPLE"
    assert speed.validation_type == "RANGE"
    assert speed.result == "NO_VERIFICADO"


def test_unknown_stock_is_stock_no_verificado_not_insuficiente():
    """unknown stock != STOCK_INSUFICIENTE."""
    assert (
        classify_commercial(
            price=100.0, stock="NO VERIFICADO", shipping_neuquen="envío", qty_needed=2
        )
        == COMM_STOCK_NO_VER
    )
    assert (
        classify_commercial(price=100.0, stock="", shipping_neuquen="", qty_needed=2)
        == COMM_STOCK_NO_VER
    )
    assert (
        classify_commercial(price=100.0, stock="1", shipping_neuquen="ok", qty_needed=2)
        == COMM_STOCK_INSUF
    )
    assert (
        classify_commercial(price=100.0, stock="5", shipping_neuquen="ok", qty_needed=2)
        == COMM_DISPONIBLE
    )
    res = _weak_seller()
    assert res.commercial_status == COMM_STOCK_NO_VER
    assert res.commercial_status != COMM_STOCK_INSUF


def test_tech_exacto_forbidden_on_weak_evidence():
    res = _weak_seller()
    assert res.technical_status != TECH_EXACTO
    assert res.fail_count + res.unknown_count > 0


def test_authoritative_specs_can_prove_exacto():
    auth = (
        "WI-AP217-Lite Indoor Ceiling Mount Access Point. Installation environment Indoor. "
        "Operating bands: 2.4 GHz and 5 GHz. 300Mbps at 2.4GHz + 867Mbps at 5GHz "
        "wireless speed up to 1200Mbps. Access Point Wi-Tek WI-AP217-Lite"
    )
    res = match_line_to_candidate(
        product="ROUTER",
        specs=AP_SPEC,
        brand="Wi-Tek",
        model="WI-AP217-Lite",
        candidate_title="Ceiling Mount WiFi 5 Access Point WI-AP217-Lite",
        candidate_text=auth,
        source_url="https://bydemes.com/en/products/networking/wireless/access-points/WITEK-0167",
        price=170319.6,
        stock="NO VERIFICADO",
        qty_needed=2,
    )
    assert res.fail_count == 0
    assert res.unknown_count == 0
    assert res.technical_status == TECH_EXACTO
    assert res.commercial_status == COMM_STOCK_NO_VER
    by_key = {e.key: e for e in res.evidence}
    assert by_key["indoor"].result == "CUMPLE"
    assert by_key["bands"].result == "CUMPLE"
    assert by_key["speed"].result == "CUMPLE"
    assert by_key["bands"].validation_type == "MULTI_VALUE"
    assert by_key["speed"].validation_type == "RANGE"


def test_email_null_disables_enviar_email_button():
    kb = rfq_inline_keyboard("rfq-16813-R5-test", email=None)
    labels = [b["text"] for row in kb["inline_keyboard"] for b in row]
    assert "ENVIAR EMAIL" not in labels
    assert "ENVIAR WHATSAPP" in labels
    kb2 = rfq_inline_keyboard("rfq-16813-R5-test", email="ventas@biosegur.com.ar")
    labels2 = [b["text"] for row in kb2["inline_keyboard"] for b in row]
    assert "ENVIAR EMAIL" in labels2
