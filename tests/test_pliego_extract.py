"""Extractores de pliego — estructurados, sin inventar."""
from pathlib import Path

from mm_commerce.extractors.pliego_lines import extract_line_items
from mm_commerce.agents.pliego import PliegoAgent
from mm_commerce.models import init_db, get_session, Opportunity, TenderItem
from mm_commerce.config import get_settings

ROOT = Path(__file__).resolve().parents[1]

SAFIPRO_SAMPLE = """
Re Cant Sol Per Item                                      Per Cant Precio         Precio
            Sol                                           Ofr Ofr Unitario         Total
 1     1        RACK PARA SERVIDOR; Material Chapa Y Vidrio - Marca Sugerida: GLC
                                                                   $          $
                Marca Ofrecida:
 2     2        SWITCH; Uso Rackeable - 24 puertos Gigabit - Marca Sugerida: TP-LINK
                                                                   $          $
                Marca Ofrecida:
 10   20        ROSETA PARA RED; Tipo Rj 45 - Cantidad Entradas Dos
                                                                   $          $
                Marca Ofrecida:
Cantidad de Renglones a Cotizar: 3
"""


def test_safipro_rows_extracted():
    items = extract_line_items(SAFIPRO_SAMPLE)
    assert len(items) >= 3
    by_ren = {i["line_no"]: i for i in items}
    assert by_ren[1]["qty"] == 1.0
    assert "RACK" in by_ren[1]["product"].upper()
    assert by_ren[2]["qty"] == 2.0
    assert by_ren[10]["qty"] == 20.0
    assert by_ren[1]["verification"] == "NO VERIFICADO"
    assert by_ren[1]["source_pattern"] == "safipro_row"


def test_numbered_fixture_style():
    text = """
1) 10 unidades Notebook 15 pulgadas Intel i5
2) 5 unidades Monitor LED 24
"""
    items = extract_line_items(text)
    assert len(items) == 2
    assert items[0]["qty"] == 10.0
    assert "Notebook" in items[0]["product"]


def test_never_invent_from_empty():
    assert extract_line_items("") == []
    assert extract_line_items("   ") == []
    # boilerplate without rows
    assert extract_line_items("PLIEGO DE BASES Y CONDICIONES\nLey 2141") == []


def test_fixture_pliego_16589():
    text = (ROOT / "fixtures/pliegos/16589/pliego.txt").read_text(encoding="utf-8")
    items = extract_line_items(text)
    assert len(items) == 4
    assert items[0]["line_no"] == 1
    assert items[0]["qty"] == 10.0


def test_fixture_pliego_16592():
    text = (ROOT / "fixtures/pliegos/16592/pliego.txt").read_text(encoding="utf-8")
    items = extract_line_items(text)
    assert len(items) == 1
    assert items[0]["qty"] == 25.0
    assert "SILLA" in items[0]["product"].upper()


def test_pliego_agent_no_invent(tmp_path, monkeypatch):
    db = tmp_path / "p.db"
    url = f"sqlite:///{db}"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("PLIEGOS_DIR", str(tmp_path / "empty_pliegos"))
    monkeypatch.setenv("USE_FIXTURES", "1")
    get_settings.cache_clear()
    import mm_commerce.models as m

    m._engine = None
    m._SessionLocal = None
    init_db(url)
    session = get_session(url)
    opp = Opportunity(
        external_id="99999",
        title="Notebooks para oficina — título solo",
        fit_score=80,
        state="RADAR",
    )
    session.add(opp)
    session.commit()
    tender = PliegoAgent(session).process(opp)
    assert tender.extraction_status in {"SIN_DOCUMENTO", "SIN_LINEAS"}
    n = session.query(TenderItem).filter_by(tender_id=tender.id).count()
    assert n == 0  # never invent from title
    session.close()
    get_settings.cache_clear()
