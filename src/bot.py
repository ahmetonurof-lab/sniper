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
import time
from collections import deque
from datetime import UTC, datetime, timezone, timedelta
from decimal import Decimal

import config as cfg
from bot_binance import BinanceRESTClient
from bot_infra import (
    _close_ohlc_writers,
    _flush_ohlc_writers,
    _fmt_price,
    export_ohlc_1m,
    export_ohlc_15m,
    _RateLimiter,
)
from indicators import calculate_true_range, update_atr
from models import (
    ActiveTrade,
    Bar,
    PendingLock,
    Result,
    STATUS_ACTIVE,
    STATUS_EXIT_REQUESTED,
    STATUS_REPAIR_REQUIRED,
    UNRESTRICTED_STATUSES,
)
from retrace_state import RetraceStateMachine
from session import DailyBias, SessionState
from risk_manager import RiskManager
from fvg import fvg_is_alive
from session_router import (
    should_trade,
    get_cbdr_multiplier,
    get_session_hours,
)
from state_manager import (
    mark_trade_opened,
    reconcile_from_active,
    get_trade_count_today,
)
from state_writer import write_state
from event_log import cleanup_old_event_logs, log_event
from paper_trade_logger import (
    configure as pt_configure,
    EventType as PtEventType,
    log_event as pt_log,
)
from trading import (
    SignalEngine,
    EntryManager,
    TrailingManager,
    OrderManager,
    RecoveryManager,
    ConsoleReporter,
    UserDataHandler,
    ExitLifecycleService,
    ProtectionLifecycleService,
    _trade_identity_key,
)
from trading.trailing_manager import TrailingConfig, TrailLevel
from websocket import BinanceWSHub


class BotPriceReader:
    def __init__(self, hub: BinanceWSHub) -> None:
        self.hub = hub

    async def get_last_price(self, symbol: str) -> Decimal:
        bars = self.hub.get_bars(symbol, "1m")
        if not bars:
            return Decimal("0")
        return Decimal(str(bars[-1].close))


TR_TZ = timezone(timedelta(hours=3))

COMMISSION_RATE = 0.0005  # %0.05 Binance futures taker fee (each leg)

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
        tmp_file = f"{_FVG_STATE_FILE}.tmp"
        with open(tmp_file, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp_file, _FVG_STATE_FILE)
    except Exception as e:
        log.error("[FVG_STATE] %s kayit hatasi: %s", sym, e)


def _load_fvg_state(sym: str) -> dict:
    """Diskten FVG verisini oku."""
    try:
        if os.path.exists(_FVG_STATE_FILE):
            with open(_FVG_STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f).get(sym, {})
    except Exception as e:
        log.error("[FVG_STATE] %s okuma hatasi: %s", sym, e)
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

# Patch Set 2 (new_refactoring_plan1.md) — ExitLifecycleService rollout.
# EXIT_LIFECYCLE_SERVICE_ENABLED kaldirildi (P0-1): tum exit'ler
# ExitLifecycleService.execute() uzerinden gider. Eski flag ve legacy
# _exit_trade_legacy silindi.


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
        # P0-1 idempotency guard: _exit_reason_log[sym]["exit_price"] = exit_reason
        self._exit_reason_log: dict[str, dict[float, str]] = {}
        # P0-1 per-trade lock: key = sym + entry_timestamp
        self._exit_locks: dict[str, asyncio.Lock] = {}

        pt_configure(
            log_path=os.path.join(_OUTPUT_DIR, "paper_trade.log"),
            run_id=f"paper-{datetime.now(timezone.utc).strftime('%Y%m%d-%H%M%S')}",
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
            is_live=self._live,
        )
        self.order_manager = OrderManager(
            rest_client=self.rest,
            is_live=self._live,
        )
        self.trailing_manager = TrailingManager(
            price_reader=BotPriceReader(self.hub),
            protection_gateway=self.order_manager,
            config=TrailingConfig(
                default_tick_size=Decimal("0.10"),
                epsilon_ticks=1,
                pivot_strength=2,
                sl_buffer_ticks=2,
            ),
        )
        # Patch Set 3 (new_refactoring_plan1.md): Protection policy kararlari
        # ProtectionLifecycleService her zaman aktif.
        self.protection_service = ProtectionLifecycleService()
        self.order_manager._protection = self.protection_service
        # P0-1: ExitLifecycleService tum exit'leri yonetir. Legacy kaldirildi.
        self.exit_service = ExitLifecycleService(
            rest_client=self.rest,
            order_manager=self.order_manager,
            active_trades=self.active_trades,
            states=self.states,
            rsms=self.rsms,
            trades=self.trades,
            pl_callback=self._pl,
            risk_mgr=self.risk_mgr,
            balance_getter=lambda: self._available_balance,
            balance_setter=lambda v: setattr(self, "_available_balance", v),
            wallet_balance_getter=lambda: self._wallet_balance,
            output_dir=_OUTPUT_DIR,
            fvg_state_file=_FVG_STATE_FILE,
            exit_log=self._exit_reason_log,
            exit_locks=self._exit_locks,
            is_live=self._live,
        )
        # ── Gerçek Wilder's ATR rolling state (sembol bazlı) ──
        # TANIM: RecoveryManager'dan ÖNCE gelmeli (atr_state parametresi)
        self._atr_state: dict[str, float] = {}
        self._atr_prev_close: dict[str, float] = {}
        self._orphan_check_counter = 0
        self._pos_check_task: asyncio.Task | None = None
        self.recovery_manager = RecoveryManager(
            rest_client=self.rest,
            symbols=self.symbols,
            cfgs=self.cfgs,
            states=self.states,
            active_trades=self.active_trades,
            pl_callback=self._pl,
            order_manager=self.order_manager,
            atr_state=self._atr_state,
            protection_service=self.protection_service,
            exit_locks=self._exit_locks,
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

    def _build_fvg_scan_trail_extractor(self, sym: str):
        """Post-entry pencerede her 15m bar'da taze FVG taramasi yapan extractor.

        rsm.trigger_fvg (entry'nin tek FVG'si) yerine backtest (analyzer_v5)
        ile ayni kurali uygular: detect_fvgs + fvg_confirm_mode (retrace /
        continuation) + ATR buffer (ATR_TRAIL_MULT) + TRAIL_MIN_MOVE_MULT,
        birden fazla FVG'ye atlayabilir (coklu-hop). is_placeable: turetilen
        SL'nin current price'tan uygun tarafta oldugu dogrulanir (stale
        candidate uretmez). Dondurulen SL seviyesi sl_buffered=True ile
        isaretlenir (compute_trail_candidate tick x2 buffer uygulamaz).
        """

        def extractor(scoped_bars, trade):
            if len(scoped_bars) < 4:
                return None

            atr_val = self._atr_state.get(sym, 0.0)
            if atr_val <= 0:
                last = scoped_bars[-1]
                atr_val = max(last.range, last.close * cfg.DEFAULT_ATR_FALLBACK_PCT)

            min_mult = cfg.FVG_SIZE_MAP.get(sym, cfg.FVG_MIN_SIZE_ATR_MULT)
            min_fvg_size = max(atr_val * min_mult, 1e-8)

            res = TrailingManager._fvg_multihop(
                scoped_bars,
                trade,
                atr_val,
                min_fvg_size,
                current_price=float(scoped_bars[-1].close),
                tick_size=trade.get("tick_size"),
            )
            if not res.updated or res.last_bar_index is None:
                return None

            return TrailLevel(
                price=Decimal(str(res.new_sl)),
                source_bar_index=int(res.last_bar_index),
                reason="fvg_scan_multihop",
                sl_buffered=True,
            )

        return extractor

    def _session_label(self, hour: int) -> str:
        """Saati piyasa seansina cevir."""
        if 2 <= hour < 13:
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
        export_ohlc_15m(current, sym)

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

        # ── Session/CBDR status display → ConsoleReporter (Faz 6.2) ──
        self.reporter.display_session_status(sym, session, hour, dt.minute, ss)

        # ── Sweep status display → ConsoleReporter (Faz 6.2) ──
        # Not: display_sweep_status artik entry kapisi degil; sadece durum gosterimi.
        # Yeni on_sweep() yalnizca SignalEngine.progress_rsm icinde ss.sweep_confirmed
        # kosulu ile baslatilir (backtest analyzer_v5.py:266 ile aynı). Mevcut
        # SWEEP_DETECTED state'i ise her 15m barinda on_sweep_confirmed ile
        # invalidation kontrolunden gecer (analyzer_v5.py:273-274 ile aynı).
        self.reporter.display_sweep_status(sym, ss, hour, dt.minute)

        rsm = self.rsms[sym]
        engine = self.signal_engines[sym]

        # ── Blok 8: RSM state progression → SignalEngine (her bar) ──
        engine.progress_rsm(bars_15m, current, ss, atr_val, sym)

        # ── Blok 9: FVG/Wick durum yazdırma → ConsoleReporter (Faz 6.2) ──
        self.reporter.display_fvg_status(
            sym,
            rsm,
            max(atr_val * cfg.FVG_SIZE_MAP.get(sym, cfg.FVG_MIN_SIZE_ATR_MULT), 1e-8),
            current.close,
        )

        # ── Backtest parity: yeni CBDR gunune tasinan TRIGGER_READY'yi temizle ──
        # backtest analyzer_v5.py:276-284 ayni — kilitsizken can_trigger ise
        # bias_reject kontrol edilir, daily_bias NEUTRAL ise rsm.reset().
        # Entry kilitliyken evaluate_trigger (Blok 10) zaten ayni kontrolu yapar.
        if not ss.cbdr_locked and rsm.can_trigger() and sym not in self.active_trades:
            sd = rsm.direction
            db = ss.daily_bias
            bias_reject = (
                (sd == "bullish" and db == DailyBias.BEARISH)
                or (sd == "bearish" and db == DailyBias.BULLISH)
                or db == DailyBias.NEUTRAL
            )
            if bias_reject:
                log.info(
                    "[PARITY] %s TRIGGER_READY yeni gune tasindi bias=%s -> rsm.reset",
                    sym,
                    db.name,
                )
                rsm.reset()

        if not ss.cbdr_locked:
            log.info("[SKIP] %s CBDR henuz kilitlenmedi — akis baslatilmadi", sym)
            return

        # ── Blok 10: Trigger check + filtreler → SignalEngine ──
        result = engine.evaluate_trigger(current, ss)

        if result.decision == "TRIGGER":
            tf = rsm.trigger_fvg
            if tf is not None:
                if not fvg_is_alive(
                    tf.direction, tf.top, tf.bottom, tf.bar_index, bars_15m[:-1]
                ):
                    log.info(
                        "[FVG-FILTER] %s FVG bar=%d dokunulmus veya invalid — canli degil (iptal)",
                        sym,
                        tf.bar_index,
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
                max(
                    atr_val * cfg.FVG_SIZE_MAP.get(sym, cfg.FVG_MIN_SIZE_ATR_MULT),
                    1e-8,
                ),
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
        current = bars_1m[-1]
        export_ohlc_1m(current, sym)

        self._orphan_check_counter += 1
        if self._orphan_check_counter % 10 == 0:
            _flush_ohlc_writers()

        trade = self.active_trades.get(sym)
        if not trade:
            return

        # ── Orphan sweep (every 5 calls, tüm sembolleri tarar) ──
        if self._orphan_check_counter % 5 == 0:
            await self.recovery_manager.reconcile_orphan_orders()
            # WATCHDOG: status kilidi kontrolü — TRAIL_REPLACING/EXIT_REQUESTED
            # gibi restricted status'lar 90s'den uzun süredir devam ediyorsa
            # ACTIVE'e zorla geri çek (exit kontrolünü tekrar başlat).
            for _sym, _trade in list(self.active_trades.items()):
                _status = _trade.get("status", "")
                if _status not in UNRESTRICTED_STATUSES:
                    _since = _trade.get("status_since", 0)
                    if _since and time.time() - _since > 90:
                        log.critical(
                            "[WATCHDOG] %s %s status=%s kilitli — ACTIVE'e zorla geri "
                            "cekiliyor (exit kontrolu %ss suredir calismiyordu)",
                            _sym,
                            _trade.get("symbol", _sym),
                            _status,
                            int(time.time() - _since),
                        )
                        _trade["status"] = STATUS_ACTIVE

        # ── Trailing + Exit: yalnizca unrestricted durumda ──
        if trade.get("status") in UNRESTRICTED_STATUSES:
            # ATR (1m'de ATR güncellenmez) — extractor ATR'yi _atr_state'ten okur
            _atr_val = self._atr_state.get(
                sym, max(current.range, current.close * cfg.DEFAULT_ATR_FALLBACK_PCT)
            )

            # ── FVG Trailing ──
            # Restart'ta recover edilen trade'lerde extractor closure kaybolur;
            # trail_mode="fvg" ise ayni yolu korumak icin extractor'u yeniden kur.
            if not callable(trade.get("trail_level_extractor")):
                trade["trail_level_extractor"] = self._build_fvg_scan_trail_extractor(
                    sym
                )
            bars_15m = self.hub.get_bars(sym, "15m")
            trail_res = None
            if bars_15m:
                try:
                    trail_res = await self.trailing_manager.orchestrate_trail(
                        trade, bars_15m
                    )
                    if trail_res and trail_res.action == "updated":
                        log.info(
                            "[TRAIL] %s koruma güncellendi: sl=%s tp=%s (reason: %s)",
                            sym,
                            trade.get("sl"),
                            trade.get("tp"),
                            trail_res.candidate.reason if trail_res.candidate else "?",
                        )
                except Exception as e:
                    log.critical(
                        "[TRAIL] %s orchestrate_trail exception, status ACTIVE'e "
                        "zorla geri cekiliyor: %s",
                        sym,
                        e,
                    )
                    trade["status"] = STATUS_ACTIVE
                # Not: orchestrate_trail exchange rejection durumunda action="skip" doner,
                # invalidation durumunda (immediate trigger) local flag set eder.

            # ── Exit kontrolü ──
            log.debug(
                "[P1-15_DEBUG] %s check_exit oncesi: current.high=%r trade_sl=%r current.ts=%s",
                sym,
                current.high,
                trade.get("sl"),
                current.timestamp,
            )
            exit_decision = self.trailing_manager.check_exit(current, trade)
            if exit_decision.triggered:
                _trade_id_key = _trade_identity_key(trade)
                trade_key = f"{sym}_{_trade_id_key}"
                lock = self._exit_locks.setdefault(trade_key, asyncio.Lock())
                async with lock:
                    trade["status"] = STATUS_EXIT_REQUESTED
                    trade["pending_exit_price"] = exit_decision.exit_price
                    trade["exit_bar"] = current.index
                    trade["exit_timestamp"] = current.timestamp
                    trade["result"] = exit_decision.result
                await self._exit_trade(sym, trade, current.timestamp)
                return

        # ── UPNL + state writer — her bar'da (frozen dahil) ──
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

        tick_size = 0.0
        if getattr(self.rest, "get_tick_size", None) is not None:
            try:
                tick_size = await self.rest.get_tick_size(sym)
            except Exception:
                tick_size = 0.0

        sl, tp = EntryManager.calculate_sl_tp(
            side=side,
            entry_price=entry_price,
            risk_pts=risk_pts,
            fvg_buf=fvg_buf,
            tp_rr=tp_rr,
            trigger_fvg=rsm.trigger_fvg,
            tick_size=tick_size,
        )

        trade_id = f"{sym}-{current.index}"

        fvg_data = None
        if fvg:
            fvg_data = {
                "present": True,
                "top": fvg.top,
                "bottom": fvg.bottom,
                "height": fvg.top - fvg.bottom,
                "bar_index": fvg.bar_index,
                "buffer": 0.0,
                "fallback_used": False,
                "max_risk_cap_used": False,
            }

        pt_log(
            PtEventType.INITIAL_SL_CALCULATED,
            sym,
            side,
            trade_id=trade_id,
            entry={
                "signal_price": entry_price,
                "actual_fill_price": None,
                "requested_qty": None,
                "actual_qty": None,
            },
            protection={
                "raw_sl": sl,
                "raw_tp": tp,
                "normalized_sl": None,
                "normalized_tp": None,
                "final_sl": sl,
                "final_tp": tp,
                "risk_distance": round(abs(sl - entry_price), 8),
                "tp_rr": tp_rr,
                "tick_size": None,
                "epsilon": None,
                "rounding": None,
                "sl_order_id": None,
                "tp_order_id": None,
            },
            fvg=fvg_data,
            result="calculated",
            reason="initial_sl_tp_ready",
        )

        # ── 1. SENKRON VALİDASYONLAR (PENDING KİLİDİNDEN ÖNCE) → EntryManager ──
        risk_dist = abs(sl - entry_price)
        valid, err_msg = EntryManager.validate_risk(risk_dist, atr_val)
        if not valid:
            log.warning("[ENTRY] %s %s — trade atlandı", sym, err_msg)
            rsm.reset()
            return

        # ── 1b. PRE-ENTRY SL-eps guard (tüm semboller, tek genel kural) ──
        # SL/TP'nin (a) giriş fiyatına ve (b) FVG sınırına (long: fvg.bottom,
        # short: fvg.top) borsa "immediately trigger" epsilon'undan
        # (SL_EPSILON_TICKS) yakınsaması durumunda sinyal BAŞTAN reddedilir —
        # pozisyon hiç açılmaz, gereksiz MARKET emri + fill-sonrası acil
        # kapanma trafiği (fee/slippage) olmaz. SEI/ENA gibi sembole özel
        # koşul YOKTUR; kural tüm semboller için EntryManager'da ortaktır.
        # tick_size bilinemiyorsa (0.0) epsilon hesaplanamaz, guard atlanır
        # (fill-sonrası validate_protection_with_actual_fill yine devrededir).
        valid_dir, dir_msg = EntryManager.validate_pre_entry_protection(
            side,
            entry_price,
            sl,
            tp,
            tick_size,
            trigger_fvg=fvg,
            epsilon_ticks=cfg.SL_EPSILON_TICKS,
        )
        if not valid_dir:
            log.warning(
                "[PRE-ENTRY] %s %s — SL/TP eps icinde, sinyal reddedildi: %s",
                sym,
                side,
                dir_msg,
            )
            rsm.reset()
            ss.sweep_confirmed = False
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
        # ── Nihai carpan (Guvenlik Freni) ──
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

        pt_log(
            PtEventType.ENTRY_QTY_READY,
            sym,
            side,
            trade_id=trade_id,
            entry={
                "signal_price": entry_price,
                "requested_qty": qty,
                "risk_distance": round(risk_dist, 8),
                "final_risk_mult": final_risk_mult,
            },
            result="accepted",
            reason="entry_qty_ready",
        )

        with PendingLock(self.active_trades, sym, logger=log) as lock:
            entry_price_original = entry_price
            sl_id = ""
            tp_id = ""
            if self._live:
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
                    risk_pts=risk_pts,
                    fvg_buf=fvg_buf,
                    tp_rr=tp_rr,
                    trigger_fvg=fvg,
                    trade_id=trade_id,
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
                qty = (
                    exec_result.actual_qty
                    if exec_result.actual_qty > 0
                    else exec_result.qty
                )
                actual_entry_price = (
                    exec_result.actual_price
                    if exec_result.actual_price > 0
                    else entry_price
                )
                if qty <= 0 or actual_entry_price <= 0:
                    self._pl(sym, "order_err", "\u274c ORDER: gecersiz fill verisi")
                    log.warning(
                        "[ORDER] %s actual_qty=%.4f price=%.6f iptal",
                        sym,
                        qty,
                        actual_entry_price,
                    )
                    rsm.reset()
                    return
                entry_price = actual_entry_price
                if exec_result.entry_log_msg:
                    self._pl(sym, "entry", exec_result.entry_log_msg)
                live_entry_order_id = exec_result.order_id
                live_requested_qty = exec_result.qty or qty

                # ── Post-entry sanity check (P1-7) ──
                # ~2.5s bekle, SL/TP'nin gerçekten Binance'te olup olmadığını doğrula.
                # Mevcut periodic_check_loop ile ÇAKIŞMAZ — sadece gözlem amaçlı,
                # recover_positions/repair_protection tetiklemez.
                if sl_id or tp_id:
                    try:
                        await asyncio.sleep(2.5)
                        open_ids = await self.order_manager.get_open_order_ids(sym)
                        log.debug(
                            "[POST_ENTRY_DEBUG] %s raw_ids=%s sl_id=%s tp_id=%s sl_id_type=%s tp_id_type=%s",
                            sym,
                            sorted(open_ids) if open_ids is not None else None,
                            sl_id,
                            tp_id,
                            type(sl_id).__name__,
                            type(tp_id).__name__,
                        )
                        if open_ids is None:
                            log.warning(
                                "[POST_ENTRY] %s SL/TP sorgu basarisiz — check atlaniyor",
                                sym,
                            )
                            sl_ok = True
                            tp_ok = True
                        else:
                            sl_ok = not sl_id or sl_id in open_ids
                            tp_ok = not tp_id or tp_id in open_ids
                        if not sl_ok or not tp_ok:
                            log.critical(
                                "[POST_ENTRY] %s SL/TP sanity check BASARISIZ! "
                                "sl_id=%s sl_ok=%s tp_id=%s tp_ok=%s — "
                                "harici kapanis veya emir reddi olabilir",
                                sym,
                                sl_id,
                                sl_ok,
                                tp_id,
                                tp_ok,
                            )
                            log_event(
                                "post_entry_check_failed",
                                sym,
                                sl_id=sl_id,
                                tp_id=tp_id,
                                sl_ok=sl_ok,
                                tp_ok=tp_ok,
                                side=side,
                                entry_price=entry_price,
                                qty=qty,
                            )
                        else:
                            log.info(
                                "[POST_ENTRY] %s SL/TP sanity check OK (sl=%s tp=%s)",
                                sym,
                                sl_id,
                                tp_id,
                            )
                    except Exception as e:
                        log.warning("[POST_ENTRY] %s sanity check hatasi: %s", sym, e)
            else:
                assert self.entry_manager is not None
                paper_result = await self.entry_manager.execute_live_entry(
                    sym,
                    side,
                    qty,
                    sl,
                    tp,
                    entry_price,
                    risk_pts=risk_pts,
                    fvg_buf=fvg_buf,
                    tp_rr=tp_rr,
                    trigger_fvg=fvg,
                    trade_id=trade_id,
                )
                if paper_result.entry_log_msg:
                    self._pl(sym, "entry", paper_result.entry_log_msg)
                live_entry_order_id = ""
                live_requested_qty = 0.0

            log.info(
                "[PAPER] %s %s @ %s sl=%s tp=%s qty=%.4f",
                sym,
                side,
                _fmt_price(entry_price),
                _fmt_price(sl),
                _fmt_price(tp),
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

        tick_size = 0.10
        if cfg.BINANCE_API_KEY:
            try:
                tick_size = await self.rest.get_tick_size(sym)
            except Exception:
                log.warning("[TRY_ENTRY] %s tick_size alinamadi (0.10 fallback)", sym)

        self.active_trades[sym] = ActiveTrade(
            symbol=sym,
            side=side,
            status=STATUS_ACTIVE,
            entry_price=entry_price,
            entry_bar_index=current.index,
            entry_timestamp=int(time.time() * 1000),
            sl=sl,
            tp=tp,
            qty=qty,
            initial_sl=sl,
            initial_tp=tp,
            risk_pts=risk_pts,
            tick_size=tick_size,
            trail_count=0,
            trail_level_extractor=self._build_fvg_scan_trail_extractor(sym),
            trail_mode="fvg",
            trigger_fvg=fvg,
            fvg_top=getattr(fvg, "top", None) if fvg else None,
            fvg_bottom=getattr(fvg, "bottom", None) if fvg else None,
            fvg_direction=getattr(fvg, "direction", None) if fvg else None,
            fvg_bar_index=fvg.bar_index if fvg else -1,
            sweep_level=ss.sweep_level,
            cbdr_high=ss.cbdr_body_high,
            cbdr_low=ss.cbdr_body_low,
            sl_order_id=sl_id if self._live else "",
            tp_order_id=tp_id if self._live else "",
            entry_order_id=live_entry_order_id,
            entry_requested_qty=live_requested_qty,
            entry_price_estimate=entry_price_original,
            entry_actual_qty=qty,
            entry_actual_price=entry_price,
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
        # Bias Kilit Modu: entry sonrasi IDLE'a donme — yon korunur, kilitlenir.
        # Kapanis (exit) sonrasi ayni yonde taze bir FVG, yeni sweep beklemeden
        # tekrar TRIGGER_READY olabilir (on_bias_fvg). Bias tersine donerse /
        # nötrlesirse signal_engine BIAS_LOCKED'i resetler.
        rsm.lock_bias(bar_index=current.index)

    async def _exit_trade(self, sym, trade, exit_timestamp: int):
        """P0-1: tum exit'ler ExitLifecycleService.execute() uzerinden."""
        return await self.exit_service.execute(sym, trade, exit_timestamp)

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

        # FIX (P0-4): Restart sonrası REPAIR_REQUIRED/EXIT_REQUESTED trade'leri
        # temizle — recover_positions SL/TP'yi zaten tazelediyse, trade'i
        # ACTIVE'e dondur. Aksi halde onceki session'dan kalan bozuk trade
        # sonsuza kadar REPAIR_REQUIRED'da kalir.
        _stuck_statuses = {STATUS_REPAIR_REQUIRED, STATUS_EXIT_REQUESTED}
        for sym, trade in list(self.active_trades.items()):
            status = trade.get("status", "")
            if status in _stuck_statuses:
                has_sl = bool(trade.get("sl_order_id"))
                has_tp = bool(trade.get("tp_order_id"))
                if has_sl and has_tp:
                    trade["status"] = STATUS_ACTIVE
                    log.info(
                        "[RESTART] %s onceki durum=%s -> ACTIVE (SL/TP saglikli, recover edildi)",
                        sym,
                        status,
                    )
                else:
                    log.warning(
                        "[RESTART] %s durum=%s ama SL/TP eksik (sl=%s tp=%s) — "
                        "pozisyon korumasiz, periyodik kontrol duzeltmeli",
                        sym,
                        status,
                        has_sl,
                        has_tp,
                    )

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
                        exit_locks=self._exit_locks,
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
        self.entry_manager._is_live = True
        self.order_manager._is_live = True
        self.exit_service._is_live = True

        # Periyodik pozisyon+emir kontrolü (her 60sn)
        self._pos_check_task = asyncio.create_task(
            self.recovery_manager.periodic_check_loop()
        )
        log.info("[POS-CHECK] periyodik pozisyon kontrolü baslatildi (60sn aralikla)")

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
            if self._pos_check_task:
                self._pos_check_task.cancel()
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
