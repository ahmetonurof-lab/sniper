"""
fvg.py
──────
Nexus SMC Trading Bot — Fair Value Gap (FVG) Motoru
Katmanlar: Core Engine (Tespit/State/Retest) + Quality Overlay (Skorlama/Veto)
Bağımlılıklar: models, indicators, volume_profile (opsiyonel)
KRİTİK KURAL: FVG dataclass'ında timestamp yoktur. real_index mutlak bar indeksidir.
"""

from __future__ import annotations

import logging
from typing import Final, Literal

from models import FVG, Bar, FVGQuality, SwingPoint

logger = logging.getLogger("nexus.fvg")

# ──────────────────────────────────────────────────────────
# SABİTLER & YAPILANDIRMA
# ──────────────────────────────────────────────────────────
DEFAULT_LOOKBACK: Final[int] = 100
MAX_FVG_AGE_BARS: Final[int] = 500
MIN_FVG_SIZE: Final[float] = 0.0
ATR_PERIOD: Final[int] = 14
# Sembol bazlı periyodik cleanup sayacı
_SYMBOL_COUNTERS: dict[str, int] = {}


# ──────────────────────────────────────────────────────────
# 1. CORE ENGINE (Tespit → State → Retest → Pipeline)
# ──────────────────────────────────────────────────────────
def detect_fvgs(
    bars: list[Bar],
    lookback: int = DEFAULT_LOOKBACK,
    timeframe: str = "5m",
    min_fvg_size: float = MIN_FVG_SIZE,
    since_index: int | None = None,
) -> list[FVG]:
    """
    Rolling buffer'daki son `lookback` bar'ı tarayarak FVG listesi üretir.
    - Dilim göreceli indeks yerine Bar.index (mutlak) saklanır.
    - Kapanmamış son mum FVG tespitine girmez.
    - Inside-bar eşit high/low dahil yakalanır.
    - since_index verilirse, sadece real_index >= since_index olan FVG'ler döner.
    """
    segment = bars[-lookback:] if len(bars) > lookback else bars
    fvgs: list[FVG] = []

    for i in range(1, len(segment) - 1):
        b_prev = segment[i - 1]
        b_curr = segment[i]  # mother bar
        b_next = segment[i + 1]

        if not b_next.is_closed:
            continue

        # Inside bar filtresi (eşit seviyeler dahil)
        if b_next.high <= b_curr.high and b_next.low >= b_curr.low:
            continue

        gap_bull = b_next.low - b_prev.high
        gap_bear = b_prev.low - b_next.high

        if gap_bull > 0:
            fvg = FVG(
                direction="bullish",
                top=b_next.low,
                bottom=b_prev.high,
                real_index=b_curr.index,
                timeframe=timeframe,
            )
            if fvg.size >= min_fvg_size:
                if since_index is None or fvg.real_index >= since_index:
                    fvgs.append(fvg)
            else:
                logger.debug("[FVG-SIZE] bullish size=%.6f < min=%.6f → atlanıyor.", fvg.size, min_fvg_size)

        elif gap_bear > 0:
            fvg = FVG(
                direction="bearish",
                top=b_prev.low,
                bottom=b_next.high,
                real_index=b_curr.index,
                timeframe=timeframe,
            )
            if fvg.size >= min_fvg_size:
                if since_index is None or fvg.real_index >= since_index:
                    fvgs.append(fvg)
            else:
                logger.debug("[FVG-SIZE] bearish size=%.6f < min=%.6f → atlanıyor.", fvg.size, min_fvg_size)

    return fvgs


def update_fvg_states(
    fvgs: list[FVG],
    bars: list[Bar],
) -> None:
    """
    Mevcut bar listesine göre her FVG'nin filled / invalidated durumunu günceller.
    SMC Kuralı: Wick geçişi allow edilir, gövde kapanışı (close) invalidasyon sayılır.
    """
    if not bars:
        return

    first_abs = bars[0].index
    last_abs = bars[-1].index

    for fvg in fvgs:
        if fvg.invalidated or fvg.real_index < first_abs:
            continue

        scan_from_abs = max(getattr(fvg, "_next_check_abs", fvg.real_index + 2), fvg.real_index + 2)

        for abs_i in range(scan_from_abs, last_abs + 1):
            list_pos = abs_i - first_abs
            if not (0 <= list_pos < len(bars)):
                continue
            b = bars[list_pos]
            if not b.is_closed:
                break

            if fvg.direction == "bullish":
                # SMC: Close < bottom → invalid
                if b.close < fvg.bottom:
                    object.__setattr__(fvg, "invalidated", True)
                    object.__setattr__(fvg, "filled", False)
                    logger.debug("[FVG-STATE] bullish invalidated (close=%.5f < bottom=%.5f)", b.close, fvg.bottom)
                    break
                elif fvg.bottom <= b.close <= fvg.top:
                    object.__setattr__(fvg, "filled", True)
                else:
                    object.__setattr__(fvg, "filled", False)

            else:  # bearish
                # SMC: Close > top → invalid
                if b.close > fvg.top:
                    object.__setattr__(fvg, "invalidated", True)
                    object.__setattr__(fvg, "filled", False)
                    logger.debug("[FVG-STATE] bearish invalidated (close=%.5f > top=%.5f)", b.close, fvg.top)
                    break
                elif fvg.bottom <= b.close <= fvg.top:
                    object.__setattr__(fvg, "filled", True)
                else:
                    object.__setattr__(fvg, "filled", False)

        if not fvg.invalidated:
            object.__setattr__(fvg, "_next_check_abs", last_abs)


def find_latest_unfilled_fvg(
    fvgs: list[FVG],
    direction: Literal["bullish", "bearish"],
    min_fvg_size: float = MIN_FVG_SIZE,
) -> FVG | None:
    """Belirtilen yönde, en güncel geçerli (unfilled + not invalidated) FVG'yi döner."""
    matches = [
        f for f in fvgs if f.direction == direction and not f.filled and not f.invalidated and f.size >= min_fvg_size
    ]
    logger.debug(
        "[FVG-DEBUG] dir=%s total=%d filled=%d invalidated=%d size_fail=%d active=%d",
        direction,
        len([f for f in fvgs if f.direction == direction]),
        sum(1 for f in fvgs if f.direction == direction and f.filled),
        sum(1 for f in fvgs if f.direction == direction and f.invalidated),
        sum(
            1 for f in fvgs if f.direction == direction and not f.filled and not f.invalidated and f.size < min_fvg_size
        ),
        len(matches),
    )
    if not matches:
        return None
    return max(matches, key=lambda f: f.real_index)


def is_retesting_fvg(
    fvg: FVG | None,
    current_bar: Bar,
    atr: float,
    atr_buffer_factor: float = 0.10,
) -> bool:
    """
    FVG retest kontrolü. ATR bazlı dinamik buffer kullanır.
    None guard + is_active kontrolü içerir.
    """
    if fvg is None or not fvg.is_active:
        return False

    body_high = max(current_bar.open, current_bar.close)
    body_low = min(current_bar.open, current_bar.close)
    buffer = max(atr * atr_buffer_factor, fvg.size * 0.10)

    if fvg.direction == "bullish":
        lower_bound = max(fvg.bottom - buffer, 0.0)
        wick_touches = current_bar.low <= fvg.top + buffer and current_bar.low >= lower_bound
        body_safe = body_low >= lower_bound
        return wick_touches and body_safe
    else:
        lower_bound = max(fvg.bottom - buffer, 0.0)
        wick_touches = current_bar.high >= lower_bound and current_bar.high <= fvg.top + buffer
        body_safe = body_high <= fvg.top + buffer
        return wick_touches and body_safe


def cleanup_fvgs(
    fvgs: list[FVG],
    current_abs: int,
    max_age: int = MAX_FVG_AGE_BARS,
) -> list[FVG]:
    """Eski / iptal edilmiş / tamamen mitigation edilmiş FVG'leri listeden çıkarır."""
    before = len(fvgs)
    kept = [
        f
        for f in fvgs
        if not f.invalidated
        and not (f.filled and (current_abs - f.real_index) > max_age)
        and not (not f.filled and (current_abs - f.real_index) > max_age * 2)
    ]
    if before != len(kept):
        logger.info("[FVG-CLEANUP] %d FVG temizlendi (%d → %d).", before - len(kept), before, len(kept))
    return kept


def refresh_fvg_list(
    fvgs: list[FVG],
    bars: list[Bar],
    lookback: int = DEFAULT_LOOKBACK,
    min_fvg_size: float = MIN_FVG_SIZE,
    max_age: int = MAX_FVG_AGE_BARS,
    timeframe: str = "5m",
    cleanup_every: int = 50,
    symbol: str = "default",
) -> list[FVG]:
    """Tek entry-point: tespit → mükerrer önleme → state güncelleme → periyodik temizlik."""
    _SYMBOL_COUNTERS[symbol] = _SYMBOL_COUNTERS.get(symbol, 0) + 1
    call_n = _SYMBOL_COUNTERS[symbol]

    existing_indices = {f.real_index for f in fvgs}
    new_fvgs = [
        f
        for f in detect_fvgs(bars, lookback=lookback, timeframe=timeframe, min_fvg_size=min_fvg_size)
        if f.real_index not in existing_indices
    ]
    fvgs.extend(new_fvgs)
    update_fvg_states(fvgs, bars)

    if call_n % cleanup_every == 0 and bars:
        fvgs = cleanup_fvgs(fvgs, current_abs=bars[-1].index, max_age=max_age)

    return fvgs


def create_fvg_event(fvg: FVG, timeframe: str) -> dict:
    """
    FVG objesini normalize edilmiş V3 market event dict'e çevirir.
    DÜZELTME: .upper/.lower/.timestamp → .top/.bottom/.real_index
    """
    return {
        "type": "FVG_CREATED",
        "tf": timeframe,
        "upper": float(fvg.top),  # FVG.top
        "lower": float(fvg.bottom),  # FVG.bottom
        "time": int(fvg.real_index),  # FVG.real_index (timestamp yok)
    }


# ──────────────────────────────────────────────────────────
# 3. YAPISAL SL & LTF TETİKLEYİCİ
# ──────────────────────────────────────────────────────────


def compute_structural_sl(fvg: FVG, direction: str) -> float:
    """
    FVG yapısına göre stop-loss seviyesini hesaplar.
    NOT: Bu fonksiyon artık yalnızca fallback olarak kullanılır.
    Asıl SL = 4H swing high/low (risk_manager.py sorumluluğunda).
    - Bullish: bottom'un bir miktar altı
    - Bearish: top'un bir miktar üstü
    """
    buffer = fvg.size * 0.1 if fvg.size > 0 else 0.0001
    if direction == "bullish":
        return fvg.bottom - buffer
    else:
        return fvg.top + buffer


def check_ltf_trigger(
    bars_5m: list[Bar],
    fvg: FVG,
    retracement_swing: SwingPoint | None = None,
) -> bool:
    """
    5m LTF tetikleyici — LTFTriggerDetector V1 (2 kriter) ile validasyon.

    Kriterler (LTFTriggerDetector V1):
      1. Body >= body_atr_mult × ATR(14)  (default mult=0.5)
      2. Close > retracement_swing.price   (bullish)
         Close < retracement_swing.price   (bearish)

    retracement_swing: SwingPoint | None — analyzer.py'ın bulduğu son karşı-yön pivot.
    """

    from mss import LTFTriggerDetector

    if not bars_5m or len(bars_5m) < 16:  # min: atr_period(14) + 2
        return False

    direction: Literal["bullish", "bearish"] = fvg.direction
    detector = LTFTriggerDetector()
    result = detector.validate(
        bars=bars_5m,
        direction=direction,
        retracement_swing=retracement_swing,
    )

    if not result.is_valid:
        logger.debug("[LTF-TRIGGER] FAIL — %s", result.reason)

    return result.is_valid


# ──────────────────────────────────────────────────────────
# 4. GÜVENLİ BAR RESOLUTION YARDIMCISI
# ──────────────────────────────────────────────────────────


def resolve_fvg_bar(bars: list[Bar], fvg: FVG) -> Bar | None:
    """
    FVG'nin mother/impulse bar'ını real_index üzerinden güvenli çözümler.
    Döner: mother bar (listede yoksa bars[-2] fallback)
    """
    if not bars:
        return None
    first_abs = bars[0].index
    fvg_bar_pos = fvg.real_index - first_abs
    if 0 <= fvg_bar_pos < len(bars):
        return bars[fvg_bar_pos]
    return bars[-2] if len(bars) >= 2 else bars[-1]


# ──────────────────────────────────────────────────────────
# 5. QUALITY OVERLAY (Skorlama Fonksiyonları — scoring.py tarafından tüketilir)
# ──────────────────────────────────────────────────────────


def score_displacement(mother_bar: Bar, atr: float, fvg_direction: str) -> float:
    """Mother bar momentum skoru (ATR-normalized). [0.0, 1.0]"""
    if atr <= 0:
        return 0.0
    displacement = abs(mother_bar.close - mother_bar.open)
    return min(displacement / (atr * 0.5), 1.0)


def score_fvg_size(fvg: FVG, atr: float) -> float:
    """FVG boyut skoru (ATR-relative). [0.0, 1.0]"""
    if atr <= 0:
        return 0.0
    return min(fvg.size / (atr * 0.3), 1.0)


def score_sweep(bars: list[Bar], fvg: FVG, lookback: int = 5) -> float:
    """Likidite sweep skoru — son N bar içinde swing low/high süpürmesi. [0.0, 1.0]"""
    if not bars:
        return 0.0
    first_abs = bars[0].index
    fvg_pos = fvg.real_index - first_abs
    if fvg_pos < 2 or fvg_pos >= len(bars):
        return 0.0

    # Mother bar'dan önceki lookback penceresindeki en uç seviyeyi bul
    window_start = max(0, fvg_pos - lookback)
    if fvg.direction == "bullish":
        swing_low = min(bars[j].low for j in range(window_start, fvg_pos))
        # Bu swing low'u kaç barın wick'i kırdı? (likidite avı)
        sweep_count = sum(1 for j in range(window_start, fvg_pos) if bars[j].low < swing_low)
    else:
        swing_high = max(bars[j].high for j in range(window_start, fvg_pos))
        sweep_count = sum(1 for j in range(window_start, fvg_pos) if bars[j].high > swing_high)
    return min(sweep_count / max(lookback, 1), 1.0)


def score_retest(bars_since: int) -> float:
    """Retest zamanlama skoru. [0.0, 1.0]"""
    if bars_since <= 3:
        return 1.0
    if bars_since <= 6:
        return 0.8
    if bars_since <= 10:
        return 0.5
    if bars_since <= 20:
        return 0.3
    return 0.0


def compute_fvg_quality(
    bars_tf: list[Bar],
    current_price: float,
    fvg: FVG,
    adx: float,
    d: float,
    f: float,
    s: float,
    r: float,
    choch_score: float = 0.0,
    choch_direction: str = "",
    vp: object | None = None,
) -> FVGQuality:
    """FVG kalite skoru — ağırlıklandırılmış bileşen skorları. FVGQuality döner.

    Not: bars_tf, current_price, fvg, adx, vp, choch_direction parametreleri
    caller (scoring.py) tarafından ön işleme için tüketilir; bu fonksiyon
    yalnızca (d, f, s, r, choch_score) üzerinden ağırlıklı ortalama alır.
    """
    _ = bars_tf, current_price, fvg, adx, vp, choch_direction  # consumed by caller
    weights = {"displacement": 0.25, "fvg_size": 0.30, "sweep": 0.20, "retest": 0.15, "choch": 0.10}
    score = (
        d * weights["displacement"]
        + f * weights["fvg_size"]
        + s * weights["sweep"]
        + r * weights["retest"]
        + choch_score * weights["choch"]
    )
    return FVGQuality(displacement=d, fvg_size=f, sweep=s, retest=r, score=min(score, 1.0))


def _get_vp_status(fvg: FVG, vp: object) -> str:
    """Volume Profile seviye durumu — LVN / HVN / none."""
    try:
        if hasattr(vp, "get_zone_type"):
            return vp.get_zone_type(fvg.midpoint)
    except Exception as e:
        logger.debug("VP zone type tespit edilemedi: %s", e)
    return "none"


def is_premium_discount_valid(
    bars: list[Bar],
    current_price: float,
    fvg_direction: str,
    lookback: int = 50,
) -> bool:
    """Premium/Discount bölgesi validasyonu — son N bar'ın range'ine göre."""
    if len(bars) < lookback:
        segment = bars
    else:
        segment = bars[-lookback:]

    if not segment:
        return False

    highest = max(b.high for b in segment)
    lowest = min(b.low for b in segment)
    mid = (highest + lowest) / 2.0

    if fvg_direction == "bullish":
        # Discount zone: fiyat alt yarıda
        return current_price <= mid
    else:
        # Premium zone: fiyat üst yarıda
        return current_price >= mid
