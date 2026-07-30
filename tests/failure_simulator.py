"""
failure_simulator.py — Binance rejection simulator for entry_manager.py.

Deterministic FakeExchange that replics the REST adapter contract
used by EntryManager without sending real orders.  Supports every
failure mode listed in FailureMode.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any


class FailureMode(str, Enum):
    NONE = "none"
    SL_2021 = "sl_2021"
    SL_GENERIC = "sl_generic"
    TP_2021 = "tp_2021"
    MARKET_TIMEOUT = "market_timeout"
    PARTIAL_FILL = "partial_fill"
    CLOSE_FAIL = "close_fail"
    CANCEL_FAIL = "cancel_fail"


class BinanceReject(Exception):
    """Binance API hatasi (code + message)."""

    def __init__(self, code: int, message: str):
        self.code = code
        self.message = message
        super().__init__(f"{code}: {message}")


@dataclass
class FakeExchange:
    fill_price: Decimal = Decimal("100")
    requested_qty: Decimal = Decimal("1")
    actual_qty: Decimal = Decimal("1")
    mode: FailureMode = FailureMode.NONE
    tick_size: Decimal = Decimal("0.10")
    calls: list[tuple[str, dict[str, Any]]] = field(default_factory=list)
    protected: bool = False
    closed: bool = False
    close_error: Exception | None = None

    # ── REST adapter helpers (pass-through) ──────────────────────

    async def apply_amount_precision(self, symbol: str, qty: float) -> float:
        return qty

    async def validate_min_amount(self, symbol: str, qty: float) -> float:
        return qty

    async def estimate_market_price(self, symbol: str) -> float:
        return float(self.fill_price)

    async def get_min_notional(self, symbol: str) -> float:
        return 5.0

    async def get_step_size(self, symbol: str) -> float:
        return 0.001

    async def get_max_qty(self, symbol: str) -> float:
        return 1000.0

    async def get_positions(self) -> list[dict[str, Any]]:
        return []

    async def get_tick_size(self, symbol: str) -> float:
        return float(self.tick_size)

    async def apply_price_precision(self, symbol: str, price: float) -> float:
        return price

    # ── Order placement (deterministic based on FailureMode) ─────

    async def place_market_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        **kwargs,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "market",
                {
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "kwargs": kwargs,
                },
            )
        )
        if self.mode == FailureMode.MARKET_TIMEOUT:
            raise TimeoutError("market order timeout")
        # CLOSE_FAIL only affects emergency close calls (which pass reduce_only=True)
        if self.mode == FailureMode.CLOSE_FAIL and kwargs.get("reduce_only"):
            raise RuntimeError("simulated emergency close failure")
        return {
            "orderId": "MKT-001",
            "status": "FILLED",
            "avgPrice": str(self.fill_price),
            "executedQty": str(self.actual_qty),
            "cummulativeQuoteQty": str(float(self.actual_qty) * float(self.fill_price)),
        }

    async def place_stop_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "sl",
                {
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "price": price,
                },
            )
        )
        if self.mode in (FailureMode.SL_2021, FailureMode.CLOSE_FAIL):
            return {"code": -2021, "msg": "Order would immediately trigger"}
        if self.mode == FailureMode.SL_GENERIC:
            raise RuntimeError("simulated stop placement failure")
        self.protected = True
        return {"algoId": "SL-001", "status": "NEW"}

    async def place_tp_order(
        self,
        symbol: str,
        side: str,
        qty: float,
        price: float,
    ) -> dict[str, Any]:
        self.calls.append(
            (
                "tp",
                {
                    "symbol": symbol,
                    "side": side,
                    "qty": qty,
                    "price": price,
                },
            )
        )
        if self.mode == FailureMode.TP_2021:
            return {"code": -2021, "msg": "Order would immediately trigger"}
        return {"algoId": "TP-001", "status": "NEW"}

    async def cancel_order(self, *args, **kwargs) -> dict[str, Any]:
        self.calls.append(("cancel", {"args": args, "kwargs": kwargs}))
        if self.mode == FailureMode.CANCEL_FAIL:
            raise RuntimeError("simulated cancel failure")
        return {"status": "CANCELED"}


class Expected(str, Enum):
    CLOSE = "close"
    RAISE = "raise"
    NO_RETRY = "no_retry"
    PROTECTED = "protected"
    UNPROTECTED = "unprotected"


@dataclass(frozen=True)
class Scenario:
    name: str
    mode: FailureMode
    side: str
    expected: tuple[Expected, ...]


SCENARIOS = (
    Scenario(
        "long_sl_2021",
        FailureMode.SL_2021,
        "long",
        (Expected.CLOSE, Expected.NO_RETRY, Expected.UNPROTECTED),
    ),
    Scenario(
        "short_sl_2021",
        FailureMode.SL_2021,
        "short",
        (Expected.CLOSE, Expected.NO_RETRY, Expected.UNPROTECTED),
    ),
    Scenario(
        "sl_generic_failure",
        FailureMode.SL_GENERIC,
        "long",
        (Expected.RAISE, Expected.UNPROTECTED),
    ),
    Scenario(
        "emergency_close_failure",
        FailureMode.CLOSE_FAIL,
        "long",
        (Expected.CLOSE, Expected.UNPROTECTED),
    ),
    Scenario(
        "partial_fill_uses_actual_qty",
        FailureMode.PARTIAL_FILL,
        "long",
        (Expected.PROTECTED,),
    ),
)
