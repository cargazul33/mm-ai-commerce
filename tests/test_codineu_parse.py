"""Parseo GeneXus HTML + hard-skip 16514 + fallback fixture."""
from pathlib import Path

from mm_commerce.connectors.codineu import (
    filter_hard_skips,
    load_fixture,
    parse_genexus_html,
    parse_html_file,
    row_to_item,
)
from mm_commerce.config import HARD_SKIP_IDS

ROOT = Path(__file__).resolve().parents[1]
HTML_FIXTURE = ROOT / "fixtures" / "html" / "wwlicitacion_sample.html"
LIVE_SNAPSHOT = Path("/workspace/codineu-list.html")


def test_parse_html_fixture_rows():
    items = parse_html_file(HTML_FIXTURE)
    assert len(items) == 3
    ids = {x["id"] for x in items}
    assert "16514" in ids
    assert "16589" in ids
    assert "16592" in ids
    it = next(x for x in items if x["id"] == "16589")
    assert "informát" in it["titulo"].lower() or "equipamiento" in it["titulo"].lower()
    assert it["organismo"]
    assert "TOKEN16589" in it["url"] or "wwlicitacion" in it["url"]


def test_hard_skip_16514_filter():
    items = parse_html_file(HTML_FIXTURE)
    kept, skipped = filter_hard_skips(items)
    assert skipped >= 1
    assert all(x["id"] != "16514" for x in kept)
    assert "16514" in HARD_SKIP_IDS


def test_row_to_item_mapping():
    row = [
        "16605",
        "2",
        "16",
        "203",
        "Contratación directa - 873\r\nEPAS",
        "Contratación directa con mas de un presupuesto - 873",
        "873",
        "Adquisición de materiales\r\nFerretería - ",
        "Adquisición de materiales",
        "06/09/26 10:30 ART",
        "Acto no Realizado",
        "4",
        "false",
        "false",
        "",
        "",
        "",
        "ENTE PROVINCIAL DE AGUA Y SANEAMIENTO - EPAS",
        "Ferretería - ",
    ]
    item = row_to_item(row, "https://example/detalle")
    assert item is not None
    assert item["id"] == "16605"
    assert item["numero"] == "873"
    assert item["titulo"] == "Adquisición de materiales"
    assert "EPAS" in item["organismo"]
    assert item["url"].endswith("detalle")


def test_fixture_json_loads():
    items = load_fixture(str(ROOT / "fixtures" / "codineu_sample.json"))
    assert len(items) >= 5
    assert any(str(x.get("id")) == "16514" for x in items)


def test_live_snapshot_if_present():
    if not LIVE_SNAPSHOT.exists():
        return
    items = parse_genexus_html(
        LIVE_SNAPSHOT.read_text(encoding="utf-8", errors="replace")
    )
    assert len(items) >= 5
    assert all("id" in x and x["id"].isdigit() for x in items)
    assert all(x.get("titulo") for x in items)
