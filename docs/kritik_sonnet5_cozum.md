Beş hatanın ve yeni bulgunun hepsi tek bir ortak tasarım ilkesiyle çözülüyor: belirsizlik anında sessizce vazgeçmek yerine, mevcut verify_protection/repair_protection gibi zaten var olan doğrulama araçlarını tekrar sorup DAHA FAZLA koruma yönünde karar vermek. Hiçbir yeni mekanizma icat etmiyorum — kod tabanının kendi "kırmızı çizgiler: strateji mantığında sıfır değişiklik" prensibine uyarak var olan fonksiyonları doğru yerde ve doğru sırada çağırıyorum. Üç dosya birbirine bağımlı (Grup A), diğer ikisi bağımsız.

Grup A — Exit ve koruma zinciri (HATA-2 + HATA-4 + gizli bir cleanup_on_exit hatası)
Mantık: Bu üçü tek başına düzeltilemez, çünkü birbirini örtüyor. cancel_all_open_orders'ın market close'dan önce çalışmasının asıl nedenini buldum: cleanup_on_exit(), result=="SL" değilse otomatik olarak "TP tetiklendi" varsayıyor (trigger_id = ... if result=="SL" else trade.get("tp_order_id")). Ama TRAIL_CLOSE gibi sonuçlarda ne SL ne TP tetiklenmiş olur — ikisi de hâlâ borsada açık kalan emirdir. Eski kod bu ikili varsayımla sadece birini iptal ediyordu; muhtemelen bu yüzden birileri "olsun, hepsini baştan silelim" diyip cancel_all_open_orders'ı öne almış — ama bu da HATA-2'yi yarattı. Doğru çözüm, cancel_all_open_orders'ı öne almak değil, cleanup_on_exit'in SL/TP varsayımını düzeltip iptali pozisyon gerçekten kapandıktan sonraya taşımak. Bunu yapınca HATA-4'ü de düzeltmek zorunlu hale geliyor, çünkü "kapanmadı" dalında artık verify_protection'a güveneceğiz ve o fonksiyon bugün boş ID'yi yanlışlıkla "var" sayıyor.
1) src/trading/order_manager.py — verify_protection
Bul:
python        s_id = str(trade.get("sl_order_id", ""))
        t_id = str(trade.get("tp_order_id", ""))
        try:
            orders = await self._rest.get_all_orders(sym)
            open_ids = {str(o.get("algoId") or o.get("orderId") or "") for o in orders}
            sl_present = (not s_id) or (s_id in open_ids)
            tp_present = (not t_id) or (t_id in open_ids)
            return sl_present, tp_present
Bununla değiştir:
python        s_id = str(trade.get("sl_order_id", ""))
        t_id = str(trade.get("tp_order_id", ""))
        # FIX (HATA-4): bos ID artik otomatik "mevcut" sayilmiyor. Trade'in
        # bir sl/tp hedefi varsa (trade["sl"]/["tp"] doluysa) ama ID bosa,
        # bu "hic hedeflenmedi" degil "kayboldu/hic olusmadi" demektir.
        expects_sl = bool(trade.get("sl"))
        expects_tp = bool(trade.get("tp"))
        try:
            orders = await self._rest.get_all_orders(sym)
            open_ids = {str(o.get("algoId") or o.get("orderId") or "") for o in orders}
            sl_present = (s_id in open_ids) if s_id else (not expects_sl)
            tp_present = (t_id in open_ids) if t_id else (not expects_tp)
            return sl_present, tp_present
2) src/trading/order_manager.py — cleanup_on_exit
Bul:
python        try:
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
Bununla değiştir:
python        try:
            sl_id = trade.get("sl_order_id")
            tp_id = trade.get("tp_order_id")

            # FIX: eskiden "result != SL ise TP tetiklendi" varsayiliyordu.
            # TRAIL_CLOSE/TIMEOUT gibi sonuclarda NE SL NE TP tetiklenmis
            # olmaz - ikisi de "kalan" emirdir. Eski kod bu durumda TP'ye hic
            # dokunmuyordu; pozisyon kapandiktan sonra TP borsada asili
            # kaliyor ve bir sonraki trade'i etkileyebiliyordu.
            if result == "SL":
                remaining_ids = [tp_id] if tp_id else []
            elif result == "TP":
                remaining_ids = [sl_id] if sl_id else []
            else:
                remaining_ids = [oid for oid in (sl_id, tp_id) if oid]

            for remaining_id in remaining_ids:
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

            # Acil kapanis SADECE gercek SL/TP tetiklenme senaryosunda
            # anlamli. TRAIL_CLOSE/TIMEOUT zaten bot.py::_exit_trade icinde
            # ayrica market close deniyor, burada tekrarlamaya gerek yok.
            trigger_id = (
                trade.get("sl_order_id") if result == "SL" else trade.get("tp_order_id")
            )
            if result in ("SL", "TP") and not trigger_id:
3) src/bot.py — _exit_trade (iki ayrı değişiklik)
3a — üstteki toptan iptali kaldır:
Bul:
python        # ── Bazı exit tipleri zaten Binance tarafindan kapatilmistir ──
        _exit_already_closed = trade.get("result") in ("SL", "TP", "WS_FALLBACK")

        # ── Önce tüm açık emirleri iptal et (SL/TP çakışmasını önle) ──
        if cfg.BINANCE_API_KEY:
            try:
                await self.order_manager.cancel_all_open_orders(sym)
            except Exception as e:
                log.warning(
                    "[EXIT] %s cancel_all_open_orders hatasi (devam): %s", sym, e
                )

        # ── Pozisyon kapatma (reduceOnly market) — SL/TP ile kapandıysa atla ──
        if cfg.BINANCE_API_KEY and not _exit_already_closed:
Bununla değiştir:
python        # ── Bazı exit tipleri zaten Binance tarafindan kapatilmistir ──
        _exit_already_closed = trade.get("result") in ("SL", "TP", "WS_FALLBACK")

        # FIX (HATA-2): cancel_all_open_orders BURADAN KALDIRILDI. Eskiden
        # pozisyon fiilen kapanmadan ONCE tum SL/TP korumasini iptal
        # ediyordu; asagidaki market close 5 denemede basarisiz olursa
        # pozisyon tamamen korumasiz kaliyordu. SL/TP artik SADECE pozisyon
        # gercekten kapandiktan SONRA cleanup_on_exit() ile iptal ediliyor.
        # reduceOnly zaten net yon degistirmeyi engelledigi icin bu sira
        # guvenli.

        # ── Pozisyon kapatma (reduceOnly market) — SL/TP ile kapandıysa atla ──
        if cfg.BINANCE_API_KEY and not _exit_already_closed:
3b — "kapanmadı" dalını onarımla değiştir:
Bul:
python            if not pos_closed:
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
                # Pozisyon fiilen KAPANMADI -> yukarida hesaplanan PNL hayali.
                # Geri almazsak gercek kapanis oldugunda PNL cift sayilir.
                self._available_balance -= pnl
                # peak_equity de bu hayali PNL ile sismis olabilir (drawdown %
                # hesabini bozar, circuit breaker'i gereksiz yere tetikleyebilir).
                # Baska bir islem araya girip zirveyi gercekten yukselttiyse
                # (deger artik bizim yazdigimizdan farkliysa) DOKUNMA.
                if abs(self.risk_mgr.peak_equity - _balance_after_fictional_pnl) < 1e-9:
                    self.risk_mgr.peak_equity = _peak_before_exit
                    self.risk_mgr._save_state()
                trade["sl_order_id"] = ""
                trade["tp_order_id"] = ""
                trade["result"] = None
                self.active_trades[sym] = trade
                return
Bununla değiştir:
python            if not pos_closed:
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
                self._available_balance -= pnl
                if abs(self.risk_mgr.peak_equity - _balance_after_fictional_pnl) < 1e-9:
                    self.risk_mgr.peak_equity = _peak_before_exit
                    self.risk_mgr._save_state()

                # FIX (HATA-2 + HATA-4): ID'leri artik BOSALTMIYORUZ.
                # cancel_all yukarida calismadigi icin eski SL/TP muhtemelen
                # hala Binance'te gecerli. Once GERCEKTEN dogrula, sadece
                # eksikse onar.
                try:
                    sl_present, tp_present = await self.order_manager.verify_protection(
                        sym, trade
                    )
                except Exception as e:
                    log.critical(
                        "[CRITICAL] %s koruma dogrulamasi da basarisiz: %s", sym, e
                    )
                    sl_present, tp_present = False, False

                if not sl_present or not tp_present:
                    log.critical(
                        "[CRITICAL] %s pozisyon acik VE koruma eksik (sl=%s tp=%s) — acil onarim deneniyor",
                        sym,
                        sl_present,
                        tp_present,
                    )
                    try:
                        await self.order_manager.repair_protection(
                            sym, trade, has_sl=sl_present, has_tp=tp_present
                        )
                    except Exception as e:
                        log.critical(
                            "[CRITICAL] %s acil onarim da basarisiz — MANUEL MUDAHALE SART: %s",
                            sym,
                            e,
                        )
                else:
                    log.warning(
                        "[EXIT] %s market close basarisiz ama SL/TP hala aktif — pozisyon korumali kaldi, sonraki bar'da tekrar denenecek",
                        sym,
                    )

                trade["result"] = None
                self.active_trades[sym] = trade
                return

HATA-5 — Orphan sweep race
Mantık: known_ids fonksiyon başında tek seferlik, senkron bir küme olarak kuruluyor; sonra çok-sembollü REST taraması (birden fazla await) sürüyor. Bu pencere açıkken başka bir sembolün trailing güncellemesi yeni bir SL/TP ID'si yazabiliyor ve o ID eski snapshot'ta olmadığı için "orphan" sanılıp iptal edilebiliyor. Kilit (lock) eklemek yerine — ki bu daha invaziv ve gecikme riski taşır — known_ids'i her sembol taramasından hemen önce ve her iptalden hemen önce bellek içi, ucuz bir işlemle tazeliyorum. Bu, yarış penceresini "tüm taramanın süresi"nden "tek bir iptal çağrısının süresi"ne indiriyor.
src/trading/recovery_manager.py — reconcile_orphan_orders
Bul:
python    async def reconcile_orphan_orders(self) -> None:
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
Bununla değiştir:
python    def _known_protection_ids(self) -> set[str]:
        """active_trades icindeki tum sl/tp_order_id'lerin GUNCEL anlik
        goruntusu. Bellek ici, REST cagrisi yok — ucuz oldugu icin taramanin
        her adiminda tekrar cagirmak guvenlidir."""
        ids: set[str] = set()
        for t in self._active_trades.values():
            for k in ("sl_order_id", "tp_order_id"):
                oid = t.get(k)
                if oid:
                    ids.add(str(oid))
        return ids

    async def reconcile_orphan_orders(self) -> None:
        """Binance'teki acik tum emirleri tara, bot'un bildigi
        trade'lere ait olmayanlari iptal et (crash sonrasi birikme onlenir).

        FIX (HATA-5): known_ids artik fonksiyon basinda tek seferlik
        kurulmuyor — her sembol taramasindan ve her iptalden hemen once
        taze alinip TOCTOU pencersi daraltiliyor.
        """
        if not cfg.BINANCE_API_KEY:
            return

        for sym in self._symbols:
            try:
                orders = await self._rest.get_all_orders(sym)
            except Exception:
                continue

            known_ids = self._known_protection_ids()

            for o in orders:
                oid = str(o.get("orderId") or o.get("algoId") or "")
                if not oid or oid in known_ids:
                    continue
                # Iptalden hemen once tekrar kontrol et.
                if oid in self._known_protection_ids():
                    continue
                is_algo = "algoId" in o
(Bu satırdan sonrası — cancel_id = ... ve devamı — aynen kalıyor, dokunmuyorum.)

HATA-1 — Trailing count görünürlüğü
Mantık: Burada önce yanlış varsayımımı düzeltmem lazım: SL ve TP ikisi de başarısız olduğunda hiçbir alan değişmiyor (sl, tp, trailing_count hepsi eski değerinde kalıyor) — yani state kendi içinde tutarlı, "yanlış" değil. Asıl eksik görünürlük: art arda başarısızlıklar sessizce log.warning'e gömülüyor, kimse fark etmiyor. Bu yüzden sayacın artış koşulunu değiştirmiyorum (o zaten doğru), bunun yerine ardışık tam-başarısızlığı ayrıca sayıp bir eşikten sonra critical'a yükseltiyorum.
src/trading/order_manager.py — __init__ ve update_trail_orders
Bul:
python    def __init__(self, rest_client, is_live: bool = False):
        self._rest = rest_client
        self._is_live = is_live
Bununla değiştir:
python    def __init__(self, rest_client, is_live: bool = False):
        self._rest = rest_client
        self._is_live = is_live
        self._trail_fail_streak: dict[str, int] = {}  # FIX (HATA-1): ardisik tam basarisizlik sayaci
Bul:
python        if sl_ok or tp_ok:
            trade["trailing_count"] = new_trail_count

        if not (sl_ok and tp_ok):
Bununla değiştir:
python        if sl_ok or tp_ok:
            trade["trailing_count"] = new_trail_count
            self._trail_fail_streak[sym] = 0
        else:
            # FIX (HATA-1): trailing_count'u degistirmiyoruz - state zaten
            # tutarli. Eksik olan gorunurluktu: ust uste basarisizlik artik
            # bir esikten sonra critical'a yukseliyor.
            streak = self._trail_fail_streak.get(sym, 0) + 1
            self._trail_fail_streak[sym] = streak
            if streak >= 3:
                log.critical(
                    "[TRAIL] %s trailing %d kez ust uste TAMAMEN basarisiz — "
                    "koruma hala eski seviyede (sl=%.4f tp=%.4f), manuel kontrol onerilir",
                    sym,
                    streak,
                    trade.get("sl", 0.0),
                    trade.get("tp", 0.0),
                )

        if not (sl_ok and tp_ok):

Yeni bulgu — WS_FALLBACK state kirliliği
Mantık: trade objesi, _exit_trade'in "aslında stale event, geri al" kararından önce mutasyona uğratılıyor. Guard sonradan geri alsa bile (result=None, trade pop edilmez) mutasyonlar kalıcı. Fonksiyon imzalarını değiştirmeden en az invaziv çözüm: mutasyondan önce eski değerleri sakla, _exit_trade geri alırsa (trade hâlâ active_trades'te ve result yeniden None ise) eski değerleri geri yaz.
src/trading/user_data_handler.py — on_order_update
Bul:
python                    else:
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
Bununla değiştir:
python                    else:
                        # FIX #3: ID eslesmiyor AMA reduceOnly FILLED geldi!
                        if is_reduce_only:
                            # YENİ FIX: mutasyondan once eski degerleri
                            # sakla. _exit_trade WS_FALLBACK guard'i bunun
                            # stale/phantom event oldugunu (pozisyon hala
                            # acik) anlarsa trade'i pop ETMEZ, result'u
                            # None'a geri cevirir — ama bu 5 alan eskiden
                            # geri alinmiyordu, trade aktif kalirken hayalet
                            # fill verisiyle kirlenmis kaliyordu.
                            _prev = {
                                k: trade.get(k)
                                for k in (
                                    "exit_price",
                                    "exit_actual_price",
                                    "exit_actual_qty",
                                    "exit_order_id",
                                    "exit_timestamp",
                                )
                            }

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

                            # Exit geri alindiysa (stale event -> trade hala
                            # active_trades'te, result yeniden None) kirlenen
                            # alanlari geri al.
                            if (
                                sym in _active_trades
                                and _active_trades.get(sym) is trade
                                and trade.get("result") is None
                            ):
                                trade.update(_prev)

                            raise WSFallbackError(sym, oid, s_id, t_id)

Uygulama sırası ve test notu
Önce order_manager.py'ı yamalayın (bot.py'daki HATA-2 düzeltmesi, düzeltilmiş verify_protection'a bağımlı). recovery_manager.py ve user_data_handler.py bağımsız, herhangi bir sırada gidebilir. Canlıya almadan önce testnet'te en az bir zorla-market-close-başarısız senaryosu simüle edip şu yeni log satırlarının doğru tetiklendiğini görün: pozisyon acik VE koruma eksik — acil onarim deneniyor ve market close basarisiz ama SL/TP hala aktif — pozisyon korumali kaldi. Gerçek parayla ilk turda sadece bunları izlemek, sessiz bir regresyonu erken yakalamanın en ucuz yolu.
