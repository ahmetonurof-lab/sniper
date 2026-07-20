from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import config as cfg
from fvg import detect_fvgs
from models import Bar, FVG

log = logging.getLogger("sniper.trailing_manager")


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
    result: Literal["SL", "TP"] | None = None
    exit_price: float = 0.0


class TrailingManager:
    @staticmethod
    def _fvg_close_confirmed(fvg: FVG, bars: list[Bar]) -> bool:
        """FVG olustuktan sonraki barlardan en az biri FVG icinde kapandi mi?
        Sadece fitil degil, gövde kapanisi lazim — wick yetmez."""
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
        current = bars_15m[-1]  # validation icin son barin kapanisi
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
        trail_steps = trade["trail_steps"]
        updated = False
        atr_buffer = atr_val * cfg.ATR_TRAIL_MULT

        for fvg in fvgs:
            if side == "long" and fvg.direction != "bullish":
                continue
            if side == "short" and fvg.direction != "bearish":
                continue
            # Mitigation sarti: FVG icinde kapali 15m mumu olmali
            if not TrailingManager._fvg_close_confirmed(fvg, chunk):
                continue

            if side == "long":
                new_sl = fvg.bottom - atr_buffer
                # FVG kirildi — price new_sl seviyesini coktan gecti, trade bitmis
                if new_sl >= current.close:
                    return TrailResult(exit_now=True)
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
                # FVG kirildi — price new_sl seviyesini coktan gecti, trade bitmis
                if new_sl <= current.close:
                    return TrailResult(exit_now=True)
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
                    "[TRAIL] trail#%d sl=%.2f tp=%.2f",
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

    @staticmethod
    def check_exit(current: Bar, trade: dict) -> ExitDecision:
        side = trade["side"]
        sl = trade["sl"]
        tp = trade["tp"]
        if side == "long":
            if current.low <= sl:
                return ExitDecision(triggered=True, result="SL", exit_price=sl)
            elif current.high >= tp:
                return ExitDecision(triggered=True, result="TP", exit_price=tp)
        else:
            if current.high >= sl:
                return ExitDecision(triggered=True, result="SL", exit_price=sl)
            elif current.low <= tp:
                return ExitDecision(triggered=True, result="TP", exit_price=tp)
        return ExitDecision()
