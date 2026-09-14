"""Evidence consistency: hard_requirements_total == pass+fail+unknown."""
from __future__ import annotations

from pathlib import Path

from mm_commerce.extractors.pliego_lines import extract_line_items
from mm_commerce.matching import (
    INTERNAL_VALIDATION_ERROR,
    AttrEvidence,
    build_line_spec,
    match_line_to_candidate,
    summarize_evidence_counts,
    validate_evidence_consistency,
)

ROOT = Path(__file__).resolve().parents[1]
PLIEGO = ROOT / "data" / "pliegos" / "16813" / "pliego.txt"

OLT_SPEC = (
    "SWITCH; Uso Red Lan/Wan Olt Gpon 4 Puertos 2 Puertos Gb 1 Puerto Gb Uplink GB - "
    "Tipo De Conexión Fibra Optica GB/S - Marca Sugerida: TP-Link SM1250-C+ - "
    "Especificacion Adicional: OLT TP-Link DeltaStream DS-P7001-04 con 4 Módulos PON"
)


def test_r1_listed_hard_equals_evidence_fraction():
    line = build_line_spec(line_no=1, product="SWITCH", specs=OLT_SPEC, brand="TP-Link", model="DS-P7001-04")
    # soft (non-mandatory) must NOT inflate hard list
    assert all(r.mandatory for r in line.hard_requirements)
    soft_keys = {r.key for r in line.soft_requirements}
    assert "sfp_or_10g" in soft_keys or all(r.key != "sfp_or_10g" for r in line.hard_requirements)

    res = match_line_to_candidate(
        product="SWITCH",
        specs=OLT_SPEC,
        brand="TP-Link",
        model="DS-P7001-04",
        candidate_title="OLT TP-LINK DS-P7001-04 GPON 4 PUERTOS+2P 10G+1P GIGA U",
        candidate_text="OLT GPON 4 puertos PON uplink 10G TP-Link DS-P7001-04",
        source_url="https://katech.com.ar/producto/ds-p7001-04",
        price=1497405.0,
        stock="1",
        shipping_neuquen="envío nacional",
        tender_item_id=1,
        candidate_id="c1-test",
        line_no=1,
    )
    listed = len(line.hard_requirements)
    assert listed == res.hard_requirements_total == res.evidence_total
    assert res.hard_requirements_total == res.pass_count + res.fail_count + res.unknown_count
    assert res.validation_error is None
    # Must not show 7 listed vs 6/6
    assert listed == res.evidence_ok + res.fail_count + res.unknown_count


def test_count_inconsistency_raises_internal_validation_error():
    ev = [
        AttrEvidence("a", "A", "x", "x", "", "CUMPLE", True),
        AttrEvidence("b", "B", "y", "y", "", "NO_CUMPLE", True),
    ]
    # Lie about totals
    check = validate_evidence_consistency(
        evidence=ev,
        hard_requirements=[{"key": "a", "mandatory": True}, {"key": "b", "mandatory": True}, {"key": "c", "mandatory": True}],
        evidence_ok=2,
        evidence_total=2,
    )
    assert check["validation_error"] == INTERNAL_VALIDATION_ERROR
    assert check["consistent"] is False


def test_summarize_identity():
    ev = [
        AttrEvidence("a", "A", "x", "x", "", "CUMPLE", True),
        AttrEvidence("b", "B", "y", "", "", "NO_VERIFICADO", True),
        AttrEvidence("c", "C", "z", "z", "", "NO_CUMPLE", True),
        AttrEvidence("soft", "S", "s", "s", "", "CUMPLE", False),
    ]
    s = summarize_evidence_counts(ev)
    assert s["hard_requirements_total"] == 3
    assert s["pass_count"] == 1
    assert s["fail_count"] == 1
    assert s["unknown_count"] == 1
    assert s["hard_requirements_total"] == s["pass_count"] + s["fail_count"] + s["unknown_count"]


def test_16813_r1_from_pliego_no_seven_vs_six():
    items = extract_line_items(PLIEGO.read_text(encoding="utf-8"))
    r1 = next(i for i in items if i["line_no"] == 1)
    hard = r1["HARD_REQUIREMENTS"]
    assert all(h.get("mandatory", True) for h in hard)
    res = match_line_to_candidate(
        product=r1["product"],
        specs=r1["specs"],
        brand=r1.get("brand") or "",
        model=r1.get("model") or "",
        candidate_title="OLT TP-LINK DS-P7001-04 GPON 4 PUERTOS+2P 10G+1P GIGA U",
        candidate_text="GPON OLT 4 puertos 10G uplink DS-P7001-04 TP-Link",
        source_url="https://example.com/olt",
        tender_item_id=11,
        candidate_id="c-r1",
        line_no=1,
    )
    assert len(hard) == res.evidence_total == res.hard_requirements_total
    assert res.hard_requirements_total == res.pass_count + res.fail_count + res.unknown_count
