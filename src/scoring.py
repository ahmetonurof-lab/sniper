"""
scoring.py
──────────
Nexus SMC Trading Bot — Birleşik sinyal skorlama, yön tayini,
risk/ödül hesaplama ve piyasa rejimi tespiti katmanı.

Bağımlılıklar: models, indicators, fvg, config
Döngüsel Import Riski: TYPE_CHECKING + lazy resolution ile sıfırlandı.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

import config
from fvg import (
    _get_vp_status,
    compute_fvg_quality,
    find_latest_unfilled_fvg,
    is_premium_discount_valid,
    is_retesting_fvg,
    score_displacement,
    score_fvg_size,
    score_retest,
    score_sweep,
)
from indicators import (
    clamp,
    compute_adx,
    compute_atr_series as _indicators_atr_series,
    compute_ema100,
    compute_ema200,
)
from models import FVG, Bar, CHoCH, FVGQuality

if TYPE_CHECKING:
    from volume_profile import VPLevels

logger = logging.getLogger("nexus.scoring")

# ─────────────────────────────────────────────────────────
# Sabitler & Kalibrasyon Parametreleri (Config'den çekilebilir)
# ─────────────────────────────────────────────────────────
DEFAULT_ATR_PERIOD: int = 14
MIN_CONFIDENCE_THRESHOLD: float = getattr(config, "MIN_CONFIDENCE_THRESHOLD", 0.55)
STRONG_CONFIDENCE_THRESHOLD: float = getattr(config, "STRONG_CONFIDENCE_THRESHOLD", 0.75)
MAX_SIGNAL_AGE_BARS: int = 100
DEFAULT_LOOKBACK: int = 100

# Rejim & Konfluens Kalibrasyon Katsayıları
CONFLUENCE_WEIGHT: float = 0.05
MAX_CONFLUENCE_BONUS: float = 0.20
REGIME_PENALTY_RANGE: float = 0.85
REGIME_PENALTY_VOLATILE: float = 0.75
REGIME_BONUS_TREND: float = 1.10
REGIME_PENALTY_COUNTER_TREND: float = 0.70


# ─────────────────────────────────────────────────────────
# Veri Yapıları
# ─────────────────────────────────────────────────────────
@dataclass
class TradeSignal:
    """Birleşik alım/satım sinyali."""

    direction: Literal["LONG", "SHORT", "NEUTRAL"]
    confidence: float  # 0.0 - 1.0
    fvg_quality: FVGQuality | None
    choch_score: float
    choch_direction: str
    entry_zone_low: float
    entry_zone_high: float
    stop_loss: float
    take_profit_1: float
    take_profit_2: float
    risk_reward_ratio: float
    market_regime: str  # "trending_up", "trending_down", "ranging", "volatile"
    confluence_count: int
    timestamp: int = 0


@dataclass
class ScoringContext:
    """Skorlama için gerekli tüm bağlam verisi (Dependency Injection Container)."""

    bars: list[Bar]
    fvgs: list[FVG]
    chochs: list[CHoCH]
    current_price: float
    atr: float
    atr_series: list[float]
    adx: float
    ema100: float
    ema200: float
    timeframe: str = "5m"
    vp_status: str = "none"


# ─────────────────────────────────────────────────────────
# 1. ScoringContext OluÅŸturma
# ─────────────────────────────────────────────────────────
def build_scoring_context(
    bars: list[Bar],
    fvgs: list[FVG],
    chochs: list[CHoCH],
    timeframe: str = "5m",
    vp: VPLevels | None = None,
    current_fvg: FVG | None = None,
) -> ScoringContext:
    """Tüm göstergeleri hesaplayarak ScoringContext döner."""
    if not bars:
        return ScoringContext(
            bars=[],
            fvgs=[],
            chochs=[],
            current_price=0.0,
            atr=0.0,
            atr_series=[],
            adx=0.0,
            ema100=math.nan,
            ema200=math.nan,
            timeframe=timeframe,
            vp_status="none",
        )

    current_price = bars[-1].close
    atr_series = _indicators_atr_series(bars, DEFAULT_ATR_PERIOD)
    atr = atr_series[-1] if atr_series else 0.0
    adx = compute_adx(bars)
    ema100 = compute_ema100(bars)
    ema200 = compute_ema200(bars)

    vp_status = "none"
    if vp is not None and current_fvg is not None:
        try:
            vp_status = _get_vp_status(current_fvg, vp)
        except Exception:
            logger.exception("[SCORING] VP status hesaplama hatası, fallback 'none'")
            vp_status = "none"

    return ScoringContext(
        bars=bars,
        fvgs=fvgs,
        chochs=chochs,
        current_price=current_price,
        atr=atr,
        atr_series=atr_series,
        adx=adx,
        ema100=ema100,
        ema200=ema200,
        timeframe=timeframe,
        vp_status=vp_status,
    )


# ─────────────────────────────────────────────────────────
# 2. Piyasa Rejimi Tespiti
# ─────────────────────────────────────────────────────────
def detect_market_regime(
    bars: list[Bar],
    adx: float,
    ema100: float,
    ema200: float,
    current_price: float,
) -> str:
    """ADX, EMA konumu ve fiyat hareketine göre piyasa rejimini belirler."""
    if len(bars) < 50:
        return "ranging"

    if adx >= 30:
        if not math.isnan(ema100) and not math.isnan(ema200):
            if current_price > ema100 > ema200:
                return "trending_up"
            if current_price < ema100 < ema200:
                return "trending_down"
        # EMA fallback: short vs long MA
        if len(bars) >= 50:
            short_ma = sum(b.close for b in bars[-20:]) / 20
            long_ma = sum(b.close for b in bars[-50:]) / 50
            return "trending_up" if short_ma > long_ma else "trending_down"
        return "trending_up" if adx >= 25 else "ranging"

    elif adx >= 20:
        if not math.isnan(ema100) and not math.isnan(ema200):
            if abs(current_price - ema100) / abs(ema100) < 0.02:
                return "ranging"
            return "trending_up" if current_price > ema100 else "trending_down"
        return "ranging"

    else:
        # Düşük ADX → ranging veya volatile
        if len(bars) >= 14:
            atr_series = _indicators_atr_series(bars, 14)
            recent_atr = atr_series[-1] if atr_series else 0.0
            older_atr = sum(atr_series[-14:-1]) / 13 if len(atr_series) >= 14 else recent_atr
            if older_atr > 0 and recent_atr > older_atr * 1.5:
                return "volatile"
        return "ranging"


# ─────────────────────────────────────────────────────────
# 3. FVG Bileşen Skorlarının Hesaplanması
# ─────────────────────────────────────────────────────────
def compute_fvg_component_scores(
    fvg: FVG,
    bars: list[Bar],
    atr: float,
    atr_series: list[float],
    current_price: float,
) -> tuple[float, float, float, float, int]:
    """Tek bir FVG için displacement, size, sweep, retest alt skorlarını hesaplar."""
    first_abs = bars[0].index
    fvg_pos = fvg.real_index - first_abs

    if fvg_pos < 0 or fvg_pos >= len(bars):
        return 0.0, 0.0, 0.0, 0.0, 999

    mother_bar = bars[fvg_pos]
    d = score_displacement(mother_bar, atr, fvg.direction)
    f = score_fvg_size(fvg, atr)
    s = score_sweep(bars, fvg, lookback=5)

    # Retest kontrolü
    bars_since = len(bars) - 1 - fvg_pos
    r = 0.0
    if is_retesting_fvg(fvg, bars[-1], atr):
        r = score_retest(bars_since)
    else:
        for offset in range(1, min(bars_since, 20)):
            check_bar = bars[fvg_pos + offset]
            if is_retesting_fvg(fvg, check_bar, atr):
                r = score_retest(offset)
                break

    return d, f, s, r, bars_since


# ─────────────────────────────────────────────────────────
# 4. CHoCH Skor Entegrasyonu
# ─────────────────────────────────────────────────────────
def _get_choch_score_for_direction(
    chochs: list[CHoCH],
    bars: list[Bar],
    fvg_direction: str,
    atr_series: list[float],
    adx: float,
) -> tuple[float, str]:
    """CHoCH listesinden FVG yönüne uygun en güncel CHoCH'ün skorunu döner."""
    if not chochs:
        return 0.0, ""

    matching = [c for c in chochs if c.direction == fvg_direction]
    if not matching:
        if any(c.direction != fvg_direction for c in chochs):
            logger.debug("[SCORING] CHoCH yön uyuşmazlığı: FVG=%s, zıt CHoCH mevcut.", fvg_direction)
        return 0.0, ""

    best = max(matching, key=lambda c: (c.strength, c.bar_index))
    first_abs = bars[0].index
    choch_pos = best.bar_index - first_abs

    if choch_pos < 0 or choch_pos >= len(atr_series):
        return 0.0, ""

    atr_val = atr_series[choch_pos]
    if atr_val <= 0:
        return 0.0, ""

    # Kırılım büyüklüğü skoru
    if choch_pos < len(bars):
        break_bar = bars[choch_pos]
        penetration = abs(break_bar.close - best.level)
        pen_ratio = clamp(penetration / (atr_val * 0.5), 0.0, 1.0)
        pen_score = pen_ratio * 0.40
    else:
        pen_score = 0.20

    # Onay skoru
    confirmation_count = 0
    for i in range(choch_pos + 1, len(bars)):
        b = bars[i]
        if not getattr(b, "is_closed", True):
            break
        if fvg_direction == "bullish" and b.close > best.level:
            confirmation_count += 1
        elif fvg_direction == "bearish" and b.close < best.level:
            confirmation_count += 1
        else:
            break

    if confirmation_count >= 2:
        confirmation_score = 0.25
    elif confirmation_count >= 1:
        confirmation_score = 0.15
    else:
        confirmation_score = 0.05

    # ADX katkısı
    adx_norm = clamp(adx / 50.0, 0.0, 1.0)
    adx_score = adx_norm * 0.20

    # Pivot yaşı skoru
    pivot_age = best.bar_index - best.pivot_bar_index
    age_norm = clamp(pivot_age / 50.0, 0.0, 1.0)
    age_score = age_norm * 0.15

    total = pen_score + confirmation_score + adx_score + age_score
    return clamp(total, 0.0, 1.0), best.direction


# ─────────────────────────────────────────────────────────
# 5. Konfluens (Çoklu Sinyal Uyumu) Analizi
# ─────────────────────────────────────────────────────────
def analyze_confluence(ctx: ScoringContext, fvg_direction: str) -> tuple[int, list[str]]:
    """FVG yönüyle aynı yönde kaç bağımsız sinyal olduğunu sayar."""
    active: list[str] = []
    count = 0

    # 1. FVG varlığı
    active.append("FVG")
    count += 1

    # 2. CHoCH uyumu
    if any(c.direction == fvg_direction for c in ctx.chochs):
        active.append("CHoCH")
        count += 1

    # 3. EMA hizalaması
    if not math.isnan(ctx.ema100) and not math.isnan(ctx.ema200):
        if (fvg_direction == "bullish" and ctx.ema100 > ctx.ema200) or (
            fvg_direction == "bearish" and ctx.ema100 < ctx.ema200
        ):
            active.append("EMA_alignment")
            count += 1

    # 4. Fiyat-EMA iliÅŸkisi
    if not math.isnan(ctx.ema100):
        if (fvg_direction == "bullish" and ctx.current_price > ctx.ema100) or (
            fvg_direction == "bearish" and ctx.current_price < ctx.ema100
        ):
            active.append("Price_EMA100")
            count += 1

    # 5. ADX trend gücü
    if ctx.adx >= 20:
        active.append("ADX_trend")
        count += 1

    # 6. Premium/Discount
    if is_premium_discount_valid(ctx.bars, ctx.current_price, fvg_direction, lookback=50):
        active.append("Premium/Discount")
        count += 1

    # 7. Volume Profile avantajı
    if ctx.vp_status == "LVN":
        active.append("VP_LVN")
        count += 1

    return count, active


# ─────────────────────────────────────────────────────────
# 6. Giriş / Çıkış Bölgeleri ve Risk Yönetimi
# ─────────────────────────────────────────────────────────
def compute_entry_exit_zones(fvg: FVG, atr: float, current_price: float, direction: str) -> dict[str, float]:
    """FVG ve ATR bazlı giriş bölgesi, stop loss ve take profit seviyelerini hesaplar."""
    if direction == "bullish":
        entry_low = fvg.bottom
        entry_high = fvg.midpoint + fvg.size * 0.25
        stop_loss = fvg.bottom - atr * 0.5
        tp1 = fvg.top + atr * 1.0
        tp2 = fvg.top + atr * 2.0
    else:
        entry_low = fvg.midpoint - fvg.size * 0.25
        entry_high = fvg.top
        stop_loss = fvg.top + atr * 0.5
        tp1 = fvg.bottom - atr * 1.0
        tp2 = fvg.bottom - atr * 2.0

    return {"entry_low": entry_low, "entry_high": entry_high, "stop_loss": stop_loss, "tp1": tp1, "tp2": tp2}


def calculate_rr_ratio(entry: float, stop_loss: float, take_profit: float) -> float:
    """Risk/Ödül oranını hesaplar."""
    risk = abs(entry - stop_loss)
    reward = abs(take_profit - entry)
    return 0.0 if risk <= 0 else reward / risk


# ─────────────────────────────────────────────────────────
# 7. Ana Skorlama Fonksiyonu
# ─────────────────────────────────────────────────────────
def evaluate_trade_signal(
    ctx: ScoringContext,
    fvg_direction: str | None = None,
    min_confidence: float = MIN_CONFIDENCE_THRESHOLD,
    vp: VPLevels | None = None,
) -> TradeSignal:
    """Tüm sinyal kaynaklarını değerlendirerek birleşik TradeSignal üretir."""
    if not ctx.bars or ctx.atr <= 0:
        return TradeSignal(
            direction="NEUTRAL",
            confidence=0.0,
            fvg_quality=None,
            choch_score=0.0,
            choch_direction="",
            entry_zone_low=0.0,
            entry_zone_high=0.0,
            stop_loss=0.0,
            take_profit_1=0.0,
            take_profit_2=0.0,
            risk_reward_ratio=0.0,
            market_regime="ranging",
            confluence_count=0,
            timestamp=ctx.bars[-1].timestamp if ctx.bars else 0,
        )

    directions_to_check = ["bullish", "bearish"] if fvg_direction is None else [fvg_direction]
    best_signal: TradeSignal | None = None
    best_confidence = -1.0

    for direction in directions_to_check:
        fvg = find_latest_unfilled_fvg(ctx.fvgs, direction)
        if fvg is None:
            logger.debug("[SCORING] %s yönünde açık FVG bulunamadı.", direction)
            continue

        # 2. FVG bileşen skorları
        d, f, s, r, _ = compute_fvg_component_scores(fvg, ctx.bars, ctx.atr, ctx.atr_series, ctx.current_price)

        # 3. CHoCH skoru
        choch_score, choch_dir = _get_choch_score_for_direction(
            ctx.chochs,
            ctx.bars,
            direction,
            ctx.atr_series,
            ctx.adx,
        )

        # 4. FVG kalite skoru (Veto + ağırlıklandırma)
        fvg_quality = compute_fvg_quality(
            bars_tf=ctx.bars,
            current_price=ctx.current_price,
            fvg=fvg,
            adx=ctx.adx,
            d=d,
            f=f,
            s=s,
            r=r,
            choch_score=choch_score,
            choch_direction=choch_dir,
            vp=vp,
        )

        if fvg_quality.score <= 0:
            continue  # Veto yedi, diğer hesaplamaları atla

        # 5. Konfluens
        confluence_count, _ = analyze_confluence(ctx, direction)

        # 6. Piyasa rejimi
        regime = detect_market_regime(ctx.bars, ctx.adx, ctx.ema100, ctx.ema200, ctx.current_price)

        # 7. Rejim & Konfluens bazlı kalibrasyon
        base_confidence = fvg_quality.score
        confluence_bonus = min(max(0, confluence_count - 1) * CONFLUENCE_WEIGHT, MAX_CONFLUENCE_BONUS)
        base_confidence += confluence_bonus

        if regime == "ranging":
            base_confidence *= REGIME_PENALTY_RANGE
        elif regime == "volatile":
            base_confidence *= REGIME_PENALTY_VOLATILE
        elif regime in ("trending_up", "trending_down"):
            if (direction == "bullish" and regime == "trending_up") or (
                direction == "bearish" and regime == "trending_down"
            ):
                base_confidence *= REGIME_BONUS_TREND
            else:
                base_confidence *= REGIME_PENALTY_COUNTER_TREND

        final_confidence = clamp(base_confidence, 0.0, 1.0)

        # CHoCH yön uyuşmazlığı double-lock vetosu
        if choch_score > 0 and choch_dir and choch_dir != direction:
            final_confidence = 0.0

        # 8. Giriş/çıkış bölgeleri
        zones = compute_entry_exit_zones(fvg, ctx.atr, ctx.current_price, direction)
        entry_mid = (zones["entry_low"] + zones["entry_high"]) / 2.0
        rr1 = calculate_rr_ratio(entry_mid, zones["stop_loss"], zones["tp1"])
        rr2 = calculate_rr_ratio(entry_mid, zones["stop_loss"], zones["tp2"])
        avg_rr = (rr1 + rr2) / 2.0 if rr1 > 0 and rr2 > 0 else max(rr1, rr2)

        # 9. Sinyal yönü & eşik kontrolü
        if final_confidence < min_confidence:
            signal_dir: Literal["LONG", "SHORT", "NEUTRAL"] = "NEUTRAL"
        else:
            signal_dir = "LONG" if direction == "bullish" else "SHORT"

        signal = TradeSignal(
            direction=signal_dir,
            confidence=round(final_confidence, 3),
            fvg_quality=fvg_quality,
            choch_score=round(choch_score, 3),
            choch_direction=choch_dir,
            entry_zone_low=round(zones["entry_low"], 5),
            entry_zone_high=round(zones["entry_high"], 5),
            stop_loss=round(zones["stop_loss"], 5),
            take_profit_1=round(zones["tp1"], 5),
            take_profit_2=round(zones["tp2"], 5),
            risk_reward_ratio=round(avg_rr, 2),
            market_regime=regime,
            confluence_count=confluence_count,
            timestamp=ctx.bars[-1].timestamp if ctx.bars else 0,
        )

        if final_confidence > best_confidence:
            best_confidence = final_confidence
            best_signal = signal

    if best_signal is None:
        return TradeSignal(
            direction="NEUTRAL",
            confidence=0.0,
            fvg_quality=None,
            choch_score=0.0,
            choch_direction="",
            entry_zone_low=0.0,
            entry_zone_high=0.0,
            stop_loss=0.0,
            take_profit_1=0.0,
            take_profit_2=0.0,
            risk_reward_ratio=0.0,
            market_regime=detect_market_regime(ctx.bars, ctx.adx, ctx.ema100, ctx.ema200, ctx.current_price),
            confluence_count=0,
            timestamp=ctx.bars[-1].timestamp if ctx.bars else 0,
        )

    # Telemetry-only rank score (zero-drift): does not affect confidence/decision
    try:
        if best_signal is not None:
            # center estimate for CE/size from entry zone bounds
            lo = float(getattr(best_signal, "entry_zone_low", 0.0) or 0.0)
            hi = float(getattr(best_signal, "entry_zone_high", 0.0) or 0.0)
            ce = (lo + hi) / 2.0 if (lo and hi) else 0.0
            size = abs(hi - lo)
            # validated flag (if analyzer attached into quality)
            validated = False
            try:
                fvgq = best_signal.fvg_quality
                validated = bool(getattr(fvgq, "validated", False))
            except Exception:
                validated = False
            import config as _cfg

            def _clip01(x: float) -> float:
                return 0.0 if x < 0.0 else (1.0 if x > 1.0 else x)

            a_val = 1.0 if validated else 0.0
            if getattr(ctx, "atr", 0.0) and getattr(ctx, "h1_liquidity_level", None) is not None:
                d = abs(float(ctx.h1_liquidity_level) - ce) / max(
                    1e-12, float(ctx.atr) * float(getattr(_cfg, "RANK_DIST_K", 1.2))
                )
                b_dist = _clip01(1.0 - d)
            else:
                b_dist = 0.5
            if getattr(ctx, "atr", 0.0):
                c_span = _clip01(size / max(1e-12, float(ctx.atr) * float(getattr(_cfg, "RANK_SPAN_K", 1.0))))
            else:
                c_span = 0.5
            w1 = float(getattr(_cfg, "RANK_W1", 0.01))
            w2 = float(getattr(_cfg, "RANK_W2", 0.50))
            w3 = float(getattr(_cfg, "RANK_W3", 0.49))
            best_signal.rank_score = round(w1 * a_val + w2 * b_dist + w3 * c_span, 4)
    except Exception as e:
        logger.warning("rank_score hesaplama hatası: %s", e, exc_info=True)
    return best_signal


# ─────────────────────────────────────────────────────────
# 8-10. Yardımcı & Toplu Fonksiyonlar
# ─────────────────────────────────────────────────────────
def classify_signal_strength(confidence: float) -> str:
    if confidence >= STRONG_CONFIDENCE_THRESHOLD:
        return "STRONG"
    if confidence >= MIN_CONFIDENCE_THRESHOLD:
        return "MODERATE"
    if confidence >= 0.30:
        return "WEAK"
    return "NONE"


def evaluate_all_signals(
    ctx: ScoringContext,
    min_confidence: float = MIN_CONFIDENCE_THRESHOLD,
    vp: VPLevels | None = None,
) -> dict[str, TradeSignal]:
    return {
        "bullish": evaluate_trade_signal(ctx, fvg_direction="bullish", min_confidence=min_confidence, vp=vp),
        "bearish": evaluate_trade_signal(ctx, fvg_direction="bearish", min_confidence=min_confidence, vp=vp),
    }


def generate_market_summary(ctx: ScoringContext) -> dict[str, str | float]:
    regime = detect_market_regime(ctx.bars, ctx.adx, ctx.ema100, ctx.ema200, ctx.current_price)
    if not math.isnan(ctx.ema100) and not math.isnan(ctx.ema200):
        if ctx.ema100 > ctx.ema200 and ctx.current_price > ctx.ema100:
            trend = "strong_bullish"
        elif ctx.ema100 > ctx.ema200:
            trend = "bullish"
        elif ctx.ema100 < ctx.ema200 and ctx.current_price < ctx.ema100:
            trend = "strong_bearish"
        elif ctx.ema100 < ctx.ema200:
            trend = "bearish"
        else:
            trend = "neutral"
        ema_status = "golden_cross" if ctx.ema100 > ctx.ema200 else "death_cross"
    else:
        trend, ema_status = "unknown", "insufficient_data"

    active_fvgs = sum(1 for f in ctx.fvgs if not f.invalidated and not f.filled)
    recent_chochs = sum(1 for c in ctx.chochs if ctx.bars and (ctx.bars[-1].index - c.bar_index) <= 50)

    return {
        "regime": regime,
        "adx": round(ctx.adx, 1),
        "atr": round(ctx.atr, 6),
        "trend": trend,
        "ema_status": ema_status,
        "active_fvgs": active_fvgs,
        "recent_chochs": recent_chochs,
    }
