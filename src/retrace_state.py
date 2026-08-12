"""
retrace_state.py — HTF FVG Wick Rejection State Machine.
Sadece FVG kullanilir (OB yok). ADX filtresi kaldirildi.
Sweep + FVG wick rejection = aninda TRIGGER_READY.
"""

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import Literal

from fvg import detect_fvgs
from models import Bar

logger = logging.getLogger("nexus.retrace_state")

# Grup 3 (Sonnet direktifi): order/fill gibi operasyonel hatalarda sınırsız
# lock_bias + tekrar dene dongusu riskine karsi — art arda bu kadar hata
# sonrasi tam reset'e (IDLE) dusulur. stale-event backstop'taki _stale_n>=3
# mantigiyla ayni pattern.
MAX_CONSECUTIVE_OP_FAILS = 3


class RetraceState(Enum):
    IDLE = auto()
    SWEEP_DETECTED = auto()
    TRIGGER_READY = auto()
    BIAS_LOCKED = auto()


class HTFFVG:
    """HTF FVG key level."""

    def __init__(self, top: float, bottom: float, direction: str, bar_index: int):
        self.top = top
        self.bottom = bottom
        self.direction = direction
        self.bar_index = bar_index

    def __repr__(self):
        return f"FVG([{self.bottom:.2f}-{self.top:.2f}] dir={self.direction} bar={self.bar_index})"


def scan_htf_fvgs(
    bars_15m: list[Bar],
    lookback: int = 100,
    min_fvg_size: float = 10.0,
    max_wick_ratio: float = 1.0,
) -> list[HTFFVG]:
    """Son 15m bar'ler icinde FVG'leri tara. min_fvg_size coin'e gore dinamik."""
    segment = bars_15m[-lookback:] if len(bars_15m) > lookback else bars_15m
    if len(segment) < 5:
        return []

    fvgs = detect_fvgs(
        segment,
        lookback=len(segment),
        timeframe="15m",
        min_fvg_size=min_fvg_size,
        max_wick_ratio=max_wick_ratio,
    )
    levels = [HTFFVG(f.top, f.bottom, f.direction, f.real_index) for f in fvgs]
    levels.sort(key=lambda x: x.bar_index)
    return levels[-10:] if len(levels) > 10 else levels


class RetraceStateMachine:
    def __init__(self, max_wick_ratio: float = 1.0):
        self.state: RetraceState = RetraceState.IDLE
        self.direction: Literal["bullish", "bearish"] | None = None
        self.sweep_level: float | None = None
        self.trigger_fvg: HTFFVG | None = None
        self._max_wick_ratio = max_wick_ratio
        self._pending_sweep_id: str | None = None
        self._locked_from_bar: int | None = None
        self._fail_count: int = 0

    @property
    def state_name(self) -> str:
        return self.state.name

    @property
    def bias_locked(self) -> bool:
        return self.state == RetraceState.BIAS_LOCKED

    @property
    def locked_direction(self) -> Literal["bullish", "bearish"] | None:
        return self.direction if self.state == RetraceState.BIAS_LOCKED else None

    def can_trigger(self) -> bool:
        return self.state == RetraceState.TRIGGER_READY

    def _mark_sweep_used(self):
        if self._pending_sweep_id is not None:
            try:
                from state_manager import mark_sweep_used

                mark_sweep_used(self._pending_sweep_id)
            except Exception:
                pass
            self._pending_sweep_id = None

    def reset(self):
        self.state = RetraceState.IDLE
        self.direction = None
        self.sweep_level = None
        self.trigger_fvg = None
        self._pending_sweep_id = None
        self._locked_from_bar = None
        self._fail_count = 0

    def lock_bias(self, bar_index: int | None = None):
        """Bias kilit moduna gec: yon korunur, yeni sweep beklemeden FVG re-entry.

        State -> BIAS_LOCKED. Sweep verileri temizlenir (kilit zaten bir sweep
        sonrasi entry'den gelir). _locked_from_bar korunur: on_bias_fvg yalnizca
        kilit noktasi SONRASI olusan FVG'lerin tekrar tetiklenmesine izin verir
        (aynı FVG'nin donguye girerek tekrar tekrar entry yapmasi engellenir).
        """
        if self.direction is None:
            return
        self.state = RetraceState.BIAS_LOCKED
        self.sweep_level = None
        self.trigger_fvg = None
        self._pending_sweep_id = None
        if bar_index is not None:
            self._locked_from_bar = bar_index
        logger.info(
            f"[RST] BIAS_LOCKED | dir={self.direction} from_bar={self._locked_from_bar}"
        )

    def on_operational_fail(self, bar_index: int | None = None):
        """Grup 3 (Sonnet direktifi): operasyonel hata (order/fill).

        Hata FVG'den bagimsiz olabileceginden (API kesintisi, delist vb.)
        sinirsiz lock_bias + tekrar dene dongusu riski var. Art arda
        MAX_CONSECUTIVE_OP_FAILS hata -> tam reset (IDLE); aksi halde bias
        kilitli kalir ve sonraki taze FVG denenir. Sayac reset() veya basarili
        entry (clear_fail_streak) ile sifirlanir.
        """
        self._fail_count += 1
        if self._fail_count >= MAX_CONSECUTIVE_OP_FAILS:
            logger.warning(
                f"[RST] {self._fail_count} ardışık operasyonel hata -> full reset (IDLE)"
            )
            self.reset()
            return
        logger.warning(
            f"[RST] operasyonel hata #{self._fail_count}/{MAX_CONSECUTIVE_OP_FAILS} "
            f"-> BIAS_LOCKED (yeni FVG bekleniyor)"
        )
        self.lock_bias(bar_index=bar_index)

    def clear_fail_streak(self):
        """Basarili entry sonrasi ardisik hata sayacini sifirla."""
        self._fail_count = 0

    def on_bias_fvg(
        self,
        bars_15m: list[Bar],
        current: Bar,
        atr_val: float = 0.0,
        symbol: str = "",
    ):
        """BIAS_LOCKED'de: kilit yonunde TAZE FVG'nin wick rejection'i -> TRIGGER_READY.

        Sweep gerektirmez (bias kilidi zaten aktif). Ayni FVG'nin tekrar
        tetiklenmesini onlemek icin FVG, kilit noktasi (_locked_from_bar) SONRASI
        olusmus ve current bar'dan ONCE olmali.
        """
        if self.state != RetraceState.BIAS_LOCKED:
            return

        import config as _cfg

        min_mult = _cfg.FVG_SIZE_MAP.get(symbol, _cfg.FVG_MIN_SIZE_ATR_MULT)
        min_fvg_size = max(atr_val * min_mult, 1e-8)

        htf_fvgs = scan_htf_fvgs(
            bars_15m,
            lookback=100,
            min_fvg_size=min_fvg_size,
            max_wick_ratio=self._max_wick_ratio,
        )
        if not htf_fvgs:
            return

        for fvg in reversed(htf_fvgs):
            if fvg.direction != self.direction:
                continue
            if (
                self._locked_from_bar is not None
                and fvg.bar_index <= self._locked_from_bar
            ):
                # Kilit oncesi FVG — ayni sinyal tekrar tetiklenmesin
                logger.info(
                    f"[RST] BIAS_FVG reject=stale | dir={self.direction} "
                    f"fvg_bar={fvg.bar_index} <= locked_from={self._locked_from_bar}"
                )
                continue
            if fvg.bar_index >= current.index:
                # Henuz olusmamis / current bar sonrasi
                continue

            if self.direction == "bullish":
                wick_touched = current.low <= fvg.top
                body_broke_down = current.close < fvg.bottom
            else:
                wick_touched = current.high >= fvg.bottom
                body_broke_down = current.close > fvg.top

            if not wick_touched:
                continue
            if body_broke_down:
                continue

            logger.info(
                f"[RST] BIAS_FVG ACCEPT=trigger_ready | dir={self.direction} "
                f"fvg=[{fvg.bottom:.2f}-{fvg.top:.2f}] bar={fvg.bar_index}"
            )
            self.state = RetraceState.TRIGGER_READY
            self.trigger_fvg = fvg
            return

    def on_sweep(
        self,
        direction: Literal["bullish", "bearish"],
        level: float,
        bar_index: int | None = None,
    ):
        if self.state != RetraceState.IDLE:
            return

        # ── Sweep tekilleştirme: aynı sweep bar'ı restart sonrası tekrar tetiklenmesin ──
        if bar_index is not None:
            try:
                from state_manager import is_sweep_used

                sweep_id = f"{direction}_{bar_index}"
                if is_sweep_used(sweep_id):
                    logger.info(
                        f"[RST] SWEEP SKIP | sweep_id={sweep_id} zaten bugün kullanıldı"
                    )
                    return
            except Exception as e:
                logger.warning(f"[RST] sweep state kontrol hatası (geçiliyor): {e}")
        # ── Sweep tekilleştirme sonu ──

        self.state = RetraceState.SWEEP_DETECTED
        self.direction = direction
        self.sweep_level = level
        self._pending_sweep_id = (
            f"{direction}_{bar_index}" if bar_index is not None else None
        )
        logger.info(f"[RST] SWEEP_DETECTED | dir={direction} level={level:.2f}")

    def on_sweep_confirmed(
        self,
        bars_15m: list[Bar],
        sweep_bar: Bar,
        atr_val: float = 0.0,
        symbol: str = "",
    ):
        """Sweep onaylandiginda FVG taramasi + govde-ici kapanis onayi.

        min_fvg_size artik coin bazli: FVG_SIZE_MAP[symbol] veya FVG_MIN_SIZE_ATR_MULT fallback."""
        if self.state != RetraceState.SWEEP_DETECTED:
            return

        last = sweep_bar

        # ── Sweep invalidation: likidite okumasi ters yonde kirilirsa TAM reset ──
        if self.sweep_level is not None:
            if self.direction == "bullish" and last.close < self.sweep_level:
                logger.info(
                    f"[RST] SWEEP INVALID | close={last.close:.2f} < sweep={self.sweep_level:.2f} -> IDLE"
                )
                self.reset()
                return
            if self.direction == "bearish" and last.close > self.sweep_level:
                logger.info(
                    f"[RST] SWEEP INVALID | close={last.close:.2f} > sweep={self.sweep_level:.2f} -> IDLE"
                )
                self.reset()
                return

        # ── Coin bazli dinamik FVG eşiği ──
        import config as _cfg

        min_mult = _cfg.FVG_SIZE_MAP.get(symbol, _cfg.FVG_MIN_SIZE_ATR_MULT)
        min_fvg_size = max(atr_val * min_mult, 1e-8)

        htf_fvgs = scan_htf_fvgs(
            bars_15m,
            lookback=100,
            min_fvg_size=min_fvg_size,
            max_wick_ratio=self._max_wick_ratio,
        )
        if not htf_fvgs:
            logger.info("[FVG-DEBUG] %s no FVG found in last 100 bars", self.direction)
            return  # sweep hala gecerli, bir sonraki bar'i bekle — RESET YOK

        for fvg in reversed(htf_fvgs):
            # ── Debug: her FVG adayini logla ──
            fvg_first = max(0, fvg.bar_index - 1)
            fvg_third = fvg.bar_index + 1
            _fvg_debug = (
                f"[FVG-DEBUG] candidate |"
                f" dir={fvg.direction} |"
                f" bars=[{fvg_first},{fvg.bar_index},{fvg_third}] |"
                f" FVG=[{fvg.bottom:.4f}-{fvg.top:.4f}] |"
                f" sweep_bar_idx={last.index} |"
                f" sweep_dir={self.direction}"
            )
            if fvg.direction != self.direction:
                logger.info("%s | reject=wrong_direction", _fvg_debug)
                continue
            if fvg.bar_index >= last.index:
                logger.info(
                    "%s | reject=FVG_after_sweep (bar_idx=%d >= sweep=%d)",
                    _fvg_debug,
                    fvg.bar_index,
                    last.index,
                )
                continue

            if self.direction == "bullish":
                wick_touched = last.low <= fvg.top
                body_broke_down = last.close < fvg.bottom
            else:
                wick_touched = last.high >= fvg.bottom
                body_broke_down = last.close > fvg.top

            if not wick_touched:
                logger.info("%s | reject=wick_not_touched", _fvg_debug)
                continue
            if body_broke_down:
                logger.info("%s | reject=body_broke_fvg", _fvg_debug)
                continue

            # NOTE: fvg_close_confirmed gecici olarak devre disi — backtest karsilastirmasi icin
            # if not fvg_close_confirmed(
            #     fvg.direction, fvg.top, fvg.bottom, fvg.bar_index, bars_15m
            # ):
            #     logger.info("%s | reject=no_close_inside_fvg", _fvg_debug)
            #     continue

            logger.info("%s | ACCEPT=trigger_ready", _fvg_debug)
            self.state = RetraceState.TRIGGER_READY
            self.trigger_fvg = fvg
            self._mark_sweep_used()
            return

        return  # bu bar'da hicbir FVG tetiklenmedi — SWEEP_DETECTED'de kal, reset YOK
