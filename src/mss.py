"""
choch.py
────────
Nexus SMC Trading Bot — Change of Character (CHoCH) Modülü (Final Merge)
File 1 SMC mikro-yapı filtreleri + File 2 pipeline/state yönetimi birleştirildi.
KRİTİK KURAL: `timestamp` SADECE `CHoCH` dataclass'ında set edilir.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Final, Literal

import config
from indicators import compute_atr_series as _compute_atr_series
from models import (
    Bar,
    CHoCH,
    SwingPoint,
    tf_params,
)
from pivot import SwingStateManager

logger = logging.getLogger("nexus.choch")

# ──────────────────────────────────────────────────────────
# SABITLER & YAPILANDIRMA
# ──────────────────────────────────────────────────────────
MIN_CHOCH_ATR_MULT: Final[float] = 0.15

# Sembol bazlı periyodik cleanup sayacı (thread-safe dict)
_SYMBOL_COUNTERS: dict[str, int] = {}


# ──────────────────────────────────────────────────────────
# 1. SMC MİKRO-YAPI YARDIMCILARI
# ──────────────────────────────────────────────────────────
def _is_convincing_break(
    break_bar: Bar,
    level: float,
    avg_body: float,
    atr_val: float,
    direction: Literal["bullish", "bearish"],
    bars_after: list[Bar],
    sfp_n: int,
) -> bool:
    """
    Kırılma kalitesi & SFP follow-through filtresi.
    avg_body/atr==0 fallback'leri içerir.
    """
    body_ok = atr_ok = False

    if avg_body > 0:
        body_ok = break_bar.body >= avg_body * config.CHoCH_MIN_BODY_RATIO
    else:
        bar_range = break_bar.high - break_bar.low
        body_ok = bar_range > 0 and break_bar.body >= bar_range * 0.3

    if atr_val > 0:
        atr_ok = abs(break_bar.close - level) >= atr_val * config.CHoCH_ATR_OVERSHOOT
    else:
        atr_ok = (direction == "bearish" and break_bar.close < level) or (
            direction == "bullish" and break_bar.close > level
        )

    if not (body_ok or atr_ok):
        return False

    # SFP Follow-through
    confirmations = 0
    for fb in bars_after[:sfp_n]:
        if not fb.is_closed:
            break
        if direction == "bearish" and fb.close < level:
            confirmations += 1
        elif direction == "bullish" and fb.close > level:
            confirmations += 1

    return confirmations >= sfp_n


def _resolve_outside_bar_priority(
    bar: Bar,
    swing_highs: list[SwingPoint],
    swing_lows: list[SwingPoint],
) -> Literal["bearish", "bullish", "both", "none"]:
    """
    Tek bar hem high hem low süpürüyorsa fitil uzunluğuna göre öncelik belirler.
    Eşitlikte SMC convention gereği bearish baskın kabul edilir.
    """
    breaks_high = any(bar.close > sp.price for sp in swing_highs)
    breaks_low = any(bar.close < sp.price for sp in swing_lows)

    if breaks_high and breaks_low:
        uw, lw = bar.upper_wick, bar.lower_wick
        if lw > uw:
            return "bearish"
        elif uw > lw:
            return "bullish"
        return "bearish"
    if breaks_high:
        return "bullish"
    if breaks_low:
        return "bearish"
    return "none"


# ──────────────────────────────────────────────────────────
# 2. MSS TESPİTİ (Hybrid Pipeline)
# ──────────────────────────────────────────────────────────


def _detect_bar_direction(
    bar: Bar,
    bar_pos: int,
    atr_val: float,
    swing_map: dict[int, SwingPoint],
    direction: Literal["bullish", "bearish"],
    body_lookback: int,
    sfp_n: int,
    atr_mult: float,
    bars: list[Bar],
    swing_mgr: SwingStateManager,
    timeframe: str,
) -> CHoCH | None:
    """Tek bar için tek yönlü MSS tespiti. CHoCH döndürür veya None."""
    bar_close = bar.close
    bar_abs = bar.index
    is_bull = direction == "bullish"

    best_sp: SwingPoint | None = None
    mitigated: list[SwingPoint] = []

    for _sp_abs, sp in swing_map.items():
        if sp.bar_index >= bar_abs or sp.mitigated:
            continue
        if (is_bull and bar_close > sp.price) or (not is_bull and bar_close < sp.price):
            if best_sp is None or (
                (is_bull and sp.price > best_sp.price) or (not is_bull and sp.price < best_sp.price)
            ):
                best_sp = sp
            mitigated.append(sp)

    if best_sp is None:
        return None

    penetration = (bar_close - best_sp.price) if is_bull else (best_sp.price - bar_close)
    passes_size_filter = atr_val <= 0 or penetration >= atr_val * atr_mult

    if passes_size_filter:
        swing_highs = [best_sp] if is_bull else []
        swing_lows = [] if is_bull else [best_sp]
        prio = _resolve_outside_bar_priority(bar, swing_highs, swing_lows)
        if prio != direction:
            body_start = max(0, bar_pos - body_lookback)
            local_bodies = [bars[x].body for x in range(body_start, bar_pos) if bars[x].is_closed]
            local_avg_body = sum(local_bodies) / len(local_bodies) if local_bodies else 0.0
            bars_after = bars[bar_pos + 1 : bar_pos + 1 + sfp_n]

            if not _is_convincing_break(bar, best_sp.price, local_avg_body, atr_val, direction, bars_after, sfp_n):
                passes_size_filter = False

    if not passes_size_filter:
        logger.debug("[CHoCH] %s veto @ %d — pivotlar korunuyor", direction.capitalize(), bar_abs)
        return None

    # Strength: penetration + SFP follow-through bileşik skoru
    pen_ratio = max(0.0, min(1.0, penetration / (atr_val * config.CHoCH_ATR_OVERSHOOT))) if atr_val > 0 else 0.0
    _bars_after = bars[bar_pos + 1 : bar_pos + 1 + sfp_n]
    _confirmations = 0
    for fb in _bars_after[:sfp_n]:
        if not fb.is_closed:
            break
        if (is_bull and fb.close > best_sp.price) or (not is_bull and fb.close < best_sp.price):
            _confirmations += 1
    sfp_ratio = _confirmations / sfp_n if sfp_n and sfp_n > 0 else 0.0
    strength = round(max(0.0, min(1.0, pen_ratio * 0.6 + sfp_ratio * 0.4)), 3)

    choch = CHoCH(
        direction=direction,
        level=best_sp.price,
        bar_index=bar_abs,
        pivot_bar_index=best_sp.bar_index,
        timeframe=timeframe,
        timestamp=bar.timestamp,
        strength=strength,
    )
    logger.info("[CHoCH] %s @ %d (level=%.5f)", direction.capitalize(), bar_abs, best_sp.price)

    # Mitigation SADECE sinyal geçerliyse yapılır.
    mit_key = "high" if is_bull else "low"
    for sp_m in mitigated:
        object.__setattr__(sp_m, "mitigated", True)
        swing_mgr.mark_mitigated(mit_key, sp_m.bar_index)

    return choch


def detect_mss(
    bars: list[Bar],
    swing_mgr: SwingStateManager,
    lookback: int | None = None,  # None → config'den dinamik hesapla
    timeframe: str = "5m",
    atr_series: list[float] | None = None,
    atr_mult: float = MIN_CHOCH_ATR_MULT,
) -> list[CHoCH]:
    """
    O(N) tarama + ATR size filter + SMC mikro-yapı veto + pivot mitigation.
    """
    # ── Dinamik lookback: timeframe'e göre saat bazında hesapla ──
    if lookback is None:
        _tf_minutes = {"5m": 5, "15m": 15, "1h": 60, "4h": 240}
        tf_min = _tf_minutes.get(timeframe, 15)
        lookback = int(config.CHOCH_MAX_AGE_HOURS * 60 / tf_min)

    if len(bars) < lookback:
        segment = bars
    else:
        segment = bars[-lookback:]

    if atr_series is None:
        atr_series = _compute_atr_series(bars, period=config.CHoCH_ATR_PERIOD)

    break_window, body_lookback, sfp_n = tf_params(timeframe)
    active_high_map: dict[int, SwingPoint] = {p.bar_index: p for p in swing_mgr.active_highs()}
    active_low_map: dict[int, SwingPoint] = {p.bar_index: p for p in swing_mgr.active_lows()}

    found: list[CHoCH] = []
    first_abs = bars[0].index

    for bar in segment:
        # KRİTİK 1: Kapanmamış mum kesinlikle atlanır
        if not bar.is_closed:
            continue

        bar_abs = bar.index
        bar_pos = bar_abs - first_abs  # KRİTİK 2: Birebir indeks hizalaması
        atr_val = atr_series[bar_pos] if 0 <= bar_pos < len(atr_series) else 0.0

        # ── Bullish MSS ──────────────────────────────────
        bull = _detect_bar_direction(
            bar,
            bar_pos,
            atr_val,
            active_high_map,
            "bullish",
            body_lookback,
            sfp_n,
            atr_mult,
            bars,
            swing_mgr,
            timeframe,
        )
        if bull is not None:
            found.append(bull)

        # ── Bearish MSS ──────────────────────────────────
        bear = _detect_bar_direction(
            bar,
            bar_pos,
            atr_val,
            active_low_map,
            "bearish",
            body_lookback,
            sfp_n,
            atr_mult,
            bars,
            swing_mgr,
            timeframe,
        )
        if bear is not None:
            found.append(bear)

    return found


def create_mss_event(symbol: str, timeframe: str, direction: str, level: float, timestamp: int) -> dict:
    """Converts a structural Market Structure Shift (MSS) into a normalized V3 market event."""
    return {
        "type": "MSS",
        "tf": timeframe,
        "direction": direction,  # "LONG" veya "SHORT"
        "level": float(level),
        "time": int(timestamp),
    }


# ═══════════════════════════════════════════════════════════════
# 3. LTF Trigger Detector — V1
# ═══════════════════════════════════════════════════════════════
# İki kriter — ikisi de TRUE olursa LTF confirm geçerli sayılır:
#
#   1. body >= BODY_ATR_MULT × ATR(14)
#      Mumun gövdesi yeterince büyük — wick değil, kararlı kapanış.
#
#   2. close > last_retracement_swing_high  (LONG)
#      close < last_retracement_swing_low   (SHORT)
#      Retracement sürecinde oluşan son karşı-yön pivot kırıldı —
#      dönüş başladı teyiti.
#
# NOT: retracement_swing, analyzer.py tarafından hesaplanıp
#      validate() çağrısına parametre olarak verilir.
# ═══════════════════════════════════════════════════════════════


_DEFAULT_BODY_ATR_MULT: Final[float] = 0.5  # başlangıç değeri; 0.6–0.8 test edilecek
_DEFAULT_ATR_PERIOD: Final[int] = 14


@dataclass
class LTFTriggerResult:
    """Dedektör çıktısı — state machine bu dataclass'ı okur."""

    is_valid: bool = False
    body_ok: bool = False
    close_ok: bool = False
    # eski alanlar — geriye dönük uyumluluk için korundu, kullanılmıyor
    volume_ok: bool = False
    fvg_ok: bool = False
    volume_val: float = 0.0
    volume_sma_val: float = 0.0
    body_val: float = 0.0
    atr_val: float = 0.0
    direction: Literal["bullish", "bearish"] | None = None
    reason: str = ""


class LTFTriggerDetector:
    """
    LTF Confirm V1 — İki kriter:
        1. Güçlü gövde  : bar.body >= body_atr_mult × ATR(14)
        2. Pivot kırılımı: close, retracement swing'ini geçti mi?

    validate() → LTFTriggerResult
    """

    def __init__(
        self,
        body_atr_mult: float = _DEFAULT_BODY_ATR_MULT,
        atr_period: int = _DEFAULT_ATR_PERIOD,
    ) -> None:
        self.body_atr_mult = body_atr_mult
        self.atr_period = atr_period

    # ── ATR yardımcısı ──────────────────────────────────────

    @staticmethod
    def _atr(bars: list[Bar], period: int = 14) -> float:
        if len(bars) < period + 1:
            return 0.0
        tr_sum = 0.0
        for i in range(len(bars) - period, len(bars)):
            h, lo, pc = bars[i].high, bars[i].low, bars[i - 1].close
            tr_sum += max(h - lo, abs(h - pc), abs(lo - pc))
        return tr_sum / period

    # ── Kriter 1: Güçlü gövde ───────────────────────────────

    @staticmethod
    def _chk_body(bar: Bar, atr: float, mult: float) -> tuple[bool, float]:
        """bar.body >= mult × ATR"""
        return bar.body >= atr * mult, bar.body

    # ── Kriter 2: Retracement pivot kırılımı ────────────────

    @staticmethod
    def _chk_close(
        bar: Bar,
        direction: Literal["bullish", "bearish"],
        retracement_swing: SwingPoint | None,
    ) -> bool:
        """
        LONG : close > retracement_swing.price  (son 5m swing high kırıldı)
        SHORT: close < retracement_swing.price  (son 5m swing low kırıldı)
        swing None ise False — analyzer pivot bulamadıysa confirm yok.
        """
        if retracement_swing is None:
            return False
        if direction == "bullish":
            return bar.close > retracement_swing.price
        return bar.close < retracement_swing.price

    # ── Ana giriş ────────────────────────────────────────────

    def validate(
        self,
        bars: list[Bar],
        direction: Literal["bullish", "bearish"],
        retracement_swing: SwingPoint | None = None,
    ) -> LTFTriggerResult:
        """
        bars      : son N adet 5m bar (en az atr_period + 2 gerekli)
        direction : "bullish" (LONG setup) | "bearish" (SHORT setup)
        retracement_swing : analyzer.py'ın bulduğu son karşı-yön pivot
        """
        result = LTFTriggerResult(direction=direction)

        if len(bars) < self.atr_period + 2:
            result.reason = f"[LTF-V1] Yetersiz bar: {len(bars)} < {self.atr_period + 2}"
            logger.debug(result.reason)
            return result

        cur = bars[-1]
        atr = self._atr(bars, self.atr_period)
        result.atr_val = atr

        # ── Kriter 1 ──
        result.body_ok, result.body_val = self._chk_body(cur, atr, self.body_atr_mult)

        # ── Kriter 2 ──
        result.close_ok = self._chk_close(cur, direction, retracement_swing)

        # ── Sadece close_ok — body sadece log'da ──
        result.is_valid = result.body_ok and result.close_ok
        swing_price = retracement_swing.price if retracement_swing else None
        result.reason = (
            f"[LTF] body_ok={result.body_ok} close_ok={result.close_ok} | "
            f"dir={direction} close={cur.close:.5f} "
            f"body={result.body_val:.5f} (ATR×{self.body_atr_mult}={atr * self.body_atr_mult:.5f}) "
            f"swing={swing_price:.5f}"
        )
        logger.info(result.reason)
        return result
