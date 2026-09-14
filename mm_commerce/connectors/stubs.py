"""Stubs honestos — portales privados REQUIERE CREDENCIALES."""
from __future__ import annotations

PRIVATE_PORTALS = {
    "ypf": "REQUIERE CREDENCIALES — portal energía YPF no scrapeable sin auth",
    "pan_american": "REQUIERE CREDENCIALES",
    "pluspetrol": "REQUIERE CREDENCIALES",
}


def portal_status(name: str) -> str:
    return PRIVATE_PORTALS.get(name.lower(), "DESCONOCIDO")
