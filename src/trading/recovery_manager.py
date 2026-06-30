"""
recovery_manager.py — Binance pozisyon kurtarma + ghost temizliği.

PaperTrader._recover_positions() ve _reconcile_ghost_positions()
metodlarını kapsar. Sadece run() başlangıcında çağrılır.

Kırmızı çizgiler:
  - Strateji mantığında sıfır değişiklik
  - extract_order_id, cfg import'ları korunur
  - _pl() formatı birebir aynı (pl_callback üzerinden)
"""

from __future__ import annotations

import logging

import config as cfg
from bot_infra import extract_order_id
from event_log import log_event
from models import ActiveTrade

log = logging.getLogger("sniper.recovery_manager")


class RecoveryManager:
    """Binance pozisyon kurtarma + ghost temizliği.

    PaperTrader'dan DI ile alır:
      - rest_client: BinanceRESTClient
      - symbols: list[str] — takip edilen semboller
      - cfgs: dict[sym, dict] — sembol konfigürasyonları
      - states: dict[sym, SessionState] — session durumları
      - active_trades: dict[sym, ActiveTrade] — aktif trade'ler
      - pl_callback: callable(sym, key, msg) — _pl() delegesi
      - order_manager: OrderManager (şimdilik kullanılmıyor)
    """

    def __init__(
        self,
        rest_client,
        symbols: list[str],
        cfgs: dict,
        states: dict,
        active_trades: dict,
        pl_callback,
        order_manager=None,
    ):
        self._rest = rest_client
        self._symbols = symbols
        self._cfgs = cfgs
        self._states = states
        self._active_trades = active_trades
        self._pl = pl_callback
        self._order_manager = order_manager

    # ── Pozisyon kurtarma ──────────────────────────────────────

    async def recover_positions(self) -> None:
        """Binance'deki açık pozisyonları tara, SL/TP varsa envantere al,
        yoksa yeni koruma emri kur."""
        if not cfg.BINANCE_API_KEY:
            return
        try:
            positions = await self._rest.get_positions()
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
                if sym not in self._symbols:
                    continue
                amt = float(pos.get("positionAmt", 0))
                direction = "long" if amt > 0 else "short"
                entry = float(pos.get("entryPrice", 0))

                open_orders = await self._rest.get_all_orders(sym)
                sl_orders = [
                    o
                    for o in open_orders
                    if self._rest.get_order_type(o)
                    in ("STOP_MARKET", "STOP", "STOP_LIMIT")
                    and (
                        o.get("reduceOnly") in (True, "true", "True")
                        or o.get("closePosition") in (True, "true", "True")
                    )
                ]
                tp_orders = [
                    o
                    for o in open_orders
                    if self._rest.get_order_type(o)
                    in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT")
                    and (
                        o.get("reduceOnly") in (True, "true", "True")
                        or o.get("closePosition") in (True, "true", "True")
                    )
                ]

                if sl_orders and tp_orders:
                    sl_price = self._rest.get_order_price(sl_orders[0])
                    tp_price = self._rest.get_order_price(tp_orders[0])
                    risk_pts = abs(entry - sl_price)
                    sl_id = extract_order_id(sl_orders[0])
                    tp_id = extract_order_id(tp_orders[0])
                    self._active_trades[sym] = ActiveTrade(
                        symbol=sym,
                        entry_bar_index=0,
                        entry_price=entry,
                        sl=sl_price,
                        tp=tp_price,
                        qty=abs(amt),
                        side=direction,
                        trigger_fvg=None,
                        initial_sl=sl_price,
                        initial_tp=tp_price,
                        trailing_count=0,
                        risk_pts=risk_pts,
                        is_recovered=True,
                        sl_order_id=sl_id,
                        tp_order_id=tp_id,
                    )
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
                    atr_est = entry * cfg.DEFAULT_ATR_FALLBACK_PCT
                    risk_pts = atr_est * self._cfgs[sym]["SL_ATR_MULT"]
                    if direction == "long":
                        sl = entry - risk_pts * 2
                        tp = entry + risk_pts * self._cfgs[sym]["TP_RR"]
                    else:
                        sl = entry + risk_pts * 2
                        tp = entry - risk_pts * self._cfgs[sym]["TP_RR"]

                    sl_id = ""
                    tp_id = ""
                    if cfg.BINANCE_API_KEY:
                        try:
                            sl_side = "SELL" if direction == "long" else "BUY"
                            rounded_sl = await self._rest.apply_price_precision(sym, sl)
                            rounded_tp = await self._rest.apply_price_precision(sym, tp)

                            sl_resp = await self._rest.place_stop_order(
                                sym, sl_side, abs(amt), rounded_sl
                            )
                            sl_id = extract_order_id(sl_resp)

                            tp_resp = await self._rest.place_tp_order(
                                sym, sl_side, abs(amt), rounded_tp
                            )
                            tp_id = extract_order_id(tp_resp)

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

                    self._active_trades[sym] = ActiveTrade(
                        symbol=sym,
                        entry_bar_index=0,
                        entry_price=entry,
                        sl=sl,
                        tp=tp,
                        qty=abs(amt),
                        side=direction,
                        trigger_fvg=None,
                        initial_sl=sl,
                        initial_tp=tp,
                        trailing_count=0,
                        risk_pts=risk_pts,
                        is_recovered=True,
                        sl_order_id=sl_id,
                        tp_order_id=tp_id,
                    )
                    self._pl(
                        sym,
                        "recover",
                        f"\U0001f512 {direction.upper()} @ {entry:.2f} | SL={sl:.2f} (id={sl_id}) TP={tp:.2f} (id={tp_id}) kuruldu",
                    )
        except Exception as e:
            self._pl("SYSTEM", "recover", f"\u274c Pozisyon kurtarma hatasi: {e}")

    # ── Ghost pozisyon temizliği ───────────────────────────────

    async def reconcile_ghost_positions(self) -> None:
        """trade_state.json'da open=true görünüp Binance'de kapalı
        olan pozisyonları temizle."""
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
            if sym in self._active_trades:
                continue

            log.info(
                "[GHOST] %s state'de open=true ama active_trades'te yok — Binance sorgulaniyor...",
                sym,
            )
            try:
                positions = await self._rest.get_positions()
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
                    open_orders = await self._rest.get_all_orders(sym)
                    has_sl = any(
                        self._rest.get_order_type(o)
                        in ("STOP_MARKET", "STOP", "STOP_LIMIT")
                        for o in open_orders
                    )
                    has_tp = any(
                        self._rest.get_order_type(o)
                        in ("TAKE_PROFIT_MARKET", "TAKE_PROFIT", "TAKE_PROFIT_LIMIT")
                        for o in open_orders
                    )
                    if not has_sl or not has_tp:
                        log_event(
                            "ghost_missing_sltp",
                            sym,
                            side=direction,
                            entry=entry,
                            has_sl=has_sl,
                            has_tp=has_tp,
                        )
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
                        log_event("ghost_ok", sym, side=direction, entry=entry)
                        self._pl(
                            sym,
                            "ghost_ok",
                            f"\U0001f512 GHOST: {direction.upper()} @ {entry:.2f} | SL/TP mevcut",
                        )
                else:
                    mark_trade_closed(sym)
                    self._states[sym].trades_today = 0
                    log_event("ghost_cleaned", sym)
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

    async def reconcile_orphan_orders(self) -> None:
        """Binance'teki acik tum emirleri tara, bot'un bildigi
        trade'lere ait olmayanlari iptal et (crash sonrasi birikme onlenir)."""
        if not cfg.BINANCE_API_KEY:
            return

        known_ids: set[str] = set()
        for t in self._active_trades.values():
            for k in ("sl_order_id", "tp_order_id"):
                oid = t.get(k)
                if oid:
                    known_ids.add(str(oid))

        for sym in self._symbols:
            try:
                orders = await self._rest.get_all_orders(sym)
            except Exception:
                continue
            for o in orders:
                oid = str(o.get("orderId") or o.get("algoId") or "")
                if not oid or oid in known_ids:
                    continue
                is_algo = "algoId" in o
                cancel_id = o.get("algoId") or o.get("orderId")
                otype = self._rest.get_order_type(o)
                try:
                    await self._rest.cancel_order(
                        cancel_id, sym, reason="orphan_sweep", is_algo=is_algo
                    )
                    log_event("orphan_cleaned", sym, order_id=oid, order_type=otype)
                    log.info(
                        "[ORPHAN] %s emir iptal edildi (id=%s, type=%s)",
                        sym,
                        oid,
                        otype,
                    )
                except Exception:
                    pass
