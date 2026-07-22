"""
user_data_handler.py — Binance User Data Stream callback'leri.

PaperTrader._register_user_data_callbacks() içindeki iç içe
async fonksiyonları kapsülleyen DI tabanlı sınıf.

Faz 6.3: ORDER_TRADE_UPDATE + ACCOUNT_UPDATE callback'leri
PaperTrader'dan ayrıştırıldı.

Patch Set 4 (WS normalization): normalize_order_event() ile raw WS
payload'ı NormalizedOrderEvent'e çevirir. WS_EVENT_NORMALIZATION_ENABLED
aktifken confirmed trade alanlarını doğrudan mutasyona uğratmaz —
pending_exit_* alanlarına yazar, _exit_trade() promote eder.
"""

from __future__ import annotations

import logging
import time
from typing import Any, Callable

import config as cfg
from models import (
    NormalizedOrderEvent,
    UNRESTRICTED_STATUSES,
    STATUS_REPAIR_REQUIRED,
    STATUS_EXIT_REQUESTED,
    STATUS_EXIT_SUBMITTED,
    STATUS_EXIT_VERIFYING,
    WSFallbackError,
    INCIDENT_WS_UNMATCHED_REDUCE_ONLY,
    INCIDENT_ORPHAN_CANCEL_DURING_TRANSITION,
)

_SELF_EXIT_IN_PROGRESS_STATUSES = frozenset(
    {STATUS_EXIT_REQUESTED, STATUS_EXIT_SUBMITTED, STATUS_EXIT_VERIFYING}
)

log = logging.getLogger("sniper.user_data")

WS_EVENT_NORMALIZATION_ENABLED = cfg.WS_EVENT_NORMALIZATION_ENABLED


# ── WS event normalization ─────────────────────────────────────


def normalize_order_event(raw: dict) -> NormalizedOrderEvent | None:
    """Raw Binance ORDER_TRADE_UPDATE 'o' alanından NormalizedOrderEvent üretir.

    Dönüş: NormalizedOrderEvent veya None (symbol yoksa - ignore).
    """
    sym = raw.get("s", "")
    if not sym:
        return None
    return NormalizedOrderEvent(
        symbol=sym,
        order_id=str(raw.get("c", "") or raw.get("i", "")),
        client_order_id=str(raw.get("c", "")),
        status=raw.get("X", ""),
        reduce_only=bool(raw.get("R", False) or raw.get("reduceOnly", False)),
        avg_price=(float(raw.get("ap", 0)) if raw.get("ap") else None),
        last_price=(float(raw.get("L", 0)) if raw.get("L") else None),
        cum_qty=(float(raw.get("z", 0)) if raw.get("z") else None),
        cum_quote_qty=(float(raw.get("Z", 0)) if raw.get("Z") else None),
        ts_ms=int(time.time() * 1000),
        raw=raw,
    )


# ── ID koleksiyonu ─────────────────────────────────────────────


def _collect_trade_order_ids(
    trade: dict,
) -> tuple[str, str, str, str, list[str], list[str]]:
    """Trade'in tüm bilinen order ID'lerini topluca döndür.

    Returns: (s_id, t_id, s_id_prev, t_id_prev, s_id_hist, t_id_hist)
    """
    s_id = str(trade.get("sl_order_id", ""))
    t_id = str(trade.get("tp_order_id", ""))
    s_id_prev = str(trade.get("sl_order_id_prev", ""))
    t_id_prev = str(trade.get("tp_order_id_prev", ""))
    s_id_hist = [str(x) for x in trade.get("sl_order_id_history", [])]
    t_id_hist = [str(x) for x in trade.get("tp_order_id_history", [])]
    return s_id, t_id, s_id_prev, t_id_prev, s_id_hist, t_id_hist


def _oid_matches_trade(
    oid: str,
    s_id: str,
    t_id: str,
    s_id_prev: str,
    t_id_prev: str,
    s_id_hist: list[str],
    t_id_hist: list[str],
) -> bool:
    """oid herhangi bir trade order ID'si ile eşleşiyor mu?"""
    if not oid:
        return False
    return oid in (s_id, t_id, s_id_prev, t_id_prev, *s_id_hist, *t_id_hist)


def _resolve_fill_result(
    oid: str,
    s_id: str,
    t_id: str,
    s_id_prev: str,
    s_id_hist: list[str],
) -> str:
    """oid SL mi TP mi? SL-side ID'lerden herhangi biriyse 'SL', değilse 'TP'."""
    if oid in (s_id, s_id_prev, *s_id_hist):
        return "SL"
    return "TP"


# ── UserDataHandler ────────────────────────────────────────────


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
        exit_callback: Callable[..., Any],
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

            if WS_EVENT_NORMALIZATION_ENABLED:
                await _on_order_update_normalized(od)
                return
            await _on_order_update_legacy(od)

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

        # ── Normalized handler (Patch Set 4) ─────────────────

        async def _on_order_update_normalized(od: dict) -> None:
            evt = normalize_order_event(od)
            if evt is None:
                return
            status = evt.status
            sym = evt.symbol
            oid = evt.order_id
            oid_c = str(od.get("c", ""))
            oid_i = str(od.get("i", ""))
            log.info("[WS-ORDER] %s status=%s id=%s", sym, status, oid)

            if status in ("FILLED", "TRIGGERED"):
                price = evt.fill_price
                cum_qty = evt.cum_qty or 0
                cum_quote = evt.cum_quote_qty or 0
                is_reduce_only = evt.reduce_only

                trade = _active_trades.get(sym)
                if trade:
                    s_id, t_id, s_id_prev, t_id_prev, s_id_hist, t_id_hist = (
                        _collect_trade_order_ids(trade)
                    )

                    if _oid_matches_trade(
                        oid_c, s_id, t_id, s_id_prev, t_id_prev, s_id_hist, t_id_hist
                    ) or _oid_matches_trade(
                        oid_i, s_id, t_id, s_id_prev, t_id_prev, s_id_hist, t_id_hist
                    ):
                        result = _resolve_fill_result(
                            oid_c or oid_i, s_id, t_id, s_id_prev, s_id_hist
                        )
                        _pl(
                            sym,
                            "filled_confirm",
                            f"\u2705 BINANCE CONFIRMED: pozisyon kapatildi @ {price} ({result})",
                        )
                        # FIX (Patch Set 4): confirmed alanlara DEGIL,
                        # pending_exit_* alanlarina yaz. _exit_trade
                        # dogrularsa promote eder.
                        trade["pending_exit_price"] = price
                        trade["pending_exit_qty"] = cum_qty
                        trade["pending_exit_order_id"] = oid_c or oid_i
                        trade["pending_exit_timestamp"] = evt.ts_ms
                        trade["result"] = result
                        if cum_quote > 0:
                            trade["exit_quote_qty"] = cum_quote
                        await _exit_trade(
                            sym, trade, evt.ts_ms or int(time.time() * 1000)
                        )
                    else:
                        if is_reduce_only:
                            # FIX (race): trade zaten kendi exit surecindeyse
                            # (TRAIL_CLOSE/force_close/manual — market close
                            # emri, SL/TP algo ID setinde hic yer almaz) bu
                            # fill'i orphan/WS_FALLBACK sanma. exit_lifecycle
                            # zaten kendi REST polling'iyle bu kapanisi
                            # dogrulayip commit edecek — burada result'i ezmek
                            # ve _exit_trade'i ikinci kez tetiklemek sadece
                            # yanlis "WS_FALLBACK" kaydina ve yakalanmamis
                            # WSFallbackError'a yol acar.
                            if trade.get("status") in _SELF_EXIT_IN_PROGRESS_STATUSES:
                                log.info(
                                    "[WS-ORDER] %s reduceOnly FILLED (oid=%s) — "
                                    "trade zaten exit surecinde (status=%s), "
                                    "kendi kapanis emri olarak kabul edildi, "
                                    "WS_FALLBACK'e cevrilmiyor",
                                    sym,
                                    oid,
                                    trade.get("status"),
                                )
                                return
                            trade["pending_exit_reason"] = (
                                INCIDENT_WS_UNMATCHED_REDUCE_ONLY
                            )
                            trade["pending_exit_price"] = price
                            if cum_qty > 0:
                                trade["pending_exit_qty"] = cum_qty
                            if cum_quote > 0:
                                trade["exit_quote_qty"] = cum_quote
                            trade["pending_exit_order_id"] = oid
                            trade["pending_exit_timestamp"] = evt.ts_ms
                            trade["result"] = "WS_FALLBACK"
                            await _exit_trade(
                                sym, trade, evt.ts_ms or int(time.time() * 1000)
                            )
                            raise WSFallbackError(sym, oid, s_id, t_id)
                else:
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
            s_id, t_id, s_id_prev, t_id_prev, s_id_hist, t_id_hist = (
                _collect_trade_order_ids(trade)
            )

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
            if (
                trade.get("status") not in UNRESTRICTED_STATUSES
                and trade.get("status") != STATUS_REPAIR_REQUIRED
            ):
                log.info(
                    "[%s] %s status=%s — otomatik repair atlaniyor (baska bir akis yonetiyor)",
                    INCIDENT_ORPHAN_CANCEL_DURING_TRANSITION,
                    sym,
                    trade.get("status"),
                )
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

        # ── Legacy handler (değiştirildi: self-exit race guard eklendi) ─────

        async def _on_order_update_legacy(od: dict) -> None:
            sym = od.get("s", "")
            status = od.get("X", "")
            oid_c = str(od.get("c", ""))
            oid_i = str(od.get("i", ""))
            oid = oid_c or oid_i
            log.info("[WS-ORDER] %s status=%s id=%s", sym, status, oid)

            is_reduce_only = od.get("R", False) or od.get("reduceOnly", False)

            if status in ("FILLED", "TRIGGERED"):
                ap = float(od.get("ap", 0))
                last_price = float(od.get("L", 0))
                price = ap if ap > 0 else last_price
                cum_qty = float(od.get("z", 0))
                cum_quote = float(od.get("Z", 0))

                # FIX: WS fill hem 'c' (clientOrderId) hem 'i' (orderId) ile eslestir
                trade = _active_trades.get(sym)
                if trade:
                    s_id, t_id, s_id_prev, t_id_prev, s_id_hist, t_id_hist = (
                        _collect_trade_order_ids(trade)
                    )

                    if _oid_matches_trade(
                        oid_c, s_id, t_id, s_id_prev, t_id_prev, s_id_hist, t_id_hist
                    ) or _oid_matches_trade(
                        oid_i, s_id, t_id, s_id_prev, t_id_prev, s_id_hist, t_id_hist
                    ):
                        result = _resolve_fill_result(
                            oid_c or oid_i, s_id, t_id, s_id_prev, s_id_hist
                        )
                        _pl(
                            sym,
                            "filled_confirm",
                            f"\u2705 BINANCE CONFIRMED: pozisyon kapatildi @ {price} ({result})",
                        )
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
                        if is_reduce_only:
                            if trade.get("status") in _SELF_EXIT_IN_PROGRESS_STATUSES:
                                log.info(
                                    "[WS-ORDER] %s reduceOnly FILLED (oid=%s) — "
                                    "trade zaten exit surecinde (status=%s), "
                                    "kendi kapanis emri olarak kabul edildi, "
                                    "WS_FALLBACK'e cevrilmiyor",
                                    sym,
                                    oid,
                                    trade.get("status"),
                                )
                                return
                            trade["pending_exit_reason"] = (
                                INCIDENT_WS_UNMATCHED_REDUCE_ONLY
                            )
                            trade["pending_exit_price"] = price
                            if cum_qty > 0:
                                trade["pending_exit_qty"] = cum_qty
                            if cum_quote > 0:
                                trade["exit_quote_qty"] = cum_quote
                            trade["pending_exit_order_id"] = oid
                            trade["pending_exit_timestamp"] = int(time.time() * 1000)
                            trade["result"] = "WS_FALLBACK"
                            await _exit_trade(sym, trade, int(time.time() * 1000))
                            raise WSFallbackError(sym, oid, s_id, t_id)
                else:
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
            s_id, t_id, s_id_prev, t_id_prev, s_id_hist, t_id_hist = (
                _collect_trade_order_ids(trade)
            )

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
            if (
                trade.get("status") not in UNRESTRICTED_STATUSES
                and trade.get("status") != STATUS_REPAIR_REQUIRED
            ):
                log.info(
                    "[%s] %s status=%s — otomatik repair atlaniyor (baska bir akis yonetiyor)",
                    INCIDENT_ORPHAN_CANCEL_DURING_TRANSITION,
                    sym,
                    trade.get("status"),
                )
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
