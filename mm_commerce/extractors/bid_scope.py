"""BID_SCOPE — parse official pliego extract for offer modality.

Never assume partial bids are allowed. UNKNOWN blocks presentation.
States: TOTAL_REQUIRED | ITEM_LEVEL_ALLOWED | LOT_LEVEL_ALLOWED | UNKNOWN
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from typing import Any


STATE_TOTAL = "TOTAL_REQUIRED"
STATE_ITEM = "ITEM_LEVEL_ALLOWED"
STATE_LOT = "LOT_LEVEL_ALLOWED"
STATE_UNKNOWN = "UNKNOWN"

MODALIDAD_LABEL = {
    STATE_TOTAL: "TOTAL",
    STATE_ITEM: "POR RENGLÓN",
    STATE_LOT: "POR LOTE",
    STATE_UNKNOWN: "NO VERIFICADA",
}


def _yn(val: bool | None) -> str:
    if val is True:
        return "SÍ"
    if val is False:
        return "NO"
    return "NO VERIFICADO"


@dataclass
class BidScope:
    state: str = STATE_UNKNOWN
    oferta_total_obligatoria: bool | None = None
    cotizar_por_renglon: bool | None = None
    cotizar_parcialmente: bool | None = None
    adjudicacion_por_renglon: bool | None = None
    adjudicacion_por_grupo_lote: bool | None = None
    min_lines: int | None = None
    lines_to_quote: int | None = None
    oferta_restrictions: list[str] = field(default_factory=list)
    citations: list[str] = field(default_factory=list)
    source_pages: list[str] = field(default_factory=list)
    blocks_presentation: bool = True
    notes: str = ""

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["oferta_total_obligatoria_txt"] = _yn(self.oferta_total_obligatoria)
        d["cotizar_por_renglon_txt"] = _yn(self.cotizar_por_renglon)
        d["cotizar_parcialmente_txt"] = _yn(self.cotizar_parcialmente)
        d["adjudicacion_por_renglon_txt"] = _yn(self.adjudicacion_por_renglon)
        d["adjudicacion_por_grupo_lote_txt"] = _yn(self.adjudicacion_por_grupo_lote)
        d["modalidad"] = MODALIDAD_LABEL.get(self.state, "NO VERIFICADA")
        return d


def _page_hint(text: str, idx: int) -> str:
    """Best-effort page/section citation near match index."""
    window = text[max(0, idx - 400) : idx + 80]
    m = re.search(r"P[aá]gina\s+(\d+)\s+de\s+(\d+)", window, re.I)
    if m:
        return f"pág. {m.group(1)}/{m.group(2)}"
    m = re.search(r"Art[ií]culo\s+(\d+°?)", window, re.I)
    if m:
        return f"Art. {m.group(1)}"
    m = re.search(r"PLIEG-[\w\-#%]+", window)
    if m:
        return m.group(0)[:48]
    return "pliego"


def parse_bid_scope(text: str, *, line_count: int | None = None) -> BidScope:
    """Extract REQUIRED bid-scope fields from official pliego text."""
    raw = text or ""
    low = raw.lower()
    scope = BidScope()
    cites: list[str] = []
    pages: list[str] = []
    restrictions: list[str] = []

    # --- cotizar por renglón ---
    m = re.search(
        r"precio unitario y total de\s+cada rengl[oó]n",
        low,
    )
    if m:
        scope.cotizar_por_renglon = True
        pages.append(_page_hint(raw, m.start()))
        cites.append(
            "Art. Presentación de Oferta — precio unitario y total de cada renglón"
        )
    elif re.search(r"cotizar.*(por|cada)\s+rengl[oó]n", low):
        scope.cotizar_por_renglon = True
        cites.append("cotizar por/cada renglón")
    else:
        scope.cotizar_por_renglon = None

    # --- cantidad de renglones a cotizar ---
    m = re.search(r"cantidad de renglones a cotizar\s*:\s*(\d+)", low)
    if m:
        n = int(m.group(1))
        scope.lines_to_quote = n
        scope.min_lines = n
        pages.append(_page_hint(raw, m.start()))
        cites.append(f"Cantidad de Renglones a Cotizar: {n}")
        restrictions.append(f"debe_cotizar_{n}_renglones")

    # --- partial offers ---
    partial_yes = re.search(
        r"(ofertas?\s+parciales?\s+(admitidas?|permitidas?|aceptadas?)"
        r"|se\s+aceptar[aá]n?\s+ofertas?\s+parciales?"
        r"|cotizaci[oó]n\s+parcial\s+(admitida|permitida|aceptada)"
        r"|podr[aá]n?\s+cotizar\s+(uno|algunos|parte)\s+de\s+los\s+renglones)",
        low,
    )
    partial_no = re.search(
        r"(no\s+se\s+aceptar[aá]n?\s+ofertas?\s+parciales?"
        r"|ofertas?\s+parciales?\s+no\s+(ser[aá]n?\s+)?(admitidas?|aceptadas?)"
        r"|deber[aá]\s+cotizar\s+(la\s+)?(totalidad|todos\s+los\s+renglones)"
        r"|oferta\s+total\s+obligatoria)",
        low,
    )
    if partial_yes and not partial_no:
        scope.cotizar_parcialmente = True
        cites.append("ofertas parciales admitidas (texto pliego)")
        pages.append(_page_hint(raw, partial_yes.start()))
    elif partial_no:
        scope.cotizar_parcialmente = False
        cites.append("ofertas parciales no admitidas / totalidad obligatoria")
        pages.append(_page_hint(raw, partial_no.start()))
    elif scope.lines_to_quote:
        # Never assume partial allowed — N renglones a cotizar ⇒ no parcial
        scope.cotizar_parcialmente = False
        restrictions.append("sin_texto_parcial_explícito; no_asumir_parcial")
        cites.append(
            "Sin cláusula de oferta parcial; Cantidad de Renglones a Cotizar implica total"
        )
    else:
        scope.cotizar_parcialmente = None  # unknown

    # --- adjudicación por renglón ---
    adj_item = re.search(
        r"(adjudicaci[oó]n\s+por\s+rengl[oó]n"
        r"|adjudicaci[oó]n\s+se\s+efectuar[aá]\s+por\s+rengl[oó]n"
        r"|se\s+adjudicar[aá]\s+por\s+rengl[oó]n"
        r"|se\s+efectuar[aá]\s+por\s+rengl[oó]n"
        r"|podr[aá]\s+adjudicarse\s+por\s+rengl[oó]n)",
        low,
    )
    if adj_item:
        scope.adjudicacion_por_renglon = True
        cites.append("adjudicación por renglón")
        pages.append(_page_hint(raw, adj_item.start()))
    else:
        scope.adjudicacion_por_renglon = False if scope.lines_to_quote else None

    # --- adjudicación por grupo/lote ---
    adj_lot = re.search(
        r"(adjudicaci[oó]n\s+por\s+(grupo|lote|lotes)"
        r"|se\s+adjudicar[aá]\s+por\s+(grupo|lote)"
        r"|adjudicaci[oó]n\s+por\s+ítems?\s+agrupados)",
        low,
    )
    if adj_lot:
        scope.adjudicacion_por_grupo_lote = True
        cites.append("adjudicación por grupo/lote")
        pages.append(_page_hint(raw, adj_lot.start()))
    else:
        scope.adjudicacion_por_grupo_lote = False if (scope.lines_to_quote or adj_item) else None

    # --- oferta total obligatoria ---
    if scope.cotizar_parcialmente is False and scope.lines_to_quote:
        scope.oferta_total_obligatoria = True
    elif re.search(r"oferta\s+total\s+obligatoria|totalidad\s+de\s+los\s+renglones", low):
        scope.oferta_total_obligatoria = True
        cites.append("oferta total / totalidad de renglones")
    elif scope.cotizar_parcialmente is True:
        scope.oferta_total_obligatoria = False
    else:
        scope.oferta_total_obligatoria = None

    # total general de la propuesta (supports per-line quote + total)
    if re.search(r"total general de la propuesta", low):
        cites.append("total general de la propuesta (Art. Presentación)")
        if scope.cotizar_por_renglon is None:
            scope.cotizar_por_renglon = True

    # --- derive state ---
    if scope.adjudicacion_por_grupo_lote is True and scope.adjudicacion_por_renglon is not True:
        scope.state = STATE_LOT
        scope.blocks_presentation = False
    elif scope.adjudicacion_por_renglon is True or (
        scope.cotizar_parcialmente is True and scope.oferta_total_obligatoria is False
    ):
        scope.state = STATE_ITEM
        scope.blocks_presentation = False
    elif scope.oferta_total_obligatoria is True and scope.lines_to_quote:
        if line_count and scope.lines_to_quote != line_count:
            scope.state = STATE_UNKNOWN
            scope.blocks_presentation = True
            scope.notes = (
                f"lines_to_quote={scope.lines_to_quote} != extracted_lines={line_count}"
            )
        else:
            scope.state = STATE_TOTAL
            scope.blocks_presentation = False
    else:
        scope.state = STATE_UNKNOWN
        scope.blocks_presentation = True
        scope.notes = "BID_SCOPE no verificado — bloquear presentación hasta confirmar modalidad"

    scope.citations = list(dict.fromkeys(cites))
    scope.source_pages = list(dict.fromkeys(pages))
    scope.oferta_restrictions = list(dict.fromkeys(restrictions))
    return scope
