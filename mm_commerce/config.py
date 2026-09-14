"""Configuración ZERO SPEND — sin APIs de pago."""
from __future__ import annotations

from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Hard-avoid (categoría / organismo / título)
AVOID_CATEGORIES = (
    "salud",
    "medicament",
    "policía",
    "policia",
    "construcción pesada",
    "construccion pesada",
    "maquinaria pesada",
    "obras",
    "instalaciones complejas",
    "instalacion compleja",
    "instalación compleja",
    "prótesis",
    "protesis",
    "traumato",
)

# Didáctico/escolar: NO alto FIT por título solo — requiere renglones revendibles
DIDACTIC_FLAGS = (
    "didáctic",
    "didactic",
    "escolar",
    "material didact",
    "material didáct",
)

# Prioridad comercial M&M (rubros revendibles)
PRIORITY_KEYWORDS = (
    "informát",
    "informat",
    "tecnolog",
    "notebook",
    "computadora",
    "pc ",
    " pcs",
    "impresor",
    "printer",
    "redes",
    "network",
    "conectividad",
    "electr",
    "electrodomést",
    "electrodomest",
    "oficina",
    "librer",
    "papeler",
    "útiles",
    "utiles",
    "toner",
    "cartucho",
    "monitor",
    "silla",
    "mobili",
    "escritorio",
    "mueble",
    "herramienta liviana",
    "herramientas livianas",
    "herramientas",
    "insumos inform",
    "equipamiento inform",
    "elementos inform",
    "hardware",
    "switch",
    "router",
    "olt",
    "gpon",
    "fibra",
    "wifi",
    "wi-fi",
    "access point",
    "ups",
    "estabilizador",
    "splitter",
    "revendible",
    "insumos",
)

# Tokens de renglón que sí permiten alto FIT en didáctico
RESELLABLE_LINE_KEYWORDS = (
    "notebook",
    "computadora",
    "impresor",
    "toner",
    "cartucho",
    "monitor",
    "mouse",
    "teclado",
    "router",
    "switch",
    "cable utp",
    "pendrive",
    "disco",
    "ssd",
    "ram ",
    "papel a4",
    "resma",
    "carpeta",
    "folio",
    "bolígrafo",
    "boligrafo",
    "lapicera",
    "marcador",
    "silla",
    "escritorio",
    "archivador",
    "olt",
    "gpon",
    "fibra",
    "ups",
    "estabilizador",
    "splitter",
    "access point",
    "wifi",
    "wi-fi",
)

HARD_SKIP_IDS = frozenset({"16514"})


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    database_url: str = "sqlite:///./data/mm_commerce.db"
    margin_multiplier: float = 1.90
    fit_score_alert_min: int = 60
    telegram_bot_token: str = ""
    telegram_user_id: str = ""
    llm_base_url: str = ""
    llm_model: str = ""
    codineu_login_env: str = "/home/box/.config/codineu/login.env"
    codineu_fixture: str = "/workspace/codineu-vigentes.json"
    pliegos_dir: str = "/workspace/pliegos"
    use_fixtures: bool = True
    excluded_process_ids: str = "16514"
    project_root: Path = Path(__file__).resolve().parent.parent

    @property
    def excluded_ids(self) -> frozenset[str]:
        ids = {x.strip() for x in self.excluded_process_ids.split(",") if x.strip()}
        return frozenset(ids) | HARD_SKIP_IDS


@lru_cache
def get_settings() -> Settings:
    s = Settings()
    db = s.database_url
    if db.startswith("sqlite:///"):
        path = db.replace("sqlite:///", "", 1)
        if path.startswith("./"):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    return s
