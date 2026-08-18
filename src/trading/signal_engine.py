"""
signal_engine.py — CBDR → Sweep → FVG → Trigger decision.

PaperTrader._on_15m_close() içindeki iki mantıksal bloğu kapsar:
  Blok 8  — RSM state progression (IDLE → SWEEP_DETECTED → TRIGGER_READY)
  Blok 10 — Trigger check + bias/session filtreleri

Kırmızı çizgiler:
  - Strateji mantığında sıfır değişiklik
  - _pl() formatına dokunulmaz (PaperTrader'da kalır)
  - Import yolları kırılmayacak
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Literal

from models import Bar
from retrace_state import RetraceStateMachine, HTFFVG, RetraceState
from session import DailyBias, SessionState

log = logging.getLogger("sniper.signal_engine")


@dataclass
class EvalResult:
    """SignalEngine.evaluate() dönüş değeri.

    Attributes:
        decision: SKIP (filtre reddetti), WAIT (FVG henüz hazır değil), TRIGGER (entry yapılabilir)
        direction: TRIGGER kararında sweep yönü
        trigger_fvg: TRIGGER kararında wick rejection yapılan FVG
        reason: SKIP kararının sebebi (log için)
    """

    decision: Literal["SKIP", "WAIT", "TRIGGER"]
    direction: Literal["bullish", "bearish"] | None = None
    trigger_fvg: HTFFVG | None = None
    reason: str = ""


class SignalEngine:
    """CBDR/Sweep/FVG sinyal akışını yönetir.

    PaperTrader'dan DI (dependency injection) ile alır:
      - rsm: sembole özel RetraceStateMachine (primary)
      - cfg: sembole özel config dict (opsiyonel, ileride magic number'lar için)

    PaperTrader._on_15m_close() akışı:
      1. progress_rsm(bars_15m, current, ss)  → RSM state ilerlet
      2. _pl() ile FVG durumunu yazdır            → PaperTrader'da
      3. evaluate_trigger(current, ss)            → filtreler + karar
      4. EvalResult.decision == TRIGGER ise _try_entry() → PaperTrader'da
    """

    def __init__(self, rsm: RetraceStateMachine):
        self.rsm = rsm

    # ── Blok 8: RSM state progression ──────────────────────────

    def progress_rsm(
        self,
        bars_15m: list[Bar],
        current: Bar,
        ss: SessionState,
        atr_val: float = 0.0,
        symbol: str = "",
    ) -> None:
        """RSM state machine'i ilerlet: IDLE → SWEEP_DETECTED → TRIGGER_READY.

        Orijinal _on_15m_close() Blok 8 ile birebir aynı mantık.
        atr_val: ATR-bazlı dinamik FVG eşiği için on_sweep_confirmed'e iletilir.
        symbol: coin bazli FVG_SIZE_MAP lookup icin.
        """
        # IFVG (ikincil yol): her bar'da kaynak etiketini 'NORMAL' sifirla;
        # asagidaki IFVG blogu tetiklenirse 'IFVG' ile ezer. Flag kapaliyken
        # check_ifvg_retest None dondugu icin islevsel etkisi yoktur.
        self.rsm._last_trigger_source = "NORMAL"

        if self.rsm.state_name == "IDLE" and ss.sweep_confirmed:
            self.rsm.on_sweep(
                direction=ss.sweep_direction or "bullish",
                level=ss.sweep_level or 0.0,
                bar_index=current.index,
                symbol=symbol,
            )
            # Sweep tüketildi (SWEEP_DETECTED'e geçildi veya dedup reddetti):
            # bayrağı temizle. Aksi halde aynı sweep her 15m bar'da yeniden
            # onaylanıp aynı "ölü" sinyali üretirdi (SEIUSDT direction-fail
            # döngüsü — giriş reddi sonrası pozisyon açılmadan tekrar denenir).
            ss.sweep_confirmed = False

        if self.rsm.state_name == "SWEEP_DETECTED":
            self.rsm.on_sweep_confirmed(bars_15m, current, atr_val, symbol)
            if self.rsm.state_name == "IDLE":
                ss.sweep_confirmed = False

        if self.rsm.state_name == "BIAS_LOCKED":
            db = ss.daily_bias
            locked_dir = self.rsm.direction
            bias_conflict = (
                (locked_dir == "bullish" and db == DailyBias.BEARISH)
                or (locked_dir == "bearish" and db == DailyBias.BULLISH)
                or db == DailyBias.NEUTRAL
            )
            if bias_conflict:
                # Bias tersine dondu veya nötr (yeni CBDR gunu) -> kiliti kaldir,
                # yeni sweep bekle. Kilit yonune ters duşen FVG'lerle
                # surdurulebilir kayip zincirini onler.
                self.rsm.reset()
            else:
                # Bias hala kilit yonunu destekliyor -> taze FVG wick rejection'i
                # ile yeniden TRIGGER_READY olmaya calis (yeni sweep gerekmez).
                self.rsm.on_bias_fvg(bars_15m, current, atr_val, symbol)

        # ── IFVG ikincil yol: normal yol TRIGGER_READY yapmadiysa dene ──
        # Ana sweep+FVG yolu onceeliklidir; ayni bar'da normal kazandiysa
        # (state zaten TRIGGER_READY) buraya girilmez. IFVG tetiklenirse
        # trigger_fvg ayni tiptedir (HTFFVG) -> entry_manager SL/TP ayni kaliyla
        # calisir (direktif madde 6).
        if self.rsm.state != RetraceState.TRIGGER_READY:
            ifvg_hit = self.rsm.check_ifvg_retest(current)
            if ifvg_hit is not None:
                # IFVG entry state makinesini kirletmesin: trigger aninda yon
                # IFVG yonune cekilir (entry side hesabi icin gerekli) AMA giris
                # oncesi sweep/bias yonu saklanir. Entry tarafi (bot.py)
                # kapanista bu yone geri donup normal entry gibi BIAS_LOCKED'a
                # gecer — ters yon kilidi bias_conflict -> reset ile gunun sweep
                # penceresini oldurmesin.
                self.rsm._pre_ifvg_direction = self.rsm.direction
                self.rsm.state = RetraceState.TRIGGER_READY
                self.rsm.direction = ifvg_hit.direction
                self.rsm.trigger_fvg = ifvg_hit
                self.rsm._last_trigger_source = "IFVG"

        ss.fvg_ready = self.rsm.state_name == "TRIGGER_READY"

    # ── Blok 10: Trigger check + filtreler ─────────────────────

    def evaluate_trigger(self, current: Bar, ss: SessionState) -> EvalResult:
        """Trigger hazırsa bias/session filtrelerini uygula, karar ver.

        Orijinal _on_15m_close() Blok 10 ile birebir aynı mantık.
        Filtreler:
          1. Bias uyuşmazlığı → SKIP + rsm.reset()
          2. NEUTRAL bias → SKIP + rsm.reset()
          3. Session LONDON/NEWYORK değilse → SKIP + rsm.reset()
        """
        if not self.rsm.can_trigger():
            return EvalResult(decision="WAIT")

        # Candle close guard — sadece kapali mum ile entry
        if not current.is_closed:
            log.info("[SKIP] trigger — bar not closed, atlandi (rsm reset)")
            self.rsm.reset()
            return EvalResult(decision="SKIP", reason="bar_not_closed")

        # Bias filter — IFVG kaynakli trigger'lar bu filtreden MUAF:
        # counter-trend (inversion) yapisi geregi bias uyumu gerektirmez.
        # IFVG_ENABLED=False iken _last_trigger_source hep 'NORMAL' oldugundan
        # guard her zaman True calisir, davranis bugunku ile bit-bit ayni.
        if getattr(self.rsm, "_last_trigger_source", None) != "IFVG":
            if self.rsm.direction == "bullish" and ss.daily_bias == DailyBias.BEARISH:
                log.info("[SKIP] bullish trigger — bias BEARISH, atlandi (rsm reset)")
                self.rsm.reset()
                return EvalResult(decision="SKIP", reason="bias_bearish")

            if self.rsm.direction == "bearish" and ss.daily_bias == DailyBias.BULLISH:
                log.info("[SKIP] bearish trigger — bias BULLISH, atlandi (rsm reset)")
                self.rsm.reset()
                return EvalResult(decision="SKIP", reason="bias_bullish")

            if ss.daily_bias == DailyBias.NEUTRAL:
                log.info("[SKIP] trigger — bias NEUTRAL, atlandi (rsm reset)")
                self.rsm.reset()
                return EvalResult(decision="SKIP", reason="bias_neutral")

        # Session filter — analyzer_v5.py:302-303 ile birebir:
        # CBDR penceresi ICINDE ise SKIP, disinda her saat TRIGGER'a acik.
        # detect_phase yerine sembole ozel cbdr_start/end penceresi kullanilir;
        # SOL (19-1) gibi pencerelerde saat 01 backtest'te serbesttir, detect_phase
        # ise onu CLOSED sayip SKIP yapar — bu fark canliyi backtest'ten saptirir.
        h = datetime.fromtimestamp(current.timestamp / 1000, tz=timezone.utc).hour
        sh, eh = ss.cbdr_start, ss.cbdr_end
        in_cbdr = (h >= sh or h < eh) if sh > eh else (sh <= h < eh)
        if in_cbdr:
            log.info(
                "[SKIP] trigger — CBDR penceresinde (h=%d), atlandi (rsm reset)", h
            )
            self.rsm.reset()
            return EvalResult(decision="SKIP", reason="session_filter")

        return EvalResult(
            decision="TRIGGER",
            direction=self.rsm.direction,
            trigger_fvg=self.rsm.trigger_fvg,
        )
