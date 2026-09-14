"""FIT SCORE 0-100 y MATCH SCORE — determinístico; título didáctico NO da FIT alto."""
from __future__ import annotations

from mm_commerce.config import (
    AVOID_CATEGORIES,
    DIDACTIC_FLAGS,
    PRIORITY_KEYWORDS,
    RESELLABLE_LINE_KEYWORDS,
)


def text_blob(*parts: str) -> str:
    return " ".join(p or "" for p in parts).lower()


def is_avoided(blob: str) -> bool:
    return any(a in blob for a in AVOID_CATEGORIES)


def is_didactic(blob: str) -> bool:
    return any(f in blob for f in DIDACTIC_FLAGS)


def _line_blob(line_items: list[str] | None) -> str:
    if not line_items:
        return ""
    return " ".join(line_items).lower()


def has_resellable_lines(line_items: list[str] | None) -> bool:
    blob = _line_blob(line_items)
    if not blob:
        return False
    return any(k in blob for k in RESELLABLE_LINE_KEYWORDS) or any(
        k in blob for k in PRIORITY_KEYWORDS
    )


def fit_score(
    title: str = "",
    rubros: str = "",
    organism: str = "",
    modality: str = "",
    line_items: list[str] | None = None,
) -> tuple[int, str]:
    """
    Retorna (score 0-100, razón).
    - Categorías evitadas → score bajo.
    - Didáctico/escolar: alto FIT SOLO si renglones concretos son revendibles IT/oficina.
      Título solo NUNCA debe llegar a ~83 por 'librería' en rubros.
    """
    blob = text_blob(title, rubros, organism, modality)
    lines_blob = _line_blob(line_items)
    combined = text_blob(blob, lines_blob)

    if is_avoided(combined):
        return 5, "categoría_evitada"

    didactic = is_didactic(blob)
    resellable = has_resellable_lines(line_items)

    if didactic and not resellable:
        # Title/rubros may mention librería — still NOT a commercial FIT without lines
        return 18, "didactico_sin_renglones_revendibles"

    score = 15  # conservative base
    hit_source = lines_blob if (line_items and resellable) else blob
    hits = [kw for kw in PRIORITY_KEYWORDS if kw in hit_source]
    uniq: list[str] = []
    for h in hits:
        if h not in uniq:
            uniq.append(h)
    score += min(50, len(uniq) * 10)

    strong_it = any(
        k in hit_source or k in blob
        for k in (
            "informát",
            "informat",
            "notebook",
            "computadora",
            "impresor",
            "hardware",
            "elementos inform",
            "equipamiento inform",
            "conectividad",
            "redes",
            "switch",
            "router",
            "olt",
            "gpon",
            "wifi",
            "wi-fi",
            "ups",
            "access point",
        )
    )
    if strong_it:
        score += 20

    office = any(
        k in hit_source
        for k in ("librer", "papeler", "oficina", "útiles", "utiles", "mobili", "silla")
    )
    if office and not didactic:
        score += 12
    elif office and didactic and resellable:
        score += 10

    if any(k in hit_source for k in ("electrodomést", "electrodomest", "herramienta")):
        score += 8

    # Concrete verified line items unlock commercial FIT (esp. didactic titles)
    if line_items and resellable:
        score += 15

    # Cap title-only so ambiguous rubros cannot hit alert threshold alone
    # unless strong IT signal in title/rubros.
    if not line_items:
        if didactic:
            score = min(score, 25)
        elif not strong_it:
            score = min(score, 55)

    if didactic and resellable:
        return max(0, min(100, score)), "didactico_con_renglones:" + ",".join(uniq[:5])

    reason = ("prioridad:" + ",".join(uniq[:5])) if uniq else "base"
    return max(0, min(100, score)), reason


def match_score(need: str, candidate: str) -> int:
    """Legacy token overlap. Prefer mm_commerce.matching.match_line_to_candidate."""
    n = set(w for w in need.lower().split() if len(w) > 2)
    c = set(w for w in candidate.lower().split() if len(w) > 2)
    if not n:
        return 0
    overlap = len(n & c)
    return max(0, min(100, int(100 * overlap / len(n))))
