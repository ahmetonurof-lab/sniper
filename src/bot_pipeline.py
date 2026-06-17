"""
bot_pipeline.py — NEXUS V4
────────────────────────────
Her 1m bar kapanışında çalışan sinyal analiz + emir kapısı.
_on_1m_close 7 odaklanmış alt metoda bölündü — her biri cc < 15.

Orijinal konum: sonnet/src/main.py
  _is_15m_closed   satır 1866
  _on_1m_close     satır 1877 (326 satır, cc=96 → 7 × cc<15)
"""

from __future__ import annotations

import asyncio
import logging
import time
from datetime import datetime
from typing import TYPE_CHECKING

import config
import monitor
import state_logger
from bot_infra import export_ohlc_15m, get_lock
from models import Bar
from state_machine import SetupState, StateMachine

if TYPE_CHECKING:
    from analyzer import MarketAnalyzer
    from bot_infra import DailyDataCache, TradeEntry
    from bot_positions import PositionManager
    from event_router import EventRouter
    from trader import LiveExecutor
    from websocket import BinanceWSHub

log = logging.getLogger("nexus.live")


# Fallback print — logger handler yoksa bile çıktı al
def _ll(fmt: str, *args: object) -> None:
    """Zincirli log satırı — sadece logger, print YOK."""
    if args:
        log.info(fmt, *args)
    else:
        log.info("%s", fmt)


class TradingPipeline:
    """
    Her 1m bar kapanışında analiz ve emir gönderme sorumluluğu.

    on_1m_close() delegasyon merkezi — iş mantığı alt metodlarda.
    Her alt metod bağımsız olarak test edilebilir.

    Bağımlılıklar constructor'a inject edilir.
    active_trades mutable dict referansı bot.py ile paylaşılır.
    """

    def __init__(
        self,
        hub: BinanceWSHub,
        state_machine: StateMachine,
        event_router: EventRouter,
        analyzers: dict[str, MarketAnalyzer],
        active_trades: dict[str, TradeEntry],
        executor: LiveExecutor,
        positions: PositionManager,
        daily_cache: DailyDataCache,
    ) -> None:
        self.hub = hub
        self.state_machine = state_machine
        self.event_router = event_router
        self.analyzers = analyzers
        self.active_trades = active_trades
        self.executor = executor
        self.positions = positions
        self.daily_cache = daily_cache

        self._15m_close_cache: dict[str, int] = {}
        self._last_log_symbol: str | None = None

    # ─────────────────────────────────────────────────────────────────
    # Ana giriş noktası — delegasyon, iş mantığı yok (cc = 5)
    # ─────────────────────────────────────────────────────────────────

    async def on_1m_close(self, symbol: str, bars_m1: list[Bar]) -> None:
        try:
            current_bar = bars_m1[-1]
            monitor.update_tick(symbol)

            await self.positions.safe_manage_open_trades(current_bar)
            asyncio.create_task(self.positions.safe_sync(current_bar))

            bars_h4 = self.hub.get_bars(symbol, "4h")
            bars_h1 = self.hub.get_bars(symbol, "1h")
            bars_15m = self.hub.get_bars(symbol, "15m")
            bars_d1 = await self.daily_cache.get(symbol)

            if not self._check_buffers(symbol, bars_h4, bars_h1, bars_15m, bars_d1):
                return

            if self._is_15m_closed(symbol):
                self._handle_15m_close(symbol, bars_15m)

            if symbol not in self.active_trades:
                await self._evaluate_state(symbol, current_bar)
                await self._check_partial_entry(symbol, bars_m1, current_bar)
                await self._check_ready_to_enter(symbol, bars_m1, current_bar)

            if symbol in self.active_trades:
                self._log_active_trade_guard(symbol)
                return

            await self._emit_events(symbol, bars_d1, bars_h4, bars_h1, bars_15m, bars_m1)

        except Exception as e:
            log.error("[_on_1m_close] %s | Hata: %s", symbol, str(e), exc_info=True)

        finally:
            # Log hiyerarşisi her durumda basılır (aktif trade olsa bile)
            try:
                self._state_debug_log(symbol, current_bar)
            except Exception:
                pass

    # ─────────────────────────────────────────────────────────────────
    # Alt metod 1: Buffer yeterliliği kontrolü (saf, cc = 4)
    # ─────────────────────────────────────────────────────────────────

    def _check_buffers(
        self,
        symbol: str,
        bars_h4: list | None,
        bars_h1: list | None,
        bars_15m: list | None,
        bars_d1: list | None,
    ) -> bool:
        """Gerekli bar sayıları karşılanmıyorsa False döner."""
        if bars_h4 is None or bars_h1 is None or bars_15m is None or bars_d1 is None:
            log.warning(
                "[SKIP] %s bar buffer None: h4=%s h1=%s 15m=%s d1=%s",
                symbol,
                bars_h4 is not None,
                bars_h1 is not None,
                bars_15m is not None,
                bars_d1 is not None,
            )
            return False

        if len(bars_d1) < 110 or len(bars_h4) < 200 or len(bars_h1) < 10 or len(bars_15m) < 5:
            log.warning(
                "[SKIP] %s yetersiz bar: d1=%d h4=%d h1=%d m15=%d",
                symbol,
                len(bars_d1),
                len(bars_h4),
                len(bars_h1),
                len(bars_15m),
            )
            return False

        return True

    # ─────────────────────────────────────────────────────────────────
    # Alt metod 2: 15m kapanış tespiti (saf, cc = 3)
    # ─────────────────────────────────────────────────────────────────

    def _is_15m_closed(self, symbol: str) -> bool:
        """15m mumun kapandığını timestamp ile tespit et."""
        bars_15m = self.hub.get_bars(symbol, "15m")
        if not bars_15m:
            return False
        last_15m_ts = bars_15m[-1].timestamp
        prev = self._15m_close_cache.get(symbol)
        if prev is not None and prev == last_15m_ts:
            return False
        self._15m_close_cache[symbol] = last_15m_ts
        return True

    # ─────────────────────────────────────────────────────────────────
    # Alt metod 3: 15m kapanış işlemleri (cc = 2)
    # ─────────────────────────────────────────────────────────────────

    def _handle_15m_close(self, symbol: str, bars_15m: list[Bar]) -> None:
        """15m bar kapandığında: OHLC export + state snapshot."""
        export_ohlc_15m(bars_15m[-1], symbol)
        state_logger.write_snapshot(
            symbol=symbol,
            state=self.state_machine.get(symbol),
        )

    # ─────────────────────────────────────────────────────────────────
    # Alt metod 4: State makine değerlendirmesi (cc = 5)
    # ─────────────────────────────────────────────────────────────────

    async def _evaluate_state(self, symbol: str, current_bar: Bar) -> None:
        """State check'leri + IDLE geçişinde cache reset.
        V40: Pipeline sadece check_retrace çağırır."""
        self.state_machine.check_retrace(symbol, current_bar)

        _state_before = self.state_machine.get(symbol).state
        self.state_machine._evaluate(
            self.state_machine.get(symbol),
            current_time=datetime.now(),
            last_closed_bar=current_bar,
        )
        _state_after = self.state_machine.get(symbol).state

        if _state_before != _state_after and _state_after == SetupState.IDLE and _state_before != SetupState.IDLE:
            if symbol in self.analyzers:
                self.analyzers[symbol].reset_symbol_cache()
            log.debug("[CACHE-RESET] %s → IDLE, analyzer cache temizlendi", symbol)

    # ─────────────────────────────────────────────────────────────────
    # Alt metod 5: Time-boxed partial entry (cc = 9)
    # ─────────────────────────────────────────────────────────────────

    async def _check_partial_entry(
        self,
        symbol: str,
        bars_m1: list[Bar],
        current_bar: Bar,
    ) -> None:
        """WAIT_CONFIRM'de zaman aşımı sonrası kısmi giriş."""
        current_state = self.state_machine.get(symbol)
        if current_state.state != SetupState.WAIT_CONFIRM:
            return

        try:
            tc_min = getattr(config, "WAIT_CONFIRM_TIMEBOX_MIN", 0)
            scale = getattr(config, "PARTIAL_RISK_SCALE", 0.0)
            if tc_min <= 0 or scale <= 0:
                return

            since = getattr(current_state, "wait_confirm_since_ts", None)
            if not since:
                return

            elapsed_min = max(0.0, (current_bar.timestamp - since) / 60000.0)
            if elapsed_min < tc_min:
                return
            if not (current_state.fvg_upper and current_state.fvg_lower):
                return
            if symbol in self.active_trades:
                return

            from state_machine import PenetrationEngine  # noqa: PLC0415

            engine = PenetrationEngine(current_state.fvg_upper, current_state.fvg_lower, current_state.direction)
            pen = engine.get_penetration(current_bar.close)
            pen_min = getattr(config, "FVG_PENETRATION_MIN", 0.15)
            if not (pen_min <= pen <= 1.00):
                return

            async with get_lock(symbol):
                if symbol in self.active_trades:
                    return
                await self._send_partial_order(symbol, bars_m1, current_state, scale, pen)

        except Exception as e:
            log.warning("[PARTIAL] %s error: %s", symbol, e)

    async def _send_partial_order(
        self,
        symbol: str,
        bars_m1: list[Bar],
        current_state: object,
        scale: float,
        pen: float,
    ) -> None:
        """Partial emir gönder — lock içinde çağrılır."""
        risk_mgr = self.positions.get_risk_manager(symbol)
        tp = risk_mgr.build_trade(
            state=current_state,
            entry_price=bars_m1[-1].close,
            h4_swing_level=current_state.h4_swing_level,
            h1_liquidity_level=current_state.h1_liquidity_level,
        )
        if tp is None:
            log.warning("[PARTIAL] %s build_trade rejected", symbol)
            return

        scaled_lot = max(0.0, tp.lot * scale)
        try:
            scaled_lot = risk_mgr._round_lot(symbol, scaled_lot)
        except Exception as e:
            log.warning("[PARTIAL] %s _round_lot hatası: %s", symbol, e)
            scaled_lot = 0.0
        if scaled_lot <= 0:
            log.warning("[PARTIAL] %s scaled lot <=0; skip", symbol)
            return

        risk_dist = abs(tp.entry - tp.sl)
        tp.lot = scaled_lot
        tp.risk_usd = round(risk_dist * scaled_lot, 4)

        order = await self.executor.send_order(
            tp,
            entry_order_type=getattr(config, "ENTRY_ORDER_TYPE", "MARKET"),
            current_price=bars_m1[-1].close,
            stop_offset_pct=getattr(config, "ENTRY_STOP_OFFSET_PCT", 0.0),
            partial=True,
        )
        if order is None:
            return

        self.active_trades[symbol] = self._build_trade_dict(tp, current_state, partial=True)
        self.state_machine.set_state(symbol, SetupState.ENTERED)
        self.positions.flush_state()
        log.info("[PARTIAL] %s entry sent (scale=%.2f pen=%.2f)", symbol, scale, pen)

    # ─────────────────────────────────────────────────────────────────
    # Alt metod 6: READY_TO_ENTER giriş (cc = 6)
    # ─────────────────────────────────────────────────────────────────

    async def _check_ready_to_enter(
        self,
        symbol: str,
        bars_m1: list[Bar],
        current_bar: Bar,
    ) -> None:
        """READY_TO_ENTER state'inde emir gönder."""
        current_state = self.state_machine.get(symbol)
        if current_state.state != SetupState.READY_TO_ENTER:
            return

        async with get_lock(symbol):
            if symbol in self.active_trades:
                log.warning("[EXECUTE] %s zaten aktif trade var — atlandı", symbol)
                return

            risk_mgr = self.positions.get_risk_manager(symbol)
            trade_params = risk_mgr.build_trade(
                state=current_state,
                entry_price=bars_m1[-1].close,
                h4_swing_level=current_state.h4_swing_level,
                h1_liquidity_level=current_state.h1_liquidity_level,
            )
            if trade_params is None:
                log.warning("[EXECUTE] %s build_trade reddetti → atlanıyor", symbol)
                self.state_machine.invalidate(symbol)
                return

            order = await self.executor.send_order(
                trade_params,
                entry_order_type=getattr(config, "ENTRY_ORDER_TYPE", "MARKET"),
                current_price=bars_m1[-1].close,
                stop_offset_pct=getattr(config, "ENTRY_STOP_OFFSET_PCT", 0.0),
                partial=False,
            )
            if order is None:
                return

            self.active_trades[symbol] = self._build_trade_dict(trade_params, current_state, partial=False)
            self.state_machine.set_state(symbol, SetupState.ENTERED)
            self.positions.flush_state()
            log.info(
                "[EXECUTE] %s ✅ emir gönderildi — entry=%.5f sl=%.5f tp=%.5f RR=%.2f",
                symbol,
                trade_params.entry,
                trade_params.sl,
                trade_params.tp,
                trade_params.gross_rr,
            )

    # ─────────────────────────────────────────────────────────────────
    # Alt metod 7: Event emit (cc = 4)
    # ─────────────────────────────────────────────────────────────────

    async def _emit_events(
        self,
        symbol: str,
        bars_d1: list[Bar],
        bars_h4: list[Bar],
        bars_h1: list[Bar],
        bars_15m: list[Bar],
        bars_m1: list[Bar],
    ) -> None:
        """Analyzer → event_router → state_machine."""
        if bars_m1 is None or len(bars_m1) < 5:
            log.warning("[SKIP] %s yetersiz 1m bar: %d", symbol, len(bars_m1) if bars_m1 else 0)
            return

        events = self.analyzers[symbol].analyze(
            bars_d1=bars_d1,
            bars_h4=bars_h4,
            bars_h1=bars_h1,
            bars_15m=bars_15m,
            bars_m1=bars_m1,
        )
        if events:
            for event in events:
                self.event_router.publish(symbol, event)

    # ─────────────────────────────────────────────────────────────────
    # Yardımcılar
    # ─────────────────────────────────────────────────────────────────

    def _log_active_trade_guard(self, symbol: str) -> None:
        """Aktif trade varken sinyal bloklama log'u."""
        existing = self.active_trades[symbol]
        if existing.get("protection_missing"):
            log.warning("🟡 SAFE MODE | %s | yeni sinyal ENGELLENDİ", symbol.ljust(12))
        if existing.get("protection_repairing"):
            log.warning("🟡 REPAIR MODE | %s | yeni sinyal ENGELLENDİ", symbol.ljust(12))

    def _state_debug_log(self, symbol: str, current_bar: Bar | None = None) -> None:
        """
        V4 Zincirli Log Hiyerarşisi.

        A — Bias beklemede (PENDING/RANGE/STRICT/SKIP): 1 satır  🟨
        B — Bias onaylı, setup aranıyor (IDLE/ARMED + STRONG): 2 satır
        C — Setup 3 yeşil, retrace bekleniyor (WAIT_RETRACE): 3 satır
        D — Kutu içi, 1m pusu aktif (WAIT_CONFIRM): 4 satır
        E — Tetik çekildi (READY_TO_ENTER): 4 satır
        F — INVALIDATED: 1 satır  ❌
        Sessiz: ENTERED, EXPIRED
        """
        # ── Sembol gruplama: aynı sembole ait logları blokla, her sembol arası boş satır
        if symbol != self._last_log_symbol:
            if self._last_log_symbol is not None:
                _ll("")  # boş satır
            self._last_log_symbol = symbol

        st = self.state_machine.get(symbol)
        state = st.state

        # Sessiz
        if state in (SetupState.ENTERED, SetupState.EXPIRED):
            return

        # F: INVALIDATED
        if state == SetupState.INVALIDATED:
            _ll("[%s] ❌ SETUP_INVALIDATED | FVG DESTRUCTED (PEN > %%100)", symbol.ljust(12))
            return

        # ── Bias bilgisini çöz ──────────────────────────────────────
        strength = (st.htf_strength or "NONE").upper()
        d1 = (getattr(st, "d1_bias", None) or st.htf_bias or "RANGE").upper()
        h4 = (getattr(st, "h4_bias_val", None) or st.htf_bias or "RANGE").upper()
        bias_confirmed = strength == "STRONG" and d1 not in ("RANGE", "NONE") and h4 not in ("RANGE", "NONE")

        # A: Bias beklemede / reddedildi
        if not bias_confirmed:
            if strength == "SKIP_DAY":
                _ll("[%s] 🟥 BIAS: REJECTED | D1: OUTSIDE | H4: ANY | HIGH_VOLATILITY", symbol.ljust(12))
            elif strength == "STRICT_WAIT":
                _ll("[%s] 🟥 BIAS: REJECTED | D1: %s | H4: %s | AVOID_COUNTER", symbol.ljust(12), d1, h4)
            elif d1 == "RANGE" and h4 == "CONSOLIDATION":
                _ll("[%s] � BIAS: REJECTED | D1: RANGE | H4: CONSOLIDATION", symbol.ljust(12))
            elif d1 == "RANGE":
                h4_icon = "🟩" if h4 == "LONG" else "🟥"
                _ll("[%s] 🟨 BIAS: PENDING | D1: RANGE | H4: %s | 1H=%s BOS bekliyor", symbol.ljust(12), h4, h4_icon)
            else:
                d1_icon = "🟩" if d1 == "LONG" else "🟥"
                _ll(
                    "[%s] 🟨 BIAS: PENDING | D1: %s | H4: CONSOLIDATION | H4=%s BOS bekliyor",
                    symbol.ljust(12),
                    d1,
                    d1_icon,
                )
            return

        # B satır 1: Bias onaylı
        bias_label = f"STRONG_{d1}"
        _ll("[%s] 🟩 BIAS: %s | D1: %s | H4: %s", symbol.ljust(12), bias_label, d1, h4)

        # B satır 2: Setup scan
        sweep_ok = st.sweep_detected
        mss_ok = st.mss_confirmed
        fvg_ok = st.fvg_upper is not None and st.fvg_lower is not None
        sweep_tf = getattr(st, "sweep_tf", None) or "1H"
        setup_type = "15M_FALLBACK" if sweep_tf == "15m" else "1H_MAIN"
        fvg_lbl = "15M" if sweep_tf == "15m" else "1H"

        if not (sweep_ok and mss_ok and fvg_ok):
            _ll(
                "[%s] 🟨 SETUP_SCAN | TYPE: %s | SWEEP(%s): %s | MSS(15M): %s | FVG(%s): %s",
                symbol.ljust(12),
                setup_type,
                sweep_tf,
                "🟩" if sweep_ok else "🟥",
                "🟩" if mss_ok else "🟥",
                fvg_lbl,
                "🟩" if fvg_ok else "🟥",
            )
            return

        # C/D/E satır 2: Setup OK
        _ll(
            "[%s] 🟩 SETUP_OK | TYPE: %s | SWEEP(%s): 🟩 | MSS(15M): 🟩 | FVG(%s): 🟩",
            symbol.ljust(12),
            setup_type,
            sweep_tf,
            fvg_lbl,
        )

        # Pen hesapla
        pen_pct = 0
        if current_bar is not None and fvg_ok and st.direction is not None:
            from state_machine import PenetrationEngine

            pen_pct = round(
                PenetrationEngine(st.fvg_upper, st.fvg_lower, st.direction).get_penetration(current_bar.close) * 100
            )

        # C: WAIT_RETRACE — pen düşük
        if state == SetupState.WAIT_RETRACE:
            _ll("[%s] 🟨 RETRACE | PEN: %%%d 🟥 | WAITING_ZONE...", symbol.ljust(12), pen_pct)
            return

        # D/E: Kutu içi
        if state in (SetupState.WAIT_CONFIRM, SetupState.READY_TO_ENTER):
            _ll("[%s] 🟩 RETRACE | PEN: %%%d 🟩 | INSIDE_ZONE", symbol.ljust(12), pen_pct)
            if state == SetupState.WAIT_CONFIRM:
                pivot_icon = "🟩" if st.ltf_confirmed else "🟨"
                candle_label = "CEMENTED_CANDLE" if st.ltf_confirmed else "CEMENTING..."
                _ll("[%s] 🟨 LTF_SCAN | PIVOT_WAIT: %s | CANDLE: 🟨 %s", symbol.ljust(12), pivot_icon, candle_label)
            else:
                _ll("[%s] 🟩 LTF_SCAN | CEMENTED_CANDLE: 🟩 | [ACTION: ENTRY_TRIGGERED]", symbol.ljust(12))

    def _build_trade_dict(self, trade_params: object, current_state: object, partial: bool) -> dict:
        """TradeEntry dict'ini trade_params + current_state'ten oluştur."""
        base = {
            "symbol": getattr(trade_params, "symbol", ""),
            "direction": trade_params.direction,
            "entry": trade_params.entry,
            "initial_sl": trade_params.initial_sl,
            "current_sl": trade_params.initial_sl,
            "tp": trade_params.tp,
            "lot": trade_params.lot,
            "risk_usd": trade_params.risk_usd,
            "breakeven_level": trade_params.breakeven_level,
            "trailing_level": trade_params.trailing_level,
            "breakeven_done": False,
            "trailing_done": False,
            "open_time": int(time.time() * 1000),
            "status": "open",
            "pnl": 0.0,
            "last_price": trade_params.entry,
            "d1_bias": current_state.htf_bias,
            "h4_bias": current_state.htf_bias,
            "bias_strength": current_state.htf_strength,
            "h4_sl": current_state.h4_swing_level,
            "h1_tp": current_state.h1_liquidity_level,
            "sweep": current_state.sweep_detected,
            "sweep_side": "SSL" if current_state.direction == "LONG" else "BSL",
            "sweep_level": current_state.sweep_level,
            "sweep_bar_index": current_state.sweep_bar_index,
            "mss": current_state.mss_confirmed,
            "mss_level": current_state.mss_level,
            "mss_bar_index": current_state.mss_bar_index,
            "mss_direction": current_state.direction,
            "impulse_origin": getattr(current_state, "displacement_origin", None),
            "fvg_upper": current_state.fvg_upper,
            "fvg_lower": current_state.fvg_lower,
            "fvg_bar_index": current_state.fvg_entry_bar_index,
            "fvg_direction": "bearish" if current_state.direction == "SHORT" else "bullish",
            "ltf": current_state.ltf_confirmed,
            "state": current_state.state.value,
            "partial": partial,
        }
        if not partial:
            base.update(
                {
                    "sl": trade_params.sl,
                    "tp_val": trade_params.tp,
                    "rr": trade_params.gross_rr,
                    "exit": None,
                    "lot_val": trade_params.lot,
                }
            )
        return base
