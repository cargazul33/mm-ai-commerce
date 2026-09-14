"""Telegram UI — allowlist TELEGRAM_USER_ID; callbacks reales approve|reject|view."""
from __future__ import annotations

import logging
import time
from typing import Any

from sqlalchemy.orm import Session

from mm_commerce.config import get_settings
from mm_commerce.models import Approval, Opportunity

log = logging.getLogger(__name__)


def format_card(d: dict) -> str:
    """Tarjeta obligatoria: publicación, cierre, restantes, enlaces, renglones, FIT, RISK."""
    oid = d.get("id", "?")
    fit = d.get("fit_score", "-")
    risk = d.get("risk") or "—"
    approval = d.get("approval") or "PENDIENTE"
    organism = (d.get("organism") or "—")[:48]
    title = (d.get("title") or "")[:140]
    timing = d.get("timing_state") or ""
    pub = d.get("publicacion") or "NO VERIFICADA"
    cierre = d.get("cierre") or "NO VERIFICADA"
    remaining = d.get("remaining") or "—"
    url = d.get("url") or "—"
    pliego = d.get("pliego_url") or url
    lines_n = d.get("line_count", 0)
    util = d.get("utilidad")
    util_s = f"{util:.0f}" if isinstance(util, (int, float)) else "N/D"

    lines = [
        f"┌─ #{oid} · FIT {fit} · RISK {risk}",
        f"│ {title}",
        f"│ 🏛 {organism}",
        f"│ 📢 publicación: {pub}",
        f"│ ⏰ cierre: {cierre}",
        f"│ ⏳ {remaining}" + (f" · {timing}" if timing else ""),
        f"│ 📎 renglones: {lines_n} · utilidad est.: {util_s}",
        f"│ 🔗 fuente: {url}",
        f"│ 📄 pliego: {pliego}",
        f"│ estado={d.get('state') or '—'} · {approval}",
        "│ [APROBAR]  [RECHAZAR]  [VER]",
        "└────────────────────────────",
    ]
    return "\n".join(lines)


def format_digest(digest: list[dict]) -> str:
    if not digest:
        return (
            "🔔 M&M AI Commerce — digest vacío\n"
            "Sin oportunidades ABIERTAS accionables "
            "(VENCIDAS/FECHA_NO_VERIFICADA excluidas)."
        )
    header = [
        "🔔 M&M AI Commerce — top oportunidades ABIERTAS",
        f"Tarjetas: {len(digest)} · humano solo APRUEBA dinero/legal",
        "Nunca auto-submit. VENCIDAS no se envían.",
        "",
    ]
    cards = [format_card(d) for d in digest]
    return "\n".join(header + cards)


def _inline_keyboard(external_id: str) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "APROBAR", "callback_data": f"approve:{external_id}"},
                {"text": "RECHAZAR", "callback_data": f"reject:{external_id}"},
                {"text": "VER", "callback_data": f"view:{external_id}"},
            ]
        ]
    }



def build_full_detail(session: Session, opp: Opportunity) -> str:
    """Secciones completas para botón VER (persiste lectura desde DB)."""
    import json
    from mm_commerce.models import Offer, SupplierQuote, Tender

    tender = session.query(Tender).filter_by(opportunity_id=opp.id).one_or_none()
    offer = (
        session.query(Offer)
        .filter_by(opportunity_id=opp.id)
        .order_by(Offer.id.desc())
        .first()
    )
    quotes = (
        session.query(SupplierQuote)
        .filter_by(opportunity_id=opp.id)
        .order_by(SupplierQuote.match_score.desc())
        .all()
    )
    lines = [
        f"🔎 VER #{opp.external_id}",
        f"Título: {(opp.title or '')[:160]}",
        f"FIT {opp.fit_score} · RISK {opp.risk_level or '—'} · {opp.timing_state}",
        f"Cierre: {opp.cierre_at or opp.opening_at}",
        f"Approval: {opp.approval_status}",
        f"Fuente: {opp.url}",
        f"Pliego: {opp.pliego_url or (tender.doc_path if tender else '')}",
        "",
        "— RENGLONES —",
    ]
    if tender and tender.items:
        for it in tender.items:
            lines.append(
                f"R{it.line_no} x{it.qty}: {it.product[:80]} | {(it.specs or '')[:100]}"
            )
    else:
        lines.append("(sin renglones)")
    lines.append("")
    lines.append("— PROVEEDORES —")
    if not quotes:
        lines.append("(sin cotizaciones)")
    for q in quotes[:20]:
        lines.append(
            f"• {q.product_label[:70]} | ${q.unit_cost if q.unit_cost is not None else 'N/D'} "
            f"| stock={q.stock_note or 'N/D'} | ship NQN={q.shipping_neuquen or 'N/D'} "
            f"| MATCH {q.match_pct or q.match_score}% | {q.verification} | {q.verified_at}"
        )
        if q.url:
            lines.append(f"  URL: {q.url}")
    lines.append("")
    lines.append("— ECONOMÍA —")
    if offer:
        econ = {}
        try:
            econ = json.loads(offer.economic_json or "{}")
        except Exception:
            econ = {}
        lines.append(f"Mercadería: {offer.merchandise_cost}")
        lines.append(f"Logística: {offer.logistics_cost} ({offer.logistics_status})")
        lines.append(f"Otros: {offer.other_costs}")
        lines.append(f"Total costo: {offer.total_cost}")
        lines.append(f"Precio×{offer.margin_multiplier}: {offer.precio_objetivo}")
        lines.append(f"Utilidad $: {offer.utilidad}")
        lines.append(f"Margen %: {offer.margen_pct}")
        lines.append(f"Capital: {offer.capital_requerido}")
        if econ.get("note"):
            lines.append(f"Nota: {econ['note']}")
    else:
        lines.append("(sin offer)")
    text = "\n".join(lines)
    return text[:3500]


def apply_callback(
    session: Session,
    *,
    data: str,
    user_id: str | int,
    actor: str = "telegram",
) -> dict[str, Any]:
    """Actualiza approval en DB si user allowlist. callback_data: action:id."""
    settings = get_settings()
    allow = str(settings.telegram_user_id or "").strip()
    if not allow or str(user_id) != allow:
        return {"ok": False, "error": "USER_NOT_ALLOWLISTED", "user_id": str(user_id)}

    if ":" not in data:
        return {"ok": False, "error": "BAD_CALLBACK", "data": data}
    action, ext_id = data.split(":", 1)
    action = action.strip().lower()
    ext_id = ext_id.strip()
    if action.startswith("rfq_") or action in (
        "enviar_rfq",
        "descartar",
        "enviar_whatsapp",
        "enviar_email",
        "copiar",
    ):
        from mm_commerce.rfq import handle_rfq_callback

        return handle_rfq_callback(data)
    opp = session.query(Opportunity).filter_by(external_id=ext_id).one_or_none()
    if opp is None:
        return {"ok": False, "error": "OPP_NOT_FOUND", "id": ext_id}

    if action in ("approve", "aprobar"):
        # CRITICAL: never allow APROBAR while matching/verifier blocked
        if opp.approval_status == "BLOQUEADO":
            return {
                "ok": False,
                "error": "BLOQUEADO_MATCHING",
                "id": ext_id,
                "detail": "Matching/verifier bloqueado — no se puede APROBAR ni oferta completa",
            }
        # Also re-check offer apto flag
        from mm_commerce.models import Offer
        import json as _json
        off = (
            session.query(Offer)
            .filter_by(opportunity_id=opp.id)
            .order_by(Offer.id.desc())
            .first()
        )
        if off and off.economic_json:
            try:
                econ = _json.loads(off.economic_json)
                if econ.get("apto_para_cotizar") is False:
                    return {
                        "ok": False,
                        "error": "NO_APTO_COTIZAR",
                        "id": ext_id,
                        "detail": econ.get("note") or "matching incompleto",
                    }
            except Exception:
                pass
        status = "APROBADO"
    elif action in ("reject", "rechazar"):
        status = "RECHAZADO"
    elif action in ("view", "ver"):
        detail = build_full_detail(session, opp)
        return {
            "ok": True,
            "action": "view",
            "id": ext_id,
            "title": opp.title,
            "fit": opp.fit_score,
            "timing": opp.timing_state,
            "cierre": opp.cierre_at or opp.opening_at,
            "url": opp.url,
            "approval": opp.approval_status,
            "detail": detail,
        }
    else:
        return {"ok": False, "error": "UNKNOWN_ACTION", "action": action}

    opp.approval_status = status
    session.add(
        Approval(
            opportunity_id=opp.id,
            status=status,
            actor=f"{actor}:{user_id}",
            reason=f"callback:{data}",
        )
    )
    session.commit()
    return {"ok": True, "action": action, "id": ext_id, "status": status}


def notify_high_fit(session: Session, digest: list[dict]) -> dict[str, Any]:
    """Envía una tarjeta por oportunidad con botones reales; sin token → CLI."""
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

    # Safety: never send non-actionable (defense in depth)
    safe = [
        d
        for d in digest
        if (d.get("timing_state") or "") in ("ABIERTA", "CIERRA_HOY")
    ]
    if not safe:
        return {
            "status": "SKIPPED_EMPTY_OPEN",
            "digest_count": 0,
            "filtered_out": len(digest),
        }

    try:
        import httpx

        sent = 0
        errors: list[dict] = []
        message_ids: list[int] = []
        with httpx.Client(timeout=30.0) as client:
            # summary header
            header = (
                f"🔔 M&M AI Commerce — {len(safe)} oportunidad(es) ABIERTA(S)\n"
                "VENCIDAS excluidas. Botones actualizan DB (allowlist)."
            )
            r0 = client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json={"chat_id": user_id, "text": header},
            )
            if r0.status_code != 200:
                errors.append({"header": r0.status_code, "body": r0.text[:200]})

            for d in safe:
                card = format_card(d)
                payload = {
                    "chat_id": user_id,
                    "text": card[:4000],
                    "reply_markup": _inline_keyboard(str(d["id"])),
                    "disable_web_page_preview": True,
                }
                r = client.post(
                    f"https://api.telegram.org/bot{token}/sendMessage",
                    json=payload,
                )
                if r.status_code == 200:
                    sent += 1
                    try:
                        message_ids.append(r.json()["result"]["message_id"])
                    except Exception:  # noqa: BLE001
                        pass
                else:
                    errors.append(
                        {"id": d.get("id"), "http": r.status_code, "body": r.text[:200]}
                    )

        return {
            "status": "SENT" if sent else "ERROR",
            "digest_count": sent,
            "message_ids": message_ids,
            "errors": errors,
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}


def answer_callback_query(token: str, callback_query_id: str, text: str) -> dict:
    import httpx

    with httpx.Client(timeout=20.0) as client:
        r = client.post(
            f"https://api.telegram.org/bot{token}/answerCallbackQuery",
            json={
                "callback_query_id": callback_query_id,
                "text": text[:200],
                "show_alert": False,
            },
        )
        return {"http": r.status_code, "body": r.text[:300]}


def poll_callbacks_once(
    session: Session, *, timeout_sec: int = 8, limit: int = 50
) -> dict[str, Any]:
    """getUpdates corto; procesa approve/reject/view del allowlist."""
    settings = get_settings()
    token = (settings.telegram_bot_token or "").strip()
    if not token:
        return {"status": "NO_TOKEN", "processed": []}

    import httpx

    processed: list[dict] = []
    with httpx.Client(timeout=timeout_sec + 10) as client:
        r = client.get(
            f"https://api.telegram.org/bot{token}/getUpdates",
            params={"timeout": timeout_sec, "limit": limit},
        )
        if r.status_code != 200:
            return {"status": "ERROR", "http": r.status_code, "body": r.text[:300]}
        data = r.json()
        updates = data.get("result") or []
        max_id = 0
        for u in updates:
            max_id = max(max_id, int(u.get("update_id", 0)))
            cq = u.get("callback_query")
            if not cq:
                continue
            from_user = (cq.get("from") or {}).get("id")
            cb_data = cq.get("data") or ""
            cq_id = cq.get("id")
            result = apply_callback(
                session, data=cb_data, user_id=from_user, actor="telegram"
            )
            if cq_id:
                msg = (
                    f"OK {result.get('status') or result.get('action')}"
                    if result.get("ok")
                    else f"ERR {result.get('error')}"
                )
                answer_callback_query(token, cq_id, msg)
            if result.get("ok") and result.get("detail"):
                try:
                    client.post(
                        f"https://api.telegram.org/bot{token}/sendMessage",
                        json={
                            "chat_id": from_user,
                            "text": str(result["detail"])[:4000],
                            "disable_web_page_preview": True,
                        },
                    )
                except Exception:
                    pass
            processed.append({"update_id": u.get("update_id"), "result": result})
        if max_id:
            client.get(
                f"https://api.telegram.org/bot{token}/getUpdates",
                params={"offset": max_id + 1, "timeout": 0, "limit": 1},
            )
    return {"status": "OK", "processed": processed, "updates_seen": len(updates)}


def simulate_callback(
    session: Session, *, external_id: str, action: str = "approve"
) -> dict[str, Any]:
    """Prueba local: simula callback allowlist sin esperar tap humano."""
    settings = get_settings()
    uid = settings.telegram_user_id or "0"
    return apply_callback(
        session,
        data=f"{action}:{external_id}",
        user_id=uid,
        actor="simulate",
    )


# backward compat
def callback_stub(data: str) -> str:
    return f"use apply_callback for {data}"


def send_telegram_text(
    text: str,
    *,
    reply_markup: dict | None = None,
    chat_id: str | None = None,
) -> dict[str, Any]:
    """Send a plain text message to allowlisted user (or CLI fallback)."""
    settings = get_settings()
    print("\n===== CLI TELEGRAM =====\n" + text[:4000] + "\n========================\n")
    token = (settings.telegram_bot_token or "").strip()
    user_id = (chat_id or settings.telegram_user_id or "").strip()
    if not token or not user_id:
        return {"status": "CLI_ONLY", "chars": len(text)}
    # Defense: only allowlist unless explicit chat_id matches allowlist
    allow = str(settings.telegram_user_id or "").strip()
    if allow and str(user_id) != allow and not chat_id:
        return {"status": "ERROR", "error": "USER_NOT_ALLOWLISTED", "user_id": user_id}
    if allow and chat_id and str(chat_id) != allow:
        return {"status": "ERROR", "error": "USER_NOT_ALLOWLISTED", "user_id": chat_id}
    try:
        import httpx
        payload: dict[str, Any] = {
            "chat_id": user_id,
            "text": text[:4000],
            "disable_web_page_preview": True,
        }
        if reply_markup:
            payload["reply_markup"] = reply_markup
        with httpx.Client(timeout=30.0) as client:
            r = client.post(
                f"https://api.telegram.org/bot{token}/sendMessage",
                json=payload,
            )
        return {
            "status": "SENT" if r.status_code == 200 else "ERROR",
            "http": r.status_code,
            "body": r.text[:300],
            "chat_id": str(user_id),
        }
    except Exception as exc:  # noqa: BLE001
        return {"status": "ERROR", "error": f"{type(exc).__name__}: {exc}"}


def push_opportunity_report(session: Session, opp: Opportunity) -> dict[str, Any]:
    """Build matching report + RFQ drafts and push to Telegram."""
    from mm_commerce.report import write_matching_report, format_matching_report_text
    from mm_commerce.rfq import format_rfq_telegram, rfq_inline_keyboard

    jp, tp, report = write_matching_report(session, opp)
    text = format_matching_report_text(report)
    tg = send_telegram_text(text)
    rfq_results = []
    for d in report.get("rfq_drafts") or []:
        msg = format_rfq_telegram(d)
        kb = None if d.get("blocked") else rfq_inline_keyboard(d["rfq_id"], email=d.get("email"), draft=d)
        rfq_results.append(send_telegram_text(msg, reply_markup=kb))
    return {
        "report_json": str(jp),
        "report_txt": str(tp),
        "telegram": tg,
        "rfq_telegram": rfq_results,
        "modalidad": report.get("modalidad"),
        "apto_global": report.get("apto_global"),
    }
