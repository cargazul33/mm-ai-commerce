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
    r"(?P<product>[A-ZÁÉÍÓÚÑÜ][A-Za-zÁÉÍÓÚáéíóúÑñÜü0-9 /&\-\.\(\)\+:@°×,]{2,}(?:;.*)?)\s*$",
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
    r"TP-?LINK|TP\s*Link|GLC|Glc|ZOLODA|LYONN|Omada|Atomlux|Wi-?Tek|WiTek|"
    r"Meraki|Ericsson|PowerFiber|Katech)\b",
    re.I,
)

SKIP_LINE = re.compile(
    r"(?i)^(re\s+cant|cantidad de renglones|total cotizado|marca ofrecida|"
    r"mantenimiento de oferta|forma de pago|plazo de entrega|referencias|"
    r"safipro|página|pagina|cuit:|descripcion\s*$|pedido de presupuesto|"
    r"plieg-|ministerio de |naturales\s*$|sol\s+ofr|per cant)",
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
    # product name short; full text kept in specs elsewhere
    return text[:500]


def _model_from(text: str) -> str:
    """Pull commercial SKU from FULL pliego line (prefer Especificacion Adicional)."""
    from mm_commerce.matching import _extract_model_candidates

    models = _extract_model_candidates(text)
    return models[0] if models else ""


def _brand_from(text: str) -> str:
    # Prefer Marca Sugerida (mandatory commercial brand) over Marca Equipo noise
    m = re.search(
        r"marca\s+sugerida\s*:\s*(?!ninguna)(TP-?Link|Atomlux|GLC|Glc|Wi-?Tek|Cisco|Ericsson|Omada|Meraki|PowerFiber)",
        text,
        re.I,
    )
    if m:
        return m.group(1)
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
        found = _enrich_from_descripcion_annex(text, found)
        found = [_finalize_item(it) for it in found]
        return found[:max_items]

    found = _from_numbered(text)
    if found:
        return [_finalize_item(it) for it in found][:max_items]

    found = _from_item_label(text)
    if found:
        return [_finalize_item(it) for it in found][:max_items]

    # Anexo descripción sin qty — solo si hay renglones claros con ";" (SAFIPRO style)
    found = _from_descripcion_annex(text)
    return [_finalize_item(it) for it in found][:max_items]


def _finalize_item(it: dict[str, Any]) -> dict[str, Any]:
    """Attach RAW_SPEC / NORMALIZED_SPEC / HARD / SOFT — never truncate."""
    from mm_commerce.matching import build_line_spec, normalize_spec

    specs = (it.get("specs") or it.get("product") or "").strip()
    # Normalize FO- 4075 etc. inside stored specs
    specs = normalize_spec(specs)
    it["specs"] = specs
    line = build_line_spec(
        line_no=int(it.get("line_no") or 0),
        product=it.get("product") or "",
        specs=specs,
        brand=it.get("brand") or "",
        model=it.get("model") or "",
        qty=float(it.get("qty") or 1),
    )
    if not it.get("brand"):
        it["brand"] = line.brand
    if not it.get("model"):
        it["model"] = line.model
    it["RAW_SPEC"] = line.raw_spec
    it["NORMALIZED_SPEC"] = line.normalized_spec
    it["HARD_REQUIREMENTS"] = [__import__("dataclasses").asdict(r) for r in line.hard_requirements]
    it["SOFT_REQUIREMENTS"] = [__import__("dataclasses").asdict(r) for r in line.soft_requirements]
    it["product_type"] = line.product_type
    return it


def _enrich_from_descripcion_annex(text: str, items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Merge Descripcion annex continuations (e.g. Meraki license after page break) into rows."""
    if not items:
        return items
    m = re.search(r"(?is)\bDescripcion\b(.*)$", text)
    if not m:
        # Still try to attach license blob if present
        desc_section = ""
    else:
        desc_section = m.group(1)

    license_parts: list[str] = []
    for mlic in re.finditer(
        r"(?is)Se solicita la contrataci[oó]n de la licencia.{0,400}",
        text,
    ):
        license_parts.append(re.sub(r"\s+", " ", mlic.group(0)).strip())
    for mlic in re.finditer(r"(?is)\bLIC-MR-[A-Z]\b.{0,200}", text):
        license_parts.append(re.sub(r"\s+", " ", mlic.group(0)).strip())
    license_blob = " ".join(dict.fromkeys(license_parts))

    by_ren: dict[int, str] = {}
    if desc_section:
        row_re = re.compile(
            r"(?m)^\s*(?P<ren>\d{1,3})\s+"
            r"(?P<body>[A-ZÁÉÍÓÚÑÜ][\s\S]*?)(?=^\s*\d{1,3}\s+[A-ZÁÉÍÓÚÑÜ]|\Z)"
        )
        for mr in row_re.finditer(desc_section):
            ren = int(mr.group("ren"))
            body = re.sub(r"\s+", " ", mr.group("body")).strip()
            body = re.sub(r"(?i)tracto sucesivo:.*?(?=--|\Z)", " ", body)
            body = re.sub(r"-{5,}Detalle-{5,}", " ", body)
            body = re.sub(r"\s+", " ", body).strip()
            if len(body) > 20:
                by_ren[ren] = body

    max_ren = max(int(i["line_no"]) for i in items)
    out: list[dict[str, Any]] = []
    for it in items:
        ren = int(it["line_no"])
        specs = it.get("specs") or ""
        annex = by_ren.get(ren, "")
        merged = specs
        if annex:
            for token in ("LIC-MR", "licencia", "36 meses", "antenas", "inyector", "Essentials"):
                if token.lower() in annex.lower() and token.lower() not in merged.lower():
                    merged = f"{merged} | ANEXO: {annex}"
                    break
        if ren == max_ren and license_blob:
            if "lic-mr" in license_blob.lower() and "lic-mr" not in merged.lower():
                merged = f"{merged} | {license_blob}"
        it2 = dict(it)
        it2["specs"] = merged
        it2["brand"] = it2.get("brand") or _brand_from(merged)
        it2["model"] = it2.get("model") or _model_from(merged)
        out.append(it2)
    return out


def _flatten_safipro_blocks(text: str) -> str:
    """Une continuaciones indentadas de un renglón SAFIPRO en una sola línea lógica.

    Survives PDF page breaks (form-feed, headers) so 'Especificacion' + 'Adicional: FO-4075'
    stay on the same logical row.
    """
    # Normalize form-feeds / odd spaces
    text = text.replace("\x0c", "\n")
    lines = text.splitlines()
    out: list[str] = []
    buf = ""
    row_start = re.compile(r"^\s*\d{1,3}\s+\d+[.,]?\d*\s+[A-ZÁÉÍÓÚÑÜ]")
    page_noise = re.compile(
        r"(?i)^(página|pagina|plieg-|safipro|ministerio de |naturales\s*$|"
        r"re\s+cant|sol\s+ofr|per cant|ofr ofr|unitario|total\s*$)"
    )

    def _incomplete(b: str) -> bool:
        b = (b or "").rstrip()
        if not b:
            return False
        return bool(
            re.search(r"(?i)(especificaci[oó]n|adicional:?|marca sugerida:?|tipo\s*)$", b)
            or b.endswith("-")
            or b.endswith(";")
        )

    for raw in lines:
        line = raw.rstrip()
        stripped = line.strip()
        if not stripped:
            # Do NOT flush if current renglón looks incomplete (page break mid-spec)
            if buf and not _incomplete(buf):
                out.append(buf)
                buf = ""
            continue
        if SKIP_LINE.search(stripped) or page_noise.search(stripped) or re.match(r"(?i)^página\s+\d+", stripped):
            continue
        if row_start.match(line):
            if buf:
                out.append(buf)
            buf = stripped
            continue
        # Continuation: indented OR dangling 'Adicional:' after page break while buf incomplete
        cont = stripped
        if cont.startswith("$"):
            continue
        if cont.lower().startswith("marca ofrecida"):
            if buf:
                out.append(buf)
                buf = ""
            continue
        if buf and (
            line.startswith(" ")
            or line.startswith("\t")
            or line[:16].strip() == ""
            or _incomplete(buf)
            or cont.lower().startswith("adicional:")
        ):
            buf = f"{buf} {cont}"
            continue
        if buf:
            out.append(buf)
            buf = ""
        out.append(stripped)
    if buf:
        out.append(buf)
    return "\n".join(out)


def _from_safipro(text: str) -> list[dict[str, Any]]:
    items: dict[int, dict[str, Any]] = {}
    legalese = (
        "ley ", "decreto", "artículo", "articulo", "en caso", "el nombre",
        "se comunica", "saludo", "página", "pagina", "plazo de", "forma de pago",
        "mantenimiento de oferta", "cantidad de renglones",
    )
    for m in SAFIPRO_ROW.finditer(text):
        ren = int(m.group("ren"))
        qty = _parse_qty(m.group("qty"))
        product = _clean_product(m.group("product"))
        if qty is None or qty <= 0 or len(product) < 3:
            continue
        low = product.lower()
        if any(low.startswith(x) for x in legalese):
            continue
        has_semi = ";" in product
        # Prefer SAFIPRO catalog rows (NAME; specs…) over prose false positives
        cand = {
            "line_no": ren,
            "product": product.split(";")[0].strip()[:200],
            "qty": qty,
            "unit": "u",
            "brand": _brand_from(product),
            "model": _model_from(product),
            "specs": product,  # FULL pliego spec — no truncate
            "verification": "NO VERIFICADO",
            "source_pattern": "safipro_row",
            "_has_semi": has_semi,
        }
        prev = items.get(ren)
        if prev is None:
            items[ren] = cand
        elif has_semi and not prev.get("_has_semi"):
            items[ren] = cand
    out = []
    for it in sorted(items.values(), key=lambda x: x["line_no"]):
        it.pop("_has_semi", None)
        out.append(it)
    return out


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
                "model": _model_from(product),
                "specs": product,
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
                "model": _model_from(product),
                "specs": product,
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
