"""FIT SCORE 0-100 y MATCH SCORE — determinístico, sin inventar."""
from __future__ import annotations

from mm_commerce.config import AVOID_CATEGORIES, PRIORITY_KEYWORDS


def text_blob(*parts: str) -> str:
    return " ".join(p or "" for p in parts).lower()


def is_avoided(blob: str) -> bool:
    return any(a in blob for a in AVOID_CATEGORIES)


def fit_score(
    title: str = "",
    rubros: str = "",
    organism: str = "",
    modality: str = "",
) -> tuple[int, str]:
    """
    Retorna (score 0-100, razón).
    Categorías evitadas → score bajo + flag.
    """
    blob = text_blob(title, rubros, organism, modality)
    if is_avoided(blob):
        return 5, "categoría_evitada"

    score = 20  # base
    hits = [kw for kw in PRIORITY_KEYWORDS if kw in blob]
    score += min(60, len(hits) * 12)

    # IT / office boosts
    if any(k in blob for k in ("informát", "informat", "notebook", "computadora", "impresor")):
        score += 15
    if any(k in blob for k in ("librer", "papeler", "oficina", "útiles", "utiles")):
        score += 10
    if "silla" in blob or "mobili" in blob:
        score += 8

    # organism noise (police already avoided)
    if "educ" in blob or "escuela" in blob or "universidad" in blob:
        score += 5

    return max(0, min(100, score)), ("prioridad:" + ",".join(hits[:5]) if hits else "base")


def match_score(need: str, candidate: str) -> int:
    """MATCH SCORE simple por tokens compartidos."""
    n = set(w for w in need.lower().split() if len(w) > 2)
    c = set(w for w in candidate.lower().split() if len(w) > 2)
    if not n:
        return 0
    overlap = len(n & c)
    return max(0, min(100, int(100 * overlap / len(n))))
