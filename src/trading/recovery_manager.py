"""
recovery_manager.py — Binance pozisyon kurtarma + ghost temizliği.

PaperTrader._recover_positions() ve _reconcile_ghost_positions()
metodlarını kapsar. Sadece run() başlangıcında çağrılır.

Kırmızı çizgiler:
  - Strateji mantığında sıfır değişiklik
  - extract_order_id, cfg import'ları korunur
  - _pl() formatı birebir aynı (pl_callback üzerinden)

Patch Set 3: _known_protection_ids() ve should_skip_reconcile()
ProtectionLifecycleService'e delege edilir (varsa).
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

import config as cfg
from bot_infra import extract_order_id
from event_log import log_event
from models import (
    INCIDENT_POSITION_OPEN_BUT_STATE_MISSING,
    ActiveTrade,
    UNRESTRICTED_STATUSES,
)

if TYPE_CHECKING:
    from trading.protection_lifecycle import ProtectionLifecycleService

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
      - atr_state: dict[sym, float] — sembol bazlı gerçek Wilder's ATR
      - protection_service: ProtectionLifecycleService | None —
        policy kararlari icin (None ise eski inline logic korunur)
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
        atr_state: dict | None = None,
        protection_service: "ProtectionLifecycleService | None" = None,
    ):
        self._rest = rest_client
        self._symbols = symbols
        self._cfgs = cfgs
        self._states = states
        self._active_trades = active_trades
        self._pl = pl_callback
        self._order_manager = order_manager
        self._atr_state = atr_state or {}
        self._protection = protection_service

    # ── Pozisyon kurtarma ──────────────────────────────────────

    async def recover_positions(self, quiet: bool = False) -> None:
        """Binance'deki açık pozisyonları tara, SL/TP varsa envantere al,
        yoksa yeni koruma emri kur.

        Args:
            quiet: True ise _pl() konsol mesaji atlanir (periyodik cagri).
        """
        if not cfg.BINANCE_API_KEY:
            return
        try:
            positions = await self._rest.get_positions()
            if not positions:
                if not quiet:
                    self._pl("SYSTEM", "recover", "\u2705 API'de acik pozisyon yok")
                return

            if not quiet:
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

                existing = self._active_trades.get(sym)
                if sl_orders and tp_orders:
                    sl_price = self._rest.get_order_price(sl_orders[0])
                    tp_price = self._rest.get_order_price(tp_orders[0])
                    risk_pts = abs(entry - sl_price)
                    sl_id = extract_order_id(sl_orders[0])
                    tp_id = extract_order_id(tp_orders[0])
                    if existing:
                        existing["sl"] = sl_price
                        existing["tp"] = tp_price
                        existing["sl_order_id"] = sl_id
                        existing["tp_order_id"] = tp_id
                        existing["risk_pts"] = risk_pts
                    else:
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
                    if not quiet:
                        self._pl(
                            sym,
                            "recover",
                            f"\U0001f512 {direction.upper()} @ {entry:.2f} | SL={sl_price:.2f} TP={tp_price:.2f} | yeni trade engellendi",
                        )
                else:
                    if not quiet:
                        self._pl(
                            sym,
                            "recover",
                            f"[{INCIDENT_POSITION_OPEN_BUT_STATE_MISSING}] \u26a0\ufe0f {direction.upper()} @ {entry:.2f} | SL/TP bulunamadi (pozisyon korumasiz)",
                        )
                    # Gercek ATR varsa kullan. Yoksa DEFAULT_ATR_FALLBACK_PCT (0.01%)
                    # KULLANMA: SL/TP giris fiyatina yapisir, Binance "immediately
                    # trigger" hatasiyla reddeder ve pozisyon sessizce korumasiz kalir.
                    # Bunun yerine ayri, gercekci bir acil durum mesafesi kullan.
                    real_atr = self._atr_state.get(sym, 0.0)
                    if real_atr > 0:
                        atr_est = real_atr
                        risk_pts = atr_est * self._cfgs[sym]["SL_ATR_MULT"]
                    else:
                        risk_pts = entry * cfg.RECOVERY_SL_FALLBACK_PCT
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
                            # SL basarisizsa (fiyat coktan gecti), mevcut fiyata gore yeni SL dene
                            if not sl_id:
                                log.warning(
                                    "[RECOVER] %s SL basarisiz (sl=%.4f), mevcut fiyata gore yeniden hesaplaniyor...",
                                    sym,
                                    sl,
                                )
                                try:
                                    cur_px = await self._rest.estimate_market_price(sym)
                                    if direction == "long" and cur_px < sl:
                                        new_sl = await self._rest.apply_price_precision(
                                            sym, cur_px * 0.97
                                        )
                                    elif direction == "short" and cur_px > sl:
                                        new_sl = await self._rest.apply_price_precision(
                                            sym, cur_px * 1.03
                                        )
                                    else:
                                        new_sl = rounded_sl
                                    sl_resp2 = await self._rest.place_stop_order(
                                        sym, sl_side, abs(amt), new_sl
                                    )
                                    sl_id2 = extract_order_id(sl_resp2)
                                    if sl_id2:
                                        sl_id = sl_id2
                                        sl = new_sl
                                        log.info(
                                            "[RECOVER] %s SL yeniden denendi: sl=%.4f -> id=%s",
                                            sym,
                                            new_sl,
                                            sl_id,
                                        )
                                except Exception as e2:
                                    log.warning(
                                        "[RECOVER] %s SL yeniden deneme de basarisiz: %s",
                                        sym,
                                        e2,
                                    )

                            tp_resp = await self._rest.place_tp_order(
                                sym, sl_side, abs(amt), rounded_tp
                            )
                            tp_id = extract_order_id(tp_resp)
                            # TP basarisizsa (SL ile ayni sebep) mevcut fiyata gore yeni TP dene
                            if not tp_id:
                                log.warning(
                                    "[RECOVER] %s TP basarisiz (tp=%.4f), mevcut fiyata gore yeniden hesaplaniyor...",
                                    sym,
                                    tp,
                                )
                                try:
                                    cur_px = await self._rest.estimate_market_price(sym)
                                    if direction == "long":
                                        new_tp = await self._rest.apply_price_precision(
                                            sym, max(rounded_tp, cur_px * 1.01)
                                        )
                                    else:
                                        new_tp = await self._rest.apply_price_precision(
                                            sym, min(rounded_tp, cur_px * 0.99)
                                        )
                                    tp_resp2 = await self._rest.place_tp_order(
                                        sym, sl_side, abs(amt), new_tp
                                    )
                                    tp_id2 = extract_order_id(tp_resp2)
                                    if tp_id2:
                                        tp_id = tp_id2
                                        tp = new_tp
                                        log.info(
                                            "[RECOVER] %s TP yeniden denendi: tp=%.4f -> id=%s",
                                            sym,
                                            new_tp,
                                            tp_id,
                                        )
                                except Exception as e2:
                                    log.warning(
                                        "[RECOVER] %s TP yeniden deneme de basarisiz: %s",
                                        sym,
                                        e2,
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

                    if not sl_id:
                        # SL hicbir sekilde kurulamadi. Pozisyonu "korumali" gibi
                        # envantere alip yoluna devam ETME — acil market kapanisi yap.
                        log.critical(
                            "[RECOVER] %s SL hicbir sekilde kurulamadi -- pozisyon "
                            "korumasiz kalmasin diye ACIL MARKET KAPANISI yapiliyor (qty=%.6f)",
                            sym,
                            abs(amt),
                        )
                        self._pl(
                            sym,
                            "recover_emergency_close",
                            f"\U0001f6a8 {direction.upper()} @ {entry:.2f} | SL kurulamadi -> ACIL KAPANIS tetiklendi",
                        )
                        close_result = None
                        close_error = None
                        try:
                            close_side = "SELL" if direction == "long" else "BUY"
                            close_result = await self._rest.place_market_order(
                                sym, close_side, abs(amt), reduce_only=True
                            )
                        except Exception as e:
                            close_error = str(e)

                        if not close_result:
                            # market order basarisizsa closePosition ile dene
                            log.warning(
                                "[RECOVER] %s market close basarisiz, closePosition deneniyor...",
                                sym,
                            )
                            try:
                                forced = await self._rest.place_force_close_order(
                                    sym, close_side, direction
                                )
                                if forced:
                                    log.info(
                                        "[RECOVER] %s closePosition kabul edildi", sym
                                    )
                                    close_result = {"closePosition": True}
                            except Exception as e2:
                                close_error = (
                                    f"{close_error or ''} + closePosition: {e2}"
                                )

                        if close_result:
                            if tp_id:
                                try:
                                    await self._rest.cancel_order(
                                        tp_id,
                                        sym,
                                        reason="recover_emergency_close",
                                        is_algo=True,
                                    )
                                except Exception:
                                    pass
                            continue

                        # place_market_order basarisiz: ya exception atti ya da
                        # {} dondu (minQty/minNotional/POST hatasi -- exception
                        # ATMAZ). Ikisinde de pozisyon Binance'de hala acik
                        # olabilir. "kapandi" varsayip continue ETME --
                        # pozisyonu (korumasiz da olsa) active_trades'e alip
                        # state'te birak ki ghost/orphan taramasi ve bir
                        # sonraki recover_positions() dongusu bunu tekrar
                        # yakalayabilsin.
                        reason = close_error or "place_market_order bos dict ({}) dondu"
                        log.critical(
                            "[RECOVER] %s ACIL KAPANIS BASARISIZ -- MANUEL MUDAHALE GEREKLI: %s",
                            sym,
                            reason,
                        )
                        if not quiet:
                            self._pl(
                                sym,
                                "recover_emergency_close_failed",
                                f"\U0001f6a8\U0001f6a8 {sym}: ACIL KAPANIS BASARISIZ -- HEMEN MANUEL KONTROL ET: {reason}",
                            )
                        if existing:
                            existing["sl"] = sl
                            existing["tp"] = tp
                            existing["sl_order_id"] = ""
                            existing["tp_order_id"] = tp_id
                        else:
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
                                sl_order_id="",
                                tp_order_id=tp_id,
                            )
                        continue

                    if existing:
                        existing["sl_order_id"] = sl_id
                        existing["tp_order_id"] = tp_id
                    else:
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
                    protection_note = "" if tp_id else " (TP kurulamadi, sadece SL var)"
                    if not quiet:
                        self._pl(
                            sym,
                            "recover",
                            f"\U0001f512 {direction.upper()} @ {entry:.2f} | SL={sl:.2f} (id={sl_id}) TP={tp:.2f} (id={tp_id}){protection_note} kuruldu",
                        )
        except Exception as e:
            if not quiet:
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

    def _known_protection_ids(self) -> set[str]:
        """Aktif trade'lerin sahip olabileceği tüm SL/TP order ID
        kaynaklarını toplar: current, prev, history, (varsa) pending.
        Geçiş halindeki (henüz cancel edilmemiş eski / henüz confirm
        edilmemiş yeni) ID'lerin orphan sanılmaması içindir (A5).

        Patch Set 3: ProtectionLifecycleService varsa karar ona delege
        edilir. Yoksa eski inline logic korunur.
        """
        if self._protection is not None:
            all_ids: set[str] = set()
            for t in self._active_trades.values():
                all_ids |= self._protection.known_ids(t)
            return all_ids

        known_ids: set[str] = set()
        for t in self._active_trades.values():
            for k in (
                "sl_order_id",
                "tp_order_id",
                "sl_order_id_prev",
                "tp_order_id_prev",
                "pending_sl_order_id",
                "pending_tp_order_id",
            ):
                oid = t.get(k)
                if oid:
                    known_ids.add(str(oid))
            for k in ("sl_order_id_history", "tp_order_id_history"):
                for oid in t.get(k) or []:
                    if oid:
                        known_ids.add(str(oid))
        return known_ids

    async def reconcile_orphan_orders(self) -> None:
        """Binance'teki acik tum emirleri tara, bot'un bildigi
        trade'lere ait olmayanlari iptal et (crash sonrasi birikme onlenir).

        Patch Set 3: Transition guard ProtectionLifecycleService'e
        delege edilir (varsa)."""
        if not cfg.BINANCE_API_KEY:
            return

        for sym in self._symbols:
            trade = self._active_trades.get(sym)
            if trade is not None:
                if self._protection is not None:
                    if self._protection.should_skip_reconcile(trade):
                        log.info(
                            "[ORPHAN] %s status=%s — orphan sweep bu sembolde atlaniyor",
                            sym,
                            trade.get("status"),
                        )
                        continue
                elif trade.get("status") not in UNRESTRICTED_STATUSES:
                    log.info(
                        "[ORPHAN] %s status=%s — orphan sweep bu sembolde atlaniyor",
                        sym,
                        trade.get("status"),
                    )
                    continue
            known_ids = self._known_protection_ids()
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
