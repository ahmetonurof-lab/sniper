"""
bot.py — sniper paper trade orchestrator
CBDR -> Sweep -> FVG Wick Rejection -> Entry -> Trailing (1m) -> Exit (1m)
Backtest (analyzer.py) ile birebir ayni performans.
"""

from __future__ import annotations

import asyncio
import logging
import logging.handlers
import json
import math
import os
import sys
from collections import deque
from datetime import UTC, datetime, timezone, timedelta

import config as cfg
from bot_binance import BinanceRESTClient
from bot_infra import _close_ohlc_writers, _RateLimiter
from indicators import calculate_true_range, update_atr
from models import ActiveTrade, Bar, PendingLock, Result
from retrace_state import RetraceStateMachine
from session import SessionState
from risk_manager import RiskManager
from session_router import (
    should_trade,
    get_cbdr_multiplier,
    get_session_hours,
    is_high_quality_fvg,
    is_fvg_valid,
)
from state_manager import (
    mark_trade_opened,
    mark_trade_closed,
    reconcile_from_active,
    get_trade_count_today,
    mark_sweep_consumed,
)
from state_writer import write_state
from snapshot.snapshot import capture_snapshot
from event_log import cleanup_old_event_logs, log_event
from trading import (
    SignalEngine,
    EntryManager,
    TrailingManager,
    OrderManager,
    RecoveryManager,
    ConsoleReporter,
    UserDataHandler,
)
from websocket import BinanceWSHub

TR_TZ = timezone(timedelta(hours=3))

_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_OUTPUT_DIR = os.path.join(_SCRIPT_DIR, "..", "output")
os.makedirs(_OUTPUT_DIR, exist_ok=True)

_log_file = os.path.join(_OUTPUT_DIR, "paper_trade.log")
_FVG_STATE_FILE = os.path.join(_OUTPUT_DIR, "active_fvg.json")


def _save_fvg_state(sym: str, fvg_data: dict) -> None:
    """FVG verisini diske yaz (recovery'de kaybolmasin diye)."""
    try:
        data = {}
        if os.path.exists(_FVG_STATE_FILE):
            with open(_FVG_STATE_FILE, "r", encoding="utf-8") as f:
                data = json.load(f)
        data[sym] = fvg_data
        with open(_FVG_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
    except Exception:
        pass


def _load_fvg_state(sym: str) -> dict:
    """Diskten FVG verisini oku."""
    try:
        if os.path.exists(_FVG_STATE_FILE):
            with open(_FVG_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get(sym, {})
    except Exception:
        pass
    return {}


def _setup_logging() -> logging.Logger:
    """Logger yapılandırması: TR saat dilimi, UTF-8, dosya, günlük rotate.

    Sadece main() içinde çağrılır — modül import'unda tetiklenmez,
    böylece test'ler production log'una yazmaz.
    """
    logging.Formatter.converter = staticmethod(
        lambda ts: datetime.fromtimestamp(ts, TR_TZ).timetuple()
    )

    # Eski log'u arşivle
    if os.path.exists(_log_file):
        import shutil

        archive_name = (
            _log_file + "." + datetime.now(TR_TZ).strftime("%Y%m%d_%H%M%S") + ".bak"
        )
        try:
            shutil.copy2(_log_file, archive_name)
            os.remove(_log_file)
        except Exception:
            pass

    root = logging.getLogger()
    for h in root.handlers[:]:
        root.removeHandler(h)
    root.setLevel(logging.INFO)

    handler = logging.handlers.TimedRotatingFileHandler(
        _log_file,
        when="midnight",
        interval=1,
        backupCount=14,
        encoding="utf-8",
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s [%(levelname)s] %(name)s \u2014 %(message)s")
    )
    root.addHandler(handler)

    _log = logging.getLogger("sniper.paper")
    _log.setLevel(logging.INFO)

    try:
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(encoding="utf-8")
        if hasattr(sys.stderr, "reconfigure"):
            sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        _log.debug(
            "stdout/stderr reconfigure atlandi (encoding zaten UTF-8 veya non-TTY)"
        )

    return _log


log = logging.getLogger("sniper.paper")

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
        self.signal_engines: dict[str, SignalEngine] = {}
        self.entry_manager: EntryManager | None = None
        self.cfgs: dict[str, dict] = {}
        self.active_trades: dict[str, ActiveTrade] = {}
        self.trades: deque[dict] = deque(maxlen=1000)
        self.reporter = ConsoleReporter()
        self.risk_mgr = RiskManager(
            state_file=os.path.join(_OUTPUT_DIR, "risk_state.json"),
            initial_equity=INITIAL_CAPITAL,
        )
        self._live = False
        self._wallet_balance: float = INITIAL_CAPITAL  # WS'den gelen wb (görüntüleme)
        self._available_balance: float = (
            INITIAL_CAPITAL  # REST availableBalance (position sizing)
        )

        api_key = cfg.BINANCE_API_KEY or ""
        api_secret = cfg.BINANCE_API_SECRET or ""
        self.rest = BinanceRESTClient(
            api_key=api_key,
            api_secret=api_secret,
            base_url=self.rest_base,
            rate_limiter=_RateLimiter(1200),
            semaphore=asyncio.Semaphore(5),
        )
        self.entry_manager = EntryManager(
            rest_client=self.rest,
            is_live=bool(cfg.BINANCE_API_KEY),
        )
        self.order_manager = OrderManager(
            rest_client=self.rest,
            is_live=bool(cfg.BINANCE_API_KEY),
        )
        # ── Gerçek Wilder's ATR rolling state (sembol bazlı) ──
        # TANIM: RecoveryManager'dan ÖNCE gelmeli (atr_state parametresi)
        self._atr_state: dict[str, float] = {}
        self._atr_prev_close: dict[str, float] = {}
        self._orphan_check_counter = 0
        self.recovery_manager = RecoveryManager(
            rest_client=self.rest,
            symbols=self.symbols,
            cfgs=self.cfgs,
            states=self.states,
            active_trades=self.active_trades,
            pl_callback=self._pl,
            order_manager=self.order_manager,
            atr_state=self._atr_state,
        )

        for sym in self.symbols:
            self.cfgs[sym] = {
                "SL_ATR_MULT": cfg.SL_ATR_MULT,
                "TP_RR": cfg.TP_RR,
                "FVG_BUFFER_MULT": cfg.FVG_BUFFER_MULT,
            }
            self.states[sym] = SessionState(
                start_hour=get_session_hours(sym)["start"],
                end_hour=get_session_hours(sym)["end"],
            )
            self.rsms[sym] = RetraceStateMachine(max_wick_ratio=cfg.FVG_WICK_RATIO_MAX)
            self.signal_engines[sym] = SignalEngine(self.rsms[sym])

    def _pl(self, sym: str, key: str, msg: str, force: bool = False):
        """ConsoleReporter'a delegate et. Imza birebir aynı."""
        self.reporter.emit(sym, key, msg, force)

    def _session_label(self, hour: int) -> str:
        if hour >= 22 or hour < 2:
            return "ASIA"
        elif 2 <= hour < 13:
            return "LONDON"
        return "NEWYORK"

    def _load_history(self):
        trades_file = os.path.join(_OUTPUT_DIR, "trades_history.jsonl")
        if not os.path.exists(trades_file):
            return
        try:
            count = 0
            with open(trades_file, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if line:
                        self.trades.append(json.loads(line))
                        count += 1
            log.info("[HISTORY] %d trade gecmisten yuklendi", count)
        except Exception as e:
            log.warning("[HISTORY] yukleme hatasi (devam): %s", e)

    # ── 15m: Sinyal kurulumu (CBDR, Sweep, FVG, Entry, Retrade) ──

    async def _on_15m_close(self, sym: str, bars_15m: list[Bar]):
        sym_cfg = self.cfgs[sym]
        sl_atr = sym_cfg["SL_ATR_MULT"]
        tp_rr = sym_cfg["TP_RR"]
        fvg_buf = sym_cfg["FVG_BUFFER_MULT"]

        current = bars_15m[-1]

        # ── Gerçek Wilder's ATR güncelle (her 15m kapanışında) ──
        prev_close = self._atr_prev_close.get(sym, current.open)
        tr = calculate_true_range(current, prev_close)
        prev_atr = self._atr_state.get(sym)
        atr_val = update_atr(prev_atr if prev_atr and prev_atr > 0 else None, tr)
        self._atr_state[sym] = atr_val
        self._atr_prev_close[sym] = current.close

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
            self.reporter.display_active_position(
                sym, self.active_trades[sym], hour, dt.minute
            )
            return

        if session == "ASIA":
            self._pl(
                sym,
                "st_ses",
                "\U0001f7e5 SESSION: ASIA | 22:00-02:00 UTC | trading kapali",
                force=True,
            )
            return

        # ── Session/CBDR status display → ConsoleReporter (Faz 6.2) ──
        self.reporter.display_session_status(sym, session, hour, dt.minute, ss)

        if not ss.cbdr_locked:
            log.info("[SKIP] %s CBDR henuz kilitlenmedi — sinyal taranmadi", sym)
            return

        # ── Sweep status display → ConsoleReporter (Faz 6.2) ──
        sweep_status = self.reporter.display_sweep_status(sym, ss, hour, dt.minute)
        if sweep_status in ("dead", "waiting"):
            return
        # "detected": devam

        rsm = self.rsms[sym]
        engine = self.signal_engines[sym]

        # ── Blok 8: RSM state progression → SignalEngine ──
        engine.progress_rsm(bars_15m, current, ss, atr_val)

        # ── Blok 9: FVG/Wick durum yazdırma → ConsoleReporter (Faz 6.2) ──
        self.reporter.display_fvg_status(
            sym, rsm, max(atr_val * cfg.FVG_MIN_SIZE_ATR_MULT, 1e-8), current.close
        )

        # ── Blok 10: Trigger check + filtreler → SignalEngine ──
        result = engine.evaluate_trigger(current, ss)

        if result.decision == "TRIGGER":
            # ── Dinamik FVG kalite filtresi (ATR bazli) ──
            tf = rsm.trigger_fvg
            if tf is not None:
                if not is_high_quality_fvg(tf.top - tf.bottom, atr_val, sym):
                    rel = (tf.top - tf.bottom) / atr_val if atr_val > 1e-8 else 0
                    threshold = cfg.FVG_SIZE_MAP.get(sym, cfg.MIN_REL_FVG_THRESHOLD)
                    log.info(
                        "[FVG-FILTER] %s rel_fvg=%.2f < %.2f (gurultu, iptal)",
                        sym,
                        rel,
                        threshold,
                    )
                    rsm.reset()
                    return
                if not is_fvg_valid(tf.bar_index, current.index):
                    log.info(
                        "[FVG-FILTER] %s FVG %d bar once olusmus, expiry=%d (iptal)",
                        sym,
                        current.index - tf.real_index,
                        cfg.GLOBAL_FVG_EXPIRY_BARS,
                    )
                    rsm.reset()
                    return

            # ── Session Router filtresi ──
            cbdr_w = (
                ((ss.cbdr_body_high - ss.cbdr_body_low) / ss.cbdr_body_low * 100)
                if ss.cbdr_body_low > 0 and not math.isinf(ss.cbdr_body_low)
                else None
            )
            allowed, reason = should_trade(sym, cbdr_width_pct=cbdr_w)
            if not allowed:
                log.info("[ROUTER] %s trade reddedildi: %s", sym, reason)
                rsm.reset()
                return

            await self._try_entry(
                sym,
                current,
                atr_val,
                rsm,
                ss,
                result.direction,
                sl_atr,
                tp_rr,
                fvg_buf,
                max(atr_val * cfg.FVG_MIN_SIZE_ATR_MULT, 1e-8),
            )
        elif result.decision == "SKIP":
            # Filtre reddetti → rsm zaten resetlendi, erken dönüş
            return

        # UPNL hesapla — dashboard için (sadece bu sembolün trade'i)
        trade = self.active_trades.get(sym)
        if trade:
            trade.upnl = (
                (current.close - trade.entry_price) * trade.qty
                if trade.side == "long"
                else (trade.entry_price - current.close) * trade.qty
            )

        write_state(
            self.states,
            self.active_trades,
            self._available_balance,
            self._wallet_balance,
            self.symbols,
        )

    # ── 1m: Trailing + Exit (hibrit izleme) ──

    async def _on_1m_close(self, sym: str, bars_1m: list[Bar]):
        trade = self.active_trades.get(sym)
        if not trade:
            return

        current = bars_1m[-1]
        # 1m'de ATR güncellenmez — son 15m ATR'si okunur
        atr_val = self._atr_state.get(
            sym, max(current.range, current.close * cfg.DEFAULT_ATR_FALLBACK_PCT)
        )
        min_fvg = max(atr_val * cfg.FVG_MIN_SIZE_ATR_MULT, 1e-8)

        self._orphan_check_counter += 1
        if self._orphan_check_counter % 5 == 0:
            await self.recovery_manager.reconcile_orphan_orders()

        # ── FVG Trailing → TrailingManager (ATR bazlı buffer) ──
        bars_15m = self.hub.get_bars(sym, "15m")
        if bars_15m:
            trail_result = TrailingManager.evaluate_trail(
                bars_15m,
                trade,
                atr_val,
                min_fvg,
            )

            if trail_result.updated:
                await self.order_manager.update_trail_orders(
                    sym,
                    trade,
                    trail_result.new_sl,
                    trail_result.new_tp,
                    trail_result.trail_count,
                )

            elif trail_result.exit_now:
                log.info("[TRAIL] %s trailing FVG kirildi -> aninda market close", sym)
                trade["exit_price"] = current.close
                trade["exit_bar"] = current.index
                trade["exit_timestamp"] = current.timestamp
                trade["result"] = "TRAIL_CLOSE"
                await self._exit_trade(sym, trade, current.timestamp)
                return

        # ── Exit kontrolü → TrailingManager ──
        exit_decision = TrailingManager.check_exit(current, trade)
        if exit_decision.triggered:
            trade["exit_price"] = exit_decision.exit_price
            trade["exit_bar"] = current.index
            trade["exit_timestamp"] = current.timestamp
            trade["result"] = exit_decision.result
            await self._exit_trade(sym, trade, current.timestamp)

        # UPNL + state writer — her 1m bar'da güncellenir
        trade = self.active_trades.get(sym)
        if trade:
            trade.upnl = (
                (current.close - trade.entry_price) * trade.qty
                if trade.side == "long"
                else (trade.entry_price - current.close) * trade.qty
            )
        write_state(
            self.states,
            self.active_trades,
            self._available_balance,
            self._wallet_balance,
            self.symbols,
        )

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
    ):
        if sym in self.active_trades:
            log.info("[SKIP] %s entry — aktif trade var (rsm reset)", sym)
            rsm.reset()
            return

        side = "long" if sweep_dir == "bullish" else "short"
        entry_price = current.close
        risk_pts = atr_val * sl_atr
        fvg = rsm.trigger_fvg

        sl, tp = EntryManager.calculate_sl_tp(
            side=side,
            entry_price=entry_price,
            risk_pts=risk_pts,
            fvg_buf=fvg_buf,
            tp_rr=tp_rr,
            trigger_fvg=rsm.trigger_fvg,
            london_high=ss.london_high,
            london_low=ss.london_low,
        )

        # ── 1. SENKRON VALİDASYONLAR (PENDING KİLİDİNDEN ÖNCE) → EntryManager ──
        risk_dist = abs(sl - entry_price)
        valid, err_msg = EntryManager.validate_risk(risk_dist, atr_val)
        if not valid:
            log.warning("[ENTRY] %s %s — trade atlandı", sym, err_msg)
            rsm.reset()
            return

        # Entry öncesi taze availableBalance (position sizing için)
        if cfg.BINANCE_API_KEY:
            try:
                fresh_bal = await self.rest.get_balance()
                if fresh_bal > 0:
                    self._available_balance = fresh_bal
            except Exception:
                pass

        # ── CBDR Risk Matrisi carpani ──
        cbdr_w = None
        if ss.cbdr_body_low > 0 and not math.isinf(ss.cbdr_body_low):
            cbdr_w = ((ss.cbdr_body_high - ss.cbdr_body_low) / ss.cbdr_body_low) * 100
        cbdr_mult = get_cbdr_multiplier(sym, cbdr_w) if cbdr_w is not None else 1.0
        if cbdr_mult == 0.0:
            log.info(
                "[SKIP] %s CBDR %s Zehirli Bolge (cbdr_mult=0.0)",
                sym,
                f"{cbdr_w:.2f}%" if cbdr_w is not None else "?",
            )
            rsm.reset()
            return

        # ── RiskManager: zaman (EL) + portfoy sagligi (devre kesici) ──
        current_hour = datetime.now(UTC).hour
        is_early_london = 2 <= current_hour < 8
        risk_mgr_mult = self.risk_mgr.get_dynamic_risk_multiplier(
            self._available_balance, is_early_london
        )
        is_defense_mode = self.risk_mgr.is_circuit_broken

        # ── Nihai carpan (Guvenlik Freni) ──
        if is_defense_mode:
            # PORTFOY KANIYOR (DD > %15): Elite CBDR gelse bile riski buyutme
            final_risk_mult = 1.0 * min(cbdr_mult, 1.0)
            log.warning(
                "[DEFENSE] %s DD limitinde! EL ve Elite CBDR iptal. final=%.2fx",
                sym,
                final_risk_mult,
            )
        else:
            # PORTFOY SAGLIKLI: Zaman (EL) x Kurulum (CBDR) carpani
            final_risk_mult = risk_mgr_mult * cbdr_mult

        adjusted_risk_pct = RISK_PER_TRADE * final_risk_mult

        qty = EntryManager.calculate_qty(
            self._available_balance,
            adjusted_risk_pct,
            risk_dist,
            cfg.LEVERAGE,
            entry_price,
        )
        if qty <= 0:
            log.warning("[SKIP] %s entry — qty=%.6f <= 0 (rsm reset)", sym, qty)
            rsm.reset()
            return
        if final_risk_mult != 1.0:
            log.info(
                "[RISK ENGINE] %s | EL=%s | CBDR=%.2f%% (%sx) | FINAL=%.2fx | QTY=%.4f",
                sym,
                is_early_london,
                cbdr_w,
                cbdr_mult,
                final_risk_mult,
                qty,
            )

        with PendingLock(self.active_trades, sym, logger=log) as lock:
            sl_id = ""
            tp_id = ""
            if cfg.BINANCE_API_KEY and getattr(self, "_live", False):
                assert self.entry_manager is not None
                exec_result = await self.entry_manager.execute_live_entry(
                    sym,
                    side,
                    qty,
                    sl,
                    tp,
                    entry_price,
                    balance=self._available_balance,
                    leverage=cfg.LEVERAGE,
                )
                if not exec_result.success:
                    self._pl(sym, "order_err", f"\u274c ORDER: {exec_result.error}")
                    log.warning(
                        "[ORDER] %s %s — trade kaydedilmedi", sym, exec_result.error
                    )
                    rsm.reset()
                    return
                sl_id = exec_result.sl_order_id
                tp_id = exec_result.tp_order_id
                qty = exec_result.qty
                if exec_result.entry_log_msg:
                    self._pl(sym, "entry", exec_result.entry_log_msg)
            else:
                assert self.entry_manager is not None
                paper_result = await self.entry_manager.execute_live_entry(
                    sym, side, qty, sl, tp, entry_price
                )
                if paper_result.entry_log_msg:
                    self._pl(sym, "entry", paper_result.entry_log_msg)

            log.info(
                "[PAPER] %s %s @ %.2f sl=%.2f tp=%.2f qty=%.4f",
                sym,
                side,
                entry_price,
                sl,
                tp,
                qty,
            )

            lock.commit()  # PENDING korunur

        log_event(
            "entry",
            sym,
            side=side,
            entry_price=entry_price,
            sl=sl,
            tp=tp,
            qty=qty,
        )

        # NOTE: lock.commit() ile ActiveTrade ataması arasında await yok —
        # şu an race condition teorik. Eğer ActiveTrade.__init__ asenkron
        # olursa bu window kapatılmalı (PendingLock atomic blok genişletilmeli).
        # ── 3. BAŞARILI KAYIT (PENDING ÜZERİNE YAZ) ──

        self.active_trades[sym] = ActiveTrade(
            symbol=sym,
            side=side,
            entry_price=entry_price,
            entry_bar_index=current.index,
            sl=sl,
            tp=tp,
            qty=qty,
            initial_sl=sl,
            initial_tp=tp,
            risk_pts=risk_pts,
            trailing_count=0,
            trigger_fvg=fvg,
            fvg_top=getattr(fvg, "top", None) if fvg else None,
            fvg_bottom=getattr(fvg, "bottom", None) if fvg else None,
            fvg_direction=getattr(fvg, "direction", None) if fvg else None,
            fvg_bar_index=fvg.bar_index if fvg else -1,
            sweep_level=ss.sweep_level,
            cbdr_high=ss.cbdr_body_high,
            cbdr_low=ss.cbdr_body_low,
            sl_order_id=sl_id
            if (cfg.BINANCE_API_KEY and getattr(self, "_live", False))
            else "",
            tp_order_id=tp_id
            if (cfg.BINANCE_API_KEY and getattr(self, "_live", False))
            else "",
        )

        # FVG verisini diske yaz — recovery'de kaybolmasin
        _save_fvg_state(
            sym,
            {
                "fvg_top": getattr(fvg, "top", None) if fvg else None,
                "fvg_bottom": getattr(fvg, "bottom", None) if fvg else None,
                "fvg_direction": getattr(fvg, "direction", None) if fvg else None,
                "fvg_bar_index": fvg.bar_index if fvg else -1,
            },
        )
        mark_trade_opened(sym, entry_price)
        ss.trades_today += 1
        rsm.reset()

    async def _exit_trade(self, sym, trade, exit_timestamp: int):
        trade = self.active_trades.pop(sym, None)
        if not trade:
            log.warning("[EXIT] %s zaten kapali, ikinci exit engellendi", sym)
            return

        diff = (
            (trade["exit_price"] - trade["entry_price"])
            if trade["side"] == "long"
            else (trade["entry_price"] - trade["exit_price"])
        )
        pnl = round(diff * trade["qty"], 2)
        self._available_balance += pnl
        self.risk_mgr.update_peak(self._available_balance)
        self._pl(
            sym,
            f"exit_{exit_timestamp}",
            f"\U0001f7e5 EXIT: {trade['result']} | PRICE: {trade['exit_price']:.2f} | PNL: {pnl:+.2f} | AVL: {self._available_balance:.2f} | WAL: {self._wallet_balance:.2f} | TRAIL: {trade['trailing_count']}",
        )
        log.info(
            "[PAPER] %s %s exit=%s pnl=%.2f available=%.2f",
            sym,
            trade["result"],
            trade["exit_price"],
            pnl,
            self._available_balance,
        )

        # ── Önce tüm açık emirleri iptal et (SL/TP çakışmasını önle) ──
        if cfg.BINANCE_API_KEY:
            try:
                await self.order_manager.cancel_all_open_orders(sym)
            except Exception as e:
                log.warning(
                    "[EXIT] %s cancel_all_open_orders hatasi (devam): %s", sym, e
                )

        # ── Pozisyon kapatma (reduceOnly market) ──
        if cfg.BINANCE_API_KEY:
            mkt_side = "SELL" if trade["side"] == "long" else "BUY"
            try:
                close_resp = await self.rest.place_market_order(
                    sym, mkt_side, trade["qty"], reduce_only=True
                )
                if close_resp and close_resp.get("orderId"):
                    log_event(
                        "force_close",
                        sym,
                        side=trade["side"],
                        qty=trade["qty"],
                        success=True,
                    )
                    log.info("[EXIT] %s reduceOnly market BASARILI", sym)
                else:
                    log_event(
                        "force_close",
                        sym,
                        side=trade["side"],
                        qty=trade["qty"],
                        success=False,
                    )
                    log.warning(
                        "[EXIT] %s reduceOnly market BASARISIZ — cleanup devam", sym
                    )
            except Exception as e:
                log.warning("[EXIT] %s reduceOnly market HATASI (devam): %s", sym, e)

            # ── Pozisyon doğrulama: 5 deneme, 200ms bekle, positionAmt == 0 ──
            pos_closed = False
            for attempt in range(5):
                await asyncio.sleep(0.2)
                try:
                    positions = await self.rest.get_positions()
                    for p in positions:
                        if p["symbol"] == sym:
                            amt = float(p.get("positionAmt", 0))
                            if abs(amt) < 0.0001:
                                pos_closed = True
                            break
                    else:
                        pos_closed = True
                except Exception:
                    pass
                if pos_closed:
                    break
                log.info(
                    "[EXIT] %s verify attempt %d/5 — pozisyon hala acik",
                    sym,
                    attempt + 2,
                )

            if not pos_closed:
                log.critical(
                    "[CRITICAL] %s pozisyon 5 denemede kapanmadi — manual müdahale gerekli",
                    sym,
                )
                self._pl(
                    sym,
                    f"critical_{sym}",
                    f"\U0001f6a8 CRITICAL: {sym} kapanmadi!",
                    force=True,
                )
                return

        log_event(
            "exit",
            sym,
            side=trade["side"],
            entry_price=trade["entry_price"],
            exit_price=trade["exit_price"],
            qty=trade["qty"],
            pnl=pnl,
            result=trade["result"],
            trailing_count=trade["trailing_count"],
        )
        await self.order_manager.cleanup_on_exit(sym, trade, trade["result"])

        # FVG state dosyasini temizle
        try:
            if os.path.exists(_FVG_STATE_FILE):
                data = json.loads(open(_FVG_STATE_FILE, "r", encoding="utf-8").read())
                data.pop(sym, None)
                open(_FVG_STATE_FILE, "w", encoding="utf-8").write(
                    json.dumps(data, ensure_ascii=False)
                )
        except Exception:
            pass

        try:
            snap = capture_snapshot(sym, trade, pnl, self.states[sym])
            if snap:
                trade["snapshot_file"] = snap
        except Exception:
            log.warning("[SNAPSHOT] %s snapshot alinamadi", sym)

        record = {
            **trade,
            "sym": sym,
            "pnl": pnl,
            "exit_bar": trade.get("exit_bar", 0),
            "close_time": exit_timestamp,
        }
        self.trades.append(record)
        try:
            trades_file = os.path.join(_OUTPUT_DIR, "trades_history.jsonl")
            with open(trades_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False, default=str) + "\n")
        except Exception:
            log.warning("[TRADES] %s jsonl yazma hatasi", sym)
        mark_trade_closed(sym)

        # ── Sweep consumption mark — aynı level sweep tekrar tetiklenmesin ──
        rsm = self.rsms.get(sym)
        if rsm and rsm.sweep_level is not None and rsm.direction is not None:
            try:
                mark_sweep_consumed(rsm.direction, rsm.sweep_level)
            except Exception:
                pass
        rsm.reset()

    async def on_15m(self, sym: str, bars: list[Bar]):
        if len(bars) < 10:
            return
        await self._on_15m_close(sym, bars)

    async def on_1m(self, sym: str, bars: list[Bar]):
        if len(bars) < 2:
            return
        await self._on_1m_close(sym, bars)

    async def _prefill_bars(self, sym: str, timeframe: str = "15m") -> Result[None]:
        # P9.5: urllib.request.urlopen → BinanceRESTClient.get() (native async aiohttp)
        r = await self.rest.get(
            "/fapi/v1/klines",
            f"symbol={sym}&interval={timeframe}&limit=500",
        )
        if r.is_err:
            return Result.fail(r.error)
        data = r.value
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
        return Result.ok(None)

    def _warmup_cbdr(self, sym: str):
        bars = self.hub.get_bars(sym, "15m")
        if not bars or len(bars) < 10:
            return
        ss = self.states[sym]

        # ── Gerçek Wilder's ATR inşası (rolling, tüm barlar üzerinden) ──
        atr_val: float | None = None
        prev_close: float = bars[0].open
        for bar in bars:
            tr = calculate_true_range(bar, prev_close)
            atr_val = update_atr(atr_val, tr)
            prev_close = bar.close

            try:
                dt = datetime.fromtimestamp(bar.timestamp / 1000, tz=UTC)
            except Exception:
                continue
            # Sahte ATR yerine gerçek Wilder's ATR kullan
            current_atr = (
                atr_val
                if atr_val is not None
                else max(bar.range, bar.close * cfg.DEFAULT_ATR_FALLBACK_PCT)
            )
            ss.update(dt, bar.open, bar.high, bar.low, bar.close, current_atr)

        # ATR state'ini sakla — canlı barlar buradan devam edecek
        self._atr_state[sym] = atr_val if atr_val is not None else 0.0
        self._atr_prev_close[sym] = prev_close

        # ── Sahte vs gerçek ATR karşılaştırması (BTC, LINK, ADA) ──
        if sym in ("BTCUSDT", "LINKUSDT", "ADAUSDT"):
            last_bar = bars[-1]
            fake_atr = max(
                last_bar.range, last_bar.close * cfg.DEFAULT_ATR_FALLBACK_PCT
            )
            real_atr = self._atr_state[sym]
            log.info(
                "[ATR-CMP] %s | fake=%.6f (range=%.6f fallback=%.6f) | real_wilders=%.6f | ratio=%.2fx",
                sym,
                fake_atr,
                last_bar.range,
                last_bar.close * cfg.DEFAULT_ATR_FALLBACK_PCT,
                real_atr,
                real_atr / fake_atr if fake_atr > 0 else 0.0,
            )

        log.info(
            "[WARMUP] %s CBDR body: lock=%s | body=[%.2f-%.2f] | sweep=%s | ATR=%.6f",
            sym,
            ss.cbdr_locked,
            ss.cbdr_body_low,
            ss.cbdr_body_high,
            ss.sweep_confirmed,
            self._atr_state.get(sym, 0.0),
        )

    async def _set_leverage(self, symbol: str) -> Result[None]:
        """POST /fapi/v1/leverage — sembol için kaldıraç ayarı.

        Returns:
            Result[None] — başarılıysa ok, hata varsa fail.
        """
        if not cfg.BINANCE_API_KEY:
            return Result.ok(None)
        r = await self.rest.post(
            "/fapi/v1/leverage",
            {"symbol": symbol, "leverage": cfg.LEVERAGE},
        )
        if r.is_err:
            return Result.fail(r.error)
        effective = r.value.get("leverage", cfg.LEVERAGE)
        self._pl(symbol, "leverage", f"⚙️ LEVERAGE: {effective}x set edildi")
        log.info("[LEVERAGE] %s leverage=%dx OK", symbol, effective)
        return Result.ok(None)

    async def run(self):
        for sym in self.symbols:
            self.hub.register_callback(sym, "15m", lambda b, s=sym: self.on_15m(s, b))
            self.hub.register_callback(sym, "1m", lambda b, s=sym: self.on_1m(s, b))

        self._load_history()

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
                    self._available_balance = bal
                    self._wallet_balance = bal
                    self._pl(
                        "SYSTEM",
                        "balance",
                        f"\U0001f4b0 AVL: {self._available_balance:.2f} | WAL: {self._wallet_balance:.2f} USDT ({net})",
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

        # Leverage: her sembol için config'deki değeri set et
        if cfg.BINANCE_API_KEY:
            async with asyncio.TaskGroup() as tg:
                lev_tasks = {
                    sym: tg.create_task(self._set_leverage(sym)) for sym in self.symbols
                }
            for sym in self.symbols:
                r = lev_tasks[sym].result()
                if r.is_err:
                    log.warning("[LEVERAGE] %s hatasi (devam): %s", sym, r.error)

        await self.recovery_manager.recover_positions()
        reconcile_from_active(self.active_trades)

        # Recovery sonrasi FVG verisini geri yukle
        for sym in list(self.active_trades):
            fvg_data = _load_fvg_state(sym)
            if fvg_data:
                trade = self.active_trades[sym]
                for k in ("fvg_top", "fvg_bottom", "fvg_direction", "fvg_bar_index"):
                    if k in fvg_data and fvg_data[k] is not None:
                        trade[k] = fvg_data[k]

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
                log.warning(
                    "[SYNC] %s trades_today disk okuma hatasi (devam ediliyor, count=0 varsayilacak): %s",
                    sym,
                    e,
                )

        # FIX #3: Ghost pozisyon temizliği — trade_state.json'da "open": true
        # olup Binance'de kapalı olan pozisyonları temizle.
        # FIX #8'den SONRA çalışmalı (trades_today sıfırlaması FIX #8'i ezmesin).
        await self.recovery_manager.reconcile_ghost_positions()

        # Orphan emir temizliği — Binance'te asılı kalmış STOP/TP emirlerini iptal et
        await self.recovery_manager.reconcile_orphan_orders()

        # User Data Stream (WS Zirhi — REST polling yok)
        if cfg.BINANCE_API_KEY:
            try:
                listen_key = await self.rest.get_listen_key()
                if listen_key:
                    self.hub.set_user_data_listen_key(listen_key)
                    # Faz 6.3: UserDataHandler DI ile callback'leri kur
                    udh = UserDataHandler(
                        active_trades=self.active_trades,
                        pl_callback=self._pl,
                        wallet_callback=lambda v: setattr(self, "_wallet_balance", v),
                        order_manager=self.order_manager,
                        exit_callback=self._exit_trade,
                    )
                    udh.register(self.hub)
                    asyncio.create_task(self.hub._listen_key_refresh_loop(self.rest))
                    log.info("[USER_DATA] Listen key aktif: %s...", listen_key[:10])
            except Exception as e:
                log.warning(
                    "[USER_DATA] Listen key alinamadi (devam ediliyor, WS kullanici verisi devre disi): %s",
                    e,
                )

        self._live = True

        # Gecmis barlari yukle (15m + 1m)
        async with asyncio.TaskGroup() as tg:
            prefill_tasks = []
            for sym in self.symbols:
                prefill_tasks.append(tg.create_task(self._prefill_bars(sym, "15m")))
                prefill_tasks.append(tg.create_task(self._prefill_bars(sym, "1m")))
        for t in prefill_tasks:
            r = t.result()
            if r.is_err:
                log.warning("[PREFILL] bar yukleme hatasi (devam): %s", r.error)

        for sym in self.symbols:
            self._warmup_cbdr(sym)

        for sym in self.symbols:
            bars = self.hub.get_bars(sym, "15m")
            if bars and len(bars) >= 10:
                await self.on_15m(sym, bars)
                log.info("[INIT] %s ilk analiz tamam (%d bar)", sym, len(bars))

        log.info("Gecmis barlar yuklendi, WS baslatiliyor...")
        try:
            await self.hub.run()
        finally:
            await self.rest.close()
            _close_ohlc_writers()


def main():
    """Bot giriş noktası."""
    _setup_logging()
    cleanup_old_event_logs()
    bot = PaperTrader(sys.argv[1:] if len(sys.argv) > 1 else None)
    try:
        asyncio.run(bot.run())
    except KeyboardInterrupt:
        log.info("Kullanici tarafindan durduruldu.")
        bot.hub.stop()
        _close_ohlc_writers()


if __name__ == "__main__":
    main()
