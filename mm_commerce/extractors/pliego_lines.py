"""Extracción estructurada de renglones de pliego (PDF/texto).

Reglas:
- Solo emite ítems anclados a patrones del documento.
- Nunca inventa cantidad/producto desde el título de la oportunidad.
- Toda línea sale como NO VERIFICADO hasta revisión humana.
"""
from __future__ import annotations

import re
from typing import Any

# SAFIPRO pedido de presupuesto:
#   Re  Cant Sol  ...  Item
#    1      5      SILLA; Material ...
#   10     20      ROSETA PARA RED; Tipo ...
SAFIPRO_ROW = re.compile(
    r"^\s*(?P<ren>\d{1,3})\s+(?P<qty>\d+[.,]?\d*)\s+"
    r"(?P<product>[A-ZÁÉÍÓÚÑÜ][A-Za-zÁÉÍÓÚáéíóúÑñÜü0-9 /&\-\.\(\)\+]{2,}(?:;.*)?)\s*$",
    re.M,
)

# Anexo "Descripcion": "1 RACK PARA SERVIDOR; Material ..." (sin qty)
DESC_ROW = re.compile(
    r"^\s*(?P<ren>\d{1,3})\s+"
    r"(?P<product>[A-ZÁÉÍÓÚÑÜ][A-Za-zÁÉÍÓÚáéíóúÑñÜü0-9 /&\-\.\(\)\+]{2,}(?:;.*)?)\s*$",
    re.M,
)

# Fixture / listado: "1) 10 unidades Notebook ..."
NUMBERED_QTY = re.compile(
    r"^\s*(?P<ren>\d{1,3})[\)\.\-]\s*"
    r"(?P<qty>\d+[.,]?\d*)\s*"
    r"(?P<unit>u\.?|unidades?|kits?|cajas?|resmas?|jg|juegos?|packs?)?\s*"
    r"(?:de\s+)?(?P<product>.+?)\s*$",
    re.I | re.M,
)

# "Ítem 3: 15 Monitor LED ..."
ITEM_LABEL = re.compile(
    r"^\s*(?:ítem|item|rengl[oó]n)\s*(?P<ren>\d{1,3})\s*[:\-]\s*"
    r"(?P<qty>\d+[.,]?\d*)?\s*"
    r"(?P<unit>u\.?|unidades?)?\s*"
    r"(?P<product>.+?)\s*$",
    re.I | re.M,
)

BRAND_RE = re.compile(
    r"\b(HP|Dell|Lenovo|Epson|Brother|Cisco|Samsung|LG|Acer|Canon|Logitech|"
    r"TP-?LINK|TP\s*Link|GLC|ZOLODA|LYONN|Omada)\b",
    re.I,
)

SKIP_LINE = re.compile(
    r"(?i)^(re\s+cant|cantidad de renglones|total cotizado|marca ofrecida|"
    r"mantenimiento de oferta|forma de pago|plazo de entrega|referencias|"
    r"safipro|página|pagina|cuit:|descripcion\s*$|pedido de presupuesto)",
)


def _parse_qty(raw: str | None) -> float | None:
    if not raw:
        return None
    try:
        return float(raw.replace(",", "."))
    except ValueError:
        return None


def _clean_product(text: str) -> str:
    text = re.sub(r"\s+", " ", text).strip(" -\t")
    # drop trailing price placeholders
    text = re.sub(r"\$\s*$", "", text).strip()
    return text[:240]


def _brand_from(text: str) -> str:
    m = BRAND_RE.search(text)
    return m.group(1) if m else ""


def extract_line_items(text: str, *, max_items: int = 40) -> list[dict[str, Any]]:
    """Devuelve renglones estructurados hallados en el texto. Lista vacía si nada confiable."""
    if not text or not text.strip():
        return []

    # Prefer SAFIPRO table (has qty). Merge continuation lines into product when useful.
    flattened = _flatten_safipro_blocks(text)
    found = _from_safipro(flattened)
    if found:
        return found[:max_items]

    found = _from_numbered(text)
    if found:
        return found[:max_items]

    found = _from_item_label(text)
    if found:
        return found[:max_items]

    # Anexo descripción sin qty — solo si hay renglones claros con ";" (SAFIPRO style)
    found = _from_descripcion_annex(text)
    return found[:max_items]


def _flatten_safipro_blocks(text: str) -> str:
    """Une continuaciones indentadas de un renglón SAFIPRO en una sola línea lógica."""
    lines = text.splitlines()
    out: list[str] = []
    buf = ""
    row_start = re.compile(r"^\s*\d{1,3}\s+\d+[.,]?\d*\s+[A-ZÁÉÍÓÚÑÜ]")
    for raw in lines:
        line = raw.rstrip()
        if not line.strip():
            if buf:
                out.append(buf)
                buf = ""
            continue
        if SKIP_LINE.search(line.strip()):
            if buf:
                out.append(buf)
                buf = ""
            continue
        if row_start.match(line):
            if buf:
                out.append(buf)
            buf = line.strip()
        elif buf and (line.startswith(" ") or line.startswith("\t") or line[:16].strip() == ""):
            # continuation of product description
            cont = line.strip()
            if cont and not cont.startswith("$"):
                if cont.lower().startswith("marca ofrecida"):
                    out.append(buf)
                    buf = ""
                else:
                    buf = f"{buf} {cont}"
        else:
            if buf:
                out.append(buf)
                buf = ""
            out.append(line.strip())
    if buf:
        out.append(buf)
    return "\n".join(out)


def _from_safipro(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[int] = set()
    for m in SAFIPRO_ROW.finditer(text):
        ren = int(m.group("ren"))
        if ren in seen:
            continue
        qty = _parse_qty(m.group("qty"))
        product = _clean_product(m.group("product"))
        if qty is None or qty <= 0 or len(product) < 3:
            continue
        # reject legal boilerplate false positives
        if product.lower().startswith(("ley ", "decreto", "artículo", "articulo")):
            continue
        seen.add(ren)
        brand = _brand_from(product)
        items.append(
            {
                "line_no": ren,
                "product": product.split(";")[0].strip()[:200],
                "qty": qty,
                "unit": "u",
                "brand": brand,
                "model": "",
                "specs": product[:400],
                "verification": "NO VERIFICADO",
                "source_pattern": "safipro_row",
            }
        )
    items.sort(key=lambda x: x["line_no"])
    return items


def _from_numbered(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[int] = set()
    for m in NUMBERED_QTY.finditer(text):
        ren = int(m.group("ren"))
        if ren in seen:
            continue
        qty = _parse_qty(m.group("qty"))
        product = _clean_product(m.group("product") or "")
        if qty is None or qty <= 0 or len(product) < 5:
            continue
        seen.add(ren)
        unit = (m.group("unit") or "u")[:16]
        items.append(
            {
                "line_no": ren,
                "product": product[:200],
                "qty": qty,
                "unit": unit or "u",
                "brand": _brand_from(product),
                "model": "",
                "specs": product[:400],
                "verification": "NO VERIFICADO",
                "source_pattern": "numbered_qty",
            }
        )
    items.sort(key=lambda x: x["line_no"])
    return items


def _from_item_label(text: str) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    seen: set[int] = set()
    for m in ITEM_LABEL.finditer(text):
        ren = int(m.group("ren"))
        if ren in seen:
            continue
        qty = _parse_qty(m.group("qty")) or 1.0
        product = _clean_product(m.group("product") or "")
        if len(product) < 5:
            continue
        seen.add(ren)
        items.append(
            {
                "line_no": ren,
                "product": product[:200],
                "qty": qty,
                "unit": (m.group("unit") or "u")[:16],
                "brand": _brand_from(product),
                "model": "",
                "specs": product[:400],
                "verification": "NO VERIFICADO",
                "source_pattern": "item_label",
            }
        )
    items.sort(key=lambda x: x["line_no"])
    return items


def _from_descripcion_annex(text: str) -> list[dict[str, Any]]:
    """Anexo descripción: renglón + producto; qty desconocida → no inventar (omitir o qty None→skip).

    Sin cantidad explícita no emitimos ítem (nunca inventamos qty=1).
    """
    if "descripcion" not in text.lower() and "descripción" not in text.lower():
        return []
    # Solo útil como specs complementarias; sin qty no devolvemos filas inventadas.
    return []
