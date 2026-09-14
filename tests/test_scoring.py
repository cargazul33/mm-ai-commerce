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


def test_match_score():
    assert match_score("notebook i5 16gb", "Notebook Lenovo i5 16GB SSD") >= 40
