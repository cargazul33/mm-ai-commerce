from mm_commerce.scoring import fit_score, match_score, is_avoided


def test_fit_it_high():
    score, _ = fit_score(
        "Adquisición de notebooks y monitores",
        "Informática | Electrónica",
        "Ministerio de Educación",
    )
    assert score >= 60


def test_fit_avoid_police():
    score, reason = fit_score(
        "Repuestos comunicación",
        "Electricidad",
        "POLICIA DE LA PROVINCIA",
        "Contratación Policía",
    )
    assert score <= 10
    assert reason == "categoría_evitada"


def test_fit_didactico_title_alone_not_high():
    """COMPRA DE MATERIAL DIDÁCTICO must NOT score ~83 on title/rubros alone."""
    score, reason = fit_score(
        "COMPRA DE MATERIAL DIDACTICO",
        "Educación y cultura | Librería, papelería  y útiles de oficina -",
        "CONSEJO PROVINCIAL DE EDUCACION",
    )
    assert score < 40, score
    assert score <= 25
    assert "didactico" in reason


def test_fit_didactico_with_resellable_lines_can_rise():
    score, reason = fit_score(
        "COMPRA DE MATERIAL DIDACTICO",
        "Educación y cultura | Librería",
        "CONSEJO PROVINCIAL DE EDUCACION",
        line_items=["Resma papel A4 75g", "Cartucho toner HP 85A", "Carpeta oficio"],
    )
    assert score >= 40
    assert "didactico_con_renglones" in reason


def test_match_score():
    assert match_score("notebook i5 16gb", "Notebook Lenovo i5 16GB SSD") >= 40
