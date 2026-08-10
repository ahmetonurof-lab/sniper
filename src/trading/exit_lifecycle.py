"""
exit_lifecycle.py — Canlı pozisyon kapatma (exit) lifecycle'ının kalbi.

PaperTrader._exit_trade() metodunun taşınmış hali (Patch Set 2,
new_refactoring_plan1.md). Kapsar:
  - WS-FALLBACK guard (stale/phantom event ayrımı)
  - reduceOnly market close gönderimi + adapter response belirsizliği
  - pozisyon kapanış doğrulaması (5 deneme / 200ms)
  - doğrulama başarısızsa REPAIR_REQUIRED + protection onarımı
  - confirmed close sonrası TEK SEFERLİK muhasebe (PnL/balance/peak)
  - confirmed close sonrası cleanup (SL/TP iptal) + persist (snapshot,
    trades_history.jsonl, event log, state_manager, sweep consumption)

Kırmızı çizgiler (order_manager.py / recovery_manager.py ile aynı disiplin):
  - Strateji mantığında sıfır değişiklik — bu sadece bir taşıma (move),
    yeniden yazım (rewrite) değil.
  - Tüm FIX (A#) yorumları, log mesajları ve kontrol akışı BİREBİR korunur.
  - cfg.BINANCE_API_KEY kontrolü _exit_trade'de her zaman doğrudan
    (self._live değil) yapılıyordu — bu aynen korunur.

Not (Patch Set 1 ile tutarlılık): ActiveTrade hâlâ flat. TradeRuntimeState /
TradeConfirmedState / PendingExitContext bu patch'te BAĞLANMADI — o karar
(patch_set_1_brief.md'nin "Açık kalan noktalar" #1 maddesi) planlandığı gibi
sonraki bir patch'e (Protection lifecycle ile birlikte, ActiveTrade'in kendisi
değişmeden) bırakıldı. Burada trade hâlâ dict-uyumlu ActiveTrade nesnesi
olarak, mevcut ["alan"] erişimiyle kullanılıyor.

execute()'un dönüş tipi plan taslağında bool olarak önerilimişti; orijinal
_exit_trade hiçbir zaman bir değer döndürmüyordu (hiçbir çağıran yeri de
kullanmıyordu) ve gerçek çağrı imzası planın sketch'inden farklı
(execute(trade, reason, now_ms) değil, execute(sym, trade, exit_timestamp)
— reason zaten trade["result"] üzerinden çağıran tarafından set ediliyor).
Burada True = exit confirmed + accounting commit edildi; False = exit bu
turda commit edilmedi (repair/ambiguous/ikinci-exit/stale gibi durumlarda —
akış bir sonraki 1m bar'da veya WS event'inde tekrar denenebilir).
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from typing import Any, Callable
from dataclasses import asdict

import config as cfg  # noqa: F401  (tests patch trading.exit_lifecycle.cfg)
from event_log import log_event
from paper_trade_logger import EventType as PtEventType, log_event as pt_log
from models import (
    INCIDENT_EXIT_UNCONFIRMED,
    INCIDENT_PROTECTION_BROKEN,
    STATUS_ACTIVE,
    STATUS_BROKEN_MANUAL_INTERVENTION_REQUIRED,
    STATUS_CLOSED,
    STATUS_EXIT_SUBMITTED,
    STATUS_EXIT_VERIFYING,
    STATUS_REPAIR_REQUIRED,
)
from snapshot.snapshot import capture_snapshot
from state_manager import mark_sweep_consumed, mark_trade_closed
from trading.entry_manager import EntryManager

log = logging.getLogger("sniper.exit_lifecycle")

# %0.05 Binance futures taker fee (each leg) — bot.py'deki COMMISSION_RATE ile
# aynı değer. _exit_trade_legacy fallback yolu bot.py'deki sabiti kullanmaya
# devam ediyor; iki sabit Patch 5/6'da tek kaynağa indirgenebilir.
COMMISSION_RATE = 0.0005


def _trade_identity_key(trade: Any) -> str:
    """Aynı bar/fiyat tekrar trade collision'ını önlemek için benzersiz trade anahtarı.

    BUG-12: entry_bar_index+entry_price ikilisi aynı bar aynı fiyattan iki
    trade'de çakışır. entry_order_id varsa (canlı market emri) onu kullan;
    boşsa (paper/test/eksik response) entry_bar_index+entry_price+actual_qty
    fallback kombinasyonunu kullan.
    """
    entry_order_id = trade.get("entry_order_id", "")
    if entry_order_id:
        return str(entry_order_id)
    return (
        f"{trade.get('entry_bar_index', -1)}_{trade.get('entry_price', 0)}"
        f"_{trade.get('entry_actual_qty', 0)}"
    )


class ExitLifecycleService:
    """_exit_trade() içindeki canlı riskin kalbi.

    PaperTrader'dan DI ile alır:
      - rest_client: BinanceRESTClient
      - order_manager: OrderManager — position_still_open / verify_protection /
        repair_protection / cleanup_on_exit için
      - active_trades: dict[sym, ActiveTrade] — paylaşılan referans
      - states: dict[sym, SessionState] — capture_snapshot için
      - rsms: dict[sym, RetraceStateMachine] — sweep consumption + reset için
      - trades: deque[dict] — kapanan trade geçmişi (paylaşılan referans)
      - pl_callback: callable(sym, key, msg, force=False) — _pl() delegesi
      - risk_mgr: RiskManager — update_peak() için
      - balance_getter / balance_setter: callable — self._available_balance
        okuma/yazma delegeleri (plain float attribute olduğu için)
      - wallet_balance_getter: callable — self._wallet_balance okuma
        delegesi (yalnızca log mesajı formatlamak için)
      - output_dir: str — trades_history.jsonl'in yazıldığı dizin
      - fvg_state_file: str — kapanan trade'in FVG state'inin temizleneceği dosya
    """

    def __init__(
        self,
        rest_client: Any,
        order_manager: Any,
        active_trades: dict,
        states: dict,
        rsms: dict,
        trades: Any,
        pl_callback: Callable[..., None],
        risk_mgr: Any,
        balance_getter: Callable[[], float],
        balance_setter: Callable[[float], None],
        wallet_balance_getter: Callable[[], float],
        output_dir: str,
        fvg_state_file: str,
        exit_log: dict | None = None,
        exit_locks: dict | None = None,
        is_live: bool = False,
    ):
        self._rest = rest_client
        self._order_manager = order_manager
        self._active_trades = active_trades
        self._states = states
        self._rsms = rsms
        self._trades = trades
        self._pl = pl_callback
        self._risk_mgr = risk_mgr
        self._is_live = is_live
        self._get_balance = balance_getter
        self._set_balance = balance_setter
        self._get_wallet_balance = wallet_balance_getter
        self._output_dir = output_dir
        self._fvg_state_file = fvg_state_file
        # P0-1 idempotency guard: {sym: {exit_price: result}}
        self._exit_log: dict[str, dict[float, str]] = exit_log or {}
        # P0-1 per-trade lock: key = sym+entry_timestamp
        self._exit_locks: dict[str, asyncio.Lock] = exit_locks or {}
        # P1-15: stale event cooldown — tekrarlı stale döngüsünü kırmak için
        # {sym: timestamp} — son stale event'ten itibaren belirli süre
        # içinde yeni stale tetiklenmezse pozisyon WS fill ile kapanmış
        # kabul edilir (WAIT_COOLDOWN_SECS sonra WS fill beklemeye devam edilir)
        self._stale_cooldown: dict[str, float] = {}
        self._stale_count: dict[str, int] = {}

    # ── Ana orkestrasyon ────────────────────────────────────────

    async def execute(self, sym: str, trade: Any, exit_timestamp: int) -> bool:
        # P0-1: per-trade lock — aynı sembolde farklı trade'ler birbirini bloklamaz.
        _trade_id_key = _trade_identity_key(trade)
        trade_key = f"{sym}_{_trade_id_key}"
        lock = self._exit_locks.setdefault(trade_key, asyncio.Lock())
        async with lock:
            if trade.get("_exit_committed"):
                log.warning(
                    "[EXIT] %s idempotency guard: _exit_committed=True — tekrar engellendi",
                    sym,
                )
                return False
            trade["_exit_committed"] = True
            # ── Idempotency guard (P0-1): benzersiz trade anahtari bazli ──
            _exit_reason = trade.get("result", "")
            if _exit_reason:
                _trade_id = _trade_identity_key(trade)
                prev_result = self._exit_log.get(sym, {}).get(_trade_id)
                if prev_result == _exit_reason:
                    log.warning(
                        "[EXIT] %s idempotency guard: trade_id=%s result=%s zaten kayitli — tekrar engellendi",
                        sym,
                        _trade_id,
                        _exit_reason,
                    )
                    return False

            # P0-6 EXPANDED: SL/TP/WS_FALLBACK result'larında pozisyonun gerçekten
            # kapalı olup olmadığını REST ile doğrula.
            _exit_result = trade.get("result")
            if _exit_result in ("SL", "TP", "WS_FALLBACK") and self._is_live:
                try:
                    position_open = await self._order_manager.position_still_open(sym)
                except Exception as e:
                    log.critical(
                        "[EXIT] %s %s pozisyon sorgusu basarisiz (%s) — "
                        "guvenlik nedeniyle exit/cancel_all TETIKLENMIYOR",
                        sym,
                        _exit_result,
                        e,
                    )
                    trade["_exit_committed"] = False
                    return False

                if position_open:
                    try:
                        _open_ids = await self._order_manager.get_open_order_ids(sym)
                    except Exception:
                        _open_ids = None
                    _sl_id = str(trade.get("sl_order_id", ""))
                    _tp_id = str(trade.get("tp_order_id", ""))
                    _sl_in = _open_ids is not None and _sl_id in _open_ids
                    _tp_in = _open_ids is not None and _tp_id in _open_ids
                    if _open_ids is not None and not _sl_in and not _tp_in:
                        log.warning(
                            "[EXIT] %s %s P1-14 cross-val: SL/TP open_orders'ta yok "
                            "(sl=%s tp=%s open_ids=%d) — position_still_open bekleniyor",
                            sym,
                            _exit_result,
                            _sl_id,
                            _tp_id,
                            len(_open_ids),
                        )
                        await asyncio.sleep(0.4)
                        try:
                            position_open = (
                                await self._order_manager.position_still_open(sym)
                            )
                        except Exception:
                            position_open = True
                        if not position_open:
                            log.info(
                                "[EXIT] %s %s P1-14 retry: pozisyon kapanmis — "
                                "exit devam ediyor",
                                sym,
                                _exit_result,
                            )
                        else:
                            log.warning(
                                "[EXIT] %s %s P1-14 retry: pozisyon hâlâ acik — "
                                "stale kabul ediliyor",
                                sym,
                                _exit_result,
                            )

                if position_open:
                    # P1-15: -2021 immediately trigger — pozisyon zaten dolmus,
                    # WS FILLED gecikmeli gelecek. Stale event döngüsünü kır.
                    _had_it = getattr(
                        self._order_manager, "had_immediately_trigger", None
                    )
                    if callable(_had_it) and _had_it(sym) is True:
                        log.warning(
                            "[EXIT] %s %s P1-15 -2021 reject tespit edildi — "
                            "pozisyon zaten dolmus, stale event atlaniyor "
                            "(WS FILLED bekleniyor)",
                            sym,
                            _exit_result,
                        )
                        trade["pending_exit_reason"] = None
                        trade["pending_exit_price"] = None
                        trade["pending_exit_qty"] = None
                        trade["pending_exit_order_id"] = None
                        trade["pending_exit_timestamp"] = None
                        trade["result"] = None
                        trade["status"] = STATUS_ACTIVE
                        trade["_exit_committed"] = False
                        return False

                    # P1-15: stale cooldown — tekrarlı stale döngüsü kontrolü
                    _now = time.time()
                    _last_stale = self._stale_cooldown.get(sym, 0)
                    _stale_n = self._stale_count.get(sym, 0) + 1
                    self._stale_count[sym] = _stale_n

                    if _last_stale > 0 and (_now - _last_stale) < 30:
                        # 30sn içinde tekrar stale — WS fill bekleniyor,
                        # further processing gereksiz
                        log.warning(
                            "[EXIT] %s %s P1-15 stale cooldown (#%d) — "
                            "son stale'den %ds gecti, WS fill bekleniyor",
                            sym,
                            _exit_result,
                            _stale_n,
                            int(_now - _last_stale),
                        )
                        trade["pending_exit_reason"] = None
                        trade["pending_exit_price"] = None
                        trade["pending_exit_qty"] = None
                        trade["pending_exit_order_id"] = None
                        trade["pending_exit_timestamp"] = None
                        trade["result"] = None
                        trade["status"] = STATUS_ACTIVE
                        trade["_exit_committed"] = False
                        return False

                    self._stale_cooldown[sym] = _now

                    # P1-15: stale event backstop — N ardışık stale
                    # sonrası REST ile pozisyon durumunu doğrula.
                    # Pozisyon kapandıysa active_trades'ten temizle
                    # ve exit'i başarılı olarak kabul et (stale döngüsünü kır).
                    if _stale_n >= 3:
                        try:
                            pos_open = await self._order_manager.position_still_open(
                                sym
                            )
                        except Exception:
                            pos_open = True
                        if not pos_open:
                            log.warning(
                                "[EXIT] %s %s stale backstop (#%d) — "
                                "pozisyon kapanmisti, active_trades'ten cikariliyor",
                                sym,
                                _exit_result,
                                _stale_n,
                            )
                            self._active_trades.pop(sym, None)
                            self._stale_cooldown.pop(sym, None)
                            self._stale_count.pop(sym, None)
                            _itr = getattr(
                                self._order_manager,
                                "_immediately_trigger_rejects",
                                None,
                            )
                            if isinstance(_itr, dict):
                                _itr.pop(sym, None)
                            return True

                    log.warning(
                        "[EXIT] %s %s stale event #%d — pozisyon hala acik, exit iptal",
                        sym,
                        _exit_result,
                        _stale_n,
                    )
                    try:
                        (
                            sl_present,
                            tp_present,
                        ) = await self._order_manager.verify_protection(sym, trade)
                    except Exception as e:
                        log.critical(
                            "[EXIT] %s %s koruma dogrulamasi basarisiz (%s) — "
                            "onarim atlanip guvenli tarafta kaliniyor",
                            sym,
                            _exit_result,
                            e,
                        )
                        sl_present, tp_present = True, True
                    if not sl_present or not tp_present:
                        log.warning(
                            "[EXIT] %s koruma eksik (sl=%s tp=%s) — onariliyor",
                            sym,
                            sl_present,
                            tp_present,
                        )
                        await self._order_manager.repair_protection(
                            sym, trade, has_sl=sl_present, has_tp=tp_present
                        )
                    trade["pending_exit_reason"] = None
                    trade["pending_exit_price"] = None
                    trade["pending_exit_qty"] = None
                    trade["pending_exit_order_id"] = None
                    trade["pending_exit_timestamp"] = None
                    trade["result"] = None
                    trade["status"] = STATUS_ACTIVE
                    trade["_exit_committed"] = False
                    return False

            # Patch Set 4 (WS normalization) — tek konsolide blok (BUG-11)
            if trade.get("pending_exit_price") is not None:
                trade["exit_price"] = trade["pending_exit_price"]
                trade["exit_actual_price"] = trade["pending_exit_price"]
            if trade.get("pending_exit_qty") is not None:
                trade["exit_actual_qty"] = trade["pending_exit_qty"]
            if trade.get("pending_exit_order_id") is not None:
                trade["exit_order_id"] = trade["pending_exit_order_id"]
            if trade.get("pending_exit_timestamp") is not None:
                trade["exit_timestamp"] = trade["pending_exit_timestamp"]
            trade["pending_exit_reason"] = None
            trade["pending_exit_price"] = None
            trade["pending_exit_qty"] = None
            trade["pending_exit_order_id"] = None
            trade["pending_exit_timestamp"] = None

            if not trade:
                log.warning("[EXIT] %s zaten kapali, ikinci exit engellendi", sym)
                return False

            _exit_already_closed = trade.get("result") in ("SL", "TP", "WS_FALLBACK")

            if not _exit_already_closed:
                trade["status"] = STATUS_EXIT_SUBMITTED
            else:
                trade["status"] = STATUS_EXIT_VERIFYING

            if self._is_live and not _exit_already_closed:
                pos_closed = await self._submit_and_verify_market_close(sym, trade)
                if not pos_closed:
                    trade["_exit_committed"] = False
                    return False

            result = await self._commit_confirmed_exit(sym, trade, exit_timestamp)
            # Clean up exit log key after commit
            self._exit_locks.pop(trade_key, None)
            return result

    # ── Market close gönderimi + doğrulama + repair-on-failure ──

    async def _submit_and_verify_market_close(self, sym: str, trade: Any) -> bool:
        """reduceOnly market close gönder, adapter belirsizliğini yorumla,
        pozisyonun kapandığını 5 denemede doğrula. Doğrulama başarısız
        olursa REPAIR_REQUIRED'e geçip protection onarımı dener.

        Returns: True — pozisyon doğrulandı (kapalı). False — doğrulanamadı
        (trade REPAIR_REQUIRED olarak active_trades'te bırakıldı, çağıran
        execute() burada False dönüp turu bitirmeli).
        """
        mkt_side = "SELL" if trade["side"] == "long" else "BUY"
        close_resp = {}
        log.info(
            "[INTENT] %s pozisyonunu kapatma istegi (side=%s, qty=%.6f)",
            sym,
            mkt_side,
            trade["qty"],
        )
        try:
            log.debug(
                "[EXECUTION] %s place_market_order_priority (CB bypass, reduceOnly=True) baslatiliyor...",
                sym,
            )
            # P0-5: CB bypass'li acil kapanis — SL/TP denemeleri circuit
            # breaker'i acsa bile market close gecsin.
            close_resp = await self._rest.place_market_order_priority(
                sym,
                mkt_side,
                trade["qty"],
                reduce_only=True,
                client_order_id=f"exit-{sym.lower()}-{int(time.time()*1000)}",
            )
        except Exception as e:
            log.warning("[EXIT] %s reduceOnly market HATASI (devam): %s", sym, e)

        # Sprint C: EXIT_SUBMITTED → EXIT_VERIFYING
        trade["status"] = STATUS_EXIT_VERIFYING

        # FIX (A10): adapter'dan gelen _status alanı ile belirsizlik ayrımı
        adapter_status = close_resp.get("_status", "")

        if adapter_status == "REJECTED":
            # Emir borsaya hiç gönderilmedi (qty/precision sorunu)
            # → force close ile dene
            log.warning(
                "[EXIT] %s market order REJECTED — force close deneniyor...",
                sym,
            )
            log_event(
                "force_close",
                sym,
                side=trade["side"],
                qty=trade["qty"],
                success=False,
            )
            try:
                forced = await self._rest.place_force_close_order(
                    sym, mkt_side, trade["side"]
                )
                if forced:
                    log.info("[EXIT] %s closePosition force-close kabul edildi", sym)
            except Exception as e:
                log.warning("[EXIT] %s closePosition force-close hatasi: %s", sym, e)

        elif adapter_status == "EXECUTION_CONFIRMED":
            # orderId mevcut — fill varsa PnL'e yaz
            log.info("[CONFIRMATION] %s reduceOnly market order basarili", sym)
            _q, _p, _ = EntryManager.parse_market_fill(close_resp)
            if _q > 0 and _p > 0:
                trade["exit_actual_price"] = _p
                trade["exit_actual_qty"] = _q
                trade["exit_price"] = _p
                log.info(
                    "[CONFIRMATION] %s market close fill: qty=%.4f @ %.4f",
                    sym,
                    _q,
                    _p,
                )
            log_event(
                "force_close",
                sym,
                side=trade["side"],
                qty=trade["qty"],
                success=True,
            )
            log.info("[EXIT] %s reduceOnly market BASARILI", sym)

        elif adapter_status in ("REQUEST_SENT", "ORDER_ACKNOWLEDGED"):
            # FIX (A10): emir gönderildi ama kimlik/fill yok — belirsiz
            # Pozisyon doğrulamasına geçeceğiz ama commit yapılmayacak
            log.warning(
                "[EXIT] %s market close AMBIGUOUS (_status=%s) — "
                "pozisyon dogrulamasi ile kontrol edilecek",
                sym,
                adapter_status,
            )
            log_event(
                "force_close",
                sym,
                side=trade["side"],
                qty=trade["qty"],
                success=False,
                ambiguous_status=adapter_status,
            )

        else:
            # Tamamen boş response ({}) — adapter hiçbir şey dönmedi
            log.warning(
                "[EXIT] %s market close yaniti bos/bilinmiyor — "
                "force close deneniyor...",
                sym,
            )
            log_event(
                "force_close",
                sym,
                side=trade["side"],
                qty=trade["qty"],
                success=False,
            )
            try:
                forced = await self._rest.place_force_close_order(
                    sym, mkt_side, trade["side"]
                )
                if forced:
                    log.info("[EXIT] %s closePosition force-close kabul edildi", sym)
            except Exception as e:
                log.warning("[EXIT] %s closePosition force-close hatasi: %s", sym, e)

        # ── Pozisyon doğrulama: 5 deneme, 200ms bekle, positionAmt == 0 ──
        # FIX (P0-1): Belirsiz adapter durumunda (REQUEST_SENT/ORDER_ACKNOWLEDGED/
        # bos) "sembol listede yok" sonucuna hemen guvenmeyin — Binance gecikmeli
        # donebilir. Sadece EXECUTION_CONFIRMED durumunda hemen kabul et.
        is_ambiguous = adapter_status in (
            "REQUEST_SENT",
            "ORDER_ACKNOWLEDGED",
        ) or not close_resp.get("orderId")
        pos_closed = False
        for attempt in range(5):
            await asyncio.sleep(0.2)
            try:
                positions = await self._rest.get_positions()
                for p in positions:
                    if p["symbol"] == sym:
                        amt = float(p.get("positionAmt", 0))
                        if amt == 0:
                            pos_closed = True
                        break
                else:
                    # Sembol listede yoksa:
                    # - EXECUTION_CONFIRMED: fill teyidi var, guvenle kabul et
                    # - Belirsiz: son denemeye kadar bekle (Binance gecikebilir)
                    if not is_ambiguous or attempt >= 4:
                        pos_closed = True
                    else:
                        log.info(
                            "[EXIT] %s verify %d/5 — sembol listede yok ama "
                            "adapter belirsiz (%s), bekleniyor",
                            sym,
                            attempt + 2,
                            adapter_status,
                        )
            except Exception as e:
                log.error(
                    "[EXIT] %s position verify failed (attempt %d/5): %s",
                    sym,
                    attempt + 1,
                    str(e),
                )
            if pos_closed:
                break
            log.info(
                "[EXIT] %s verify attempt %d/5 — pozisyon hala acik",
                sym,
                attempt + 2,
            )

        # FIX (P0-1): Belirsiz adapter durumunda 5 deneme de yetersizse,
        # get_all_orders ile kapanis emrinin gercekten FILLED olup olmadigini kontrol et.
        if not pos_closed and is_ambiguous:
            try:
                orders = await self._rest.get_all_orders(sym)
                for o in orders:
                    o_status = o.get("status", "")
                    is_reduce_only = o.get("reduceOnly") in (True, "true", "True")
                    is_close_position = o.get("closePosition") in (True, "true", "True")
                    if o_status == "FILLED" and (is_reduce_only or is_close_position):
                        pos_closed = True
                        log.info(
                            "[EXIT] %s get_all_orders FILLED emir bulundu "
                            "(orderId=%s) — pozisyon kapali",
                            sym,
                            o.get("orderId") or o.get("algoId"),
                        )
                        break
            except Exception as e:
                log.error(
                    "[EXIT] %s get_all_orders FILLED check hatasi: %s",
                    sym,
                    e,
                )

        if not pos_closed:
            await self._mark_repair_required(sym, trade)
            return False

        return True

    async def _mark_repair_required(self, sym: str, trade: Any) -> None:
        """Market close doğrulanamadığında: REPAIR_REQUIRED'e geç, protection
        onarımı dene, operatöre kritik uyarı bas. Orijinal _exit_trade'deki
        `if not pos_closed:` bloğunun birebir aynısı."""
        log.critical(
            "[%s] %s pozisyon 5 denemede kapanmadi — manual müdahale gerekli",
            INCIDENT_EXIT_UNCONFIRMED,
            sym,
        )
        self._pl(
            sym,
            f"critical_{sym}",
            f"\U0001f6a8 {INCIDENT_EXIT_UNCONFIRMED}: {sym} kapanmadi!",
            force=True,
        )
        # FIX (A9): geri alinacak bir pnl/balance/peak_equity commit'i
        # ARTIK YOK. Ancak basarisiz close sonrasi koruma (SL/TP)
        # emirlerinin bosaltilmamasi ve trade'in normal ACTIVE olarak
        # isleme devam etmemesi gerekir.
        trade["status"] = STATUS_REPAIR_REQUIRED
        try:
            sl_present, tp_present = await self._order_manager.verify_protection(
                sym, trade
            )
            if not sl_present or not tp_present:
                log.warning(
                    "[REPAIR] [%s] %s market close basarisiz, koruma eksik (sl=%s tp=%s) — onariliyor",
                    INCIDENT_PROTECTION_BROKEN,
                    sym,
                    sl_present,
                    tp_present,
                )
                await self._order_manager.repair_protection(
                    sym, trade, has_sl=sl_present, has_tp=tp_present
                )
        except Exception as e:
            log.critical(
                "[REPAIR] [%s] %s market close basarisiz, protection onarimi hata aldi: %s",
                INCIDENT_PROTECTION_BROKEN,
                sym,
                e,
            )

    # ── Confirmed close sonrası muhasebe + cleanup + persist ────

    async def _commit_confirmed_exit(
        self, sym: str, trade: Any, exit_timestamp: int
    ) -> bool:
        lock = self._exit_locks.setdefault(sym, asyncio.Lock())
        async with lock:
            trade = self._active_trades.pop(sym, None)
            if not trade:
                log.warning(
                    "[CONFIRMATION] %s dogrulama sirasinda ikinci exit ile kapanmis, atlaniyor",
                    sym,
                )
                return False

            trade["status"] = STATUS_CLOSED
            # P1-15: stale cooldown temizliği — pozisyon kapatildi, sayac sifirla
            self._stale_cooldown.pop(sym, None)
            self._stale_count.pop(sym, None)
            _itr = getattr(self._order_manager, "_immediately_trigger_rejects", None)
            if isinstance(_itr, dict):
                _itr.pop(sym, None)

            log.info(
                "[COMMIT] %s pnl hesaplama ve muhasebe defterine kayit basliyor...", sym
            )
        actual_entry_price = trade.get("entry_actual_price", 0) or trade["entry_price"]
        actual_entry_qty = trade.get("entry_actual_qty", 0) or trade["qty"]
        actual_exit_price = trade.get("exit_actual_price", 0) or trade["exit_price"]
        actual_exit_qty = trade.get("exit_actual_qty", 0) or actual_entry_qty
        if actual_entry_price <= 0 or actual_exit_price <= 0 or actual_entry_qty <= 0:
            # FIX (A1): trade artik SESSIZCE KAYBOLMUYOR. Pozisyon borsada
            # dogrulanmis sekilde kapali ama fill verisi gecersiz oldugu
            # icin PNL commit edilemiyor — trade INCELENEBILIR halde geri
            # birakiliyor. Bu gecici bir alan; A2 ile gercek status enum'una
            # (EXIT_UNCONFIRMED / BROKEN_MANUAL_INTERVENTION_REQUIRED) tasinacak.
            log.critical(
                "[EXIT] %s gecersiz fill verisi — PnL hesaplanamadi, pozisyon "
                "kapali ama muhasebe commit edilmedi (manuel kontrol gerekli)",
                sym,
            )
            trade["result"] = None
            trade["status"] = STATUS_BROKEN_MANUAL_INTERVENTION_REQUIRED
            trade["exit_unconfirmed_reason"] = "invalid_fill_data"
            self._active_trades[sym] = trade
            self._pl(
                sym,
                f"exit_unconfirmed_{exit_timestamp}",
                f"\U0001f6a8 EXIT_UNCONFIRMED: {sym} pozisyon kapandi ama fill verisi "
                f"gecersiz — PNL commit edilmedi, manuel kontrol gerekli",
                force=True,
            )
            return False

        pnl_qty = min(actual_entry_qty, actual_exit_qty)
        diff = (
            (actual_exit_price - actual_entry_price)
            if trade["side"] == "long"
            else (actual_entry_price - actual_exit_price)
        )
        entry_fee = actual_entry_price * pnl_qty * COMMISSION_RATE
        exit_fee = actual_exit_price * pnl_qty * COMMISSION_RATE
        total_fee = entry_fee + exit_fee
        pnl = round(diff * pnl_qty - total_fee, 2)
        trade["entry_price"] = actual_entry_price
        trade["qty"] = pnl_qty
        trade["exit_price"] = actual_exit_price
        trade["entry_fee"] = round(entry_fee, 2)
        trade["exit_fee"] = round(exit_fee, 2)
        trade["fee"] = round(total_fee, 2)
        new_balance = self._get_balance() + pnl
        self._set_balance(new_balance)
        self._risk_mgr.update_peak(new_balance)
        self._pl(
            sym,
            f"exit_{exit_timestamp}",
            f"\U0001f7e5 EXIT: {trade['result']} | PRICE: {trade['exit_price']:.2f} | PNL: {pnl:+.2f} | AVL: {new_balance:.2f} | WAL: {self._get_wallet_balance():.2f} | TRAIL: {trade['trailing_count']}",
        )
        log.info(
            "[PAPER] %s %s exit=%s pnl=%.2f available=%.2f",
            sym,
            trade["result"],
            trade["exit_price"],
            pnl,
            new_balance,
        )

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
        await self._order_manager.cleanup_on_exit(sym, trade, trade["result"])

        # FVG state dosyasini temizle (BULGU-12: file handle leak + bare except)
        try:
            if os.path.exists(self._fvg_state_file):
                with open(self._fvg_state_file, "r", encoding="utf-8") as f:
                    data = json.loads(f.read())
                data.pop(sym, None)
                with open(self._fvg_state_file, "w", encoding="utf-8") as f:
                    f.write(json.dumps(data, ensure_ascii=False))
        except Exception as e:
            log.error(
                "[FVG_STATE] %s fvg state cleanup hatasi: %s",
                sym,
                e,
            )

        try:
            snap = capture_snapshot(sym, trade, pnl, self._states[sym])
            if snap:
                trade["snapshot_file"] = snap
        except Exception:
            log.warning("[SNAPSHOT] %s snapshot alinamadi", sym)

        record = asdict(trade)
        record["sym"] = sym
        record["pnl"] = pnl
        record["exit_bar"] = trade.get("exit_bar", 0)
        record["close_time"] = exit_timestamp
        self._trades.append(record)
        try:
            trades_file = os.path.join(self._output_dir, "trades_history.jsonl")
            with open(trades_file, "a", encoding="utf-8") as f:
                f.write(json.dumps(record, ensure_ascii=False) + "\n")
        except Exception:
            log.warning("[TRADES] %s jsonl yazma hatasi", sym)
        mark_trade_closed(sym)

        # ── Sweep consumption mark — aynı level sweep tekrar tetiklenmesin ──
        rsm = self._rsms.get(sym)
        if rsm and rsm.sweep_level is not None and rsm.direction is not None:
            try:
                mark_sweep_consumed(rsm.direction, rsm.sweep_level)
            except Exception as e:
                log.warning("[EXIT] %s sweep consumption mark hatasi: %s", sym, e)
        rsm.reset()

        # P0-1 idempotency log: benzersiz trade anahtari bazli
        _trade_id = _trade_identity_key(trade)
        self._exit_log.setdefault(sym, {})[_trade_id] = trade.get("result", "")

        pt_log(
            PtEventType.TRADE_CLOSED,
            sym,
            trade.get("side", ""),
            trade_id=f"{sym}-{trade.get('entry_bar_index', '?')}",
            entry={
                "signal_price": trade.get("entry_price_estimate", 0)
                or trade.get("entry_price", 0),
                "actual_fill_price": trade.get("entry_actual_price", 0)
                or trade.get("entry_price", 0),
                "requested_qty": trade.get("entry_requested_qty", 0)
                or trade.get("qty", 0),
                "actual_qty": trade.get("entry_actual_qty", 0) or trade.get("qty", 0),
            },
            protection={
                "final_sl": trade.get("sl"),
                "final_tp": trade.get("tp"),
            },
            result=trade.get("result", ""),
            reason="trade_closed",
            extra_data={
                "pnl": round(pnl, 2),
                "exit_price": trade.get("exit_price", 0),
                "trail_count": trade.get("trail_count", 0)
                or trade.get("trailing_count", 0),
                "exit_timestamp": exit_timestamp,
            },
        )

        return True
