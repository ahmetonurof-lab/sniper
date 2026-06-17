"""
event_router.py
───────────────
Normalizes raw market data (bars, FVG, MSS, sweep) into typed events
consumed by StateMachine. Decouples analysis from state transitions.
"""

from __future__ import annotations

import logging
from typing import Literal

logger = logging.getLogger(__name__)

# ─────────────────────────────────────────────
# EVENT TYPES
# ─────────────────────────────────────────────

EventType = Literal[
    "SWEEP",
    "MSS",
    "FVG_CREATED",
    "RETRACE",
    "LTF_CONFIRM",
    "HTF_BIAS",
    "HTF_LEVELS",
]

# ─────────────────────────────────────────────
# EVENT ROUTER
# ─────────────────────────────────────────────


class EventRouter:
    """
    Accepts raw analysis signals and emits typed MarketEvent dicts
    for StateMachine consumption. Zero decision logic.
    """

    def __init__(self, sm) -> None:
        self.sm = sm

    def publish(self, symbol: str, event: dict) -> None:
        self.sm.update_from_event(symbol, event)

    # ── Normalizers ───────────────────────────

    def sweep_detected(self, symbol: str, level: float, tf: str) -> dict:
        return {"type": "SWEEP", "symbol": symbol, "level": level, "tf": tf}

    def mss_confirmed(self, symbol: str, level: float, direction: str, tf: str) -> dict:
        return {"type": "MSS", "symbol": symbol, "level": level, "direction": direction, "tf": tf}

    def fvg_created(self, symbol: str, upper: float, lower: float, time: int) -> dict:
        return {"type": "FVG_CREATED", "symbol": symbol, "upper": upper, "lower": lower, "time": time}

    def retrace_into_fvg(self, symbol: str, price: float) -> dict:
        return {"type": "RETRACE", "symbol": symbol, "price": price}

    def htf_bias_detected(self, symbol: str, direction: str) -> dict:
        return {"type": "HTF_BIAS", "symbol": symbol, "direction": direction}

    def htf_levels_detected(
        self,
        symbol: str,
        h4_swing_level: float | None,
        h1_liquidity_level: float | None,
    ) -> dict:
        return {
            "type": "HTF_LEVELS",
            "symbol": symbol,
            "h4_swing_level": h4_swing_level,
            "h1_liquidity_level": h1_liquidity_level,
        }

    def ltf_confirmed(self, symbol: str, tf: str, direction: str, close: float) -> dict:
        return {
            "type": "LTF_CONFIRM",
            "symbol": symbol,
            "tf": tf,
            "direction": direction,
            "close": close,
        }
