"""Full pliego extraction — RAW/NORMALIZED/HARD/SOFT for all 7 lines of #16813."""
from __future__ import annotations

from pathlib import Path

from mm_commerce.extractors.pliego_lines import extract_line_items
from mm_commerce.matching import match_line_to_candidate

ROOT = Path(__file__).resolve().parents[1]
PLIEGO = ROOT / "data" / "pliegos" / "16813" / "pliego.txt"


def test_16813_seven_full_lines_with_hard_requirements():
    assert PLIEGO.exists()
    items = extract_line_items(PLIEGO.read_text(encoding="utf-8"))
    assert len(items) == 7
    by = {i["line_no"]: i for i in items}

    # No truncated product-only stubs for R3/R5/R7
    assert "ODF" in by[3]["specs"].upper() or "EMPALME" in by[3]["specs"].upper()
    assert "Access point" in by[5]["specs"] or "access point" in by[5]["specs"].lower()
    assert "6E" in by[7]["specs"] or "6e" in by[7]["specs"].lower()
    assert "Meraki" in by[7]["specs"] or "MERAKI" in by[7]["specs"]
    assert "LIC-MR" in by[7]["specs"].upper() or "licencia" in by[7]["specs"].lower()

    for ren in range(1, 8):
        it = by[ren]
        assert it.get("RAW_SPEC")
        assert it.get("NORMALIZED_SPEC")
        assert it.get("HARD_REQUIREMENTS")
        assert len(it["HARD_REQUIREMENTS"]) >= 1
        assert len(it["NORMALIZED_SPEC"]) >= 40
        # never absurdly truncated
        assert not it["NORMALIZED_SPEC"].endswith("BALANCEA")  # full BALANCEADO

    # R7 Meraki outdoor AP hard set
    keys7 = {h["key"] for h in by[7]["HARD_REQUIREMENTS"]}
    for expected in (
        "product_type",
        "access_point",
        "outdoor",
        "mgmt_cloud",
        "wifi_tech",
        "speed",
        "bands",
        "ethernet",
        "poe",
        "license",
    ):
        assert expected in keys7, f"missing {expected} in R7 hard reqs: {keys7}"

    # R4 splitter FO-4075 normalized
    assert "FO-4075" in by[4]["NORMALIZED_SPEC"].upper().replace(" ", "")
    assert by[4]["product_type"] == "SPLITTER"
    assert by[6]["product_type"] == "CAJA_NAP"


def test_empty_candidate_emits_all_hard_reqs_not_0_of_1():
    items = extract_line_items(PLIEGO.read_text(encoding="utf-8"))
    by = {i["line_no"]: i for i in items}
    for ren in (3, 5, 7):
        it = by[ren]
        res = match_line_to_candidate(
            product=it["product"],
            specs=it["specs"],
            brand=it.get("brand") or "",
            model=it.get("model") or "",
            candidate_title="",
            candidate_text="",
            tender_item_id=100 + ren,
            candidate_id=f"c-empty-{ren}",
            line_no=ren,
        )
        assert res.evidence_total == len([h for h in it["HARD_REQUIREMENTS"] if h.get("mandatory", True)])
        assert res.evidence_total > 1, f"R{ren} still truncated evidence {res.evidence_total}"
        assert res.evidence_ok == 0
        assert all(e.result == "NO_VERIFICADO" for e in res.evidence)
