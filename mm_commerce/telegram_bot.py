"""Telegram UI — allowlist TELEGRAM_USER_ID; sin token → CLI digest."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from mm_commerce.config import get_settings

log = logging.getLogger(__name__)


def format_card(d: dict) -> str:
    """Una tarjeta estilo Telegram por oportunidad."""
    oid = d.get("id", "?")
    fit = d.get("fit_score", "-")
    risk = d.get("risk") or "—"
    approval = d.get("approval") or "PENDIENTE"
    organism = (d.get("organism") or "—")[:48]
    title = (d.get("title") or "")[:140]
    state = d.get("state") or ""
    apertura = d.get("apertura") or d.get("opening_at") or ""
    lines = [
        f"┌─ #{oid} · FIT {fit} · RISK {risk}",
        f"│ {title}",
        f"│ 🏛 {organism}",
    ]
    if apertura:
        lines.append(f"│ 📅 {apertura}")
    if state:
        lines.append(f"│ estado={state} · {approval}")
    else:
        lines.append(f"│ {approval}")
    lines.append("│ [APROBAR]  [RECHAZAR]  [VER…]")
    lines.append("└────────────────────────────")
    return "\n".join(lines)


def format_digest(digest: list[dict]) -> str:
    if not digest:
        return (
            "🔔 M&M AI Commerce — digest vacío\n"
            "Sin oportunidades accionables (fit alto / no skip)."
        )
    header = [
        "🔔 M&M AI Commerce — top oportunidades",
        f"Tarjetas: {len(digest)} · humano solo APRUEBA dinero/legal",
        "Nunca auto-submit.",
        "",
    ]
    cards = [format_card(d) for d in digest]
    return "\n".join(header + cards)


def notify_high_fit(session: Session, digest: list[dict]) -> dict[str, Any]:
    """Envía alerta Telegram si hay token+allowlist; si no, imprime digest CLI."""
    settings = get_settings()
    text = format_digest(digest)
    print("\n===== CLI DIGEST =====\n" + text + "\n======================\n")

    token = (settings.telegram_bot_token or "").strip()
    user_id = (settings.telegram_user_id or "").strip()
    if not token or not user_id:
        return {
            "status": "CLI_ONLY",
            "reason": "sin TELEGRAM_BOT_TOKEN o TELEGRAM_USER_ID",
            "digest_count": len(digest),
        }

    try:
        import httpx

        url = f"https://api.telegram.org/bot{token}/sendMessage"
        reply_markup = {
            "inline_keyboard": [
                [
                    {"text": "APROBAR", "callback_data": "approve_stub"},
                    {"text": "RECHAZAR", "callback_data": "reject_stub"},
                    {"text": "VER…", "callback_data": "view_stub"},
                ]
            ]
        }
        with httpx.Client(timeout=20.0) as client:
            r = client.post(
                url,
                json={
                    "chat_id": user_id,
                    "text": text[:4000],
                    "reply_markup": reply_markup,
                },
            )
        if r.status_code == 200:
            return {"status": "SENT", "digest_count": len(digest)}
        return {"status": "ERROR", "http": r.status_code, "body": r.text[:300]}
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}


def callback_stub(data: str) -> str:
    """Callbacks pueden ser stub — no ejecutan compra."""
    mapping = {
        "approve_stub": "PENDIENTE→registrar APROBADO manual en DB (stub)",
        "reject_stub": "PENDIENTE→registrar RECHAZADO manual en DB (stub)",
        "view_stub": "mostrar detalle (stub)",
    }
    return mapping.get(data, "callback_desconocido")
