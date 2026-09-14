"""Hard attribute matching — TECHNICAL status separate from COMMERCIAL status.

TECHNICAL_STATUS: EXACTO | EQUIVALENTE_PERMITIDO | POSIBLE | NO_VERIFICADO | NO_CUMPLE
COMMERCIAL_STATUS: DISPONIBLE | STOCK_INSUFICIENTE | SIN_STOCK | PRECIO_NO_VERIFICADO
                   | ENVIO_NO_VERIFICADO | NO_DISPONIBLE

Rules:
- PRODUCT_TYPE identity FIRST — wrong principal object → NO_CUMPLE, MATCH max 20%
- Ban keyword-only 100% — evidence_matrix per attr: ATTR|REQUIRED|FOUND|SOURCE|RESULT
- EXACTO 100% only if same type + ALL hard attrs proven + no contradiction
- Never mix technical class with stock/price/shipping
"""
from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from typing import Any

# --- TECHNICAL ---------------------------------------------------------------
TECH_EXACTO = "EXACTO"
TECH_EQUIV = "EQUIVALENTE_PERMITIDO"
TECH_POSIBLE = "POSIBLE"
TECH_NO_VER = "NO_VERIFICADO"
TECH_NO_CUMPLE = "NO_CUMPLE"

# Legacy aliases (spaces) used in older DB rows / tests
CLASS_EXACTO = TECH_EXACTO
CLASS_EQUIV = "EQUIVALENTE PERMITIDO"  # display form still accepted
CLASS_POSIBLE = TECH_POSIBLE
CLASS_NO_VER = "NO VERIFICADO"
CLASS_NO_CUMPLE = "NO CUMPLE"

VERIFIED_TECH = frozenset({TECH_EXACTO, TECH_EQUIV, "EQUIVALENTE PERMITIDO"})
BLOCKING_TECH = frozenset(
    {TECH_NO_CUMPLE, TECH_NO_VER, "NO CUMPLE", "NO VERIFICADO", TECH_POSIBLE}
)

# --- COMMERCIAL --------------------------------------------------------------
COMM_DISPONIBLE = "DISPONIBLE"
COMM_STOCK_INSUF = "STOCK_INSUFICIENTE"
COMM_SIN_STOCK = "SIN_STOCK"
COMM_PRECIO_NO_VER = "PRECIO_NO_VERIFICADO"
COMM_ENVIO_NO_VER = "ENVIO_NO_VERIFICADO"
COMM_NO_DISP = "NO_DISPONIBLE"

# Product types (principal object)
TYPE_OLT = "OLT_GPON"
TYPE_UPS = "UPS"
TYPE_ODF = "ODF"
TYPE_SPLITTER = "SPLITTER"
TYPE_AP_INDOOR = "AP_INDOOR"
TYPE_AP_OUTDOOR = "AP_OUTDOOR"
TYPE_NAP_BOX = "CAJA_NAP"
TYPE_NAP_SUPPORT = "SOPORTE_NAP"
TYPE_SWITCH = "SWITCH_ETH"
TYPE_ROUTER = "ROUTER"
TYPE_UNKNOWN = "UNKNOWN"

TYPE_LABELS = {
    TYPE_OLT: "OLT GPON",
    TYPE_UPS: "UPS/Estabilizador",
    TYPE_ODF: "ODF / Caja empalme",
    TYPE_SPLITTER: "Splitter PLC",
    TYPE_AP_INDOOR: "Access Point interior",
    TYPE_AP_OUTDOOR: "Access Point outdoor",
    TYPE_NAP_BOX: "Caja NAP con splitter",
    TYPE_NAP_SUPPORT: "Soporte/fijación NAP",
    TYPE_SWITCH: "Switch Ethernet",
    TYPE_ROUTER: "Router genérico",
    TYPE_UNKNOWN: "desconocido",
}

# Backward-compat category aliases used by seeds
CATEGORY_OLT = "olt_gpon"
CATEGORY_UPS = "ups_estabilizador"
CATEGORY_ODF = "odf_caja_empalme"
CATEGORY_SPLITTER = "splitter_plc"
CATEGORY_AP_INDOOR = "access_point_indoor"
CATEGORY_NAP_BOX = "caja_nap"
CATEGORY_NAP_SUPPORT = "soporte_nap"
CATEGORY_AP_OUTDOOR = "access_point_outdoor"
CATEGORY_SWITCH = "switch_lan"
CATEGORY_ROUTER = "router"
CATEGORY_UNKNOWN = "unknown"

CATEGORY_LABELS = {
    CATEGORY_OLT: TYPE_LABELS[TYPE_OLT],
    CATEGORY_UPS: TYPE_LABELS[TYPE_UPS],
    CATEGORY_ODF: TYPE_LABELS[TYPE_ODF],
    CATEGORY_SPLITTER: TYPE_LABELS[TYPE_SPLITTER],
    CATEGORY_AP_INDOOR: TYPE_LABELS[TYPE_AP_INDOOR],
    CATEGORY_NAP_BOX: TYPE_LABELS[TYPE_NAP_BOX],
    CATEGORY_NAP_SUPPORT: TYPE_LABELS[TYPE_NAP_SUPPORT],
    CATEGORY_AP_OUTDOOR: TYPE_LABELS[TYPE_AP_OUTDOOR],
    CATEGORY_SWITCH: TYPE_LABELS[TYPE_SWITCH],
    CATEGORY_ROUTER: TYPE_LABELS[TYPE_ROUTER],
    CATEGORY_UNKNOWN: TYPE_LABELS[TYPE_UNKNOWN],
}

_TYPE_TO_CAT = {
    TYPE_OLT: CATEGORY_OLT,
    TYPE_UPS: CATEGORY_UPS,
    TYPE_ODF: CATEGORY_ODF,
    TYPE_SPLITTER: CATEGORY_SPLITTER,
    TYPE_AP_INDOOR: CATEGORY_AP_INDOOR,
    TYPE_NAP_BOX: CATEGORY_NAP_BOX,
    TYPE_NAP_SUPPORT: CATEGORY_NAP_SUPPORT,
    TYPE_AP_OUTDOOR: CATEGORY_AP_OUTDOOR,
    TYPE_SWITCH: CATEGORY_SWITCH,
    TYPE_ROUTER: CATEGORY_ROUTER,
    TYPE_UNKNOWN: CATEGORY_UNKNOWN,
}

VERIFIED_CLASSES = VERIFIED_TECH
BLOCKING_CLASSES = BLOCKING_TECH


@dataclass
class HardRequirement:
    key: str
    label: str
    required: str
    mandatory: bool = True
    kind: str = "text"
    numeric_value: float | None = None
    numeric_unit: str = ""
    aliases: list[str] = field(default_factory=list)


@dataclass
class AttrEvidence:
    key: str
    label: str
    required: str
    found: str
    source_url: str
    result: str  # CUMPLE | NO_CUMPLE | NO_VERIFICADO | N/A
    mandatory: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def matrix_row(self) -> str:
        return f"{self.label}|{self.required}|{self.found}|{self.source_url}|{self.result}"


@dataclass
class MatchResult:
    match_pct: int
    match_class: str  # TECHNICAL_STATUS
    technical_status: str = ""
    commercial_status: str = COMM_PRECIO_NO_VER
    product_type_need: str = TYPE_UNKNOWN
    product_type_found: str = TYPE_UNKNOWN
    category_need: str = ""
    category_found: str = ""
    evidence: list[AttrEvidence] = field(default_factory=list)
    evidence_matrix: list[str] = field(default_factory=list)
    blockers: list[str] = field(default_factory=list)
    notes: str = ""
    evidence_ok: int = 0
    evidence_total: int = 0

    def __post_init__(self) -> None:
        if not self.technical_status:
            self.technical_status = self.match_class.replace(" ", "_") if " " in self.match_class else self.match_class
        if not self.evidence_matrix and self.evidence:
            self.evidence_matrix = [e.matrix_row() for e in self.evidence]
        mand = [e for e in self.evidence if e.mandatory]
        if mand and not self.evidence_total:
            self.evidence_total = len(mand)
            self.evidence_ok = sum(1 for e in mand if e.result == "CUMPLE")

    def to_dict(self) -> dict[str, Any]:
        return {
            "match_pct": self.match_pct,
            "match_class": self.match_class,
            "technical_status": self.technical_status,
            "commercial_status": self.commercial_status,
            "product_type_need": self.product_type_need,
            "product_type_found": self.product_type_found,
            "category_need": self.category_need or _TYPE_TO_CAT.get(self.product_type_need, ""),
            "category_found": self.category_found or _TYPE_TO_CAT.get(self.product_type_found, ""),
            "evidence": [e.to_dict() for e in self.evidence],
            "evidence_matrix": self.evidence_matrix or [e.matrix_row() for e in self.evidence],
            "evidence_ok": self.evidence_ok,
            "evidence_total": self.evidence_total,
            "blockers": list(self.blockers),
            "notes": self.notes,
        }

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False)


def full_spec_blob(product: str = "", specs: str = "", brand: str = "", model: str = "") -> str:
    parts = [product or "", specs or "", brand or "", model or ""]
    blob = " ".join(p for p in parts if p).strip()
    return re.sub(r"\s+", " ", blob).strip()


def infer_product_type(text: str, *, title_hint: str = "") -> str:
    """Principal object identity — TITLE wins for soporte vs caja."""
    title = (title_hint or "").lower().strip()
    t = (text or "").lower()

    # 1) Title: soporte/fijación NAP is NEVER the NAP box
    if title and re.search(r"soporte|fijaci[oó]n|mount|bracket", title):
        if "nap" in title or "caja" in title:
            if not re.search(r"caja\s+nap\s+(con|interior|fttb)|glc-fdb|fdb-\d", title):
                return TYPE_NAP_SUPPORT

    # 1b) Title-first strong product identities (ignore footer/nav keyword noise)
    if title:
        if re.search(r"\b(plc\s+)?splitter\b|fo-?\s*4075|1\s*[x×]\s*16", title):
            return TYPE_SPLITTER
        if re.search(r"\bodf\b|caja\s+de\s+empalme", title):
            return TYPE_ODF
        if re.search(r"\bolt\b|gpon|ds-p7001|deltastream", title):
            return TYPE_OLT
        if re.search(r"\bups\b|estabilizador", title) or re.search(r"\d{3,5}\s*va", title):
            return TYPE_UPS
        if re.search(r"caja\s+nap|glc-fdb|fdb-\d", title) and not re.search(r"soporte|fijaci", title):
            return TYPE_NAP_BOX
        if re.search(r"access\s+point|punto\s+de\s+acceso|wi-ap|9163e", title):
            if re.search(r"outdoor|exterior|6e|meraki", title):
                return TYPE_AP_OUTDOOR
            return TYPE_AP_INDOOR

    # 2) Strong NAP BOX identity (must be the box itself)
    if re.search(r"caja\s+nap", t) and (
        re.search(r"splitter|1\s*[x×]\s*8|fdb|fttb|ftth", t)
    ):
        # If title is support, already returned
        if not (title and re.search(r"soporte|fijaci", title)):
            return TYPE_NAP_BOX
    if re.search(r"glc-fdb|fdb-\d{2,}", t) and not (
        title and re.search(r"soporte|fijaci", title)
    ):
        return TYPE_NAP_BOX

    # 3) Support in body without being the box SKU as principal
    if re.search(r"soporte|fijaci[oó]n", t) and ("nap" in t or "caja" in t):
        if not re.search(r"caja\s+nap\s+(con\s+splitter|interior)|glc-fdb-\d", t):
            return TYPE_NAP_SUPPORT
        # mixed page — if support words appear early in title area, support
        if title and re.search(r"soporte|fijaci", title):
            return TYPE_NAP_SUPPORT

    # 4) ODF vs splitter — principal object
    if any(k in t for k in ("odf", "caja de empalme", "caja empalme", "distribuidor óptico", "distribuidor optico")):
        return TYPE_ODF
    if any(k in t for k in ("plc splitter", "splitter", "fo-4075", "1x16", "1 x 16")):
        # ftth+splitter alone is still SPLITTER, not NAP
        if re.search(r"caja\s+nap|glc-fdb|fdb-\d", t):
            return TYPE_NAP_BOX
        return TYPE_SPLITTER

    # 5) OLT GPON — not plain ethernet switch
    if any(k in t for k in ("olt", "gpon", "deltastream", "ds-p7001", "módulos pon", "modulos pon")):
        return TYPE_OLT
    if "switch" in t and ("pon" in t or "gpon" in t or "olt" in t):
        return TYPE_OLT

    # 6) UPS
    if any(k in t for k in ("ups", "estabilizador")) or re.search(r"\b\d{3,5}\s*va\b", t):
        return TYPE_UPS

    # 7) AP outdoor / indoor — not generic router
    if any(k in t for k in ("outdoor", "exterior", "wifi 6e", "wi-fi 6e", "meraki", "9163e", "catalyst 9163")):
        if any(k in t for k in ("access point", "punto de acceso", " ap", "wifi", "wi-fi", "router")):
            return TYPE_AP_OUTDOOR
    if any(k in t for k in ("access point", "punto de acceso", "wi-ap", "ap interior", "ap indoor")):
        return TYPE_AP_INDOOR
    if ("interior" in t or "indoor" in t or "2.4" in t) and any(
        k in t for k in ("wifi", "wi-fi", "inalámbr", "inalambr", "access point", "router")
    ):
        # Pliego lists ROUTER; Tipo Access point interior → AP
        if "access point" in t or "punto de acceso" in t or "wi-ap" in t or "ap217" in t:
            return TYPE_AP_INDOOR

    if "switch" in t and "olt" not in t and "gpon" not in t:
        return TYPE_SWITCH
    if "router" in t:
        # Generic router only if not AP
        if "access point" in t or "punto de acceso" in t:
            return TYPE_AP_OUTDOOR if "outdoor" in t or "exterior" in t else TYPE_AP_INDOOR
        return TYPE_ROUTER
    return TYPE_UNKNOWN


def infer_category(text: str, *, title_hint: str = "") -> str:
    """Legacy category string for seeds — maps from product type."""
    return _TYPE_TO_CAT.get(infer_product_type(text, title_hint=title_hint), CATEGORY_UNKNOWN)


def _extract_va(text: str) -> float | None:
    t = text.lower().replace(",", ".")
    for pat in (
        r"potencia\s*[:=]?\s*(\d{3,5})\s*v\.?\s*a\.?\b",
        r"\b(\d{3,5})\s*v\.?\s*a\.?\b",
        r"ups\s*(\d{3,5})\b",
        r"ups(\d{3,5})\b",
    ):
        m = re.search(pat, t, re.I)
        if m:
            try:
                return float(m.group(1))
            except ValueError:
                continue
    return None


def _extract_ports(text: str, keywords: tuple[str, ...] = ()) -> float | None:
    t = text.lower()
    for kw in keywords:
        m = re.search(rf"{kw}[^0-9]{{0,24}}(\d{{1,3}})\s*puertos?", t)
        if m:
            return float(m.group(1))
    m = re.search(r"odf\s*(\d{1,3})\s*puertos?", t)
    if m:
        return float(m.group(1))
    m = re.search(r"\b(\d{1,3})\s*puertos?\b", t)
    if m:
        return float(m.group(1))
    return None


def _extract_ratio(text: str) -> str | None:
    m = re.search(r"\b1\s*[x×]\s*(\d{1,2})\b", text, re.I)
    return f"1x{m.group(1)}" if m else None


def _extract_model_candidates(text: str) -> list[str]:
    models: list[str] = []
    adicional = ""
    m_ad = re.search(r"especificaci[oó]n\s+adicional\s*:\s*(.+)$", text, re.I | re.S)
    if m_ad:
        adicional = m_ad.group(1)
    primary = [
        r"\b(DS-P7001-04)\b",
        r"\b(UPS\s?3500|UPS3500)\b",
        r"\b(FO-?\s*4075)\b",
        r"\b(GLC-?FDB-?\s*012-?\s*01)\b",
        r"\b(WI-?AP217(?:-Lite)?)\b",
        r"\b(9163E)\b",
        r"\b(Catalyst\s*9163E?)\b",
        r"Atomlux-modelo:\s*([A-Z0-9@\-]+)",
    ]
    for chunk in ([adicional, text] if adicional else [text]):
        for pat in primary:
            for m in re.finditer(pat, chunk, re.I):
                val = re.sub(r"\s+", "", m.group(1)).upper().rstrip("@")
                if val and val not in models:
                    models.append(val)
    if not models:
        for m in re.finditer(
            r"modelo\s*(?:equipo)?\s*[:=]?\s*([A-Z0-9][A-Z0-9\-_/]{3,})", text, re.I
        ):
            val = re.sub(r"\s+", "", m.group(1)).upper().rstrip("@")
            if val.startswith("MINI"):
                continue
            if val and val not in models:
                models.append(val)
    return models


def _extract_brand(text: str) -> str:
    m = re.search(
        r"(?:marca\s*(?:sugerida|equipo)?\s*[:=]?\s*)"
        r"(TP-?Link|Atomlux|GLC|Glc|Wi-?Tek|Cisco|Ericsson|Omada|PowerFiber)",
        text,
        re.I,
    )
    if m:
        return m.group(1)
    m = re.search(r"\b(TP-?Link|Atomlux|GLC|Wi-?Tek|Cisco|Ericsson|Omada)\b", text, re.I)
    return m.group(1) if m else ""


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", (s or "").lower())


def extract_hard_requirements(
    product: str = "",
    specs: str = "",
    brand: str = "",
    model: str = "",
) -> list[HardRequirement]:
    blob = full_spec_blob(product, specs, brand, model)
    low = blob.lower()
    ptype = infer_product_type(blob)
    reqs: list[HardRequirement] = [
        HardRequirement(
            key="product_type",
            label="PRODUCT_TYPE",
            required=TYPE_LABELS.get(ptype, ptype),
            mandatory=True,
            kind="product_type",
            aliases=[ptype, _TYPE_TO_CAT.get(ptype, "")],
        )
    ]

    br = brand or _extract_brand(blob)
    brand_mandatory = bool(
        re.search(r"marca\s+sugerida\s*:\s*(?!ninguna)", blob, re.I)
        or re.search(r"marca\s+equipo\s*:", blob, re.I)
    )
    if br and "ninguna" not in br.lower():
        reqs.append(
            HardRequirement(
                key="brand",
                label="marca",
                required=br,
                mandatory=brand_mandatory,
                kind="brand",
                aliases=[br.lower(), br.lower().replace("-", ""), "tp-link" if "link" in br.lower() else br.lower()],
            )
        )

    models = _extract_model_candidates(blob)
    if model:
        models.insert(0, model)
    seen: set[str] = set()
    uniq: list[str] = []
    for m in models:
        k = re.sub(r"[^A-Z0-9]", "", m.upper())
        if k and k not in seen:
            seen.add(k)
            uniq.append(m)
    if uniq:
        model_mandatory = ptype in (
            TYPE_OLT, TYPE_UPS, TYPE_NAP_BOX, TYPE_SPLITTER, TYPE_AP_INDOOR, TYPE_AP_OUTDOOR
        )
        reqs.append(
            HardRequirement(
                key="model",
                label="modelo",
                required="|".join(uniq[:4]),
                mandatory=model_mandatory,
                kind="model",
                aliases=uniq,
            )
        )

    if ptype == TYPE_UPS:
        va = _extract_va(blob)
        if va:
            reqs.append(
                HardRequirement(
                    key="capacity_va",
                    label="potencia_VA",
                    required=f"{int(va)} VA",
                    mandatory=True,
                    kind="numeric_min",
                    numeric_value=va,
                    numeric_unit="VA",
                )
            )
        if "220" in low:
            reqs.append(
                HardRequirement(
                    key="voltage",
                    label="tension",
                    required="220 Vac",
                    mandatory=True,
                    kind="text",
                    aliases=["220", "220v", "220 vac", "220v ca"],
                )
            )

    if ptype == TYPE_OLT:
        reqs.append(
            HardRequirement(
                key="tech_gpon",
                label="tecnologia",
                required="GPON/OLT",
                mandatory=True,
                kind="text",
                aliases=["gpon", "olt", "pon"],
            )
        )
        reqs.append(
            HardRequirement(
                key="pon_ports",
                label="puertos_PON",
                required="4 puertos PON",
                mandatory=True,
                kind="numeric_min",
                numeric_value=4.0,
                numeric_unit="PON",
                aliases=["4 puertos", "4 módulos pon", "4 modulos pon", "gpon 4", "4p"],
            )
        )
        # Uplink / 10G from pliego: 2 Puertos Gb + 1 Puerto Gb Uplink / 2P 10G
        if "10g" in low or "10 g" in low or "uplink" in low:
            reqs.append(
                HardRequirement(
                    key="uplink",
                    label="uplink",
                    required="uplink Gb/10G",
                    mandatory=True,
                    kind="text",
                    aliases=["uplink", "10g", "10 g", "giga uplink", "1p giga"],
                )
            )
        if "2 puertos gb" in low or "2p 10g" in low or "2p" in low:
            reqs.append(
                HardRequirement(
                    key="sfp_or_10g",
                    label="puertos_10G_o_Gb",
                    required="2x Gb/10G",
                    mandatory=False,
                    kind="text",
                    aliases=["2p 10g", "10g", "2 puertos", "2p"],
                )
            )

    if ptype == TYPE_ODF:
        ports = _extract_ports(blob, ("odf", "acopladores", "puertos"))
        if ports:
            reqs.append(
                HardRequirement(
                    key="ports",
                    label="puertos_ODF",
                    required=f"{int(ports)} puertos",
                    mandatory=True,
                    kind="numeric_min",
                    numeric_value=ports,
                    numeric_unit="puertos",
                )
            )
        if "sc/apc" in low or "scapc" in low.replace("/", ""):
            reqs.append(
                HardRequirement(
                    key="connector",
                    label="conectores",
                    required="SC/APC",
                    mandatory=True,
                    kind="text",
                    aliases=["sc/apc", "sc-apc", "scapc"],
                )
            )
        reqs.append(
            HardRequirement(
                key="odf_type",
                label="tipo_ODF",
                required="ODF / caja de empalme",
                mandatory=True,
                kind="text",
                aliases=["odf", "caja de empalme", "empalme", "fusiones"],
            )
        )

    if ptype == TYPE_SPLITTER:
        ratio = _extract_ratio(blob)
        if ratio:
            reqs.append(
                HardRequirement(
                    key="split_ratio",
                    label="relacion_splitter",
                    required=ratio,
                    mandatory=True,
                    kind="text",
                    aliases=[ratio, ratio.lower(), ratio.replace("x", " x ")],
                )
            )
        if "sc/apc" in low:
            reqs.append(
                HardRequirement(
                    key="connector",
                    label="conectores",
                    required="SC/APC",
                    mandatory=True,
                    kind="text",
                    aliases=["sc/apc", "scapc"],
                )
            )

    if ptype == TYPE_NAP_BOX:
        ratio = _extract_ratio(blob) or "1x8"
        reqs.append(
            HardRequirement(
                key="nap_identity",
                label="identidad_caja_NAP",
                required=f"caja NAP + splitter {ratio}",
                mandatory=True,
                kind="text",
                aliases=["caja nap", "glc-fdb", "fdb", ratio],
            )
        )
        reqs.append(
            HardRequirement(
                key="not_support_only",
                label="no_soporte_solo",
                required="producto = caja NAP (no soporte/fijación)",
                mandatory=True,
                kind="not_support",
                aliases=["caja nap", "fdb", "fttb"],
            )
        )

    if ptype == TYPE_AP_INDOOR:
        reqs.append(
            HardRequirement(
                key="indoor",
                label="indoor_outdoor",
                required="interior/indoor",
                mandatory=True,
                kind="text",
                aliases=["interior", "indoor", "lite"],
            )
        )
        reqs.append(
            HardRequirement(
                key="not_generic_router",
                label="no_router_generico",
                required="Access Point (no router genérico)",
                mandatory=True,
                kind="text",
                aliases=["access point", "punto de acceso", "wi-ap", "ap217", "ap "],
            )
        )

    if ptype == TYPE_AP_OUTDOOR:
        reqs.append(
            HardRequirement(
                key="outdoor",
                label="indoor_outdoor",
                required="outdoor/exterior",
                mandatory=True,
                kind="text",
                aliases=["outdoor", "exterior", "exteriores"],
            )
        )
        if "meraki" in low or "nube" in low or "cloud" in low:
            reqs.append(
                HardRequirement(
                    key="mgmt_cloud",
                    label="gestion_cloud",
                    required="Meraki / nube",
                    mandatory=True,
                    kind="text",
                    aliases=["meraki", "nube", "cloud"],
                )
            )
        if "6e" in low or "wifi 6" in low or "wi-fi 6" in low:
            reqs.append(
                HardRequirement(
                    key="wifi_tech",
                    label="tecnologia_wifi",
                    required="Wi-Fi 6/6E",
                    mandatory=True,
                    kind="text",
                    aliases=["wifi 6", "wi-fi 6", "6e", "802.11ax"],
                )
            )

    return reqs


def classify_commercial(
    *,
    price: float | None = None,
    stock: str = "",
    shipping_neuquen: str = "",
    qty_needed: float = 1.0,
) -> str:
    """COMMERCIAL_STATUS — never mixed into technical class."""
    stock_s = str(stock or "").strip()
    ship = str(shipping_neuquen or "").strip()
    if price is None:
        return COMM_PRECIO_NO_VER
    if stock_s in ("0", "OutOfStock", "sin stock") or stock_s.lower() == "sin stock":
        return COMM_SIN_STOCK
    # numeric stock
    try:
        n = float(re.sub(r"[^\d.]", "", stock_s) or "nan")
        if n == 0:
            return COMM_SIN_STOCK
        if n < qty_needed:
            return COMM_STOCK_INSUF
    except ValueError:
        pass
    if not ship or ship.upper() in ("NO VERIFICADO", "NO_VERIFICADO", ""):
        # price+stock ok but envío unknown
        if stock_s and stock_s.upper() not in ("NO VERIFICADO", "NO_VERIFICADO", ""):
            return COMM_ENVIO_NO_VER
        return COMM_ENVIO_NO_VER
    if stock_s.upper() in ("NO VERIFICADO", "NO_VERIFICADO", ""):
        return COMM_STOCK_INSUF
    return COMM_DISPONIBLE


def evaluate_match(
    requirements: list[HardRequirement],
    *,
    candidate_title: str = "",
    candidate_text: str = "",
    source_url: str = "",
    need_blob: str = "",
    price: float | None = None,
    stock: str = "",
    shipping_neuquen: str = "",
    qty_needed: float = 1.0,
) -> MatchResult:
    found_blob = f"{candidate_title} {candidate_text}".strip()
    found_low = found_blob.lower()
    need_type = infer_product_type(need_blob) if need_blob else TYPE_UNKNOWN
    if need_type == TYPE_UNKNOWN and requirements:
        for r in requirements:
            if r.key == "product_type" and r.aliases:
                need_type = r.aliases[0]
                break
    found_type = infer_product_type(found_blob, title_hint=candidate_title)
    commercial = classify_commercial(
        price=price, stock=stock, shipping_neuquen=shipping_neuquen, qty_needed=qty_needed
    )

    evidence: list[AttrEvidence] = []
    blockers: list[str] = []
    type_mismatch = (
        need_type != TYPE_UNKNOWN
        and found_type != TYPE_UNKNOWN
        and need_type != found_type
    )

    # Explicit forbidden pairs
    forbidden = {
        (TYPE_NAP_BOX, TYPE_NAP_SUPPORT),
        (TYPE_NAP_SUPPORT, TYPE_NAP_BOX),
        (TYPE_ODF, TYPE_SPLITTER),
        (TYPE_SPLITTER, TYPE_ODF),
        (TYPE_OLT, TYPE_SWITCH),
        (TYPE_AP_INDOOR, TYPE_ROUTER),
        (TYPE_AP_OUTDOOR, TYPE_ROUTER),
        (TYPE_UPS, TYPE_UNKNOWN),  # not used as auto
    }
    if (need_type, found_type) in forbidden:
        type_mismatch = True
        blockers.append(f"PRODUCT_TYPE_MISMATCH:{need_type}->{found_type}")

    if not found_blob.strip():
        # still emit product_type evidence
        for req in requirements:
            if req.key == "product_type":
                evidence.append(
                    AttrEvidence(
                        key=req.key,
                        label=req.label,
                        required=req.required,
                        found="sin evidencia",
                        source_url=source_url,
                        result="NO_VERIFICADO",
                        mandatory=True,
                    )
                )
        return MatchResult(
            match_pct=0,
            match_class=TECH_NO_VER,
            technical_status=TECH_NO_VER,
            commercial_status=commercial,
            product_type_need=need_type,
            product_type_found=found_type,
            category_need=_TYPE_TO_CAT.get(need_type, ""),
            category_found=_TYPE_TO_CAT.get(found_type, ""),
            evidence=evidence,
            blockers=["SIN_EVIDENCIA"],
            notes="sin texto de proveedor",
        )

    # --- PRODUCT_TYPE FIRST ---
    for req in requirements:
        if req.key != "product_type":
            continue
        if type_mismatch or (found_type != need_type and found_type != TYPE_UNKNOWN and need_type != TYPE_UNKNOWN):
            evidence.append(
                AttrEvidence(
                    key="product_type",
                    label="PRODUCT_TYPE",
                    required=TYPE_LABELS.get(need_type, need_type),
                    found=TYPE_LABELS.get(found_type, found_type),
                    source_url=source_url,
                    result="NO_CUMPLE",
                    mandatory=True,
                )
            )
            blockers.append(f"WRONG_PRODUCT_TYPE:{need_type}->{found_type}")
        elif found_type == TYPE_UNKNOWN:
            evidence.append(
                AttrEvidence(
                    key="product_type",
                    label="PRODUCT_TYPE",
                    required=TYPE_LABELS.get(need_type, need_type),
                    found="no determinado",
                    source_url=source_url,
                    result="NO_VERIFICADO",
                    mandatory=True,
                )
            )
            blockers.append("PRODUCT_TYPE_UNVERIFIED")
        else:
            evidence.append(
                AttrEvidence(
                    key="product_type",
                    label="PRODUCT_TYPE",
                    required=TYPE_LABELS.get(need_type, need_type),
                    found=TYPE_LABELS.get(found_type, found_type),
                    source_url=source_url,
                    result="CUMPLE",
                    mandatory=True,
                )
            )

    # If wrong principal object → hard stop at max 20%, NO_CUMPLE (do not award EXACTO)
    if type_mismatch or any(
        e.key == "product_type" and e.result == "NO_CUMPLE" for e in evidence
    ):
        # still fill remaining attrs for matrix transparency
        for req in requirements:
            if req.key == "product_type":
                continue
            evidence.append(
                AttrEvidence(
                    key=req.key,
                    label=req.label,
                    required=req.required,
                    found="N/A (tipo incorrecto)",
                    source_url=source_url,
                    result="NO_CUMPLE" if req.mandatory else "N/A",
                    mandatory=req.mandatory,
                )
            )
        token_need = set(w for w in (need_blob or "").lower().split() if len(w) > 2)
        token_found = set(w for w in found_low.split() if len(w) > 2)
        overlap = int(100 * len(token_need & token_found) / len(token_need)) if token_need else 0
        return MatchResult(
            match_pct=min(20, overlap),
            match_class=TECH_NO_CUMPLE,
            technical_status=TECH_NO_CUMPLE,
            commercial_status=commercial,
            product_type_need=need_type,
            product_type_found=found_type,
            category_need=_TYPE_TO_CAT.get(need_type, ""),
            category_found=_TYPE_TO_CAT.get(found_type, ""),
            evidence=evidence,
            blockers=blockers,
            notes="objeto principal distinto — MATCH max 20%",
        )

    has_contradiction = False
    has_missing = False
    mandatory_ok = 0
    mandatory_total = 0

    for req in requirements:
        if req.key == "product_type":
            # already handled
            pt = next(e for e in evidence if e.key == "product_type")
            mandatory_total += 1
            if pt.result == "CUMPLE":
                mandatory_ok += 1
            elif pt.result == "NO_VERIFICADO":
                has_missing = True
            continue

        if req.kind == "not_support":
            is_support = found_type == TYPE_NAP_SUPPORT or (
                re.search(r"soporte|fijaci[oó]n", (candidate_title or "").lower())
                and not re.search(r"caja\s+nap|glc-fdb|fdb-\d", (candidate_title or "").lower())
            )
            if is_support:
                evidence.append(
                    AttrEvidence(
                        key=req.key,
                        label=req.label,
                        required=req.required,
                        found="soporte/fijación (no caja NAP)",
                        source_url=source_url,
                        result="NO_CUMPLE",
                        mandatory=True,
                    )
                )
                has_contradiction = True
                blockers.append("NAP_SUPPORT_NOT_PRODUCT")
                mandatory_total += 1
            elif any(a.lower() in found_low for a in req.aliases):
                evidence.append(
                    AttrEvidence(
                        key=req.key,
                        label=req.label,
                        required=req.required,
                        found="caja NAP evidenciada en título/página",
                        source_url=source_url,
                        result="CUMPLE",
                        mandatory=True,
                    )
                )
                mandatory_total += 1
                mandatory_ok += 1
            else:
                evidence.append(
                    AttrEvidence(
                        key=req.key,
                        label=req.label,
                        required=req.required,
                        found="no verificado",
                        source_url=source_url,
                        result="NO_VERIFICADO",
                        mandatory=True,
                    )
                )
                mandatory_total += 1
                has_missing = True
            continue

        if req.kind == "numeric_min" and req.numeric_value is not None:
            found_val = None
            if req.numeric_unit.upper() == "VA":
                found_val = _extract_va(found_blob)
            elif req.numeric_unit.upper() == "PON":
                m = re.search(
                    r"(\d+)\s*(?:puertos?|m[oó]dulos?)\s*pon|gpon\s*(\d+)|(\d+)\s*p\b|4\s*puertos",
                    found_low,
                )
                if m:
                    g = next((x for x in m.groups() if x), None)
                    found_val = float(g) if g else 4.0
                elif "4" in found_low and "pon" in found_low:
                    found_val = 4.0
            elif req.numeric_unit == "puertos":
                found_val = _extract_ports(found_blob, ("odf", "puertos", "acopladores"))
            if found_val is None and any(a.lower() in found_low for a in req.aliases):
                found_val = req.numeric_value
            if found_val is None:
                evidence.append(
                    AttrEvidence(
                        key=req.key,
                        label=req.label,
                        required=req.required,
                        found="no encontrado",
                        source_url=source_url,
                        result="NO_VERIFICADO",
                        mandatory=req.mandatory,
                    )
                )
                if req.mandatory:
                    mandatory_total += 1
                    has_missing = True
                continue
            if found_val + 1e-9 < req.numeric_value:
                evidence.append(
                    AttrEvidence(
                        key=req.key,
                        label=req.label,
                        required=req.required,
                        found=f"{found_val:g} {req.numeric_unit}".strip(),
                        source_url=source_url,
                        result="NO_CUMPLE",
                        mandatory=req.mandatory,
                    )
                )
                has_contradiction = True
                blockers.append(
                    f"LOWER_CAPACITY:{req.key}:{found_val}<{req.numeric_value}"
                )
                if req.mandatory:
                    mandatory_total += 1
            else:
                evidence.append(
                    AttrEvidence(
                        key=req.key,
                        label=req.label,
                        required=req.required,
                        found=f"{found_val:g} {req.numeric_unit}".strip(),
                        source_url=source_url,
                        result="CUMPLE",
                        mandatory=req.mandatory,
                    )
                )
                if req.mandatory:
                    mandatory_total += 1
                    mandatory_ok += 1
            continue

        if req.kind == "model":
            hit = None
            for a in req.aliases or [req.required]:
                if _norm(a) and _norm(a) in _norm(found_blob):
                    hit = a
                    break
            if hit:
                evidence.append(
                    AttrEvidence(
                        key=req.key,
                        label=req.label,
                        required=req.required,
                        found=hit,
                        source_url=source_url,
                        result="CUMPLE",
                        mandatory=req.mandatory,
                    )
                )
                if req.mandatory:
                    mandatory_total += 1
                    mandatory_ok += 1
            else:
                other = _extract_model_candidates(found_blob)
                if other and req.mandatory:
                    evidence.append(
                        AttrEvidence(
                            key=req.key,
                            label=req.label,
                            required=req.required,
                            found=",".join(other[:3]),
                            source_url=source_url,
                            result="NO_CUMPLE",
                            mandatory=True,
                        )
                    )
                    has_contradiction = True
                    blockers.append(f"WRONG_MODEL:{other[0]}")
                    mandatory_total += 1
                else:
                    evidence.append(
                        AttrEvidence(
                            key=req.key,
                            label=req.label,
                            required=req.required,
                            found="no encontrado",
                            source_url=source_url,
                            result="NO_VERIFICADO",
                            mandatory=req.mandatory,
                        )
                    )
                    if req.mandatory:
                        mandatory_total += 1
                        has_missing = True
            continue

        # text / brand
        aliases = req.aliases or [req.required]
        hit = next((a for a in aliases if a and a.lower() in found_low), None)
        if not hit and req.kind == "brand":
            for a in aliases:
                if _norm(a) and _norm(a) in _norm(found_blob):
                    hit = a
                    break
        if hit:
            evidence.append(
                AttrEvidence(
                    key=req.key,
                    label=req.label,
                    required=req.required,
                    found=str(hit),
                    source_url=source_url,
                    result="CUMPLE",
                    mandatory=req.mandatory,
                )
            )
            if req.mandatory:
                mandatory_total += 1
                mandatory_ok += 1
        else:
            evidence.append(
                AttrEvidence(
                    key=req.key,
                    label=req.label,
                    required=req.required,
                    found="no encontrado",
                    source_url=source_url,
                    result="NO_VERIFICADO",
                    mandatory=req.mandatory,
                )
            )
            if req.mandatory:
                mandatory_total += 1
                has_missing = True

    # Score — ban keyword-only 100%
    if has_contradiction or any(
        e.result == "NO_CUMPLE" for e in evidence if e.mandatory
    ):
        return MatchResult(
            match_pct=15,
            match_class=TECH_NO_CUMPLE,
            technical_status=TECH_NO_CUMPLE,
            commercial_status=commercial,
            product_type_need=need_type,
            product_type_found=found_type,
            category_need=_TYPE_TO_CAT.get(need_type, ""),
            category_found=_TYPE_TO_CAT.get(found_type, ""),
            evidence=evidence,
            blockers=blockers,
            notes="contradicción en atributo obligatorio",
            evidence_ok=mandatory_ok,
            evidence_total=mandatory_total,
        )

    if has_missing or mandatory_ok < mandatory_total:
        pct = int(100 * mandatory_ok / mandatory_total) if mandatory_total else 0
        pct = min(pct, 90)  # never 100% if missing
        cls = TECH_POSIBLE if pct >= 50 else TECH_NO_VER
        if pct < 40:
            cls = TECH_NO_VER
        return MatchResult(
            match_pct=pct,
            match_class=cls,
            technical_status=cls,
            commercial_status=commercial,
            product_type_need=need_type,
            product_type_found=found_type,
            category_need=_TYPE_TO_CAT.get(need_type, ""),
            category_found=_TYPE_TO_CAT.get(found_type, ""),
            evidence=evidence,
            blockers=blockers + ["MANDATORY_UNPROVEN"],
            notes="faltan pruebas de atributos obligatorios — no EXACTO",
            evidence_ok=mandatory_ok,
            evidence_total=mandatory_total,
        )

    # All mandatory proven from page evidence
    optional = [e for e in evidence if not e.mandatory]
    optional_fail = [e for e in optional if e.result not in ("CUMPLE", "N/A")]
    if not optional_fail:
        return MatchResult(
            match_pct=100,
            match_class=TECH_EXACTO,
            technical_status=TECH_EXACTO,
            commercial_status=commercial,
            product_type_need=need_type,
            product_type_found=found_type,
            category_need=_TYPE_TO_CAT.get(need_type, ""),
            category_found=_TYPE_TO_CAT.get(found_type, ""),
            evidence=evidence,
            blockers=[],
            notes="mismo tipo + todos los hard attrs evidenciados",
            evidence_ok=mandatory_ok,
            evidence_total=mandatory_total,
        )
    return MatchResult(
        match_pct=95,
        match_class=TECH_EQUIV,
        technical_status=TECH_EQUIV,
        commercial_status=commercial,
        product_type_need=need_type,
        product_type_found=found_type,
        category_need=_TYPE_TO_CAT.get(need_type, ""),
        category_found=_TYPE_TO_CAT.get(found_type, ""),
        evidence=evidence,
        blockers=[],
        notes="obligatorios OK; opcionales parciales",
        evidence_ok=mandatory_ok,
        evidence_total=mandatory_total,
    )


def match_line_to_candidate(
    *,
    product: str,
    specs: str = "",
    brand: str = "",
    model: str = "",
    candidate_title: str = "",
    candidate_text: str = "",
    source_url: str = "",
    price: float | None = None,
    stock: str = "",
    shipping_neuquen: str = "",
    qty_needed: float = 1.0,
) -> MatchResult:
    need = full_spec_blob(product, specs, brand, model)
    reqs = extract_hard_requirements(product, specs, brand, model)
    return evaluate_match(
        reqs,
        candidate_title=candidate_title,
        candidate_text=candidate_text or candidate_title,
        source_url=source_url,
        need_blob=need,
        price=price,
        stock=stock,
        shipping_neuquen=shipping_neuquen,
        qty_needed=qty_needed,
    )
