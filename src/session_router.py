"""
session_router.py — Coin bazli session filtresi + CBDR risk carpani.

Iki gorevi var:
  1. should_trade() — trade oncesi filtre (zayif coinler + session uyumu)
  2. get_cbdr_multiplier() — CBDR genisligine gore risk carpani
"""

import logging

import config as cfg

logger = logging.getLogger("sniper.session_router")


def get_cbdr_multiplier(symbol: str, cbdr_pct: float) -> float:
    profile = cfg.CBDR_RISK_MATRIX.get(symbol)
    if not profile:
        return 1.0
    for lo, hi, mult in profile["buckets"]:
        if lo <= cbdr_pct < hi:
            return mult
    return 1.0


def should_trade(
    symbol: str,
    cbdr_width_pct: float | None = None,
    current_session: str | None = None,
) -> tuple[bool, str]:
    if current_session is None:
        current_session = cfg.BOT_SESSION
    if symbol in ("ETHUSDT", "SUIUSDT"):
        return False, f"{symbol} portfoyden cikarildi (zayif halka)"
    profile = cfg.CBDR_RISK_MATRIX.get(symbol)
    if profile is None:
        return False, f"{symbol} CBDR_RISK_MATRIX'te tanimli degil"
    optimal = profile["session"]
    if current_session != optimal:
        return (
            False,
            f"{symbol} optimal={optimal}, mevcut={current_session} -> eslesmiyor",
        )
    if cbdr_width_pct is not None:
        cbdr_mult = get_cbdr_multiplier(symbol, cbdr_width_pct)
        if cbdr_mult == 0.0:
            return (
                False,
                f"{symbol} CBDR={cbdr_width_pct:.2f}% Zehirli Bolge (mult=0.0)",
            )
    return True, ""
