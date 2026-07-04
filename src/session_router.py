"""
session_router.py — Coin bazli CBDR risk carpani + session helper.

Iki gorevi var:
  1. should_trade() — trade oncesi filtre (zayif coinler + zehirli bolge)
  2. get_cbdr_multiplier() — CBDR genisligine gore risk carpani
  3. get_session_hours() — coin'in optimal session saatleri
"""

import logging

import config as cfg

logger = logging.getLogger("sniper.session_router")


def is_high_quality_fvg(fvg_pips: float, current_atr: float) -> bool:
    """FVG kalitesini volatiliteye (ATR) gore kontrol et."""
    if current_atr <= 1e-8:
        return False
    rel_fvg = fvg_pips / current_atr
    if rel_fvg < cfg.MIN_REL_FVG_THRESHOLD:
        return False
    return True


def is_fvg_valid(formation_bar_index: int, current_bar_index: int) -> bool:
    """FVG'nin zaman asimina ugrayip ugramadigini kontrol eder.
    DNA analizine gore 45 bar gecmisse FVG 'olu', magnet etkisi bitmis."""
    bars_passed = current_bar_index - formation_bar_index
    if bars_passed > cfg.GLOBAL_FVG_EXPIRY_BARS:
        return False
    return True


def get_cbdr_multiplier(symbol: str, cbdr_pct: float) -> float:
    profile = cfg.CBDR_RISK_MATRIX.get(symbol)
    if not profile:
        return 1.0
    for lo, hi, mult in profile["buckets"]:
        if lo <= cbdr_pct < hi:
            return mult
    return 1.0


def get_session_hours(symbol: str) -> dict[str, int]:
    """Coin'in optimal session saatlerini dondur."""
    profile = cfg.CBDR_RISK_MATRIX.get(symbol)
    if not profile:
        return {"start": 22, "end": 2}
    hours = cfg.SESSION_HOURS.get(profile["session"])
    return hours or {"start": 22, "end": 2}


def should_trade(
    symbol: str,
    cbdr_width_pct: float | None = None,
) -> tuple[bool, str]:
    if symbol in ("ETHUSDT", "SUIUSDT"):
        return False, symbol + " portfoyden cikarildi (zayif halka)"
    profile = cfg.CBDR_RISK_MATRIX.get(symbol)
    if profile is None:
        return False, symbol + " CBDR_RISK_MATRIX'te tanimli degil"
    if cbdr_width_pct is not None:
        cbdr_mult = get_cbdr_multiplier(symbol, cbdr_width_pct)
        if cbdr_mult == 0.0:
            return (
                False,
                symbol
                + " CBDR="
                + f"{cbdr_width_pct:.2f}%"
                + " Zehirli Bolge (mult=0.0)",
            )
    return True, ""
