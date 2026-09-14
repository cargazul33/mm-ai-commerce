"""Telegram UI — allowlist TELEGRAM_USER_ID; sin token → CLI digest."""
from __future__ import annotations

import logging
from typing import Any

from sqlalchemy.orm import Session

from mm_commerce.config import get_settings

log = logging.getLogger(__name__)


def format_digest(digest: list[dict]) -> str:
    if not digest:
        return "M&M AI Commerce — sin oportunidades accionables (fit alto)."
    lines = ["🔔 M&M AI Commerce — oportunidades accionables", ""]
    for d in digest:
        lines.append(
            f"#{d['id']} FIT={d['fit_score']} RISK={d.get('risk') or '-'} "
            f"[{d.get('approval')}] {d.get('organism','')[:40]}"
        )
        lines.append(f"  {d.get('title','')[:100]}")
        lines.append("  [APROBAR] [RECHAZAR] [VER…]  (callbacks stub sin token)")
        lines.append("")
    lines.append("Humano solo APRUEBA dinero/legal. Nunca auto-submit.")
    return "\n".join(lines)


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
        # Inline keyboard stubs
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
