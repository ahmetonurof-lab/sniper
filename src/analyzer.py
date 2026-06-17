"""
analyzer.py
───────────
V4 Event-Driven Architecture — Stateless Event Producer (Sensor).

Değişiklikler (V4):
  - [V4-1] _detect_htf_bias yeniden yazıldı — net hiyerarşi
  - [V4-2] HTF_STRICT_FILTER kaldırıldı
  - [V4-3] STRICT_WAIT: D1≠H4 → her zaman engel
  - [V4-4] RANGE_WAIT: H4 None veya D1 None → konsolidasyon
  - [V4-5] PENDING / SKIP_DAY: D1 mum yapısı analizi
  - [V4-6] Yeni log formatı birebir uygulandı
  - [V4-7] HTF_BIAS event — direction=None + strength payload (B şıkkı)

D1 Öncelik Hiyerarşisi (katı sıra):
  1. D1 mum yapısı → OUTSIDE_BAR: SKIP_DAY, INSIDE_BAR: PENDING → None dön
  2. D1 BOS yönü   → NONE ise: RANGE_WAIT → None dön
  3. H4 binary     → NONE ise: RANGE_WAIT → None dön
  4. D1 ≠ H4      → STRICT_WAIT → None dön
  5. D1 = H4      → STRONG → bias dön
"""

from __future__ import annotations

import logging

import config
from fvg import MIN_FVG_SIZE, cleanup_fvgs, detect_fvgs, update_fvg_states
from indicators import compute_atr_point
from models import FVG, Bar, SwingPoint
from mss import detect_mss
from pivot import SwingStateManager, find_swing_highs, find_swing_lows

logger = logging.getLogger("nexus.analyzer")


# ─────────────────────────────────────────────────────────────────────────────
# Yardımcılar
# ─────────────────────────────────────────────────────────────────────────────


def _interval_overlap_ratio(a_low, a_high, b_low, b_high):
    try:
        a_low, a_high = (a_low, a_high) if a_low <= a_high else (a_high, a_low)
        b_low, b_high = (b_low, b_high) if b_low <= b_high else (b_high, b_low)
        a_len = max(0.0, a_high - a_low)
        b_len = max(0.0, b_high - b_low)
        if a_len == 0.0 or b_len == 0.0:
            return 0.0
        ov = max(0.0, min(a_high, b_high) - max(a_low, b_low))
        denom = min(a_len, b_len) or 1.0
        return ov / denom
    except Exception:
        return 0.0


def _cluster_fvgs(fvgs, max_gap):
    if not fvgs:
        return []
    items = sorted(fvgs, key=lambda f: f.real_index)
    out = []
    cur = items[0]
    for f in items[1:]:
        same_dir = f.direction == cur.direction
        left_a, right_a = min(cur.bottom, cur.top), max(cur.bottom, cur.top)
        left_b, right_b = min(f.bottom, f.top), max(f.bottom, f.top)
        gap = max(0.0, max(left_b - right_a, left_a - right_b))
        if same_dir and gap <= max_gap:
            cur = FVG(
                direction=cur.direction,
                top=max(cur.top, f.top),
                bottom=min(cur.bottom, f.bottom),
                real_index=cur.real_index,
                timeframe=cur.timeframe,
            )
        else:
            out.append(cur)
            cur = f
    out.append(cur)
    return out


def _resample_to_2h(bars_h1):
    result = []
    for i in range(0, len(bars_h1) - 1, 2):
        b1, b2 = bars_h1[i], bars_h1[i + 1]
        result.append(
            Bar(
                index=i // 2,
                open=b1.open,
                high=max(b1.high, b2.high),
                low=min(b1.low, b2.low),
                close=b2.close,
                volume=b1.volume + b2.volume,
                timestamp=b1.timestamp,
            )
        )
    return result


def _detect_d1_candle_structure(bars_d1):
    """
    Son D1 mumunu bir öncekiyle karşılaştır.
    INSIDE_BAR  : curr.high < prev.high AND curr.low > prev.low  (strict)
    OUTSIDE_BAR : curr.high > prev.high AND curr.low < prev.low  (strict)
    NORMAL      : diğer
    """
    if len(bars_d1) < 2:
        return "NORMAL"
    curr, prev = bars_d1[-1], bars_d1[-2]
    if curr.high < prev.high and curr.low > prev.low:
        return "INSIDE_BAR"
    if curr.high > prev.high and curr.low < prev.low:
        return "OUTSIDE_BAR"
    return "NORMAL"


def create_mss_event(symbol, timeframe, direction, level, timestamp, impulse_origin=None):
    return {
        "type": "MSS",
        "tf": timeframe,
        "direction": direction,
        "level": float(level),
        "time": int(timestamp),
        "impulse_origin": float(impulse_origin) if impulse_origin is not None else float(level),
        "bar_index": int(timestamp),
    }


# ─────────────────────────────────────────────────────────────────────────────
# MarketAnalyzer
# ─────────────────────────────────────────────────────────────────────────────


class MarketAnalyzer:
    """
    V4 Stateless Event Producer (Sensor).

    Akış:
      0. HTF BIAS    — D1 + H4 durum matrisi. Bias yoksa LTF taraması yok.
      1. SWEEP       — H1 → 15m fallback
      2. MSS         — 15m CHoCH, sweep sonrası
      3. FVG         — 1H/2H FVG
      4. LTF_CONFIRM — 1m pivot kırılımı onayı
    """

    def __init__(self, symbol):
        self.symbol = symbol
        self._mss_state = SwingStateManager()
        self._seen_mss = set()
        self._emitted_fvg_ids = set()
        self._consumed_levels = {}
        self._last_d1_index = -1

    def reset_symbol_cache(self):
        self._emitted_fvg_ids.clear()
        self._seen_mss.clear()
        self._mss_state = SwingStateManager()
        logger.debug("[CACHE-RESET] %s temizlendi", self.symbol)

    # ── 0a. D1 BOS yönü ────────────────────────────────────────────────────────

    def _compute_d1_bias(self, bars_d1):
        """
        D1 swing kırılımı: LONG / SHORT / None.
        None → net yön yok (düz piyasa / yetersiz bar).
        """
        if not bars_d1 or len(bars_d1) < 5:
            return None

        lookback = min(config.D1_BOS_LOOKBACK, len(bars_d1))
        segment = bars_d1[-lookback:]
        last_close = bars_d1[-1].close

        last_bull = -1
        last_bear = -1
        for sh in find_swing_highs(segment, left=2, right=2):
            if last_close > sh.price and sh.bar_index > last_bull:
                last_bull = sh.bar_index
        for sl in find_swing_lows(segment, left=2, right=2):
            if last_close < sl.price and sl.bar_index > last_bear:
                last_bear = sl.bar_index

        if last_bull == -1 and last_bear == -1:
            return None
        return "LONG" if last_bull >= last_bear else "SHORT"

    # ── 0b. H4 binary ──────────────────────────────────────────────────────────

    def _compute_h4_bias(self, bars_h4):
        """
        H4 binary: BOS var → LONG/SHORT, BOS yok → None (CONSOLIDATION).
        Penetrasyon derinliği hesaplanmaz — sadece geçildi/geçilmedi.
        """
        if not bars_h4 or len(bars_h4) < 5:
            return None

        lookback = min(config.H4_BOS_LOOKBACK, len(bars_h4))
        segment = bars_h4[-lookback:]
        last_close = bars_h4[-1].close

        last_bull = -1
        last_bear = -1
        for sh in find_swing_highs(segment, left=2, right=2):
            if last_close > sh.price and sh.bar_index > last_bull:
                last_bull = sh.bar_index
        for sl in find_swing_lows(segment, left=2, right=2):
            if last_close < sl.price and sl.bar_index > last_bear:
                last_bear = sl.bar_index

        if last_bull == -1 and last_bear == -1:
            return None
        return "LONG" if last_bull >= last_bear else "SHORT"

    # ── 0c. HTF Durum Matrisi ──────────────────────────────────────────────────

    def _detect_htf_bias(self, bars_d1, bars_h4):
        """
        [V4] Katı hiyerarşi — her adım erken dönebilir.

        Adım 1 — D1 mum yapısı  (OUTSIDE → SKIP_DAY, INSIDE → PENDING)
        Adım 2 — D1 BOS yönü    (None    → RANGE_WAIT)
        Adım 3 — H4 binary      (None    → RANGE_WAIT)
        Adım 4 — D1 ≠ H4        (         STRICT_WAIT)
        Adım 5 — D1 = H4        (         STRONG)

        Returns: (bias, strength)
          bias=None      → LTF tarama yok
          bias=LONG/SHORT → LTF'e in
        """
        if not bars_d1 or len(bars_d1) < 5:
            return None, "NONE"

        # Tek seferlik sembol (12 karaktere hizala)
        _sym = (bars_d1[-1].symbol if hasattr(bars_d1[-1], "symbol") else "?").ljust(12)

        # ── Adım 1: D1 mum yapısı ─────────────────────────────────────────
        d1_structure = _detect_d1_candle_structure(bars_d1)

        if d1_structure == "OUTSIDE_BAR":
            logger.warning(
                "[%s] 🟥 BIAS: REJECTED | D1: OUTSIDE | H4: ANY | HIGH_VOLATILITY",
                _sym,
            )
            return None, "SKIP_DAY"

        if d1_structure == "INSIDE_BAR":
            h4_label = self._compute_h4_bias(bars_h4) or "CONSOLIDATION"
            if h4_label in ("LONG", "SHORT"):
                h4_icon = "🟩" if h4_label == "LONG" else "🟥"
                logger.info(
                    "[%s] 🟨 BIAS: PENDING | D1: RANGE | H4: %s | 1H=%s BOS bekliyor",
                    _sym,
                    h4_label,
                    h4_icon,
                )
            else:
                logger.info(
                    "[%s] � BIAS: REJECTED | D1: RANGE | H4: CONSOLIDATION",
                    _sym,
                )
            return None, "PENDING"

        # ── Adım 2: D1 BOS yönü ──────────────────────────────────────────
        d1_bias = self._compute_d1_bias(bars_d1)

        if d1_bias is None:
            logger.warning(
                "[%s] � BIAS: REJECTED | D1: RANGE | H4: CONSOLIDATION",
                _sym,
            )
            return None, "RANGE_WAIT"

        # ── Adım 3: H4 binary ────────────────────────────────────────────
        h4_bias = self._compute_h4_bias(bars_h4)

        if h4_bias is None:
            d1_icon = "🟩" if d1_bias == "LONG" else "🟥"
            logger.warning(
                "[%s] 🟨 BIAS: PENDING | D1: %s | H4: CONSOLIDATION | H4=%s BOS bekliyor",
                _sym,
                d1_bias,
                d1_icon,
            )
            return None, "RANGE_WAIT"

        # ── Adım 4: D1 ≠ H4 ──────────────────────────────────────────────
        if d1_bias != h4_bias:
            logger.warning(
                "[%s] 🟥 BIAS: REJECTED | D1: %s | H4: %s | AVOID_COUNTER",
                _sym,
                d1_bias,
                h4_bias,
            )
            return None, "STRICT_WAIT"

        # ── Adım 5: D1 = H4 ──────────────────────────────────────────────
        logger.info(
            "[%s] 🟩 BIAS: STRONG_%s | D1: %s | H4: %s",
            _sym,
            d1_bias,
            d1_bias,
            h4_bias,
        )
        return d1_bias, "STRONG"

    # ── HTF Seviyeleri ──────────────────────────────────────────────────────────

    @staticmethod
    def _detect_h4_swing_level(bars_h4, bias):
        if not bars_h4 or len(bars_h4) < 5:
            return None
        if bias == "LONG":
            lows = find_swing_lows(bars_h4, left=2, right=2)
            return lows[-1].price if lows else None
        highs = find_swing_highs(bars_h4, left=2, right=2)
        return highs[-1].price if highs else None

    @staticmethod
    def _detect_h1_liquidity(bars_h1, bias):
        if not bars_h1 or len(bars_h1) < 5:
            return None
        if bias == "LONG":
            highs = find_swing_highs(bars_h1, left=3, right=3)
            return highs[-1].price if highs else None
        lows = find_swing_lows(bars_h1, left=3, right=3)
        return lows[-1].price if lows else None

    # ── 1. SWEEP ────────────────────────────────────────────────────────────────

    def _detect_sweep_h1(self, symbol, bars_h1, bars_15m, bias):
        events = self._sweep_on_bars(symbol, bars_h1, bias, tf="1H")
        if events:
            return events
        return self._sweep_on_bars(
            symbol,
            bars_15m,
            bias,
            tf="15m",
            strength_override=getattr(config, "SWEEP_15M_STRENGTH", 1),
        )

    def _sweep_on_bars(self, symbol, bars, bias, tf, strength_override=None):
        """Milimetrik sweep dedektörü.

        Fiyatın fitili (wick) geçmiş swing high/low seviyesini 1 pip bile deldiyse
        ve mum gövdesi (close) o seviyenin ters tarafında kapandıysa sweep BAŞARILIDIR.
        İğnenin boyu, kalitesi, belirginliği önemli değildir — likidite ya alınmıştır
        ya alınmamıştır. Momentum kontrolü ilerleyen aşamada FVG ile yapılır.
        """
        consumed = self._consumed_levels.setdefault(symbol, set())
        events = []
        if not bars:
            return events
        current_bar = bars[-1]
        strength = strength_override if strength_override is not None else getattr(config, "SWEEP_SWING_STRENGTH", 2)
        highs = find_swing_highs(bars, left=strength, right=strength)
        lows = find_swing_lows(bars, left=strength, right=strength)

        if bias == "LONG":
            for sl in reversed(lows[-5:]):
                lk = (tf, round(sl.price, 5))
                if lk in consumed:
                    continue
                # Wick swing low'u deldi mi ve close üstünde mi?
                if current_bar.low < sl.price and current_bar.close > sl.price:
                    consumed.add(lk)
                    events.append(
                        {
                            "type": "SWEEP",
                            "symbol": symbol,
                            "level": sl.price,
                            "tf": tf,
                            "side": "SSL",
                            "bar_index": current_bar.index,
                        }
                    )
                    break
        else:
            for sh in reversed(highs[-5:]):
                lk = (tf, round(sh.price, 5))
                if lk in consumed:
                    continue
                # Wick swing high'ı deldi mi ve close altında mı?
                if current_bar.high > sh.price and current_bar.close < sh.price:
                    consumed.add(lk)
                    events.append(
                        {
                            "type": "SWEEP",
                            "symbol": symbol,
                            "level": sh.price,
                            "tf": tf,
                            "side": "BSL",
                            "bar_index": current_bar.index,
                        }
                    )
                    break
        return events

    # ── 2. MSS ──────────────────────────────────────────────────────────────────

    def _detect_mss_events(self, symbol, bars_15m, bias, since_bar_index=None):
        if since_bar_index is None:
            logger.debug("[MSS] %s since_bar_index=None → atlandı", symbol)
            return []
        events = []
        self._mss_state.ingest(bars_15m, left=3, right=3)
        chochs = detect_mss(bars_15m, self._mss_state, timeframe="15m")
        for c in chochs:
            if c.bar_index < since_bar_index:
                continue
            key = hash((c.bar_index, c.direction, c.level))
            if key in self._seen_mss:
                continue
            self._seen_mss.add(key)
            direction = "LONG" if c.direction == "bullish" else "SHORT"
            if direction != bias:
                continue
            pre = [b for b in bars_15m if b.index < c.bar_index]
            impulse_origin = None
            if pre:
                pts = (
                    find_swing_lows(pre, left=2, right=2)
                    if direction == "LONG"
                    else find_swing_highs(pre, left=2, right=2)
                )
                impulse_origin = pts[-1].price if pts else None
            logger.info("[MSS-EMIT] %s bar=%s dir=%s", symbol, c.bar_index, direction)
            events.append(
                {
                    "type": "MSS",
                    "symbol": symbol,
                    "level": c.level,
                    "direction": direction,
                    "tf": "15m",
                    "bar_index": c.bar_index,
                    "impulse_origin": impulse_origin if impulse_origin is not None else c.level,
                }
            )
        return events

    # ── 4. LTF CONFIRM ──────────────────────────────────────────────────────────

    @staticmethod
    def _find_retracement_swing(bars_m1, fvg_entry_bar_timestamp, direction, left=1, right=1):
        post = (
            [b for b in bars_m1 if b.timestamp >= fvg_entry_bar_timestamp] if fvg_entry_bar_timestamp > 0 else bars_m1
        )
        if len(post) < left + right + 1:
            return None
        candidates = []
        for i in range(left, len(post) - right):
            bar = post[i]
            if direction == "LONG":
                if all(bar.high >= post[i - j].high for j in range(1, left + 1)) and all(
                    bar.high >= post[i + j].high for j in range(1, right + 1)
                ):
                    candidates.append(SwingPoint(price=bar.high, bar_index=bar.index, kind="high", mitigated=False))
            else:
                if all(bar.low <= post[i - j].low for j in range(1, left + 1)) and all(
                    bar.low <= post[i + j].low for j in range(1, right + 1)
                ):
                    candidates.append(SwingPoint(price=bar.low, bar_index=bar.index, kind="low", mitigated=False))
        return candidates[-1] if candidates else None

    def _detect_ltf_confirm(self, symbol, fvgs, bars_m1, current_close, fvg_timestamp_map=None):
        from mss import LTFTriggerDetector

        for f in fvgs:
            if not f.is_active:
                continue
            direction = "LONG" if f.direction == "bullish" else "SHORT"
            if not bars_m1:
                continue
            fvg_ts = fvg_timestamp_map.get(f.real_index, 0) if fvg_timestamp_map else 0
            swing = self._find_retracement_swing(bars_m1, fvg_ts, direction)
            if swing is None:
                continue
            result = LTFTriggerDetector().validate(bars=bars_m1, direction=f.direction, retracement_swing=swing)
            if result.is_valid:
                return [
                    {
                        "type": "LTF_CONFIRM",
                        "symbol": symbol,
                        "tf": "1m",
                        "direction": direction,
                        "fvg_top": f.top,
                        "fvg_bottom": f.bottom,
                        "close": bars_m1[-1].close,
                    }
                ]
        return []

    # ── Ana giriş noktası ──────────────────────────────────────────────────────

    def analyze(self, bars_d1, bars_h4, bars_h1, bars_15m, bars_m1):
        """
        Ham yapısal event listesi döner.

        HTF_BIAS event her zaman üretilir (direction=None olabilir).
        bias=None ise → erken dönüş, LTF taraması yok.
        """
        events = []
        try:
            if not all([bars_d1, bars_15m, bars_m1]):
                return events

            current_close = bars_15m[-1].close

            # 0 ─ HTF Bias (ANA FİLTRE)
            bias, strength = self._detect_htf_bias(bars_d1, bars_h4)

            # [V4-7] HTF_BIAS event her zaman üretilir
            events.append(
                {
                    "type": "HTF_BIAS",
                    "symbol": self.symbol,
                    "direction": bias,
                    "strength": strength,
                    "d1_bias": self._compute_d1_bias(bars_d1) if bars_d1 and len(bars_d1) >= 5 else None,
                    "h4_bias": self._compute_h4_bias(bars_h4) if bars_h4 and len(bars_h4) >= 5 else None,
                }
            )

            if bias is None:
                logger.info("[ANALYZE] %s: bias yok (strength=%s), LTF atlandı.", self.symbol, strength)
                return events

            # D1 gün değişimi → cache sıfırla
            last_d1_idx = bars_d1[-1].index
            if last_d1_idx != self._last_d1_index:
                self._consumed_levels.clear()
                self._emitted_fvg_ids.clear()
                self._last_d1_index = last_d1_idx
                logger.info("[RESET] %s günlük cache sıfırlandı", self.symbol)

            logger.info("[ANALYZE] %s | bias=%s | strength=%s | close=%.5f", self.symbol, bias, strength, current_close)

            # HTF seviyeleri
            h4_sl = self._detect_h4_swing_level(bars_h4, bias)
            h1_tp = self._detect_h1_liquidity(bars_h1, bias)
            events.append(
                {"type": "HTF_LEVELS", "symbol": self.symbol, "h4_swing_level": h4_sl, "h1_liquidity_level": h1_tp}
            )

            # 1 ─ SWEEP
            sweep_events = self._detect_sweep_h1(self.symbol, bars_h1, bars_15m, bias)
            events.extend(sweep_events)
            sweep_since = max((ev["bar_index"] for ev in sweep_events if "bar_index" in ev), default=None)

            # 2 ─ MSS
            mss_events = self._detect_mss_events(self.symbol, bars_15m, bias, since_bar_index=sweep_since)
            events.extend(mss_events)

            # 3 ─ FVG
            fvg_direction = "bullish" if bias == "LONG" else "bearish"
            sweep_tf = sweep_events[0]["tf"] if sweep_events else "1H"
            use_15m = sweep_tf == "15m"
            fvgs_eff = []
            fvg_timestamp_map = {}

            if use_15m:
                fvgs_eff = detect_fvgs(
                    bars_15m, lookback=20, timeframe="15m", min_fvg_size=MIN_FVG_SIZE, since_index=None
                )
                fvgs_eff = [f for f in fvgs_eff if f.direction == fvg_direction]
                update_fvg_states(fvgs_eff, bars_15m)
                fvgs_eff = cleanup_fvgs(fvgs_eff, bars_15m[-1].index)
                fvgs_eff = sorted(fvgs_eff, key=lambda f: abs(f.top - f.bottom), reverse=True)
                bar_ts = {b.index: b.timestamp for b in bars_15m}
                fvg_timestamp_map = {f.real_index: bar_ts.get(f.real_index, 0) for f in fvgs_eff}
                new_keys = set()
                for f in fvgs_eff:
                    key = ("15m", round(float(f.top), 5), round(float(f.bottom), 5), int(f.real_index))
                    if key in self._emitted_fvg_ids:
                        continue
                    new_keys.add(key)
                    events.append(
                        {
                            "type": "FVG_CREATED",
                            "symbol": self.symbol,
                            "upper": f.top,
                            "lower": f.bottom,
                            "ce_level": (f.top + f.bottom) / 2.0,
                            "time": bar_ts.get(f.real_index, 0),
                            "bar_index": f.real_index,
                            "direction": f.direction,
                            "is_active": getattr(f, "is_active", True),
                            "tf": "15m",
                            "validated": False,
                        }
                    )
                self._emitted_fvg_ids.update(new_keys)
            else:
                fvgs_h1 = []
                if bars_h1 and len(bars_h1) >= 5:
                    fvgs_h1 = detect_fvgs(
                        bars_h1, lookback=20, timeframe="1H", min_fvg_size=MIN_FVG_SIZE, since_index=None
                    )
                    fvgs_h1 = [f for f in fvgs_h1 if f.direction == fvg_direction]
                fvgs_2h = []
                if bars_h1 and len(bars_h1) >= 4:
                    bars_2h = _resample_to_2h(bars_h1)
                    if bars_2h:
                        fvgs_2h = detect_fvgs(
                            bars_2h, lookback=10, timeframe="2H", min_fvg_size=MIN_FVG_SIZE, since_index=None
                        )
                        fvgs_2h = [f for f in fvgs_2h if f.direction == fvg_direction]
                if fvgs_h1 and bars_h1:
                    update_fvg_states(fvgs_h1, bars_h1)
                    fvgs_h1 = cleanup_fvgs(fvgs_h1, bars_h1[-1].index)
                try:
                    atr_h1 = compute_atr_point(bars_h1, period=14) if bars_h1 else 0.0
                except Exception:
                    atr_h1 = 0.0
                k = getattr(config, "FVG_CLUSTER_ATR_MULT", 0.4)
                fvgs_eff = _cluster_fvgs(fvgs_h1, max_gap=max(0.0, (atr_h1 or 0.0) * k))
                overlap_min = getattr(config, "FVG_OVERLAP_MIN", 0.60)
                validated_map = {}
                for f in fvgs_eff:
                    validated_map[f.real_index] = any(
                        g.direction == f.direction
                        and _interval_overlap_ratio(f.bottom, f.top, g.bottom, g.top) >= overlap_min
                        for g in fvgs_2h
                    )
                fvgs_eff.sort(key=lambda f: (not validated_map.get(f.real_index, False), -abs(f.top - f.bottom)))
                bar_ts_h1 = {b.index: b.timestamp for b in bars_h1} if bars_h1 else {}
                fvg_timestamp_map = {f.real_index: bar_ts_h1.get(f.real_index, 0) for f in fvgs_eff}
                new_keys = set()
                for f in fvgs_eff:
                    key = ("1H", round(float(f.top), 5), round(float(f.bottom), 5), int(f.real_index))
                    if key in self._emitted_fvg_ids:
                        continue
                    new_keys.add(key)
                    events.append(
                        {
                            "type": "FVG_CREATED",
                            "symbol": self.symbol,
                            "upper": f.top,
                            "lower": f.bottom,
                            "ce_level": (f.top + f.bottom) / 2.0,
                            "time": bar_ts_h1.get(f.real_index, 0),
                            "bar_index": f.real_index,
                            "direction": f.direction,
                            "is_active": getattr(f, "is_active", True),
                            "tf": "1H",
                            "validated": bool(validated_map.get(f.real_index, False)),
                        }
                    )
                self._emitted_fvg_ids.update(new_keys)

            # 4 ─ LTF_CONFIRM
            events.extend(self._detect_ltf_confirm(self.symbol, fvgs_eff, bars_m1, current_close, fvg_timestamp_map))

        except Exception as exc:
            logger.error("[ANALYZE] %s error: %s", self.symbol, exc, exc_info=True)

        return events
