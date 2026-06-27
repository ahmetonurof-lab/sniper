"""
trailing_manager.py — 1m FVG trailing + exit kontrolü.

PaperTrader._on_1m_close() içindeki iki mantıksal bloğu kapsar:
  1. FVG Trailing: 15m FVG'ler üzerinden SL/TP güncelleme
  2. Exit Check: 1m barında SL/TP tetiklenme kontrolü

Kırmızı çizgiler:
  - Strateji mantığında sıfır değişiklik
  - _pl() formatına dokunulmaz (PaperTrader'da kalır)
  - Import yolları kırılmayacak
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal

import config as cfg
from fvg import detect_fvgs
from models import Bar

log = logging.getLogger("sniper.trailing_manager")


@dataclass
class TrailResult:
    """FVG trailing sonucu.

    Attributes:
        updated: Yeni SL/TP değerleri hesaplandı mı?
        new_sl: Yeni stop-loss seviyesi
        new_tp: Yeni take-profit seviyesi
        trail_count: Toplam trailing sayısı (1 artırılmış)
    """

    updated: bool = False
    new_sl: float = 0.0
    new_tp: float = 0.0
    trail_count: int = 0


@dataclass
class ExitDecision:
    """Exit kontrolü sonucu.

    Attributes:
        triggered: SL veya TP tetiklendi mi?
        result: "SL" veya "TP" (sadece triggered=True ise)
        exit_price: Çıkış fiyatı
    """

    triggered: bool = False
    result: Literal["SL", "TP"] | None = None
    exit_price: float = 0.0


class TrailingManager:
    """1m barında FVG trailing + exit kontrolü.

    Tüm metodlar statik/saf — PaperTrader state'ine erişmez.
    Test edilebilirlik: Mock gerektirmez.
    """

    # ── FVG Trailing ──────────────────────────────────────────

    @staticmethod
    def evaluate_trail(
        bars_15m: list[Bar],
        trade: dict,
        fvg_buffer_mult: float,
        min_fvg_size: float,
    ) -> TrailResult:
        """15m FVG'lere göre SL trailing uygula.

        Orijinal _on_1m_close() FVG trailing döngüsü ile birebir aynı mantık:
          - Long: bullish FVG bottom - buffer > mevcut SL → trail
          - Short: bearish FVG top + buffer < mevcut SL → trail
          - Min move filtresi: risk_pts * 0.2
          - TP de SL kadar kaydırılır (RR korunur)

        Returns:
            TrailResult — updated=True ise yeni SL/TP değerleri dolu.
        """
        if not bars_15m or len(bars_15m) <= 1:
            return TrailResult()

        chunk = bars_15m[:-1] if len(bars_15m) > 1 else bars_15m
        fvgs = detect_fvgs(
            chunk,
            lookback=min(50, len(chunk)),
            timeframe="15m",
            min_fvg_size=min_fvg_size,
        )

        buffer = abs(trade["initial_sl"] - trade["entry_price"]) * fvg_buffer_mult
        side = trade["side"]
        current_sl = trade["sl"]
        current_tp = trade["tp"]
        risk_pts = trade.get(
            "risk_pts", abs(trade["initial_sl"] - trade["entry_price"])
        )
        trail_count = trade.get("trailing_count", 0)
        trail_steps = trade.get("trail_steps")
        updated = False

        for fvg in fvgs:
            if side == "long" and fvg.direction != "bullish":
                continue
            if side == "short" and fvg.direction != "bearish":
                continue
            if fvg.filled or fvg.invalidated:
                continue

            if side == "long":
                new_sl = fvg.bottom - buffer
                if new_sl > current_sl:
                    min_move = risk_pts * cfg.TRAIL_MIN_MOVE_MULT
                    if (new_sl - current_sl) <= min_move:
                        continue
                    sl_diff = new_sl - current_sl
                    current_sl = new_sl
                    current_tp += sl_diff
                    trail_count += 1
                    updated = True
                    trail_steps.append(
                        {
                            "sl": round(new_sl, 6),
                            "tp": round(current_tp, 6),
                            "fvg_top": round(fvg.top, 6),
                            "fvg_bot": round(fvg.bottom, 6),
                            "bar": fvg.real_index,
                        }
                    )
                    trade["trail_steps"] = trail_steps
                    log.info(
                        "[TRAIL] trail#%d sl=%.2f tp=%.2f",
                        trail_count,
                        current_sl,
                        current_tp,
                    )
            else:
                new_sl = fvg.top + buffer
                if new_sl < current_sl:
                    min_move = risk_pts * cfg.TRAIL_MIN_MOVE_MULT
                    if (current_sl - new_sl) <= min_move:
                        continue
                    sl_diff = current_sl - new_sl
                    current_sl = new_sl
                    current_tp -= sl_diff
                    trail_count += 1
                    updated = True
                    trail_steps.append(
                        {
                            "sl": round(new_sl, 6),
                            "tp": round(current_tp, 6),
                            "fvg_top": round(fvg.top, 6),
                            "fvg_bot": round(fvg.bottom, 6),
                            "bar": fvg.real_index,
                        }
                    )
                    trade["trail_steps"] = trail_steps
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

    # ── Exit Check ────────────────────────────────────────────

    @staticmethod
    def check_exit(current: Bar, trade: dict) -> ExitDecision:
        """1m barında SL veya TP tetiklendi mi?

        Orijinal _on_1m_close() exit kontrolü ile birebir aynı mantık:
          - Long: low <= sl → SL, high >= tp → TP
          - Short: high >= sl → SL, low <= tp → TP
        """
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
