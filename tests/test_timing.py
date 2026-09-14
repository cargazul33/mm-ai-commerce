from datetime import datetime
from zoneinfo import ZoneInfo

from mm_commerce.timing import (
    TIMING_ABIERTA,
    TIMING_CIERRA_HOY,
    TIMING_FECHA_NO_VERIFICADA,
    TIMING_VENCIDA,
    classify_timing,
    parse_cierre,
)

BA = ZoneInfo("America/Argentina/Buenos_Aires")


def test_parse_cierre_art():
    dt = parse_cierre("07/09/26 09:00 ART")
    assert dt is not None
    assert dt.year == 2026 and dt.month == 9 and dt.day == 7
    assert dt.tzinfo is not None


def test_vencida_vs_now():
    now = datetime(2026, 9, 14, 12, 0, tzinfo=BA)
    info = classify_timing("07/09/26 09:00 ART", now=now)
    assert info.state == TIMING_VENCIDA


def test_abierta_future():
    now = datetime(2026, 9, 14, 1, 0, tzinfo=BA)
    info = classify_timing("21/09/26 10:00 ART", now=now)
    assert info.state == TIMING_ABIERTA
    assert "restantes" in info.remaining_label


def test_cierra_hoy():
    now = datetime(2026, 9, 14, 1, 0, tzinfo=BA)
    info = classify_timing("14/09/26 10:00 ART", now=now)
    assert info.state == TIMING_CIERRA_HOY


def test_fecha_no_verificada():
    info = classify_timing("")
    assert info.state == TIMING_FECHA_NO_VERIFICADA
    info2 = classify_timing("  /  /     00:00:00")
    assert info2.state == TIMING_FECHA_NO_VERIFICADA
