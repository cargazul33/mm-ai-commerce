"""Opportunity timing vs America/Argentina/Buenos_Aires — never invent dates."""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

TZ_BA = ZoneInfo("America/Argentina/Buenos_Aires")

TIMING_ABIERTA = "ABIERTA"
TIMING_CIERRA_HOY = "CIERRA_HOY"
TIMING_VENCIDA = "VENCIDA"
TIMING_FECHA_NO_VERIFICADA = "FECHA_NO_VERIFICADA"

ACTIONABLE_TIMING = frozenset({TIMING_ABIERTA, TIMING_CIERRA_HOY})
TELEGRAM_BLOCKED_TIMING = frozenset({TIMING_VENCIDA, TIMING_FECHA_NO_VERIFICADA})

# dd/mm/yy[yy] [HH:MM[:SS]] [ART|…]
_DT_RE = re.compile(
    r"""
    (?P<d>\d{1,2})[/-](?P<m>\d{1,2})[/-](?P<y>\d{2,4})
    (?:[ T]+(?P<H>\d{1,2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?)?
    """,
    re.VERBOSE,
)


def now_ba() -> datetime:
    return datetime.now(TZ_BA)


def parse_cierre(value: str | None) -> datetime | None:
    """Parse CODINEU apertura/cierre strings into aware BA datetime. None if unverified."""
    if not value:
        return None
    s = str(value).strip()
    if not s or s.startswith("  /  /") or s in {"—", "-", "N/D", "ND"}:
        return None
    # ISO first — avoid matching 26-09-21 inside 2026-09-21 as dd/mm/yy
    iso = re.match(
        r"^(?P<y>\d{4})-(?P<m>\d{2})-(?P<d>\d{2})"
        r"(?:[T ](?P<H>\d{2}):(?P<M>\d{2})(?::(?P<S>\d{2}))?)?",
        s,
    )
    if iso:
        try:
            return datetime(
                int(iso.group("y")),
                int(iso.group("m")),
                int(iso.group("d")),
                int(iso.group("H") or 0),
                int(iso.group("M") or 0),
                int(iso.group("S") or 0),
                tzinfo=TZ_BA,
            )
        except ValueError:
            return None
    m = _DT_RE.search(s)
    if not m:
        return None
    d = int(m.group("d"))
    mo = int(m.group("m"))
    y = int(m.group("y"))
    if y < 100:
        y += 2000
    H = int(m.group("H") or 0)
    M = int(m.group("M") or 0)
    S = int(m.group("S") or 0)
    try:
        return datetime(y, mo, d, H, M, S, tzinfo=TZ_BA)
    except ValueError:
        return None


@dataclass(frozen=True)
class TimingInfo:
    state: str
    cierre: datetime | None
    remaining_label: str
    hours_left: float | None

    @property
    def is_actionable(self) -> bool:
        return self.state in ACTIONABLE_TIMING

    @property
    def block_telegram(self) -> bool:
        return self.state in TELEGRAM_BLOCKED_TIMING


def classify_timing(
    cierre_raw: str | None,
    *,
    now: datetime | None = None,
    cierre_dt: datetime | None = None,
) -> TimingInfo:
    """
    Compare cierre vs now in America/Argentina/Buenos_Aires.
    States: ABIERTA | CIERRA_HOY | VENCIDA | FECHA_NO_VERIFICADA
    """
    now = now or now_ba()
    if now.tzinfo is None:
        now = now.replace(tzinfo=TZ_BA)
    else:
        now = now.astimezone(TZ_BA)

    cierre = cierre_dt or parse_cierre(cierre_raw)
    if cierre is None:
        return TimingInfo(
            state=TIMING_FECHA_NO_VERIFICADA,
            cierre=None,
            remaining_label="fecha no verificada",
            hours_left=None,
        )

    if cierre.tzinfo is None:
        cierre = cierre.replace(tzinfo=TZ_BA)
    else:
        cierre = cierre.astimezone(TZ_BA)

    delta = cierre - now
    hours = delta.total_seconds() / 3600.0

    if delta.total_seconds() <= 0:
        return TimingInfo(
            state=TIMING_VENCIDA,
            cierre=cierre,
            remaining_label="vencida",
            hours_left=hours,
        )

    same_day = cierre.date() == now.date()
    state = TIMING_CIERRA_HOY if same_day else TIMING_ABIERTA
    return TimingInfo(
        state=state,
        cierre=cierre,
        remaining_label=format_remaining(delta.total_seconds()),
        hours_left=hours,
    )


def format_remaining(seconds: float) -> str:
    if seconds <= 0:
        return "vencida"
    hours = int(seconds // 3600)
    mins = int((seconds % 3600) // 60)
    days = hours // 24
    rem_h = hours % 24
    if days >= 1:
        if rem_h:
            return f"{days}d {rem_h}h restantes"
        return f"{days}d restantes"
    if hours >= 1:
        return f"{hours}h {mins}m restantes"
    return f"{mins}m restantes"


def format_cierre_display(cierre: datetime | None, raw: str = "") -> str:
    if cierre is not None:
        return cierre.strftime("%d/%m/%Y %H:%M ART")
    return (raw or "").strip() or "NO VERIFICADA"
