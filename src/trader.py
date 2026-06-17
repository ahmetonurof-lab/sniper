"""
live_executor.py — NEXUS V2
────────────────────────────
Gerçek exchange API'leri ile emir gönderme modülü.

Sorumluluklar
─────────────
  • Sinyal motorundan gelen TradeParams'i alıp MARKET emir + SL/TP göndermek
  • Pozisyon sorgulama, kapatma
  • ISOLATED margin ayarlama
  • Cooldown, duplicate emir koruması
"""

import asyncio
import logging
import time
from typing import Any

import config
from monitor import update_fill, update_order, update_reject

# Sembol bazlı asenkron kilit — race condition önleyici
# [FIX-6] Init zamanında tüm semboller için Lock önceden oluştur,
# setdefault() ile race condition engellenir.
trade_locks: dict[str, asyncio.Lock] = {s: asyncio.Lock() for s in config.SYMBOLS}

log = logging.getLogger("nexus.live_executor")

DEFAULT_COOLDOWN_SECONDS = 2.0


class ExchangeClient:
    """
    BinanceHTTPClient üzerine ince async sarmalayıcı.
    Tüm metodlar asyncio uyumludur (senkron HTTP çağrılarını
    run_in_executor ile sarar).
    """

    def __init__(self, http_client):
        self.http = http_client

    # ── Market bilgisi ───────────────────────────

    def _get_market_info(self, symbol: str) -> dict | None:
        """Sembol için market bilgisi döner (önbellekten)."""
        try:
            return self.http.get_symbol_info(symbol)
        except Exception as e:
            log.warning("Market info alınamadı %s: %s", symbol, e)
            return None

    # ── Precision ────────────────────────────────

    def _apply_amount_precision(self, symbol: str, amount: float) -> float:
        """Amount'u step size'a göre yuvarla."""
        if amount is None or amount == 0:
            return amount
        try:
            return self.http.apply_amount_precision(symbol, amount)
        except Exception as e:
            log.warning("Amount precision ayarlanamadı %s %.8f: %s", symbol, amount, e)
            return amount

    def _apply_price_precision(self, symbol: str, price: float) -> float:
        """Price'ı tick size'a göre yuvarla."""
        if price is None or price == 0:
            return price
        try:
            return self.http.apply_price_precision(symbol, price)
        except Exception as e:
            log.warning("Price precision ayarlanamadı %s %.8f: %s", symbol, price, e)
            return price

    def _validate_min_amount(self, symbol: str, amount: float, original_amount: float | None = None) -> bool:
        """Amount'un minimum gereksinimi karşılayıp karşılamadığını kontrol eder."""
        # Precision sonrası 0'a düşen ama aslında >0 olan miktar → hata
        if amount is not None and amount == 0 and original_amount is not None and original_amount > 0:
            log.warning(
                "[PRECISION] %s amount=%.8f precision sonrası 0'a düştü (orijinal=%.8f)",
                symbol,
                amount,
                original_amount,
            )
            return False
        if amount is None:
            return True
        if amount == 0:
            log.warning(
                "[PRECISION] %s amount=0 (orijinal=%s), emir iptal",
                symbol,
                original_amount,
            )
            return False
        min_qty = self.http.get_min_qty(symbol)
        if min_qty > 0 and amount < min_qty:
            log.warning(
                "[PRECISION] %s amount=%.8f < minimum=%.8f (precision hatasını önler)",
                symbol,
                amount,
                min_qty,
            )
            return False
        return True

    def _normalize_price(self, symbol: str, price: float) -> float:
        """Fiyatı tick size'a göre yuvarla."""
        return self._apply_price_precision(symbol, price)

    # ── Emir işlemleri ───────────────────────────

    async def create_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        price: float | None = None,
        params: dict | None = None,
    ) -> dict[str, Any]:
        """Ham Binance emri gönderir, Binance response dict döner."""
        if amount is not None and amount > 0:
            raw_amount = amount
            amount = self._apply_amount_precision(symbol, amount)
            if not self._validate_min_amount(symbol, amount, raw_amount):
                raise ValueError(f"{symbol} amount={amount} < minimum precision requirement")

        if price is not None and price > 0:
            price = self._apply_price_precision(symbol, price)

        # params içindeki stopPrice'a da precision uygula (SL/TP emirleri)
        if params and "stopPrice" in params and params["stopPrice"] is not None:
            params = dict(params)
            params["stopPrice"] = self._apply_price_precision(symbol, float(params["stopPrice"]))

        log.debug(
            "[ORDER_SUBMIT] %s type=%s side=%s amount=%.8f price=%s params=%s",
            symbol,
            order_type,
            side,
            amount or 0,
            price,
            params,
        )
        log.info("[ORDER-DEBUG] trader.py create_order params=%s amount=%s price=%s", params, amount, price)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.http.create_order(
                symbol=symbol,
                order_type=order_type,
                side=side,
                amount=amount,
                price=price,
                params=params or {},
            ),
        )

    # ── Algo (SL/TP) emirleri ────────────────────────

    async def create_stop_order(
        self,
        symbol: str,
        side: str,
        amount: float,
        stop_price: float,
        order_type: str = "STOP_MARKET",
        params: dict | None = None,
    ) -> dict[str, Any]:
        """STOP_MARKET / TAKE_PROFIT_MARKET emri gönderir (Yeni Algo Uç Noktası ile)."""
        if amount is not None and amount > 0:
            raw_amount = amount
            amount = self._apply_amount_precision(symbol, amount)
            if not self._validate_min_amount(symbol, amount, raw_amount):
                raise ValueError(f"{symbol} amount={amount} < minimum precision requirement")

        stop_price = self._apply_price_precision(symbol, stop_price)

        log.debug(
            "[STOP_ORDER] %s type=%s side=%s qty=%.8f stopPrice=%.8f",
            symbol,
            order_type,
            side,
            amount or 0,
            stop_price,
        )

        # Yeni API zorunlu parametreleri (algoType artık create_algo_order'da ekleniyor)
        base_params: dict[str, Any] = {"closePosition": True, "timeInForce": "GTC"}
        if params:
            # reduceOnly'yi closePosition ile değiştiriyoruz
            if "reduceOnly" in params:
                del params["reduceOnly"]
                base_params["closePosition"] = True
            base_params.update(params)

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.http.create_algo_order(
                symbol=symbol,
                order_type=order_type.upper(),
                side=side.upper(),
                amount=amount,
                stop_price=stop_price,
                price=None,
                params=base_params,
            ),
        )

    async def create_algo_order(
        self,
        symbol: str,
        order_type: str,
        side: str,
        amount: float,
        stop_price: float,
        price: float | None = None,
        params: dict | None = None,
    ) -> dict[str, Any]:
        """
        Algo emir gönderir (STOP_MARKET / TAKE_PROFIT_MARKET).
        Binance Futures'ta bu emir tipleri /fapi/v1/algoOrder endpoint'i üzerinden
        gönderilmelidir.
        """
        if amount is not None and amount > 0:
            raw_amount = amount
            amount = self._apply_amount_precision(symbol, amount)
            if not self._validate_min_amount(symbol, amount, raw_amount):
                raise ValueError(f"{symbol} amount={amount} < minimum precision requirement")

        stop_price = self._apply_price_precision(symbol, stop_price)
        if price is not None and price > 0:
            price = self._apply_price_precision(symbol, price)

        log.debug(
            "[ALGO_ORDER] %s type=%s side=%s qty=%.8f stopPrice=%.8f price=%s params=%s",
            symbol,
            order_type,
            side,
            amount or 0,
            stop_price,
            price,
            params,
        )

        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(
            None,
            lambda: self.http.create_algo_order(
                symbol=symbol,
                order_type=order_type,
                side=side,
                amount=amount,
                stop_price=stop_price,
                price=price,
                params=params or {},
            ),
        )

    async def fetch_position(self, symbol: str) -> dict | None:
        """Belirtilen sembol için açık pozisyon varsa döner, yoksa None."""
        loop = asyncio.get_running_loop()
        try:
            positions = await loop.run_in_executor(None, lambda: self.http.get_positions(symbol))
            for pos in positions:
                amt = float(pos.get("positionAmt", 0))
                if amt != 0:
                    pos["contracts"] = amt  # LiveExecutor uyumluluğu
                    return pos
            return None
        except Exception:
            return None

    async def close_position(self, symbol: str) -> bool:
        """Açık pozisyonu MARKET emirle kapatır."""
        try:
            loop = asyncio.get_running_loop()
            pos = await self.fetch_position(symbol)
            if not pos:
                log.warning("close_position: %s için açık pozisyon yok", symbol)
                return False
            side = "SELL" if float(pos.get("contracts", 0)) > 0 else "BUY"
            amount = abs(float(pos.get("contracts", 0)))
            await loop.run_in_executor(
                None,
                lambda: self.http.create_order(
                    symbol=symbol,
                    order_type="MARKET",
                    side=side,
                    amount=amount,
                    params={
                        "reduceOnly": True,
                    },
                ),
            )
            log.info("Pozisyon kapatıldı: %s %s %.4f lot", symbol, side.upper(), amount)
            return True
        except Exception as e:
            log.error("close_position hatası %s: %s", symbol, e, exc_info=True)
            update_reject(symbol, reason=f"close_error: {e}")
        return False

    async def set_margin_mode(self, symbol: str, margin_type: str = "ISOLATED") -> bool:
        """Sembol için ISOLATED margin modunu ayarlar."""
        try:
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: self.http.set_margin_mode(symbol, margin_type),
            )
            log.info("Margin modu ayarlandı: %s → %s", symbol, margin_type)
            return True
        except Exception as e:
            err = str(e)
            if "No need to change" in err or "same" in err.lower():
                log.info("Margin modu zaten %s: %s", margin_type, symbol)
                return True
            log.error("Margin modu ayarlanamadı %s: %s", symbol, e)
        return False

    async def cancel_order(self, order: dict, symbol: str) -> bool:
        """
        Emir iptali. Hem normal (orderId/id) hem de algo (algoId) emirlerini destekler.
        Binance raw ve ccxt formatlarını tanır. BinanceHTTPClient.cancel_order()
        zaten hem /fapi/v1/order hem /fapi/v1/algoOrder endpoint'lerini dener.
        """
        order_id = order.get("algoId") or order.get("id") or order.get("orderId")
        if not order_id:
            log.warning("cancel_order: emir ID bulunamadı → %s", order)
            return False
        try:
            log.info(
                "[ORDER_CANCEL] symbol=%s reason=EXECUTOR order_id=%s type=%s",
                symbol,
                order_id,
                order.get("type", "?"),
            )
            loop = asyncio.get_running_loop()
            await loop.run_in_executor(
                None,
                lambda: self.http.cancel_order(symbol, str(order_id)),
            )
            log.info("Emir iptal edildi: %s order_id=%s", symbol, order_id)
            return True
        except Exception as e:
            err_str = str(e)
            if "-2011" in err_str or "Unknown order" in err_str:
                log.info("cancel_order: %s order_id=%s zaten yok (ok)", symbol, order_id)
                return True
            log.warning("cancel_order hatası %s order_id=%s: %s", symbol, order_id, e)
        return False

    # ── Toplu emir iptali ───────────────────────

    async def get_open_orders(self, symbol: str) -> list[dict]:
        """Sembol için tüm açık emirleri döner (normal + algo)."""
        loop = asyncio.get_running_loop()
        return await loop.run_in_executor(None, lambda: self.http.get_all_open_orders(symbol))

    async def cancel_all_orders(self, symbol: str) -> int:
        """
        Semboldeki TÜM açık emirleri iptal eder.
        Dönüş: başarıyla iptal edilen emir sayısı.
        """
        orders = await self.get_open_orders(symbol)
        if not orders:
            return 0

        cancelled = 0
        for order in orders:
            ok = await self.cancel_order(order, symbol)
            if ok:
                cancelled += 1
            await asyncio.sleep(0.05)

        log.info("[CANCEL_ALL] %s → %d/%d emir iptal edildi", symbol, cancelled, len(orders))
        return cancelled


class LiveExecutor:
    def __init__(
        self,
        exchange_client: ExchangeClient,
        cooldown_seconds: float = DEFAULT_COOLDOWN_SECONDS,
    ):
        self.client = exchange_client
        self.cooldown_seconds = cooldown_seconds
        self._last_order_time: dict[str, float] = {}
        self._pending_symbols: set = set()
        self._startup_complete: bool = False

    def mark_startup_complete(self) -> None:
        self._startup_complete = True
        log.info("[EXECUTOR] Startup tamamlandı — normal işlem moduna geçildi")

    def _check_cooldown(self, symbol: str) -> bool:
        last = self._last_order_time.get(symbol, 0)
        if time.time() - last < self.cooldown_seconds:
            log.warning("Cooldown aktif: %s, emir atlanıyor", symbol)
            return True
        return False

    def _update_cooldown(self, symbol: str) -> None:
        self._last_order_time[symbol] = time.time()

    async def _wait_for_fill(self, symbol: str, timeout: float = 5.0):
        """Pozisyonun borsada oluşmasını bekle (yüksek volatilitede 5sn)."""
        for _ in range(int(timeout / 0.1)):
            pos = await self.client.fetch_position(symbol)
            if pos and abs(float(pos.get("contracts", 0))) > 0:
                return
            await asyncio.sleep(0.1)
        log.warning("[FILL] %s pozisyon onaylanamadı (%.0fs timeout), yine de devam", symbol, timeout)

    async def _safe_create_order(self, payload: dict, retries: int = 2) -> dict | None:
        for i in range(retries):
            try:
                resp = await self.client.create_order(**payload)
                return resp
            except Exception as e:
                err_str = str(e)
                if "-2021" in err_str and i < retries - 1:
                    log.warning(
                        "[RETRY] %s -2021 hatası, tekrar deneniyor (%d/%d)",
                        payload.get("symbol", "?"),
                        i + 2,
                        retries,
                    )
                    await asyncio.sleep(0.3)
                    continue
                if i == retries - 1:
                    log.error(
                        "[ORDER] %s tüm retry'ler başarısız: %s",
                        payload.get("symbol", "?"),
                        e,
                    )
                raise
        return None

    async def send_order(
        self,
        trade_params,
        stop_loss: float | None = None,
        take_profit: float | None = None,
        entry_order_type: str = "MARKET",
        current_price: float | None = None,
        stop_offset_pct: float = 0.0,
        partial: bool = False,
    ) -> dict | None:
        if hasattr(trade_params, "symbol"):
            symbol = trade_params.symbol
            direction = trade_params.direction
            lot = trade_params.lot
            sl_price = stop_loss if stop_loss is not None else getattr(trade_params, "sl", None)
            tp_price = take_profit if take_profit is not None else getattr(trade_params, "tp", None)
        else:
            symbol = trade_params.get("symbol")
            direction = trade_params.get("direction")
            lot = trade_params.get("lot")
            sl_price = stop_loss if stop_loss is not None else trade_params.get("sl")
            tp_price = take_profit if take_profit is not None else trade_params.get("tp")

        # Asenkron kilit — aynı sembol için race condition önleme
        lock = trade_locks.get(symbol)
        if lock is None:
            # Bilinmeyen sembol (config.SYMBOLS dışı) → fallback Lock
            log.warning("[SEND] Bilinmeyen sembol için fallback lock: %s", symbol)
            lock = asyncio.Lock()

        log.info(
            "[SEND-DEBUG] %s direction=%s lot=%s sl=%s tp=%s",
            symbol,
            direction,
            lot,
            sl_price,
            tp_price,
        )

        if not symbol or not direction or not lot:
            log.error(
                "send_order: eksik parametreler symbol=%s direction=%s lot=%s",
                symbol,
                direction,
                lot,
            )
            return None

        async with lock:
            # Cooldown + duplicate kontrolleri async with lock içinde —
            # race condition önlenir, aynı sembol için iki kere check geçilip
            # duplicate order gönderilmesi engellenir.
            if self._check_cooldown(symbol):
                update_reject(symbol, reason="cooldown")
                return None

            if symbol in self._pending_symbols:
                log.warning(
                    "send_order: %s için zaten bekleyen emir var (pending) → atlanıyor",
                    symbol,
                )
                update_reject(symbol, reason="pending_duplicate")
                return None

            existing = await self.client.fetch_position(symbol)
            if existing is not None:
                log.warning("send_order: %s için zaten açık pozisyon var → emir atlanıyor", symbol)
                update_reject(symbol, reason="duplicate_position")
                return None

            self._pending_symbols.add(symbol)

            side = "BUY" if direction.lower() == "long" else "SELL"

            try:
                if not config.IS_TESTNET:
                    await self.client.set_margin_mode(symbol, "ISOLATED")

                # Benzersiz clientOrderId
                client_order_id = f"choch_{symbol}_{int(time.time() * 1000)}"

                # ── STOP_MARKET entry (slippage cut) ────────────────
                if entry_order_type.upper() == "STOP_MARKET":
                    trigger = trade_params.entry if hasattr(trade_params, "entry") else 0.0
                    if current_price and trigger > 0:
                        if direction.lower() == "long":
                            trigger = max(trigger, current_price * (1.0 + max(0.0, stop_offset_pct)))
                        else:
                            trigger = min(trigger, current_price * (1.0 - max(0.0, stop_offset_pct)))
                    try:
                        stop_resp = await self.client.create_order(
                            symbol=symbol,
                            order_type="STOP_MARKET",
                            side=side,
                            amount=lot,
                            price=None,
                            stop_price=self.client._apply_price_precision(symbol, trigger),
                            params={"newClientOrderId": f"{client_order_id}_entry_stop"},
                        )
                        order_id = str(stop_resp.get("orderId") or stop_resp.get("clientOrderId") or "")
                        log.info(
                            "[ENTRY] STOP-MARKET %s trigger=%.5f id=%s",
                            symbol,
                            trigger,
                            order_id,
                        )
                        order = {
                            "symbol": symbol,
                            "side": direction,
                            "entry_order_id": order_id,
                            "entry_type": "STOP_MARKET",
                            "partial": partial,
                            "protection_missing": True,
                        }
                        self._pending_symbols.discard(symbol)
                        self._update_cooldown(symbol)
                        update_order(symbol)
                        log.info(
                            "STOP-MARKET EMİR GÖNDERİLDİ | %s %s lot=%.4f trigger=%.5f | id=%s",
                            symbol,
                            side,
                            lot,
                            trigger,
                            order_id,
                        )
                        return order
                    except Exception as e:
                        self._pending_symbols.discard(symbol)
                        log.error("[ENTRY] STOP-MARKET error %s: %s", symbol, e)
                        update_reject(symbol, reason=f"entry_stop_error: {e}")
                        return None

                # 1. Ana Giriş Emri (Market)
                order = await self.client.create_order(
                    symbol=symbol,
                    order_type="MARKET",
                    side=side,
                    amount=lot,
                    price=None,
                    params={"newClientOrderId": client_order_id},
                )
                order["entry_price"] = float(order.get("avgPrice", 0) or 0)
                self._update_cooldown(symbol)
                log.info(
                    "MARKET EMİR GÖNDERİLDİ | %s %s lot=%.4f | avgPrice=%.5f | order_id=%s",
                    symbol,
                    side,
                    lot,
                    order["entry_price"],
                    order.get("orderId") or order.get("id", "?"),
                )
                update_order(symbol)
                update_fill(symbol)

                await self._wait_for_fill(symbol)

                # ⏱ Entry sonrası kısa bekleme — Binance'in SL/TP'yi kabul etmesi için
                await asyncio.sleep(0.5)

                sl_side = "SELL" if side == "BUY" else "BUY"
                sl_order_id: str | None = None
                tp_order_id: str | None = None

                # ── TP pre‑validation: pozisyonun güncel markPrice'ını al ──
                # KALDIRILDI — markPrice kontrolleri API seviyesinde -2021
                # hatasıyla zaten ele alınıyor. Bu blok gereksiz I/O üretiyor
                # ve yarış durumlarında yanlış negatif veriyordu.
                # tp_mark_price kullanımı da aşağıdan kaldırıldı.

                # 2. Stop Loss Emri
                if sl_price is not None:
                    sl_success = False
                    sl_order_id_prefix = f"{client_order_id}_sl"

                    for sl_attempt in range(2):
                        try:
                            # Artık doğrudan kendi yazdığın create_algo_order metoduna gidiyoruz
                            sl_resp = await self.client.create_algo_order(
                                symbol=symbol,
                                order_type="STOP_MARKET",
                                side=sl_side,
                                amount=lot,
                                stop_price=sl_price,
                                params={"newClientOrderId": f"{sl_order_id_prefix}_{sl_attempt}"},
                            )

                            sl_order_id = str(
                                sl_resp.get("algoId") or sl_resp.get("clientAlgoId") or sl_resp.get("orderId") or ""
                            )
                            log.info("SL EMİR ✓: %s stopPrice=%.8f algo_id=%s", symbol, sl_price, sl_order_id)
                            sl_success = True
                            break
                        except Exception as e:
                            log.warning("SL hatası %s (deneme %d/2): %s", symbol, sl_attempt + 1, e)
                            if sl_attempt == 0:
                                await asyncio.sleep(0.3)

                    if not sl_success:
                        log.critical("🚨 EMERGENCY CLOSE | %s | SL yazılamadı (2 deneme başarısız)", symbol)
                        emergency_ok = await self.close_position(symbol, reason="emergency_sl_fail")
                        if emergency_ok:
                            log.critical("🚨 EMERGENCY CLOSE BAŞARILI | %s | pozisyon market kapatıldı", symbol)
                        else:
                            log.critical("🚨 EMERGENCY CLOSE BAŞARISIZ | %s | manuel müdahale gerekli!", symbol)
                        raise RuntimeError(f"SL yazılamadı, EMERGENCY CLOSE tetiklendi: {symbol}")

                # 3. Take Profit Emri
                tp_success = False
                if tp_price is not None:
                    tp_order_id_prefix = f"{client_order_id}_tp"

                    for tp_attempt in range(2):
                        try:
                            # Yine kendi create_algo_order metoduna gidiyoruz
                            tp_resp = await self.client.create_algo_order(
                                symbol=symbol,
                                order_type="TAKE_PROFIT_MARKET",
                                side=sl_side,
                                amount=lot,
                                stop_price=tp_price,
                                params={"newClientOrderId": f"{tp_order_id_prefix}_{tp_attempt}"},
                            )

                            tp_order_id = str(
                                tp_resp.get("algoId") or tp_resp.get("clientAlgoId") or tp_resp.get("orderId") or ""
                            )
                            log.info("TP EMİR ✓: %s stopPrice=%.8f algo_id=%s", symbol, tp_price, tp_order_id)
                            tp_success = True
                            break
                        except Exception as e:
                            err_str = str(e)
                            if "-2021" in err_str:
                                log.warning(
                                    "🟡 [TP] %s TP (%.5f) -2021 hemen tetiklenirdi" " (mark varsa) — atlanıyor",
                                    symbol,
                                    tp_price,
                                )
                                tp_order_id = "skipped_2021"
                                tp_success = True
                                break
                            log.warning("TP hatası %s (deneme %d/2): %s", symbol, tp_attempt + 1, e)
                            if tp_attempt == 0:
                                await asyncio.sleep(0.3)

                if not tp_success:
                    log.warning("⚠️ TP YAZILAMADI | %s | pozisyon korumasız kaldı (2 deneme başarısız)", symbol)

                order["sl_order_id"] = sl_order_id
                order["tp_order_id"] = tp_order_id

                if not sl_order_id:
                    log.critical("🚨 SL ORDER ID ALINAMADI | %s | pozisyon korumasız!", symbol)
                if not tp_order_id:
                    log.warning("⚠️ TP ORDER ID ALINAMADI | %s", symbol)

                order["partial"] = partial
                self._pending_symbols.discard(symbol)
                return order
            except Exception as e:
                self._pending_symbols.discard(symbol)
                log.error("send_order başarısız %s %s: %s", symbol, direction, e, exc_info=True)
                update_reject(symbol, reason=f"send_error: {e}")
                return None

    async def close_position(self, symbol: str, reason: str = "manual") -> bool:
        if self._check_cooldown(symbol):
            update_reject(symbol, reason="cooldown_close")
            return False
        self._pending_symbols.discard(symbol)
        success = await self.client.close_position(symbol)
        if success:
            self._update_cooldown(symbol)
            log.info("Pozisyon kapatıldı | %s | sebep=%s", symbol, reason)
        else:
            log.warning("close_position başarısız: %s", symbol)
            update_reject(symbol, reason="close_failed")
        return success

    async def get_position(self, symbol: str) -> dict | None:
        return await self.client.fetch_position(symbol)

    async def sync_risk_state(self, symbol: str) -> bool:
        pos = await self.get_position(symbol)
        return pos is not None

    def reset_cooldown(self, symbol: str) -> None:
        if symbol in self._last_order_time:
            del self._last_order_time[symbol]
