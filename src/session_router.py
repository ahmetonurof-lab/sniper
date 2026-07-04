"""
session_router.py — Coin bazli session + CBDR esigi filtresi.

Her trade oncesi cagrilir:
  1. Zayif coinleri portfoyden at (ETH, SUI)
  2. Coinin optimal session'ina uygun mu kontrol et
  3. Coin bazli CBDR minimum esigi kontrolu (AVAX: >%5)

Router'siz duruma gore:
  - Portfoy MaxDD: %21.7 -> ~%8-10 (tahmini)
  - Toplam PnL: yatay veya yukari
"""

import logging
from datetime import UTC, datetime

import config as cfg

logger = logging.getLogger("sniper.session_router")


# ── Session name -> saat araligi ─────────────────────────────
SESSION_HOURS_MAP = {
    "DEFAULT": {"start": 22, "end": 2},
    "REAL_CBDR": {"start": 19, "end": 1},
    "ASIA_RANGE": {"start": 1, "end": 5},
}


def _detect_session_name() -> str:
    """O anki UTC saatine gore hangi session'da oldugumuzu bul."""
    h = datetime.now(UTC).hour
    for sname, hours in SESSION_HOURS_MAP.items():
        sh, eh = hours["start"], hours["end"]
        spans_midnight = sh > eh
        in_window = (h >= sh or h < eh) if spans_midnight else (sh <= h < eh)
        if in_window:
            return sname
    return "LONDON_NY"  # CBDR penceresi disi


def should_trade(
    symbol: str,
    cbdr_width_pct: float | None = None,
    current_session: str | None = None,
) -> tuple[bool, str]:
    """Trade'in oncesinde filtreden gecir.

    Args:
        symbol: Coin adi (BTCUSDT vb.)
        cbdr_width_pct: Gunluk CBDR genisligi % (None = bilinmiyor)
        current_session: Session adi (None = otomatik tespit)

    Returns:
        (True, "") veya (False, "sebep")
    """
    if current_session is None:
        current_session = cfg.BOT_SESSION

    # ── 1. Zayif halkalar ──
    if symbol in ("ETHUSDT", "SUIUSDT"):
        return False, f"{symbol} portfoyden cikarildi (zayif halka)"

    # ── 2. Optimal session kontrolu ──
    optimal = cfg.OPTIMAL_SESSION_MAP.get(symbol)
    if optimal is None:
        return False, f"{symbol} OPTIMAL_SESSION_MAP'te tanimli degil"

    if current_session != optimal:
        return (
            False,
            f"{symbol} optimal={optimal}, mevcut={current_session} -> eslesmiyor",
        )

    # ── 3. Coin bazli CBDR minimum esigi ──
    if cbdr_width_pct is not None:
        required = cfg.CBDR_REQUIRED_MIN_PCT.get(symbol)
        if required is not None and cbdr_width_pct < required:
            return (
                False,
                f"{symbol} CBDR={cbdr_width_pct:.2f}% < required={required}%",
            )

    return True, ""
