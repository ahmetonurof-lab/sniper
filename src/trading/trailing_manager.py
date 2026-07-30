from __future__ import annotations

import logging
from dataclasses import dataclass
from decimal import Decimal, ROUND_CEILING, ROUND_FLOOR
from typing import Any, Callable, Literal, MutableMapping, Optional, Protocol, Sequence

import config as cfg
from fvg import detect_fvgs
from models import Bar, FVG
from paper_trade_logger import EventType, log_event as pt_log

log = logging.getLogger("sniper.trailing_manager")

Trade = MutableMapping[str, Any]
BarLike = Any


class PriceReader(Protocol):
    async def get_last_price(self, symbol: str) -> Decimal: ...


class ProtectionGateway(Protocol):
    async def replace_protection(
        self,
        *,
        trade: Trade,
        candidate: "TrailCandidate",
        current_price: Decimal,
    ) -> bool: ...


class ImmediateTriggerError(RuntimeError):
    """Exchange rejected the protection because it would trigger immediately."""


@dataclass(frozen=True)
class TrailLevel:
    price: Decimal
    source_bar_index: int
    reason: str


@dataclass(frozen=True)
class TrailCandidate:
    sl: Optional[Decimal]
    tp: Optional[Decimal]
    source_bar_index: int
    reason: str
    tick_size: Decimal
    fingerprint: str


@dataclass(frozen=True)
class TrailDecision:
    action: str  # updated | skip
    reason: str
    current_price: Decimal
    candidate: Optional[TrailCandidate] = None


@dataclass
class TrailResult:
    updated: bool = False
    new_sl: float = 0.0
    new_tp: float = 0.0
    trail_count: int = 0
    exit_now: bool = False


@dataclass
class ExitDecision:
    triggered: bool = False
    reason: str | None = None
    exit_price: float | Decimal = 0.0
    result: Literal["SL", "TP"] | None = None


@dataclass(frozen=True)
class TrailingConfig:
    default_tick_size: Decimal = Decimal("0.10")
    epsilon_ticks: int = 1
    pivot_strength: int = 2
    sl_buffer_ticks: int = 2


TrailLevelExtractor = Callable[[Sequence[BarLike], Trade], Optional[TrailLevel]]


class TrailingManager:
    def __init__(
        self,
        *,
        price_reader: PriceReader,
        protection_gateway: ProtectionGateway,
        config: TrailingConfig | None = None,
    ) -> None:
        self.price_reader = price_reader
        self.protection_gateway = protection_gateway
        self.config = config or TrailingConfig()

    def compute_trail_candidate(
        self,
        trade: Trade,
        bars: Sequence[BarLike],
    ) -> Optional[TrailCandidate]:
        side = self._side(trade)
        tick_size = self._tick_size(trade)
        entry_bar_index = int(self._required(trade, "entry_bar_index"))

        scoped_bars = self._scope_bars(bars, entry_bar_index)
        if not scoped_bars:
            return None

        extractor = trade.get("trail_level_extractor")
        level = self._extract_level(scoped_bars, trade, extractor)
        if level is None:
            return None

        raw_sl = self._raw_stop_from_level(level.price, side, tick_size)
        normalized_sl = self._normalize_price(
            raw_sl, side, kind="sl", tick_size=tick_size
        )

        current_sl = self._read_price(trade, "stop_loss", "sl")
        current_tp = self._read_price(trade, "take_profit", "tp")

        improved_sl = (
            normalized_sl
            if self._is_better_sl(side, normalized_sl, current_sl)
            else None
        )

        if improved_sl is None:
            return None

        improved_tp: Optional[Decimal] = None
        if current_sl is not None and current_tp is not None:
            sl_shift = improved_sl - current_sl
            new_tp_raw = current_tp + sl_shift
            improved_tp = self._normalize_price(
                new_tp_raw, side, kind="tp", tick_size=tick_size
            )

        fingerprint = self._fingerprint(
            side=side,
            sl=improved_sl,
            tp=improved_tp,
            source_bar_index=level.source_bar_index,
        )

        return TrailCandidate(
            sl=improved_sl,
            tp=improved_tp,
            source_bar_index=level.source_bar_index,
            reason=level.reason,
            tick_size=tick_size,
            fingerprint=fingerprint,
        )

    def is_placeable(
        self,
        candidate: TrailCandidate,
        current_price: Decimal,
        side: str,
    ) -> bool:
        side = side.lower()
        epsilon = candidate.tick_size * Decimal(self.config.epsilon_ticks)

        if candidate.sl is not None:
            if side == "long" and not (candidate.sl < current_price - epsilon):
                return False
            if side == "short" and not (candidate.sl > current_price + epsilon):
                return False

        if candidate.tp is not None:
            if side == "long" and not (candidate.tp > current_price + epsilon):
                return False
            if side == "short" and not (candidate.tp < current_price - epsilon):
                return False

        return True

    async def orchestrate_trail(
        self,
        trade: Trade,
        bars: Sequence[BarLike],
    ) -> TrailDecision:
        symbol = str(self._required(trade, "symbol"))
        side = self._side(trade)
        current_price = self._decimal(await self.price_reader.get_last_price(symbol))
        trade_id = f"{symbol}-{trade.get('entry_bar_index', '?')}"

        candidate = self.compute_trail_candidate(trade, bars)
        if candidate is None:
            pt_log(
                EventType.TRAIL_SKIPPED,
                symbol,
                side,
                trade_id=trade_id,
                reason="no_better_trail_candidate",
            )
            return TrailDecision(
                action="skip",
                reason="no better trail candidate",
                current_price=current_price,
                candidate=None,
            )

        protection_state = trade.setdefault("protection_state", {})

        if protection_state.get("last_applied_fingerprint") == candidate.fingerprint:
            pt_log(
                EventType.TRAIL_SKIPPED,
                symbol,
                side,
                trade_id=trade_id,
                reason="identical_candidate_already_applied",
            )
            return TrailDecision(
                action="skip",
                reason="identical candidate already applied",
                current_price=current_price,
                candidate=candidate,
            )

        if protection_state.get("last_invalid_fingerprint") == candidate.fingerprint:
            pt_log(
                EventType.TRAIL_SKIPPED,
                symbol,
                side,
                trade_id=trade_id,
                reason="identical_invalid_candidate_suppressed",
            )
            return TrailDecision(
                action="skip",
                reason="identical invalid candidate suppressed",
                current_price=current_price,
                candidate=candidate,
            )

        if not self.is_placeable(candidate, current_price, side):
            protection_state["last_invalid_fingerprint"] = candidate.fingerprint
            protection_state["last_invalid_reason"] = "local placeability check failed"
            pt_log(
                EventType.TRAIL_SKIPPED,
                symbol,
                side,
                trade_id=trade_id,
                reason="candidate_not_placeable",
            )
            return TrailDecision(
                action="skip",
                reason="candidate not placeable against current price",
                current_price=current_price,
                candidate=candidate,
            )

        try:
            changed = await self.protection_gateway.replace_protection(
                trade=trade,
                candidate=candidate,
                current_price=current_price,
            )
        except ImmediateTriggerError:
            protection_state["last_invalid_fingerprint"] = candidate.fingerprint
            protection_state["last_invalid_reason"] = (
                "exchange rejected with immediate trigger"
            )
            pt_log(
                EventType.TRAIL_SKIPPED,
                symbol,
                side,
                trade_id=trade_id,
                reason="exchange_rejected_immediate_trigger",
            )
            return TrailDecision(
                action="skip",
                reason="exchange rejected candidate as immediate trigger",
                current_price=current_price,
                candidate=candidate,
            )

        if not changed:
            pt_log(
                EventType.TRAIL_SKIPPED,
                symbol,
                side,
                trade_id=trade_id,
                reason="no_protection_update_required",
            )
            return TrailDecision(
                action="skip",
                reason="no protection update required",
                current_price=current_price,
                candidate=candidate,
            )

        if candidate.sl is not None:
            trade["stop_loss"] = float(candidate.sl)
        if candidate.tp is not None:
            trade["take_profit"] = float(candidate.tp)

        trade["trail_count"] = int(trade.get("trail_count", 0)) + 1
        protection_state.pop("last_invalid_fingerprint", None)
        protection_state.pop("last_invalid_reason", None)
        protection_state["last_applied_fingerprint"] = candidate.fingerprint

        pt_log(
            EventType.TRAIL_CANDIDATE,
            symbol,
            side,
            trade_id=trade_id,
            protection={
                "final_sl": float(candidate.sl) if candidate.sl else None,
                "final_tp": float(candidate.tp) if candidate.tp else None,
                "risk_distance": None,
                "tick_size": float(candidate.tick_size),
            },
            result="updated",
            reason=candidate.reason,
        )

        return TrailDecision(
            action="updated",
            reason="protection replaced",
            current_price=current_price,
            candidate=candidate,
        )

    def check_exit(self, current_bar: BarLike, trade: Trade) -> ExitDecision:
        side = self._side(trade)
        stop_loss = self._read_price(trade, "stop_loss", "sl")
        take_profit = self._read_price(trade, "take_profit", "tp")
        high = self._decimal(self._get(current_bar, "high"))
        low = self._decimal(self._get(current_bar, "low"))

        if side == "long":
            if stop_loss is not None and low <= stop_loss:
                return ExitDecision(
                    True, reason="stop_loss", exit_price=float(stop_loss), result="SL"
                )
            if take_profit is not None and high >= take_profit:
                return ExitDecision(
                    True,
                    reason="take_profit",
                    exit_price=float(take_profit),
                    result="TP",
                )
            return ExitDecision(False)

        if stop_loss is not None and high >= stop_loss:
            return ExitDecision(
                True, reason="stop_loss", exit_price=float(stop_loss), result="SL"
            )
        if take_profit is not None and low <= take_profit:
            return ExitDecision(
                True, reason="take_profit", exit_price=float(take_profit), result="TP"
            )
        return ExitDecision(False)

    @staticmethod
    def _scope_bars(bars: Sequence[BarLike], entry_bar_index: int) -> list[BarLike]:
        scoped: list[BarLike] = []
        for pos, bar in enumerate(bars):
            bar_index = int(TrailingManager._get(bar, "index", pos))
            if bar_index >= entry_bar_index:
                scoped.append(bar)
        return scoped

    def _extract_level(
        self,
        scoped_bars: Sequence[BarLike],
        trade: Trade,
        extractor: Any,
    ) -> Optional[TrailLevel]:
        if callable(extractor):
            return extractor(scoped_bars, trade)
        return self._default_level_from_swings(scoped_bars, self._side(trade))

    def _default_level_from_swings(
        self,
        scoped_bars: Sequence[BarLike],
        side: str,
    ) -> Optional[TrailLevel]:
        strength = self.config.pivot_strength
        if len(scoped_bars) < (strength * 2 + 1):
            return None

        snapshots = [
            {
                "index": int(self._get(bar, "index", pos)),
                "high": self._decimal(self._get(bar, "high")),
                "low": self._decimal(self._get(bar, "low")),
            }
            for pos, bar in enumerate(scoped_bars)
        ]

        if side == "long":
            for i in range(len(snapshots) - strength - 1, strength - 1, -1):
                curr = snapshots[i]
                left = snapshots[i - strength : i]
                right = snapshots[i + 1 : i + 1 + strength]
                if all(curr["low"] <= bar["low"] for bar in left) and all(
                    curr["low"] < bar["low"] for bar in right
                ):
                    return TrailLevel(
                        price=curr["low"],
                        source_bar_index=curr["index"],
                        reason="confirmed post-entry swing low",
                    )
            return None

        for i in range(len(snapshots) - strength - 1, strength - 1, -1):
            curr = snapshots[i]
            left = snapshots[i - strength : i]
            right = snapshots[i + 1 : i + 1 + strength]
            if all(curr["high"] >= bar["high"] for bar in left) and all(
                curr["high"] > bar["high"] for bar in right
            ):
                return TrailLevel(
                    price=curr["high"],
                    source_bar_index=curr["index"],
                    reason="confirmed post-entry swing high",
                )
        return None

    def _raw_stop_from_level(
        self,
        level_price: Decimal,
        side: str,
        tick_size: Decimal,
    ) -> Decimal:
        offset = tick_size * Decimal(self.config.sl_buffer_ticks)
        if side == "long":
            return level_price - offset
        return level_price + offset

    @staticmethod
    def _is_better_sl(
        side: str, candidate: Decimal, current: Optional[Decimal]
    ) -> bool:
        if current is None:
            return True
        return candidate > current if side == "long" else candidate < current

    @staticmethod
    def _fingerprint(
        *,
        side: str,
        sl: Optional[Decimal],
        tp: Optional[Decimal],
        source_bar_index: int,
    ) -> str:
        return f"{side}|{sl or '-'}|{tp or '-'}|{source_bar_index}"

    def _tick_size(self, trade: Trade) -> Decimal:
        return self._decimal(trade.get("tick_size", self.config.default_tick_size))

    @staticmethod
    def _side(trade: Trade) -> str:
        side = str(trade.get("side", "")).lower()
        if side not in {"long", "short"}:
            raise ValueError(f"unsupported side: {side!r}")
        return side

    @staticmethod
    def _required(container: MutableMapping[str, Any], key: str) -> Any:
        if key not in container:
            raise KeyError(f"missing required trade field: {key}")
        return container[key]

    @staticmethod
    def _get(obj: Any, field: str, default: Any = None) -> Any:
        if isinstance(obj, dict):
            return obj.get(field, default)
        return getattr(obj, field, default)

    @staticmethod
    def _decimal(value: Any) -> Decimal:
        return value if isinstance(value, Decimal) else Decimal(str(value))

    def _read_price(self, trade: Trade, *keys: str) -> Optional[Decimal]:
        for key in keys:
            if key in trade and trade[key] is not None:
                return self._decimal(trade[key])
        return None

    @staticmethod
    def _normalize_price(
        value: Decimal,
        side: str,
        *,
        kind: str,
        tick_size: Decimal,
    ) -> Decimal:
        ticks = value / tick_size
        if kind == "sl":
            rounding = ROUND_FLOOR if side == "long" else ROUND_CEILING
        else:
            rounding = ROUND_CEILING if side == "long" else ROUND_FLOOR
        return ticks.quantize(Decimal("1"), rounding=rounding) * tick_size

    @staticmethod
    def _fvg_close_confirmed(fvg: FVG, bars: list[Bar]) -> bool:
        scan_from = fvg.real_index + 2
        for b in bars:
            if b.index < scan_from:
                continue
            if not b.is_closed:
                break
            if fvg.direction == "bullish":
                if b.close < fvg.bottom:
                    return False
                if fvg.bottom <= b.close <= fvg.top:
                    return True
            else:
                if b.close > fvg.top:
                    return False
                if fvg.bottom <= b.close <= fvg.top:
                    return True
        return False

    @staticmethod
    def evaluate_trail(
        bars_15m: list[Bar],
        trade: dict,
        atr_val: float,
        min_fvg_size: float,
    ) -> TrailResult:
        if not bars_15m or len(bars_15m) <= 1:
            return TrailResult()

        chunk = bars_15m[:-1] if len(bars_15m) > 1 else bars_15m
        fvgs = detect_fvgs(
            chunk,
            lookback=min(50, len(chunk)),
            timeframe="15m",
            min_fvg_size=min_fvg_size,
        )

        side = trade["side"]
        current_sl = trade["sl"]
        current_tp = trade["tp"]
        risk_pts = trade.get(
            "risk_pts", abs(trade["initial_sl"] - trade["entry_price"])
        )
        trail_count = trade.get("trailing_count", 0)
        trail_steps = trade.get("trail_steps", [])
        updated = False
        atr_buffer = atr_val * cfg.ATR_TRAIL_MULT

        for fvg in fvgs:
            if side == "long" and fvg.direction != "bullish":
                continue
            if side == "short" and fvg.direction != "bearish":
                continue
            if not TrailingManager._fvg_close_confirmed(fvg, chunk):
                continue

            if side == "long":
                new_sl = fvg.bottom - atr_buffer
                if (
                    new_sl > current_sl
                    and (new_sl - current_sl) > risk_pts * cfg.TRAIL_MIN_MOVE_MULT
                ):
                    sl_diff = new_sl - current_sl
                    current_sl = new_sl
                    current_tp += sl_diff
                    trail_count += 1
                    updated = True
            else:
                new_sl = fvg.top + atr_buffer
                if (
                    new_sl < current_sl
                    and (current_sl - new_sl) > risk_pts * cfg.TRAIL_MIN_MOVE_MULT
                ):
                    sl_diff = current_sl - new_sl
                    current_sl = new_sl
                    current_tp -= sl_diff
                    trail_count += 1
                    updated = True

            if updated:
                trail_steps.append(
                    {
                        "sl": round(new_sl, 6),
                        "tp": round(current_tp, 6),
                        "fvg_top": round(fvg.top, 6),
                        "fvg_bot": round(fvg.bottom, 6),
                        "bar": fvg.real_index,
                    }
                )
                log.info(
                    "[TRAIL] trail#%d sl=%.6f tp=%.6f",
                    trail_count,
                    current_sl,
                    current_tp,
                )

        if updated:
            return TrailResult(
                updated=True,
                new_sl=current_sl,
                new_tp=current_tp,
                trail_count=trail_count,
            )
        return TrailResult()
