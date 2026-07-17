"""
user_data_handler.py — Binance User Data Stream callback'leri.

PaperTrader._register_user_data_callbacks() içindeki iç içe
async fonksiyonları kapsülleyen DI tabanlı sınıf.

Faz 6.3: ORDER_TRADE_UPDATE + ACCOUNT_UPDATE callback'leri
PaperTrader'dan ayrıştırıldı.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

from models import WSFallbackError

log = logging.getLogger("sniper.user_data")


class UserDataHandler:
    """Binance User Data Stream callback'leri: ORDER_TRADE_UPDATE + ACCOUNT_UPDATE.

    PaperTrader'dan DI ile alır:
      - active_trades: dict — trade'lere erişim
      - pl_callback: callable — _pl() delegesi
      - wallet_callback: callable — _wallet_balance setter (sadece görüntüleme)
      - order_manager: OrderManager — repair_protection() için
      - exit_callback: async callable — _exit_trade() delegesi
    """

    def __init__(
        self,
        active_trades: dict[str, Any],
        pl_callback: Callable[[str, str, str], None],
        wallet_callback: Callable[[float], None],
        order_manager: Any,
        exit_callback: Callable[..., Any],  # async
    ):
        self._active_trades = active_trades
        self._pl = pl_callback
        self._set_wallet = wallet_callback
        self._order_manager = order_manager
        self._exit_trade = exit_callback

    def register(self, hub: Any) -> None:
        """hub.on_user_data() ile ORDER_TRADE_UPDATE ve ACCOUNT_UPDATE
        callback'lerini kaydet."""

        @hub.on_user_data("ORDER_TRADE_UPDATE")
        async def on_order_update(msg: dict) -> None:
            od = msg.get("o", {})
            sym = od.get("s", "")
            status = od.get("X", "")
            oid = str(od.get("c", "") or od.get("i", ""))
            log.info("[WS-ORDER] %s status=%s id=%s", sym, status, oid)

            # Binance reduceOnly flag'i (R=True veya reduceOnly=True)
            is_reduce_only = od.get("R", False) or od.get("reduceOnly", False)

            if status in ("FILLED", "TRIGGERED"):
                ap = float(od.get("ap", 0))
                last_price = float(od.get("L", 0))
                price = ap if ap > 0 else last_price
                cum_qty = float(od.get("z", 0))
                cum_quote = float(od.get("Z", 0))

                trade = _active_trades.get(sym)
                if trade:
                    s_id = str(trade.get("sl_order_id", ""))
                    t_id = str(trade.get("tp_order_id", ""))
                    s_id_prev = str(trade.get("sl_order_id_prev", ""))
                    t_id_prev = str(trade.get("tp_order_id_prev", ""))
                    s_id_hist = [str(x) for x in trade.get("sl_order_id_history", [])]
                    t_id_hist = [str(x) for x in trade.get("tp_order_id_history", [])]

                    if oid in (
                        s_id,
                        t_id,
                        s_id_prev,
                        t_id_prev,
                        *s_id_hist,
                        *t_id_hist,
                    ):
                        # Normal akis: ID eslesti (guncel veya gecis)
                        result = "SL" if oid in (s_id, s_id_prev, *s_id_hist) else "TP"
                        _pl(
                            sym,
                            "filled_confirm",
                            f"\u2705 BINANCE CONFIRMED: pozisyon kapatildi @ {price} ({result})",
                        )

                        # Gercek fill verisini kaydet
                        if sym in _active_trades:
                            trade["exit_price"] = price
                            trade["exit_actual_price"] = price
                            if cum_qty > 0:
                                trade["exit_actual_qty"] = cum_qty
                            if cum_quote > 0:
                                trade["exit_quote_qty"] = cum_quote
                            trade["exit_order_id"] = oid
                            trade["exit_timestamp"] = int(time.time() * 1000)
                            trade["result"] = result
                            await _exit_trade(sym, trade, int(time.time() * 1000))
                    else:
                        # FIX #3: ID eslesmiyor AMA reduceOnly FILLED geldi!
                        if is_reduce_only:
                            trade["exit_price"] = price
                            trade["exit_actual_price"] = price
                            if cum_qty > 0:
                                trade["exit_actual_qty"] = cum_qty
                            if cum_quote > 0:
                                trade["exit_quote_qty"] = cum_quote
                            trade["exit_order_id"] = oid
                            trade["exit_timestamp"] = int(time.time() * 1000)
                            trade["result"] = "WS_FALLBACK"
                            await _exit_trade(sym, trade, int(time.time() * 1000))
                            raise WSFallbackError(sym, oid, s_id, t_id)
                else:
                    # trade active_trades'te yok ama reduceOnly FILLED geldi.
                    if is_reduce_only:
                        log.info(
                            "[WS-GHOST] %s reduceOnly FILLED (oid=%s) "
                            "ama active_trades bos. Ignore.",
                            sym,
                            oid,
                        )
                return

            if status not in ("CANCELED", "EXPIRED"):
                return
            trade = _active_trades.get(sym)
            if not trade:
                return
            s_id = str(trade.get("sl_order_id", ""))
            t_id = str(trade.get("tp_order_id", ""))
            s_id_prev = str(trade.get("sl_order_id_prev", ""))
            t_id_prev = str(trade.get("tp_order_id_prev", ""))
            s_id_hist = [str(x) for x in trade.get("sl_order_id_history", [])]
            t_id_hist = [str(x) for x in trade.get("tp_order_id_history", [])]

            # Geçiş penceresindeki eski emrin CANCELED bildirimi → sessizce yok say
            if oid in (s_id_prev, t_id_prev, *s_id_hist, *t_id_hist) and oid:
                log.info(
                    "[WS-ORDER] %s eski %s emri iptal edildi (prev id=%s) — ignore",
                    sym,
                    "SL" if oid == s_id_prev else "TP",
                    oid,
                )
                return

            if oid not in (s_id, t_id):
                return
            label = "SL" if oid == s_id else "TP"
            log.warning(
                "[WS-REPAIR] %s %s emri silindi \u2014 onariliyor...", sym, label
            )
            try:
                await _order_manager.repair_protection(
                    sym, trade, has_sl=(oid != s_id), has_tp=(oid != t_id)
                )
            except Exception as e:
                log.critical("[WS-REPAIR] %s onarim hatasi: %s", sym, e)

        @hub.on_user_data("ACCOUNT_UPDATE")
        async def on_account_update(msg: dict) -> None:
            ud = msg.get("a", {})
            for bal in ud.get("B", []):
                if bal.get("a") in ("USDT", "FDUSD", "USDC"):
                    _set_wallet(float(bal.get("wb", 0)))

        # Closure değişkenlerini yakala
        _active_trades = self._active_trades
        _pl = self._pl
        _set_wallet = self._set_wallet
        _order_manager = self._order_manager
        _exit_trade = self._exit_trade
