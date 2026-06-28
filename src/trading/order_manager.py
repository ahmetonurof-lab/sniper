"""
order_manager.py — Binance SL/TP emir yönetimi: trailing güncelleme + onarım.

PaperTrader'daki _update_orders() ve _repair_protection() metodlarını kapsar.

Kırmızı çizgiler:
  - Strateji mantığında sıfır değişiklik
  - extract_order_id, time.time() kullanımı aynen kalır
  - Import yolları kırılmayacak
"""

from __future__ import annotations

import logging
import time

import config as cfg
from bot_infra import extract_order_id

log = logging.getLogger("sniper.order_manager")


class OrderManager:
    """Binance SL/TP emir yönetimi: trailing güncelleme + onarım.

    PaperTrader'dan DI ile alır:
      - rest_client: BinanceRESTClient
      - is_live: bool — API key varsa ve bot canlı moddaysa True
    """

    def __init__(self, rest_client, is_live: bool = False):
        self._rest = rest_client
        self._is_live = is_live

    # ── Trailing SL/TP güncelleme ─────────────────────────────

    async def update_trail_orders(self, sym: str, trade: dict) -> bool:
        """Trailing sonrası SL/TP emirlerini güncelle.

        Orijinal _update_orders() ile birebir aynı mantık.
        Başarılıysa trade["sl_order_id"] / trade["tp_order_id"] güncellenir.

        Returns: True if both SL and TP updated successfully.
        """
        if not cfg.BINANCE_API_KEY or not self._is_live:
            return True

        sl_side = "SELL" if trade["side"] == "long" else "BUY"
        qty = trade.get("qty", trade.get("lot", 0))

        old_sl_id = trade.get("sl_order_id", "")
        old_tp_id = trade.get("tp_order_id", "")

        new_sl_id = ""
        new_tp_id = ""
        sl_ok = False
        tp_ok = False

        # ── 1. YENİ SL EMRİNİ AT (ESKİYİ HENÜZ SİLME) ──
        try:
            sl_resp = await self._rest.place_stop_order(
                sym, sl_side, qty, trade["sl"], client_id=f"sl_{sym}_{int(time.time())}"
            )
            new_sl_id = extract_order_id(sl_resp)
            if new_sl_id:
                sl_ok = True
            else:
                log.warning(
                    "[TRAIL] %s SL reject (yeni emir alinamadi) -> eski SL korunuyor",
                    sym,
                )
        except Exception as e:
            log.warning("[TRAIL] %s SL place hatasi: %s -> eski SL korunuyor", sym, e)

        # ── 2. YENİ TP EMRİNİ AT ──
        try:
            tp_resp = await self._rest.place_tp_order(
                sym, sl_side, qty, trade["tp"], client_id=f"tp_{sym}_{int(time.time())}"
            )
            new_tp_id = extract_order_id(tp_resp)
            if new_tp_id:
                tp_ok = True
            else:
                log.warning("[TRAIL] %s TP reject -> eski TP korunuyor", sym)
        except Exception as e:
            log.warning("[TRAIL] %s TP place hatasi: %s -> eski TP korunuyor", sym, e)

        # ── 3. SADECE BAŞARILI OLANLARI STATE'E YAZ VE ESKİLERİ SİL (FIX #1) ──
        if sl_ok:
            trade["sl_order_id"] = new_sl_id
            if old_sl_id:
                try:
                    await self._rest.cancel_order(
                        old_sl_id, sym, reason="trail_update", is_algo=True
                    )
                except Exception as e:
                    log.warning(
                        "[CANCEL] %s eski SL iptal hatasi (id=%s): %s",
                        sym,
                        old_sl_id,
                        e,
                    )

        if tp_ok:
            trade["tp_order_id"] = new_tp_id
            if old_tp_id:
                try:
                    await self._rest.cancel_order(
                        old_tp_id, sym, reason="trail_update", is_algo=True
                    )
                except Exception as e:
                    log.warning(
                        "[CANCEL] %s eski TP iptal hatasi (id=%s): %s",
                        sym,
                        old_tp_id,
                        e,
                    )

        if not (sl_ok and tp_ok):
            log.warning(
                "[TRAIL] %s trailing kismen/tamamen basarisiz (sl=%s, tp=%s) -> eski ID'ler korundu",
                sym,
                sl_ok,
                tp_ok,
            )
            return False

        log.info(
            "[ORDER] %s trailing guncellendi sl=%.2f (id=%s) tp=%.2f (id=%s)",
            sym,
            trade["sl"],
            new_sl_id,
            trade["tp"],
            new_tp_id,
        )
        return True

    # ── Koruma onarımı ────────────────────────────────────────

    async def repair_protection(
        self, sym: str, trade: dict, has_sl: bool, has_tp: bool
    ) -> None:
        """Eksik SL/TP emirlerini yeniden kur.

        Orijinal _repair_protection() ile birebir aynı mantık.
        Sadece _register_user_data_callbacks() içindeki WS callback'ten çağrılır.
        """
        if not has_sl and trade.get("sl"):
            sl_side = "SELL" if trade["side"] == "long" else "BUY"
            sl_resp = await self._rest.place_stop_order(
                sym, sl_side, trade["qty"], trade["sl"]
            )
            trade["sl_order_id"] = extract_order_id(sl_resp)
            log.info(
                "[REPAIR] %s SL yeniden kuruldu: %.2f (id=%s)",
                sym,
                trade["sl"],
                trade["sl_order_id"],
            )
        if not has_tp and trade.get("tp"):
            tp_side = "SELL" if trade["side"] == "long" else "BUY"
            tp_resp = await self._rest.place_tp_order(
                sym, tp_side, trade["qty"], trade["tp"]
            )
            trade["tp_order_id"] = extract_order_id(tp_resp)
            log.info(
                "[REPAIR] %s TP yeniden kuruldu: %.2f (id=%s)",
                sym,
                trade["tp"],
                trade["tp_order_id"],
            )
        log.info("[REPAIR] %s onarim tamam", sym)

    # ── Exit temizliği ─────────────────────────────────────────

    async def cleanup_on_exit(self, sym: str, trade: dict, result: str) -> None:
        """Exit sonrası Binance emir temizliği.

        - Karşı koruma emrini iptal et
        - Tetiklenen emrin ID'si yoksa acil piyasa kapanışı yap

        Orijinal _exit_trade() içindeki "if cfg.BINANCE_API_KEY and live" bloğu
        ile birebir aynı mantık.
        """
        if not cfg.BINANCE_API_KEY or not self._is_live:
            return

        try:
            remaining_id = (
                trade.get("tp_order_id") if result == "SL" else trade.get("sl_order_id")
            )
            if remaining_id:
                try:
                    await self._rest.cancel_order(
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
                trade.get("sl_order_id") if result == "SL" else trade.get("tp_order_id")
            )
            if not trigger_id:
                log.warning(
                    "[CLOSE] %s tetiklenen %s emri Binance ID'si olmadigi icin acil market kapanisi yapiliyor...",
                    sym,
                    result,
                )
                mkt_side = "SELL" if trade["side"] == "long" else "BUY"
                try:
                    await self._rest.place_market_order(
                        sym, mkt_side, trade["qty"], reduce_only=True
                    )
                except Exception as e:
                    log.warning("[CLOSE] %s acil kapanis emri hatasi: %s", sym, e)
        except Exception as e:
            log.warning("[CLOSE] %s exit temizleme hatasi: %s", sym, e)
