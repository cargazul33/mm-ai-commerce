"""Configuración ZERO SPEND — sin APIs de pago."""
from __future__ import annotations

import os
from pathlib import Path
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

# Categorías a evitar inicialmente
AVOID_CATEGORIES = (
    "salud",
    "medicament",
    "policía",
    "policia",
    "construcción pesada",
    "construccion pesada",
    "maquinaria",
    "obras",
)

# Prioridad comercial M&M
PRIORITY_KEYWORDS = (
    "informát",
    "informat",
    "notebook",
    "computadora",
    "pc ",
    "impresor",
    "printer",
    "redes",
    "network",
    "electr",
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
    "herramientas",
    "insumos inform",
    "equipamiento inform",
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
    # Ensure data dir exists relative to CWD or project
    s = Settings()
    db = s.database_url
    if db.startswith("sqlite:///"):
        path = db.replace("sqlite:///", "", 1)
        if path.startswith("./"):
            Path(path).parent.mkdir(parents=True, exist_ok=True)
    return s
