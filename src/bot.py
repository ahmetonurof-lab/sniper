"""
bot.py — sniper paper trade orchestrator
CBDR -> Sweep -> FVG Wick Rejection -> Entry -> Trailing (1m) -> Exit (1m) -> Retrade
Backtest (analyzer.py) ile birebir ayni performans.
"""

from __future__ import annotations

import asyncio
import json
import logging
import logging.handlers
import os
import sys
import time
import urllib.request
from datetime import UTC, datetime, timezone, timedelta

import config as cfg
from bot_binance import BinanceRESTClient
from bot_infra import _close_ohlc_writers, _RateLimiter
from fvg import detect_fvgs
from models import Bar
from retrace_state import RetraceStateMachine
from session import DailyBias, SessionPhase, SessionState, detect_phase_from_timestamp
from state_manager import (
    mark_trade_opened,
    mark_trade_closed,
    reconcile_from_active,
    get_trade_count_today,
    save_retrade_arm,
    load_retrade_arm,
    clear_retrade_arm,
)
from websocket import BinanceWSHub

TR_TZ = timezone(timedelta(hours=3))

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_OUTPUT_DIR = os.path.join(_SCRIPT_DIR, "..", "output")
os.makedirs(_OUTPUT_DIR, exist_ok=True)

_log_file = os.path.join(_OUTPUT_DIR, "paper_trade.log")

# Logger'ın saat dilimini Türkiye Saati (UTC+3) olarak ayarla
logging.Formatter.converter = staticmethod(
    lambda ts: datetime.fromtimestamp(ts, TR_TZ).timetuple()
)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s \u2014 %(message)s",
    handlers=[logging.FileHandler(_log_file, mode="a", encoding="utf-8-sig")],
    force=True,
)

log = logging.getLogger("sniper.paper")
log.setLevel(logging.INFO)
log.propagate = False
_fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(name)s \u2014 %(message)s")
log.addHandler(logging.FileHandler(_log_file, mode="a", encoding="utf-8-sig"))

try:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    if hasattr(sys.stderr, "reconfigure"):
        sys.stderr.reconfigure(encoding="utf-8")
except Exception:
    pass

INITIAL_CAPITAL = cfg.INITIAL_BALANCE
RISK_PER_TRADE = cfg.RISK_PER_TRADE


class PaperTrader:
    def __init__(self, symbols: list[str] | None = None):
        self.symbols = [s.upper() for s in (symbols or cfg.SYMBOLS)]

        self.testnet = cfg.IS_TESTNET
        if self.testnet:
            self.rest_base = "https://demo-fapi.binance.com"
            self.ws_base = "wss://fstream.binancefuture.com/stream?streams="
        else:
            self.rest_base = "https://fapi.binance.com"
            self.ws_base = "wss://fstream.binance.com/stream?streams="

        self.hub = BinanceWSHub(
            symbols=self.symbols,
            timeframes=["1m", "15m"],
            max_bars=500,
            base_url=self.ws_base,
        )
        self.states: dict[str, SessionState] = {}
        self.rsms: dict[str, RetraceStateMachine] = {}
        self.rsms_retrade: dict[str, RetraceStateMachine] = {}
        self.cfgs: dict[str, dict] = {}
        self.active_trades: dict[str, dict] = {}
        self.trades: list[dict] = []
        self._log_state: dict[str, dict] = {}
        self._stage: dict[str, dict] = {}
        self._balance = INITIAL_CAPITAL

        api_key = cfg.BINANCE_API_KEY or ""
        api_secret = cfg.BINANCE_API_SECRET or ""
        self.rest = BinanceRESTClient(
            api_key=api_key,
            api_secret=api_secret,
            base_url=self.rest_base,
            rate_limiter=_RateLimiter(1200),
            semaphore=asyncio.Semaphore(5),
        )

        for sym in self.symbols:
            min_fvg = cfg.FVG_SIZE_MAP.get(sym, 0.5)
            self.cfgs[sym] = {
                "MIN_FVG_SIZE": min_fvg,
                "SL_ATR_MULT": cfg.SL_ATR_MULT,
                "TP_RR": cfg.TP_RR,
                "FVG_BUFFER_MULT": cfg.FVG_BUFFER_MULT,
            }
            self.states[sym] = SessionState()
            self.rsms[sym] = RetraceStateMachine(min_fvg_size=min_fvg)
            self.rsms_retrade[sym] = RetraceStateMachine(min_fvg_size=min_fvg * 0.3)

    def _pl(self, sym: str, key: str, msg: str, force: bool = False):
        prev = self._log_state.get(sym, {}).get(key)
        if not force and prev == msg:
            return
        self._log_state.setdefault(sym, {})[key] = msg
        ts = datetime.now(TR_TZ).strftime("%H:%M:%S")
        _prev_sym = getattr(self, "_prev_print_sym", None)
        _separator = "" if _prev_sym == sym else "\n"
        self._prev_print_sym = sym
        print(f"{_separator}[{ts}] [{sym:<12}] {msg}", flush=True)

    def _session_label(self, hour: int) -> str:
        if hour >= 22 or hour < 2:
            return "ASIA"
        elif 2 <= hour < 13:
            return "LONDON"
        return "NEWYORK"

    # ── 15m: Sinyal kurulumu (CBDR, Sweep, FVG, Entry, Retrade) ──

    async def _on_15m_close(self, sym: str, bars_15m: list[Bar]):
        cfg = self.cfgs[sym]
        min_fvg = cfg["MIN_FVG_SIZE"]
        sl_atr = cfg["SL_ATR_MULT"]
        tp_rr = cfg["TP_RR"]
        fvg_buf = cfg["FVG_BUFFER_MULT"]

        current = bars_15m[-1]
        atr_val = max(current.range, current.close * 0.0001)
        try:
            dt = datetime.fromtimestamp(current.timestamp / 1000, tz=UTC)
        except Exception:
            return
        hour = dt.hour
        session = self._session_label(hour)

        ss = self.states[sym]
        ss.update(dt, current.open, current.high, current.low, current.close, atr_val)

        # Pozisyon açıkken sinyal taramasını atla. Trailing + exit _on_1m_close'da.
        if sym in self.active_trades:
            trade = self.active_trades[sym]
            side_icon = "\U0001f7e9" if trade["side"] == "long" else "\U0001f7e5"
            ts = f"{hour:02d}:{dt.minute:02d}"
            self._log_state.get(sym, {}).pop("st_fvg", None)
            self._log_state.get(sym, {}).pop("st_wck", None)
            self._pl(
                sym,
                "st_ses",
                f"{side_icon} POZISYON AKTIF | {trade['side'].upper()} @ {trade['entry_price']:.2f}"
                f" | SL: {trade['sl']:.2f} | TP: {trade['tp']:.2f}"
                f" | TRAIL: {trade.get('trailing_count', 0)}x | {ts} UTC",
                force=True,
            )
            return

        if session == "ASIA":
            self._pl(
                sym,
                "st_ses",
                "\U0001f7e5 SESSION: ASIA | 22:00-02:00 UTC | trading kapali",
                force=True,
            )
            self._stage.pop(sym, None)
            return

        st = self._stage.setdefault(sym, {})
        ts = f"{hour:02d}:{dt.minute:02d}"

        bias_str = ""
        if ss.daily_bias != DailyBias.NEUTRAL:
            d = "LONG" if ss.daily_bias == DailyBias.BULLISH else "SHORT"
            c = "\U0001f7e9" if d == "LONG" else "\U0001f7e5"
            bias_str = f" | BIAS: {c}{d}"
        cbdr_s = "\u2705 LOCKED" if ss.cbdr_locked else "\u23f3 BODY TRACKING..."
        rt = ss.range_type if ss.range_type in ("CBDR", "ASIA", "DEAD") else ""
        rt_str = f" | RANGE: {rt}" if rt else ""
        self._pl(
            sym,
            "st_ses",
            f"\U0001f7e9 SESSION: {session} | {ts} UTC | CBDR: {cbdr_s}{rt_str}{bias_str}",
            force=True,
        )

        if not ss.cbdr_locked:
            st.clear()
            log.info("[SKIP] %s CBDR henuz kilitlenmedi — sinyal taranmadi", sym)
            return

        if ss.sweep_confirmed:
            sd = ss.sweep_direction or "bullish"
            sl = ss.sweep_level or 0.0
            si = "\U0001f7e9" if sd == "bullish" else "\U0001f7e5"
            self._pl(
                sym,
                "st_swp",
                f"\U0001f7e9 SWEEP: DETECTED | {si}{sd.upper()} | [{sl:.2f}] | CBDR: [{ss.cbdr_body_low:.4f}-{ss.cbdr_body_high:.4f}]",
                force=True,
            )
        else:
            bstr = ""
            if ss.daily_bias != DailyBias.NEUTRAL:
                d = "LONG" if ss.daily_bias == DailyBias.BULLISH else "SHORT"
                c = "\U0001f7e9" if d == "LONG" else "\U0001f7e5"
                bstr = f" | BIAS: {c}{d}"
            if ss.range_type == "DEAD":
                self._pl(
                    sym,
                    "st_swp",
                    f"\U0001f480 CBDR/ASIA DEAD \u2014 sweep aranm\u0131yor | {ts}",
                    force=True,
                )
                return
            rt = ss.range_type if ss.range_type in ("CBDR", "ASIA") else "CBDR"
            cbdr_pct = (
                ((ss.cbdr_body_high - ss.cbdr_body_low) / ss.cbdr_body_low * 100)
                if ss.cbdr_body_low > 0
                else 0
            )
            self._pl(
                sym,
                "st_swp",
                f"\U0001f7e8 SWEEP: BEKLENIYOR{bstr} | {rt}: [{ss.cbdr_body_low:.4f}-{ss.cbdr_body_high:.4f}] | (%{cbdr_pct:.2f}) | {ts}",
                force=True,
            )
            self._log_state.get(sym, {}).pop("st_fvg", None)
            self._log_state.get(sym, {}).pop("st_wck", None)
            await self._check_retrade(sym, bars_15m, current, atr_val, ss)
            return

        rsm = self.rsms[sym]

        # FIX #9: Retrade armed → primary RSM'i atla, ölü döngüyü engelle.
        # 1.entry tamamlandı, retrade kolu açıldı. CBDR sweep hala True olduğu için
        # primary RSM her bar IDLE→SWEEP→reset→"FVG BULUNAMADI" döngüsüne giriyor.
        if ss.retrade_armed:
            await self._check_retrade(sym, bars_15m, current, atr_val, ss)
            return

        if rsm.state_name == "IDLE":
            rsm.on_sweep(
                direction=ss.sweep_direction or "bullish",
                level=ss.sweep_level or 0.0,
                bar_index=current.index,
            )

        if rsm.state_name == "SWEEP_DETECTED":
            rsm.on_sweep_confirmed(bars_15m, current)

        if rsm.state_name == "TRIGGER_READY":
            tfvg = rsm.trigger_fvg
            self._pl(
                sym,
                "st_fvg",
                f"\U0001f7e9 FVG_SCAN | MIN_SIZE: {min_fvg} | \u2705 FVG HAZIR",
                force=True,
            )
            self._pl(
                sym,
                "st_wck",
                f"\u23f3 WICK_REJECTION | FVG:[{tfvg.bottom:.2f}-{tfvg.top:.2f}] | BODY_SAFE | CLOSE: {current.close:.2f} | \u27a1\ufe0f ENTRY BEKLENIYOR",
                force=True,
            )
        elif rsm.state_name == "SWEEP_DETECTED":
            self._pl(
                sym,
                "st_fvg",
                f"\U0001f7e8 FVG_SCAN | MIN_SIZE: {min_fvg} | FVG ARANIYOR...",
                force=True,
            )
            self._log_state.get(sym, {}).pop("st_wck", None)
        else:
            self._pl(
                sym,
                "st_fvg",
                f"\U0001f7e8 FVG_SCAN | MIN_SIZE: {min_fvg} | FVG BULUNAMADI",
                force=True,
            )
            self._log_state.get(sym, {}).pop("st_wck", None)

        if rsm.can_trigger():
            # Bias filter (analyzer.py ile ayni)
            if rsm.direction == "bullish" and ss.daily_bias == DailyBias.BEARISH:
                log.info("[SKIP] %s bullish trigger — bias BEARISH, atlandi", sym)
                rsm.reset()
                return
            if rsm.direction == "bearish" and ss.daily_bias == DailyBias.BULLISH:
                log.info("[SKIP] %s bearish trigger — bias BULLISH, atlandi", sym)
                rsm.reset()
                return
            if ss.daily_bias == DailyBias.NEUTRAL:
                log.info("[SKIP] %s trigger — bias NEUTRAL, atlandi", sym)
                rsm.reset()
                return

            # Session filter (analyzer.py: NEWYORK + LONDON)
            phase = detect_phase_from_timestamp(current.timestamp)
            if phase not in (SessionPhase.NEWYORK, SessionPhase.LONDON):
                log.info("[SKIP] %s trigger — session %s, atlandi", sym, phase)
                rsm.reset()
                return

            await self._try_entry(
                sym,
                current,
                atr_val,
                rsm,
                ss,
                rsm.direction,
                sl_atr,
                tp_rr,
                fvg_buf,
                min_fvg,
                is_retrade=False,
            )

        await self._check_retrade(sym, bars_15m, current, atr_val, ss)

    # ── Retrade: trailing sweep + FVG + 2. entry (analyzer.py #8) ──

    async def _check_retrade(
        self,
        sym: str,
        bars_15m: list[Bar],
        current: Bar,
        atr_val: float,
        ss: SessionState,
    ):
        cfg = self.cfgs[sym]
        if not ss.retrade_armed:
            return
        if ss.trades_today != 1:
            log.info(
                "[SKIP] %s retrade — trades_today=%d (beklenen=1)", sym, ss.trades_today
            )
            return
        if sym in self.active_trades:
            log.info("[SKIP] %s retrade — aktif trade var, beklemede", sym)
            return

        WINDOW_15M = 500
        scan_bar = current.index
        sweep_bar_idx = None
        sweep_found = False
        lookback = min(5, scan_bar)
        for check_idx in range(max(0, scan_bar - 4), scan_bar + 1):
            if check_idx < 0 or check_idx >= len(bars_15m):
                continue
            cb = bars_15m[check_idx]
            if check_idx - lookback < 0:
                continue
            recent_bars = bars_15m[check_idx - lookback : check_idx]

            if ss.retrade_side == "short":
                recent_high = max(b.high for b in recent_bars)
                if cb.high > recent_high and cb.close < recent_high:
                    sweep_found = True
                    sweep_bar_idx = check_idx
                    break
            else:
                recent_low = min(b.low for b in recent_bars)
                if cb.low < recent_low and cb.close > recent_low:
                    sweep_found = True
                    sweep_bar_idx = check_idx
                    break

        if not sweep_found:
            log.info("[SKIP] %s retrade — sweep bulunamadi (%s)", sym, ss.retrade_side)
            return

        self._pl(
            sym,
            "rt_sweep",
            f"\U0001f7e9 RETRADE SWEEP | {ss.retrade_side.upper()} yonunde sweep bulundu bar={sweep_bar_idx}",
        )

        rsm_r = self.rsms_retrade[sym]
        sweep_dir = "bearish" if ss.retrade_side == "short" else "bullish"

        if rsm_r.state_name == "IDLE":
            rsm_r.on_sweep(
                direction=sweep_dir, level=0.0, bar_index=bars_15m[sweep_bar_idx].index
            )

        if rsm_r.state_name == "SWEEP_DETECTED":
            sweep_bar = bars_15m[sweep_bar_idx]
            sweep_chunk = (
                bars_15m[max(0, sweep_bar_idx - WINDOW_15M) : sweep_bar_idx + 1]
                if sweep_bar_idx >= WINDOW_15M
                else bars_15m
            )
            rsm_r.on_sweep_confirmed(sweep_chunk, sweep_bar)

        if rsm_r.can_trigger():
            # FIX #6a: Session filtresi — analyzer.py ile aynı (sadece LONDON+NEWYORK).
            phase = detect_phase_from_timestamp(current.timestamp)
            if phase not in (SessionPhase.NEWYORK, SessionPhase.LONDON):
                log.info("[SKIP] %s retrade trigger — session %s, atlandi", sym, phase)
                rsm_r.reset()
                return

            # FIX #6b: Sweep, primary entry barından sonra oluşmuş olmalı.
            # retrade_entry_bar kaydedildi ama hiç kontrol edilmiyordu;
            # primary trade'den önceki sweep'e denk düşebiliyordu.
            if sweep_bar_idx <= (ss.retrade_entry_bar or 0):
                log.info(
                    "[RETRADE] %s sweep (bar=%d) primary entry barından (bar=%d) önce — atlandı",
                    sym,
                    sweep_bar_idx,
                    ss.retrade_entry_bar or 0,
                )
                rsm_r.reset()
                return

            await self._try_entry(
                sym,
                current,
                atr_val,
                rsm_r,
                ss,
                sweep_dir,
                cfg["SL_ATR_MULT"],
                cfg["TP_RR"],
                cfg["FVG_BUFFER_MULT"],
                cfg["MIN_FVG_SIZE"],
                is_retrade=True,
            )
            # FIX #6c: rsm_r.reset() eksikti — _try_entry başarısız olsa bile
            # retrade_armed False yapılıyordu ama rsm_r TRIGGER_READY'de kalıyordu.
            # _try_entry içinde reset() çağrılıyor ama is_retrade=True durumunda
            # erken return'lerde çağrılmıyor; güvenlik için burada da sıfırlıyoruz.
            if sym not in self.active_trades:
                ss.retrade_armed = False
                clear_retrade_arm(sym)
            rsm_r.reset()

    # ── 1m: Trailing + Exit (hibrit izleme) ──

    async def _on_1m_close(self, sym: str, bars_1m: list[Bar]):
        trade = self.active_trades.get(sym)
        if not trade:
            return

        cfg = self.cfgs[sym]
        fvg_buf = cfg["FVG_BUFFER_MULT"]
        min_fvg = cfg["MIN_FVG_SIZE"]
        current = bars_1m[-1]

        # FVG Trailing (15m data, 1m kapanisinda kontrol)
        bars_15m = self.hub.get_bars(sym, "15m")
        if bars_15m and len(bars_15m) > 1:
            chunk = bars_15m[:-1] if len(bars_15m) > 1 else bars_15m
            fvgs = detect_fvgs(
                chunk,
                lookback=min(50, len(chunk)),
                timeframe="15m",
                min_fvg_size=min_fvg,
            )

            # Analyzer ile birebir ayni buffer formulu
            buffer = abs(trade["initial_sl"] - trade["entry_price"]) * fvg_buf

            # FIX #4: Döngüde her FVG için ayrı API çağrısı yerine,
            # tüm FVG'ler arasından en iyi SL'yi seç, sonra tek _update_orders çağır.
            # Analyzer.py davranışıyla örtüşür (ilk geçerli FVG'de durur).
            trailing_updated = False
            for fvg in fvgs:
                if trade["side"] == "long" and fvg.direction != "bullish":
                    continue
                if trade["side"] == "short" and fvg.direction != "bearish":
                    continue
                if fvg.filled or fvg.invalidated:
                    continue

                if trade["side"] == "long":
                    new_sl = fvg.bottom - buffer
                    if new_sl > trade["sl"]:
                        min_move = trade["risk_pts"] * 0.2
                        if (new_sl - trade["sl"]) <= min_move:
                            continue
                        sl_diff = new_sl - trade["sl"]
                        trade["sl"] = new_sl
                        trade["tp"] = trade["tp"] + sl_diff
                        trade["trailing_count"] += 1
                        log.info(
                            "[TRAIL] %s trail#%d sl=%.2f tp=%.2f",
                            sym,
                            trade["trailing_count"],
                            trade["sl"],
                            trade["tp"],
                        )
                        trailing_updated = True
                else:
                    new_sl = fvg.top + buffer
                    if new_sl < trade["sl"]:
                        min_move = trade["risk_pts"] * 0.2
                        if (trade["sl"] - new_sl) <= min_move:
                            continue
                        sl_diff = trade["sl"] - new_sl
                        trade["sl"] = new_sl
                        trade["tp"] = trade["tp"] - sl_diff
                        trade["trailing_count"] += 1
                        log.info(
                            "[TRAIL] %s trail#%d sl=%.2f tp=%.2f",
                            sym,
                            trade["trailing_count"],
                            trade["sl"],
                            trade["tp"],
                        )
                        trailing_updated = True

            if trailing_updated:
                success = await self._update_orders(sym, trade)
                if not success:
                    log.warning("[TRAIL] UPDATE FAIL → emergency SL restore")
                    sl_side = "SELL" if trade["side"] == "long" else "BUY"
                    try:
                        await self.rest.place_stop_order(
                            sym, sl_side, trade["qty"], trade["sl"]
                        )
                    except Exception as e:
                        log.critical("[TRAIL] emergency SL restore hatasi: %s", e)
                    trade["sl_fallback"] = True
                    return
                return

        # Exit kontrolu (1m bar bazli)
        if trade["side"] == "long":
            if current.low <= trade["sl"]:
                trade["exit_price"] = trade["sl"]
                trade["exit_bar"] = current.index
                trade["exit_timestamp"] = current.timestamp
                trade["result"] = "SL"
                await self._exit_trade(sym, trade, current, current.timestamp)
                return
            elif current.high >= trade["tp"]:
                trade["exit_price"] = trade["tp"]
                trade["exit_bar"] = current.index
                trade["exit_timestamp"] = current.timestamp
                trade["result"] = "TP"
                await self._exit_trade(sym, trade, current, current.timestamp)
                return
        else:
            if current.high >= trade["sl"]:
                trade["exit_price"] = trade["sl"]
                trade["exit_bar"] = current.index
                trade["exit_timestamp"] = current.timestamp
                trade["result"] = "SL"
                await self._exit_trade(sym, trade, current, current.timestamp)
                return
            elif current.low <= trade["tp"]:
                trade["exit_price"] = trade["tp"]
                trade["exit_bar"] = current.index
                trade["exit_timestamp"] = current.timestamp
                trade["result"] = "TP"
                await self._exit_trade(sym, trade, current, current.timestamp)
                return

    # ── Entry ──

    async def _try_entry(
        self,
        sym,
        current,
        atr_val,
        rsm,
        ss,
        sweep_dir,
        sl_atr,
        tp_rr,
        fvg_buf,
        min_fvg,
        is_retrade=False,
    ):
        if sym in self.active_trades:
            log.info("[SKIP] %s entry — aktif trade var (rsm reset)", sym)
            rsm.reset()
            return

        side = "long" if sweep_dir == "bullish" else "short"
        entry_price = current.close
        risk_pts = atr_val * sl_atr
        trigger_fvg = rsm.trigger_fvg

        if side == "long":
            sl = (
                (trigger_fvg.bottom - (risk_pts * fvg_buf))
                if trigger_fvg
                else (entry_price - risk_pts * 2)
            )
            tp = (
                ss.london_high
                if ss.london_high > entry_price
                else entry_price + risk_pts * tp_rr
            )
        else:
            sl = (
                (trigger_fvg.top + (risk_pts * fvg_buf))
                if trigger_fvg
                else (entry_price + risk_pts * 2)
            )
            tp = (
                ss.london_low
                if ss.london_low < entry_price
                else entry_price - risk_pts * tp_rr
            )

        risk_dist = abs(sl - entry_price)
        min_risk_dist = atr_val * 0.1
        if risk_dist < min_risk_dist:
            log.warning(
                "[ENTRY] %s risk_dist=%.6f < min=%.6f (atr=%.6f) — trade atlandı",
                sym,
                risk_dist,
                min_risk_dist,
                atr_val,
            )
            rsm.reset()
            return

        risk_map = cfg.SYMBOL_RISK_MAP.get(sym, {})
        if is_retrade:
            risk_pct = risk_map.get("retrade", RISK_PER_TRADE)
        else:
            risk_pct = risk_map.get("primary", RISK_PER_TRADE)
        qty = (self._balance * risk_pct) / risk_dist / cfg.LEVERAGE
        if qty <= 0:
            log.warning("[SKIP] %s entry — qty=%.6f <= 0 (rsm reset)", sym, qty)
            rsm.reset()
            return

        self._pl(
            sym,
            "entry",
            f"\U0001f7e8 ENTRY: {side.upper()} | PRICE: {entry_price:.2f} | SL: {sl:.2f} | TP: {tp:.2f} | QTY: {qty:.4f}",
        )
        log.info(
            "[PAPER] %s %s @ %.2f sl=%.2f tp=%.2f qty=%.4f",
            sym,
            side,
            entry_price,
            sl,
            tp,
            qty,
        )

        sl_id = ""
        tp_id = ""
        if cfg.BINANCE_API_KEY and getattr(self, "_live", False):
            mkt_side = "BUY" if side == "long" else "SELL"
            sl_side = "SELL" if side == "long" else "BUY"

            try:
                rounded_qty = await self.rest.apply_amount_precision(sym, qty)
                valid_qty = await self.rest.validate_min_amount(sym, rounded_qty)
                if valid_qty <= 0:
                    self._pl(
                        sym, "order_err", f"\u274c ORDER: qty={qty:.6f} minQty altinda"
                    )
                    log.warning(
                        "[ORDER] %s qty=%.8f minQty altinda, emir atlandi", sym, qty
                    )
                    rsm.reset()
                    return
                else:
                    rounded_sl = await self.rest.apply_price_precision(sym, sl)
                    rounded_tp = await self.rest.apply_price_precision(sym, tp)

                    mkt_resp = await self.rest.place_market_order(
                        sym, mkt_side, valid_qty
                    )
                    mkt_id = mkt_resp.get("orderId") or mkt_resp.get("id") or ""
                    if mkt_id:
                        self._pl(
                            sym,
                            "order_ok",
                            f"\u2705 ORDER: MARKET {mkt_side} OK | ID: {mkt_id}",
                        )
                        log.info(
                            "[ORDER] %s MARKET entry OK orderId=%s qty=%.8f",
                            sym,
                            mkt_id,
                            valid_qty,
                        )

                        sl_resp = await self.rest.place_stop_order(
                            sym, sl_side, valid_qty, rounded_sl
                        )
                        sl_id = (
                            sl_resp.get("algoId")
                            or sl_resp.get("orderId")
                            or sl_resp.get("id")
                            or ""
                        )
                        if sl_id:
                            log.info("[ORDER] %s SL OK algoId=%s", sym, sl_id)
                        else:
                            log.critical(
                                "[ORDER] %s SL BASARISIZ! Acil pozisyon kapatiliyor. resp=%s",
                                sym,
                                sl_resp,
                            )
                            # Acil pozisyon kapatma
                            opp_side = "SELL" if mkt_side == "BUY" else "BUY"
                            try:
                                await self.rest.place_market_order(
                                    sym, opp_side, valid_qty
                                )
                            except Exception as e:
                                log.critical(
                                    "[ORDER] %s acil pozisyon kapatma emri basarisiz: %s",
                                    sym,
                                    e,
                                )
                            rsm.reset()
                            return

                        tp_resp = await self.rest.place_tp_order(
                            sym, sl_side, valid_qty, rounded_tp
                        )
                        tp_id = (
                            tp_resp.get("algoId")
                            or tp_resp.get("orderId")
                            or tp_resp.get("id")
                            or ""
                        )
                        if tp_id:
                            log.info("[ORDER] %s TP OK algoId=%s", sym, tp_id)
                        else:
                            log.warning(
                                "[ORDER] %s TP BASARISIZ! resp=%s", sym, tp_resp
                            )
                    else:
                        # FIX #2: Market emir başarısız olduysa trade kaydedilmemeli.
                        # Eski kod buradan devam edip active_trades'e yazıyordu → hayalet pozisyon.
                        self._pl(
                            sym,
                            "order_err",
                            "\u274c ORDER: MARKET BASARISIZ \u2014 trade iptal",
                        )
                        log.warning(
                            "[ORDER] %s MARKET entry BASARISIZ \u2014 trade kaydedilmedi",
                            sym,
                        )
                        rsm.reset()
                        return
            except Exception as e:
                self._pl(sym, "order_err", f"\u274c ORDER: HATA \u2014 {e}")
                log.exception("[ORDER] %s beklenmeyen hata", sym)
                rsm.reset()
                return

        self.active_trades[sym] = {
            "entry_bar_index": current.index,
            "entry_price": entry_price,
            "sl": sl,
            "tp": tp,
            "qty": qty,
            "side": side,
            "trigger_fvg": trigger_fvg,
            "initial_sl": sl,
            "initial_tp": tp,
            "trailing_count": 0,
            "is_retrade": is_retrade,
            "risk_pts": risk_pts,
            "sl_order_id": sl_id
            if (cfg.BINANCE_API_KEY and getattr(self, "_live", False))
            else "",
            "tp_order_id": tp_id
            if (cfg.BINANCE_API_KEY and getattr(self, "_live", False))
            else "",
        }
        if is_retrade:
            clear_retrade_arm(sym)
        else:
            mark_trade_opened(sym, entry_price)
        ss.trades_today += 1
        rsm.reset()

    async def _update_orders(self, sym: str, trade: dict) -> bool:
        if not cfg.BINANCE_API_KEY or not getattr(self, "_live", False):
            return True
        sl_side = "SELL" if trade["side"] == "long" else "BUY"
        qty = trade.get("qty", trade.get("lot", 0))

        old_sl_id = trade.get("sl_order_id", "")
        old_tp_id = trade.get("tp_order_id", "")

        sl_ok = False
        tp_ok = False

        sl_resp = await self.rest.place_stop_order(
            sym, sl_side, qty, trade["sl"], client_id=f"sl_{sym}_{int(time.time())}"
        )
        sl_id = (
            sl_resp.get("algoId") or sl_resp.get("orderId") or sl_resp.get("id") or ""
        )
        trade["sl_order_id"] = sl_id

        if not sl_id:
            log.warning("[TRAIL] SL reject → eski SL korunuyor")
            return False  # BURADA STOP

        sl_ok = True

        tp_resp = await self.rest.place_tp_order(
            sym, sl_side, qty, trade["tp"], client_id=f"tp_{sym}_{int(time.time())}"
        )
        tp_id = (
            tp_resp.get("algoId") or tp_resp.get("orderId") or tp_resp.get("id") or ""
        )
        trade["tp_order_id"] = tp_id

        if tp_id:
            tp_ok = True
        else:
            log.critical(
                "[ORDER] %s TP BASARISIZ! sl_ok=%s tp_resp=%s — pozisyon SL korumalı ama TP yok",
                sym,
                sl_ok,
                tp_resp,
            )

        # sadece başarılıysa eski emirleri sil
        if sl_ok and old_sl_id:
            try:
                await self.rest.cancel_order(
                    old_sl_id, sym, reason="trail_update", is_algo=True
                )
            except Exception as e:
                log.warning(
                    "[CANCEL] %s eski SL iptal hatasi (id=%s): %s", sym, old_sl_id, e
                )
        if tp_ok and old_tp_id:
            try:
                await self.rest.cancel_order(
                    old_tp_id, sym, reason="trail_update", is_algo=True
                )
            except Exception as e:
                log.warning(
                    "[CANCEL] %s eski TP iptal hatasi (id=%s): %s", sym, old_tp_id, e
                )

        log.info(
            "[ORDER] %s trailing guncellendi sl=%.2f (id=%s) tp=%.2f (id=%s)",
            sym,
            trade["sl"],
            sl_id,
            trade["tp"],
            tp_id,
        )
        return sl_ok and tp_ok

    async def _exit_trade(self, sym, trade, current, exit_timestamp: int):
        diff = (
            (trade["exit_price"] - trade["entry_price"])
            if trade["side"] == "long"
            else (trade["entry_price"] - trade["exit_price"])
        )
        pnl = round(diff * trade["qty"], 2)
        self._balance += pnl
        self._pl(
            sym,
            f"exit_{exit_timestamp}",
            f"\U0001f7e5 EXIT: {trade['result']} | PRICE: {trade['exit_price']:.2f} | PNL: {pnl:+.2f} | BALANCE: {self._balance:.2f} | TRAIL: {trade['trailing_count']}",
        )
        log.info(
            "[PAPER] %s %s exit=%s pnl=%.2f balance=%.2f",
            sym,
            trade["result"],
            trade["exit_price"],
            pnl,
            self._balance,
        )

        # FIX #5: Manuel kapanış emri kaldırıldı.
        # SL/TP emirleri closePosition=True ile kurulduğundan Binance pozisyonu
        # zaten kapattı. Buradan tekrar place_market_order göndermek, sıfır pozisyon
        # üzerine emir atarak ters yönde yeni pozisyon açıyordu.
        # Yapılması gereken: karşı taraftaki bekleyen emri iptal etmek.
        if cfg.BINANCE_API_KEY and getattr(self, "_live", False):
            try:
                remaining_id = (
                    trade.get("tp_order_id")
                    if trade.get("result") == "SL"
                    else trade.get("sl_order_id")
                )
                if remaining_id:
                    try:
                        await self.rest.cancel_order(
                            remaining_id, sym, reason="exit_close", is_algo=True
                        )
                        log.info(
                            "[CANCEL] %s kalan koruma emri iptal edildi (id=%s)",
                            sym,
                            remaining_id,
                        )
                    except Exception as e:
                        log.warning(
                            "[CANCEL] %s kalan emir iptal hatasi (id=%s): %s",
                            sym,
                            remaining_id,
                            e,
                        )

                # Eger tetiklenen yonun Binance emri yoksa (örn: kurtarilmis/sentetik/unprotected pozisyon)
                # pozisyonun acik kalmamasi icin piyasa fiyatindan manuel kapatiyoruz.
                trigger_id = (
                    trade.get("sl_order_id")
                    if trade.get("result") == "SL"
                    else trade.get("tp_order_id")
                )
                if not trigger_id:
                    log.warning(
                        "[CLOSE] %s tetiklenen %s emri Binance ID'si olmadigi icin acil market kapanisi yapiliyor...",
                        sym,
                        trade.get("result"),
                    )
                    mkt_side = "SELL" if trade["side"] == "long" else "BUY"
                    try:
                        await self.rest.place_market_order(
                            sym, mkt_side, trade["qty"], reduce_only=True
                        )
                    except Exception as e:
                        log.warning("[CLOSE] %s acil kapanis emri hatasi: %s", sym, e)
            except Exception as e:
                log.warning("[CLOSE] %s exit temizleme hatasi: %s", sym, e)

        # FIX #2: Retrade arm
        ss = self.states[sym]
        if trade.get("is_retrade", False):
            log.info("[SKIP] %s retrade arm — bu trade zaten retrade", sym)
        elif ss.trades_today not in (0, 1):
            log.info(
                "[SKIP] %s retrade arm — trades_today=%d (beklenen=0 veya 1)",
                sym,
                ss.trades_today,
            )
        elif ss.retrade_armed:
            log.info("[SKIP] %s retrade arm — zaten armed", sym)
        else:
            ss.retrade_side = "short" if trade["side"] == "long" else "long"
            ss.retrade_sweep_level = 0.0
            ss.retrade_entry_bar = trade.get(
                "entry_bar_index", trade.get("entry_bar", 0)
            )
            ss.retrade_armed = True
            save_retrade_arm(sym, ss.retrade_side, ss.retrade_entry_bar)
            self._pl(
                sym,
                "rt_arm",
                f"\U0001f6a9 RETRADE ARMED | ters yon: {ss.retrade_side.upper()}",
            )

        self.trades.append(
            {
                **trade,
                "pnl": pnl,
                "exit_bar": trade["exit_bar"],
                "close_time": exit_timestamp,
            }
        )
        del self.active_trades[sym]
        mark_trade_closed(sym)

    async def on_15m(self, sym: str, bars: list[Bar]):
        if len(bars) < 10:
            return
        await self._on_15m_close(sym, bars)

    async def on_1m(self, sym: str, bars: list[Bar]):
        if len(bars) < 2:
            return
        await self._on_1m_close(sym, bars)

    async def _prefill_bars(self, sym: str, timeframe: str = "15m"):
        # FIX #7: Testnet modunda da mainnet URL kullanılıyordu.
        # Testnet ve mainnet fiyatları farklı olduğundan CBDR warmup yanlış
        # fiyat seviyeleriyle başlıyor, sweep/FVG tespiti sapıyordu.
        url = f"{self.rest_base}/fapi/v1/klines?symbol={sym}&interval={timeframe}&limit=500"
        try:
            loop = asyncio.get_running_loop()
            raw = await loop.run_in_executor(
                None,
                lambda: urllib.request.urlopen(url, timeout=15).read().decode(),
            )
            data = json.loads(raw)
            bars = [
                Bar(
                    index=i,
                    open=float(k[1]),
                    high=float(k[2]),
                    low=float(k[3]),
                    close=float(k[4]),
                    volume=float(k[5]),
                    timestamp=int(k[0]),
                    is_closed=True,
                )
                for i, k in enumerate(data)
            ]
            self.hub.prefill_bars(sym, timeframe, bars)
            log.info("[PREFILL] %s %s: %d bar yuklendi", sym, timeframe, len(bars))
        except Exception as e:
            log.warning("[PREFILL] %s %s REST hatasi: %s", sym, timeframe, e)

    def _warmup_cbdr(self, sym: str):
        bars = self.hub.get_bars(sym, "15m")
        if not bars or len(bars) < 10:
            return
        ss = self.states[sym]
        for bar in bars:
            try:
                dt = datetime.fromtimestamp(bar.timestamp / 1000, tz=UTC)
            except Exception:
                continue
            # FIX #1: Tüm barlar SessionState'e beslenmeli.
            # Eski filtre (sadece 22:00-02:00 CBDR saatleri) London/NY barlarını
            # atlıyordu; london_high/london_low=0 kalıyor ve TP yanlış hesaplanıyordu.
            atr = max(bar.range, bar.close * 0.0001)
            ss.update(dt, bar.open, bar.high, bar.low, bar.close, atr)
        log.info(
            "[WARMUP] %s CBDR body: lock=%s | body=[%.2f-%.2f] | sweep=%s",
            sym,
            ss.cbdr_locked,
            ss.cbdr_body_low,
            ss.cbdr_body_high,
            ss.sweep_confirmed,
        )

    async def _set_leverage(self, symbol: str) -> None:
        """POST /fapi/v1/leverage — sembol için kaldıraç ayarı."""
        if not cfg.BINANCE_API_KEY:
            return
        try:
            resp = await self.rest.post(
                "/fapi/v1/leverage",
                {"symbol": symbol, "leverage": cfg.LEVERAGE},
            )
            effective = resp.get("leverage", cfg.LEVERAGE)
            self._pl(symbol, "leverage", f"⚙️ LEVERAGE: {effective}x set edildi")
            log.info("[LEVERAGE] %s leverage=%dx OK", symbol, effective)
        except Exception as e:
            log.warning("[LEVERAGE] %s leverage set hatasi (devam): %s", symbol, e)

    async def _recover_positions(self):
        if not cfg.BINANCE_API_KEY:
            return
        try:
            positions = await self.rest.get_positions()
            if not positions:
                self._pl("SYSTEM", "recover", "\u2705 API'de acik pozisyon yok")
                return

            self._pl(
                "SYSTEM",
                "recover",
                f"\U0001f504 {len(positions)} pozisyon bulundu, envantere aliniyor...",
            )
            for pos in positions:
                sym = pos["symbol"]
                if sym not in self.symbols:
                    continue
                amt = float(pos.get("positionAmt", 0))
                direction = "long" if amt > 0 else "short"
                entry = float(pos.get("entryPrice", 0))

                open_orders = await self.rest.get_all_orders(sym)
                sl_orders = [
                    o
                    for o in open_orders
                    if self.rest.get_order_type(o)
                    in ("STOP_MARKET", "STOP", "STOP_LIMIT")
                    and (
                        o.get("reduceOnly") in (True, "true", "True")
                        or o.get("closePosition") in (True, "true", "True")
                    )
                ]
                tp_orders = [
                    o
                    for o in open_orders
                    if self.rest.get_order_type(o)
                    in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT")
                    and (
                        o.get("reduceOnly") in (True, "true", "True")
                        or o.get("closePosition") in (True, "true", "True")
                    )
                ]

                if sl_orders and tp_orders:
                    sl_price = self.rest.get_order_price(sl_orders[0])
                    tp_price = self.rest.get_order_price(tp_orders[0])
                    risk_pts = abs(entry - sl_price) / 2
                    sl_id = (
                        sl_orders[0].get("algoId") or sl_orders[0].get("orderId") or ""
                    )
                    tp_id = (
                        tp_orders[0].get("algoId") or tp_orders[0].get("orderId") or ""
                    )
                    self.active_trades[sym] = {
                        "entry_bar_index": 0,
                        "entry_price": entry,
                        "sl": sl_price,
                        "tp": tp_price,
                        "qty": abs(amt),
                        "side": direction,
                        "trigger_fvg": None,
                        "initial_sl": sl_price,
                        "initial_tp": tp_price,
                        "trailing_count": 0,
                        "risk_pts": risk_pts,
                        "is_retrade": False,
                        "sl_order_id": sl_id,
                        "tp_order_id": tp_id,
                    }
                    self._pl(
                        sym,
                        "recover",
                        f"\U0001f512 {direction.upper()} @ {entry:.2f} | SL={sl_price:.2f} TP={tp_price:.2f} | yeni trade engellendi",
                    )
                else:
                    self._pl(
                        sym,
                        "recover",
                        f"\u26a0\ufe0f {direction.upper()} @ {entry:.2f} | SL/TP bulunamadi (pozisyon korumasiz)",
                    )
                    atr_est = entry * 0.0001
                    risk_pts = atr_est * self.cfgs[sym]["SL_ATR_MULT"]
                    if direction == "long":
                        sl = entry - risk_pts * 2
                        tp = entry + risk_pts * self.cfgs[sym]["TP_RR"]
                    else:
                        sl = entry + risk_pts * 2
                        tp = entry - risk_pts * self.cfgs[sym]["TP_RR"]

                    sl_id = ""
                    tp_id = ""
                    if cfg.BINANCE_API_KEY:
                        try:
                            sl_side = "SELL" if direction == "long" else "BUY"
                            rounded_sl = await self.rest.apply_price_precision(sym, sl)
                            rounded_tp = await self.rest.apply_price_precision(sym, tp)

                            sl_resp = await self.rest.place_stop_order(
                                sym, sl_side, abs(amt), rounded_sl
                            )
                            sl_id = (
                                sl_resp.get("algoId")
                                or sl_resp.get("orderId")
                                or sl_resp.get("id")
                                or ""
                            )

                            tp_resp = await self.rest.place_tp_order(
                                sym, sl_side, abs(amt), rounded_tp
                            )
                            tp_id = (
                                tp_resp.get("algoId")
                                or tp_resp.get("orderId")
                                or tp_resp.get("id")
                                or ""
                            )

                            log.info(
                                "[RECOVER] %s icin Binance uzerinde SL/TP emirleri olusturuldu (sl_id=%s, tp_id=%s)",
                                sym,
                                sl_id,
                                tp_id,
                            )
                        except Exception as e:
                            log.warning(
                                "[RECOVER] %s icin Binance koruma emri yerlestirme hatasi: %s",
                                sym,
                                e,
                            )

                    self.active_trades[sym] = {
                        "entry_bar_index": 0,
                        "entry_price": entry,
                        "sl": sl,
                        "tp": tp,
                        "qty": abs(amt),
                        "side": direction,
                        "trigger_fvg": None,
                        "initial_sl": sl,
                        "initial_tp": tp,
                        "trailing_count": 0,
                        "risk_pts": risk_pts,
                        "is_retrade": False,
                        "sl_order_id": sl_id,
                        "tp_order_id": tp_id,
                    }
                    self._pl(
                        sym,
                        "recover",
                        f"\U0001f512 {direction.upper()} @ {entry:.2f} | SL={sl:.2f} (id={sl_id}) TP={tp:.2f} (id={tp_id}) kuruldu",
                    )
        except Exception as e:
            self._pl("SYSTEM", "recover", f"\u274c Pozisyon kurtarma hatasi: {e}")

    # FIX #3: Ghost pozisyon temizliği — trade_state.json'da "open": true
    # görünüp Binance'de kapalı olan pozisyonları temizle.
    async def _reconcile_ghost_positions(self):
        if not cfg.BINANCE_API_KEY:
            return
        from state_manager import dump_state, mark_trade_closed

        try:
            state = dump_state()
        except Exception:
            return

        for sym, s in list(state.items()):
            if sym.startswith("_"):
                continue
            if not s.get("open"):
                continue
            if sym in self.active_trades:
                continue

            log.info(
                "[GHOST] %s state'de open=true ama active_trades'te yok — Binance sorgulaniyor...",
                sym,
            )
            try:
                positions = await self.rest.get_positions()
                pos = next((p for p in positions if p["symbol"] == sym), None)
                if pos and float(pos.get("positionAmt", 0)) != 0:
                    amt = float(pos["positionAmt"])
                    entry = float(pos.get("entryPrice", 0))
                    direction = "long" if amt > 0 else "short"
                    log.info(
                        "[GHOST] %s pozisyon ACIK (amt=%s, entry=%.2f) — SL/TP kontrol ediliyor",
                        sym,
                        amt,
                        entry,
                    )
                    # _recover_positions atlamis olabilir, mevcut emirleri kontrol et
                    open_orders = await self.rest.get_all_orders(sym)
                    has_sl = any(
                        self.rest.get_order_type(o)
                        in ("STOP_MARKET", "STOP", "STOP_LIMIT")
                        for o in open_orders
                    )
                    has_tp = any(
                        self.rest.get_order_type(o)
                        in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT")
                        for o in open_orders
                    )
                    if not has_sl or not has_tp:
                        log.warning(
                            "[GHOST] %s SL/TP eksik (sl=%s tp=%s) — trade hatasi olabilir",
                            sym,
                            has_sl,
                            has_tp,
                        )
                        self._pl(
                            sym,
                            "ghost_missing_sltp",
                            f"\u26a0\ufe0f GHOST: {direction.upper()} @ {entry:.2f} | SL={has_sl} TP={has_tp} eksik",
                        )
                    else:
                        self._pl(
                            sym,
                            "ghost_ok",
                            f"\U0001f512 GHOST: {direction.upper()} @ {entry:.2f} | SL/TP mevcut",
                        )
                else:
                    mark_trade_closed(sym)
                    self.states[sym].trades_today = 0
                    log.info(
                        "[GHOST] %s pozisyon kapali, state temizlendi — trades_today sifirlandi",
                        sym,
                    )
                    self._pl(
                        sym,
                        "ghost_cleaned",
                        f"\U0001f4a4 GHOST: {sym} state temizlendi, trades_today=0",
                    )
            except Exception as e:
                log.warning("[GHOST] %s sorgu hatasi: %s", sym, e)

    async def run(self):
        for sym in self.symbols:
            self.hub.register_callback(sym, "15m", lambda b, s=sym: self.on_15m(s, b))
            self.hub.register_callback(sym, "1m", lambda b, s=sym: self.on_1m(s, b))

        net = "TESTNET" if self.testnet else "MAINNET"
        self._pl(
            "SYSTEM",
            "start",
            f"\U0001f680 PaperTrader baslatiliyor | Semboller: {self.symbols} | {net}",
        )

        if cfg.BINANCE_API_KEY:
            try:
                bal = await self.rest.get_balance()
                if bal > 0:
                    self._balance = bal
                    self._pl(
                        "SYSTEM",
                        "balance",
                        f"\U0001f4b0 BALANCE: {self._balance:.2f} USDT ({net})",
                    )
                else:
                    self._pl(
                        "SYSTEM",
                        "balance",
                        f"\u26a0\ufe0f BALANCE: 0 USDT, varsayilan {INITIAL_CAPITAL:.2f} kullaniliyor",
                    )
            except Exception as e:
                self._pl(
                    "SYSTEM",
                    "balance",
                    f"\u26a0\ufe0f BALANCE: alinamadi ({e}), varsayilan {INITIAL_CAPITAL:.2f}",
                )
        else:
            self._pl(
                "SYSTEM",
                "balance",
                f"\U0001f4b0 BALANCE: varsayilan {INITIAL_CAPITAL:.2f} USDT (API key yok)",
            )

        self._live = True

        # Leverage: her sembol için config'deki değeri set et
        if cfg.BINANCE_API_KEY:
            await asyncio.gather(
                *[self._set_leverage(sym) for sym in self.symbols],
                return_exceptions=True,
            )

        await self._recover_positions()
        reconcile_from_active(self.active_trades)

        # FIX #8: Restart sonrası trades_today senkronizasyonu.
        # ÖNCE disk'teki count'u oku, SONRA ghost recovery sıfırlasın.
        for sym in self.symbols:
            try:
                count = get_trade_count_today(sym)
                if count > 0:
                    self.states[sym].trades_today = count
                    log.info(
                        "[SYNC] %s trades_today disk'ten senkronize edildi: %d",
                        sym,
                        count,
                    )
            except Exception as e:
                log.warning("[SYNC] %s trades_today sync hatasi: %s", sym, e)

        # FIX #3: Ghost pozisyon temizliği — trade_state.json'da "open": true
        # olup Binance'de kapalı olan pozisyonları temizle.
        # FIX #8'den SONRA çalışmalı (trades_today sıfırlaması FIX #8'i ezmesin).
        await self._reconcile_ghost_positions()

        # FIX #10: Retrade state'ini diskten geri yükle (restart-proof).
        for sym in self.symbols:
            try:
                ra = load_retrade_arm(sym)
                if ra:
                    self.states[sym].retrade_armed = True
                    self.states[sym].retrade_side = ra["side"]
                    self.states[sym].retrade_entry_bar = ra["entry_bar"]
                    log.info(
                        "[RETRADE] %s diskten restore: side=%s bar=%d",
                        sym,
                        ra["side"],
                        ra["entry_bar"],
                    )
            except Exception as e:
                log.warning("[RETRADE] %s restore hatasi: %s", sym, e)

        # User Data Stream (WS Zirhi — REST polling yok)
        if cfg.BINANCE_API_KEY:
            try:
                listen_key = await self.rest.get_listen_key()
                if listen_key:
                    self.hub.set_user_data_listen_key(listen_key)
                    self._register_user_data_callbacks()
                    asyncio.create_task(self.hub._listen_key_refresh_loop(self.rest))
                    log.info("[USER_DATA] Listen key aktif: %s...", listen_key[:10])
            except Exception as e:
                log.warning("[USER_DATA] Listen key basarisiz (devam): %s", e)

        # Gecmis barlari yukle (15m + 1m)
        tasks = []
        for sym in self.symbols:
            tasks.append(self._prefill_bars(sym, "15m"))
            tasks.append(self._prefill_bars(sym, "1m"))
        await asyncio.gather(*tasks)

        for sym in self.symbols:
            self._warmup_cbdr(sym)

        for sym in self.symbols:
            bars = self.hub.get_bars(sym, "15m")
            if bars and len(bars) >= 10:
                await self.on_15m(sym, bars)
                log.info("[INIT] %s ilk analiz tamam (%d bar)", sym, len(bars))

        log.info("Gecmis barlar yuklendi, WS baslatiliyor...")
        await self.hub.run()

    # ─────────────────────────────────────────────────────────────────
    # User Data Stream callback'leri (WS Zirhi)
    # ─────────────────────────────────────────────────────────────────

    def _register_user_data_callbacks(self) -> None:
        @self.hub.on_user_data("ORDER_TRADE_UPDATE")
        async def on_order_update(msg: dict) -> None:
            od = msg.get("o", {})
            sym = od.get("s", "")
            status = od.get("X", "")
            oid = str(od.get("c", "") or od.get("i", ""))
            log.info("[WS-ORDER] %s status=%s id=%s", sym, status, oid)

            # FIX #1: FILLED/TRIGGERED → pozisyon kapanma confirmasyonu
            if status in ("FILLED", "TRIGGERED"):
                side = od.get("S", "")
                qty = float(od.get("l", 0))
                price = float(od.get("L", 0))
                cum_qty = float(od.get("z", 0))
                log.info(
                    "[WS-FILLED] %s side=%s qty=%s price=%s cum_qty=%s",
                    sym,
                    side,
                    qty,
                    price,
                    cum_qty,
                )
                trade = self.active_trades.get(sym)
                if trade:
                    s_id = str(trade.get("sl_order_id", ""))
                    t_id = str(trade.get("tp_order_id", ""))
                    if oid in (s_id, t_id):
                        self._pl(
                            sym,
                            "filled_confirm",
                            f"\u2705 BINANCE CONFIRMED: pozisyon kapatildi @ {price}",
                        )
                # FIX #2: WS confirm → retrade arm promotion
                ss = self.states.get(sym)
                if ss and getattr(ss, "pending_retrade_arm", False):
                    ss.retrade_armed = True
                    ss.pending_retrade_arm = False
                    self._pl(
                        sym,
                        "rt_arm_confirm",
                        f"\U0001f6a9 RETRADE ARMED (WS FILLED confirm) | ters yon: {ss.retrade_side.upper()}",
                    )
                    log.info(
                        "[RETRADE] %s retrade_armed=True (WS FILLED confirm) side=%s",
                        sym,
                        ss.retrade_side,
                    )
                return

            if status not in ("CANCELED", "EXPIRED"):
                return
            trade = self.active_trades.get(sym)
            if not trade:
                return
            s_id = str(trade.get("sl_order_id", ""))
            t_id = str(trade.get("tp_order_id", ""))
            if oid not in (s_id, t_id):
                return
            label = "SL" if oid == s_id else "TP"
            log.warning(
                "[WS-REPAIR] %s %s emri silindi \u2014 onariliyor...", sym, label
            )
            try:
                await self._repair_protection(
                    sym, trade, has_sl=(oid != s_id), has_tp=(oid != t_id)
                )
            except Exception as e:
                log.critical("[WS-REPAIR] %s onarim hatasi: %s", sym, e)

        @self.hub.on_user_data("ACCOUNT_UPDATE")
        async def on_account_update(msg: dict) -> None:
            ud = msg.get("a", {})
            for bal in ud.get("B", []):
                if bal.get("a") in ("USDT", "FDUSD", "USDC"):
                    self._balance = float(bal.get("wb", self._balance))

    async def _repair_protection(
        self, sym: str, trade: dict, has_sl: bool, has_tp: bool
    ) -> None:
        if not has_sl and trade.get("sl"):
            sl_side = "SELL" if trade["side"] == "long" else "BUY"
            sl_resp = await self.rest.place_stop_order(
                sym, sl_side, trade["qty"], trade["sl"]
            )
            trade["sl_order_id"] = sl_resp.get("algoId") or sl_resp.get("orderId") or ""
            log.info(
                "[REPAIR] %s SL yeniden kuruldu: %.2f (id=%s)",
                sym,
                trade["sl"],
                trade["sl_order_id"],
            )
        if not has_tp and trade.get("tp"):
            tp_side = "SELL" if trade["side"] == "long" else "BUY"
            tp_resp = await self.rest.place_tp_order(
                sym, tp_side, trade["qty"], trade["tp"]
            )
            trade["tp_order_id"] = tp_resp.get("algoId") or tp_resp.get("orderId") or ""
            log.info(
                "[REPAIR] %s TP yeniden kuruldu: %.2f (id=%s)",
                sym,
                trade["tp"],
                trade["tp_order_id"],
            )
        log.info("[REPAIR] %s onarim tamam", sym)


if __name__ == "__main__":
    bot = PaperTrader(sys.argv[1:] if len(sys.argv) > 1 else None)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("Kullanici tarafindan durduruldu.")
        bot.hub.stop()
        _close_ohlc_writers()
