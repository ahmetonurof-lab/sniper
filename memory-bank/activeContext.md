# Active Context — Sniper Bot

## Son İşlem: 2026-08-12 — FVG geçerlilik parity + sweep/FVG log düzeltmeleri (baş mühendis direktifi)

- **İnceleme bulgusu (Reis'in sorusu):** Backtest FVG'yi canlıyla aynı `detect_fvgs` ile bulur; geçersiz FVG'yi **far-side close** kuralıyla eler (`get_fvg_status` → INVALIDATED → `FVG_SWEPT`, analyzer_v5.py:135-155/514-519). "İğne atmış" FVG elenmez (o entry sinyali — ACTIVE_ENTRY_ZONE). Canlı da aynı kuralı tetikleme anında uygular (`body_broke_fvg`, retrace_state.py:304-306). **Gerçek tek fark:** canlı `bot.py:484` `fvg_is_alive` kontrolü `bars_15m[:-1]` ile **giriş (mevcut) barını hariç tutuyordu**; backtest `get_fvg_status(cur)` ile mevcut barı kontrol ediyor. Yani canlı, seçimden sonra giriş barının kendisi far-side kapanırsa kaçırıyordu.
- **Karar:** Per-bar re-check zaten büyük ölçüde vardı (fvg_is_alive) — eksik tek nokta giriş barıydı. Kapatıldı + iki log düzeltmesi.
- **Ne yapıldı:**
  - **Fix A (parity):** `bot.py:484` `fvg_is_alive(...)` çağrısı `bars_15m[:-1]` → `bars_15m` (giriş barı dahil). Giriş barının kapanışı FVG far-side kırarsa (FVG_SWEPT) backtest gibi entry iptal + `rsm.reset()`. `_try_entry` `entry_price=current.close` olduğu için mevcut bar kontrolü anlamlı.
  - **Fix B1 (sweep log):** `console_reporter.display_sweep_status` — `daily_bias != NEUTRAL` iken (sweep zaten olmuş, flag tüketilmiş) artık `🟢 SWEEP: TAMAMLANDI | 🟩LONG bias | ... FVG bekleniyor` yazar, **BEKLENIYOR değil**. Yalnızca `daily_bias == NEUTRAL` (gerçek bekleme) → `SWEEP: BEKLENIYOR`. (ARBUSDT'nin yanlış "BEKLENIYOR" logu düzeldi.)
  - **Fix B2 (FVG_SCAN log):** `display_fvg_status` — RSM **IDLE** iken (henüz sweep yok) artık `FVG BULUNAMADI` basmıyor, satırı temizliyor (tarama sırası gelmemiş). `SWEEP_DETECTED` **ve** `BIAS_LOCKED` → `FVG ARANIYOR...`; `TRIGGER_READY` → `HAZIR`. (UNIUSDT'nin "sweep beklerken FVG telaşı" logu düzeldi.)
- **Reis'in sorduğu:** "CBDR bias belirlenmiş modda sweep ne yazacak?" → **`SWEEP: TAMAMLANDI` + bias + FVG bekleniyor**. BEKLENIYOR yalnızca bias henüz yokken.
- **Doğrulama:** test_signal_engine+retrace_state+snapshot+exit_lifecycle **107/107**; yeni test_console_reporter **7/7**; test_snapshot 24/24. Pre-existing fail seti DEĞİŞMEDİ (test_bot bayat 13, test_session TestCheckCbdrSweep 2). IDLE'da FVG_SCAN basılmadığı captured stdout'da doğrulandı.
- **Commit:** bu commit — `fix: FVG gecerlilik parity (giris bari dahil) + sweep/FVG log duzeltmeleri (TAMAMLANDI / IDLE sessiz)`.
- **Sıradaki:** canlı(paper) gözlem — bias belirlenmiş günlerde log "SWEEP: TAMAMLANDI" göstermeli; FVG taraması yalnızca sweep/FVG sırasında.

---

## Son İşlem: 2026-08-11 — SNAPSHOT FVG bandı renk düzeltmesi + smoke test (baş mühendis direktifi)

- **Kapsam netleştirme (Reis kararı — soruldu):** Direktif FVG kutusu + CE çizgisini "çizilmiyorsa ekle" diye koşullandırıyordu; incelemede bunların ZATEN `chart_template.html`'de çizildiği görüldü (satır 400-419: `rangedBand` + `rangedHLine(ce,...)`; marker 214-222). Grafik motoru matplotlib/mplfinance değil — **TradingView lightweight-charts JS (HTML template)**; `capture_snapshot` Python'da hiç çizim yapmıyor, payload (`fvgTop/fvgBottom/fvgDirection/fvgBarIndex`) ile template'e veriyor. Bu alanlar `models.ActiveTrade` (526-529) + `_try_entry` (bot.py 1038-1041, 1058-1061) tarafından set ediliyor.
- **Karar:** "Renk düzelt + smoke test" seçildi (kutu uzantısı entry+12'de kaldı, tüm-FVG kapsamı dışı — yalnızca tetikleyici FVG, direktif zaten öyle netleşmişti).
- **Ne yapıldı:**
  - `chart_template.html` — FVG band fill/stroke + CE çizgisi bearish **turuncu → kırmızı** `rgba(248,81,73,...)` (SL rengiyle tutarlı); bullish yeşil `rgba(63,185,80,...)` kaldı. FVG circle marker'ı (#ffa657) minimal kapsam gereği dokunulmadı.
  - `tests/test_snapshot.py` +2 smoke test (`TestCaptureSnapshot`): `_fetch_ohlc` async mock'lanır, `_TEMPLATE_PATH`/`_SNAPSHOTS_DIR` tmp_path'e çekilir. (1) FVG alanları dolu trade → dosya üretir, payload'da fvgTop/fvgBottom var; (2) FVG verisi YOK (eski/recovered) → kutu atlanır, dosya yine üretilir (crash yok).
- **Doğrulama:** `test_snapshot.py` **24/24** (22 eski + 2 yeni). `_resolve_fvg_bar_index` dokunulmadı → `TestResolveFvgBarIndex` etkilenmedi. Ruff check+format temiz.
- **Commit:** bu commit — `fix: snapshot FVG band bearish renk kirmizi + capture_snapshot smoke testleri`.
- **Sıradaki:** canlı(paper) gözlem; gerçek trade snapshot'ında bearish FVG kutusunun kırmızı render edildiği doğrulanacak (görsel).

---

## Son İşlem: 2026-08-11 — BİAS KİLİT MODU (BIAS_LOCKED state) uygulandı (baş mühendis kararı, paper-trade)

- **Karar notu (kayıt):** Bu değişikliği ben (Kilo) uyguladım. Reis'e şunların olabileceği konusunda açıkça uyardım:
  1. **Overtrade / kayıp zinciri riski:** Kilit yönü tutulduğu sürece, ters fiyat hareketinde (ör. bullish kilit + düşen fiyat) aynı yönde FVG'lerle ARDIŞIK STOP-LOSS ticareti üretebilir → aynı gün içinde tek yönlü bir dizi kayıp (SEIUSDT senaryosu). Bias tersine dönmediği sürece durdurucu sadece yeni CBDR günü/bias flip'tir.
  2. **Canlı↔backtest parity KIRILIR:** Bu özellik yalnızca canlı(paper) akışta (bot.py + signal_engine.py) çalışır. `backtest-sniper`'ın `analyzer_v5.py`'si entry sonrası `lock_bias()` çağırmaz → offline backtest bu davranışı YANSITMAZ, sonuçlar canlıdan sapar. (Reis: "canlıda backtest yapıyoruz" — bilinçli kabul.)
  3. **Same-FVG tekrarı koruması:** `_locked_from_bar` guard'ı olmasaydı aynı FVG her bar tekrar tetiklenir, run-away re-entry oluşurdu. Guard eklendi (yalnızca kilit noktası SONRASI oluşan FVG).
  4. **Exit yolunda davranış değişikliği:** `exit_lifecycle` artık her kapanışta `rsm.reset()` yerine `rsm.lock_bias()` çağırıyor → her trade kapanışından sonra yeniden giriş kapısı açık. Bu, recovery/manual-close yollarını da etkiler.
- **Reis'in kararı:** Yukarıdaki uyarılara rağmen "telaş edilecek bir durum yok, paper-trade yapıyoruz / canlıda backtest yapıyoruz" diyerek uygulanmasını onayladı. Karar Reis'in, uygulama bana ait.
- **Ne yapıldı (BIAS_LOCKED):**
  - `retrace_state.py`: `RetraceState.BIAS_LOCKED` eklendi; `lock_bias(bar_index=None)` (yön korunur, sweep verileri temizlenir, `_locked_from_bar` set/korunur); `on_bias_fvg()` (kilit yönünde TAZE FVG wick rejection → TRIGGER_READY, sweep gerekmez); `bias_locked`/`locked_direction` property'leri.
  - `signal_engine.py progress_rsm`: BIAS_LOCKED bloğu — bias tersine/NEUTRAL dönerse `reset()` (kiliti kaldır), aksi halde `on_bias_fvg()`.
  - `bot.py _try_entry`: başarılı entry sonrası `rsm.reset()` → `rsm.lock_bias(bar_index=current.index)`.
  - `exit_lifecycle.py`: kapanışta `rsm.reset()` → `rsm.lock_bias()` (guard korunur).
  - Test: `tests/test_signal_engine.py` (yeni, 6 test), `tests/test_retrace_state.py` (+7 TestBiasLock).
- **Doğrulama:** test_retrace_state + test_signal_engine **46/46**; test_exit_lifecycle **60/60**; backtest-sniper `test_cbdr_sweep.py` **4/4** (parity bozulmadı). Ruff check+format temiz; pre-commit tüm hook'lar geçti (mypy bu repo'da regex nedeniyle skip). Pre-existing fail seti değişmedi (test_session 2, test_bot/initial_protection/integration_lifecycle bayat testler).
- **Commit:** bu commit — `feat: bias kilit modu (BIAS_LOCKED) - lock_bias/on_bias_fvg, entry+exit sonrasi kilit (paper-trade, canli-backtest)`.
- **Sıradaki:** canlı(paper) gözlem; kilit yönünde ardışık kayıp görülürse `bias_conflict` guard'ına ek durdurucu (ör. günlük max loss) eklenmesi değerlendirilecek.

---

## Son İşlem: 2026-08-11 — EXIT/RECOVERY LOCK KEY PARİTESİ FIX (baş mühendis direktifi, b3f6761 üstüne)

- **Sorun:** `recovery_manager.py` 3 mutation bloğunda `with self._exit_locks.setdefault(sym, RLock()):` (sym bazlı threading.RLock) kullanıyordu. exit_lifecycle/bot.py/user_data_handler ise `{sym}_{_trade_identity_key(trade)}` bazlı `asyncio.Lock` kullanıyor → **iki taraf hiç çakışmıyordu** (her ikisi de "kilitli" görünüp gerçekte farklı kilitlerdeydi). Recovery ve exit aynı anda aynı trade'e dokunduğunda çift emir/koruma yarışı korunmuyordu.
- **Fix:** `recovery_manager.py` — `from threading import RLock` import'u kaldırıldı; `from trading.exit_lifecycle import _trade_identity_key` eklendi. 3 site × 2 dal (existing update / yeni trade) artık `async with self._exit_locks.setdefault(f"{sym}_{_trade_identity_key(trade)}", asyncio.Lock()):`. Yeni trade dallarında key, `ActiveTrade` TAM kurulduktan (entry_timestamp set) SONRA `_trade_identity_key(new_trade)` ile hesaplanıyor → active_trades'e yazılan aynı nesne üzerinden exit tarafının üreteceği key ile birebir aynı.
- **Test (regresyon, ilk kez):** `tests/test_recovery_manager.py` +2 — `test_new_trade_lock_key_matches_exit_lifecycle` + `test_existing_trade_lock_key_matches_exit_lifecycle`: recovery'nin exit_locks'a koyduğu tek key'in `f"{sym}_{_trade_identity_key(stored_trade)}"` olduğunu ve aynı key'le `setdefault` yapan exit tarafının AYNI `asyncio.Lock` nesnesini aldığını doğrular (set eşitliği + `is` kontrolü).
- **Doğrulama:** test_recovery_manager **18/18** (16 eski + 2 yeni), test_exit_lifecycle **37/37**, importlar OK (`bot`, `trading.recovery_manager`, `trading.exit_lifecycle`). Pre-existing fail seti değişmedi.
- **Commit:** bu commit — `fix: recovery exit_locks trade-key paritesi (RLock -> asyncio.Lock, _trade_identity_key)`.
- **Sıradaki:** deploy sunucuda `git pull --ff-only` + restart (b3f6761 ile aynı pencerede). Sonra Sıra 8-9 (backtest-sniper A6-01/A6-02).

---

## Son İşlem: 2026-08-11 — ÇİFT SL/TP KAZASI KÖK NEDEN KAPANDI (DOTUSDT canlı kanıtı, is_algo fallback fix)

- **Canlı kanıt (sunucu log analizi, `/root/sniper/output/paper_trade.log`):** DOTUSDT-518 short entry 05:15 (SL id=1000000163326245 @0.8206, TP id=1000000163326249 @0.7854 — ikisi de `/fapi/v1/algoOrder` ile açılmış **algo/conditional** emir). 09:45 trail güncellemesinde (`trail#1 sl=0.8051 tp=0.7699`) eski SL/TP iptali **"zaten yok (ok)"** döndü → eski emirler borsada KALDI, yenileri açıldı → **4 açık emir** (2×SL + 2×TP, kullanıcının Binance ekran görüntüsüyle birebir). Recovery `_dedupe_protection_orders` 13 kez (09:45:10→09:59:04) aynı ID'leri "fazla koruma emri" olarak iptal etmeyi denedi, hepsi aynı "zaten yok" bug'ına takıldı.
- **Kök neden:** `cancel_order()` `is_algo=False` iken `DELETE /fapi/v1/order?orderId=...` çağırır. **Algo emirler regular endpoint'te GÖRÜNMEZ — her zaman -2011 "Unknown order" döner.** Eski kod -2011'i `_check_unknown` ile yakalayıp "zaten yok (ok)" diye TRUE dönüyor, `/fapi/v1/algoOrder` fallback'ini HİÇ denemiyordu → eski koruma emirleri hiç iptal edilmiyordu.
- **Kanıt (aynı log):** 10:00:15 trade SL ile kapanınca `exit_close` yolu `is_algo=True` ile AYNI ID'leri **başarıyla** iptal etti ("İPTAL (algo) ... reason=exit_close") — algo endpoint çalışıyor, sorun çağrı tarafındaydı.
- **Fix (3 parça):**
  1. `src/bot_binance.py cancel_order` — non-algo dalda `_check_unknown` short-circuit'i KALDIRILDI; -2011 dahil her regular-endpoint hatasında ÖNCE `/fapi/v1/algoOrder` deneniyor, o da -2011 verirse ancak "zaten yok (ok)". DOGEUSDT fix'ini (f1a84b9) pratikte devre dışı bırakan nokta buydu.
  2. `src/trading/order_manager.py:1178` `_replace_one` — eski koruma emri iptali artık `reason="trail_replace", is_algo=True` (trail-replace yolunun 09:45:02/03 iptalleri buradaydı).
  3. `src/trading/recovery_manager.py:109` `_cancel_except` (dedupe) — `reason="dedupe_extra", is_algo=True`.
- **Test:** `test_bot_binance.py` +2 (`test_cancel_regular_2011_falls_back_to_algo_endpoint` — regular -2011 + algo 200 → True + 2 çağrı; `test_cancel_algo_order_direct` — is_algo=True tek çağrı); `test_order_manager.py` `test_replace_one_cancels_flat_order_id_fallback` yeni imzaya güncellendi.
- **Doğrulama:** test_bot_binance 85/85, order_manager 57/57, recovery_manager 16/16, exit_lifecycle 37/37. Pre-existing fail'ler değişmedi (parity: SOLUSDT/BNBUSDT/AVAXUSDT + `test_trail_syncs_state_and_orphan_recovery_preserve`).
- **Commit:** `b3f6761` — `fix: cift SL/TP kazasi — cancel_order is_algo fallback (DOTUSDT kanitli)`.
- **Sıradaki:** Sıra 8-9 (backtest-sniper A6-01/A6-02). Deploy sunucuda `git pull --ff-only` + restart (canlıda şu an çift emir riski devam ediyordu).

---

## Son İşlem: 2026-08-11 — A3-02, A3-03, A4-02, A4-03 YÜKSEK bulgular fix uygulandı + push edildi (baş mühendis direktifi: yuksek-bug-fix-direktifi-2026-08-11.md)

- **A3-02 (active_trades thread-safety):** `recovery_manager.py`'de per-symbol `RLock` eklendi (`exit_locks` DI ile paylaşıldı). 3 `self._active_trades[sym] = ActiveTrade(...)` ve `existing[...]` mutation blokları `with self._exit_locks.setdefault(sym, RLock()):` içine alındı. `bot.py`'den `self._exit_locks` `RecoveryManager`'a geçirildi.
- **A3-03 (blocking requests.get):** `snapshot/snapshot.py`'de `_fetch_ohlc` `async def` yapıldı, `requests.get` → `asyncio.to_thread` ile sarıldı. `capture_snapshot` da `async def` yapıldı, çağıran `exit_lifecycle.py:771`'de `await` eklendi. Event loop artık bloklanmıyor.
- **A4-02 (orphan sweep sessiz yutma):** `reconcile_orphan_orders` except bloğuna `log.error("[ORPHAN] %s sorgu hatasi, sembol atlandi: %s", sym, e)` eklendi. `_orphan_fail_count` sayaç + 5 ardışık hatada `log_event("orphan_check_persistently_failing", ...)` eklendi.
- **A4-03 (demo API fallback sessizliği):** `bot_binance.py`'deki 3 demo-fallback `except Exception: pass` (market order, SL, TP) → `log.warning("[...] demo API open orders sorgusu hatasi: %s", symbol, e)` eklendi.
- **Commitler:**
  - `b1efbc5` — `fix: A3-02 active_trades thread-safety (per-symbol RLock)`
  - `1727e34` — `fix: A3-03 _fetch_ohlc async + capture_snapshot await`
  - `3857125` — `fix: A4-03 demo API fallback exception logging`
  - Not: A4-02 değişiklikleri A3-02 commit'inde (`recovery_manager.py` orphan fail counter) mevcut — aynı dosyaaynı commit'te birleştirildi.
- **Doğrulama:** recovery_manager suite geçti; snapshot 22/22; exit_lifecycle 37/37; bot_binance 89/89. Pre-existing 2 fail (`test_parity_contract[SOLUSDT]`, `test_trail_syncs_state_and_orphan_recovery_preserve`) dışında 0 yeni kırık.
- **Sıradaki:** Sıra 8-9 (backtest-sniper A6-01/A6-02 — A6-02 için mimari karar bekliyor). Sıra 1-2 (A4-05+A4-08) ve Sıra 3 (A2-01+A2-02) önceki commit'lerde tamamlandı.

---

## Son İşlem: 2026-08-11 — A4-05 + A4-08 YÜKSEK bulgular fix uygulandı (baş mühendis direktifi: yuksek-bug-fix-direktifi-2026-08-11.md)

- **A4-05 (recovery SL/TP yerleştirme tek try'da yutuluyor):** `recovery_manager.py:380-542` tek try bloğu SL ve TP yerleştirme için ayrı try/except'lere bölündü. Her birinin kendi except'i kendi `sl_id`/`tp_id`'sini `""` bırakıyor. SL exception'da `if not sl_id:` acil kapanış dalına düşüyor; TP exception'da `tp_id=""` ile devam ediyor (TP eksikliği kritik değil, `protection_note` mekanizması işaretliyor).
- **A4-08 (acil kapanış retry/escalation yok):** `recovery_manager.py:558-664` acil kapanış başarısız olduğunda 1 sn backoff'lu retry eklendi (`await asyncio.sleep(1.0)` + tekrar `place_market_order_priority`). Mevcut `log.critical` + `self._pl(...)` korundu, dış alarm sistemi eklenmedi (kapsam dışı). Pozisyonu korumasız `active_trades`'e ekleme davranışı değişmedi.
- **Test:** `test_recovery_manager.py` +4 yeni test:
  1. `test_sl_place_exception_triggers_emergency_close` — SL exception → acil kapanış tetiklenir.
  2. `test_tp_place_exception_adds_trade_with_sl_only` — TP exception → pozisyon SL'li ama TP'siz active_trades'e girer.
  3. `test_emergency_close_retry_succeeds_on_second_attempt` — ilk deneme başarısız, retry sonra başarılı → active_trades'e eklenmez.
  4. Mevcut `test_position_stays_when_both_close_methods_fail` hâlâ geçiyor.
- **Doğrulama:** recovery_manager **16 passed / 0 failed**; test_bot_binance **80 passed / 0 failed**. Pre-existing 2 fail (`test_sl_matched_pending_promoted_to_confirmed`, `test_ws_fallback_promotion_still_works`) dışında hiçbir kırık test yok.
- **Commit:** `e1565c7` — `fix: A4-05+A4-08 recovery SL/TP try ayrımı + emergency close retry`.
- **Sıradaki:** Sıra 3 (A2-01+A2-02 MIN_NOTIONAL dust pozisyon), Sıra 4 (A3-02 lock'suz active_trades yazımı), Sıra 5-7 (A3-03, A4-02, A4-03), Sıra 8-9 (backtest-sniper A6-01/A6-02 — A6-02 için mimari karar bekliyor).

---

## Son İşlem: 2026-08-11 — RECOVER_EMERGENCY_CLOSE CONTINUE BUG FIX (baş mühendis direktifi)

- **Kök neden:** `recover_positions` içindeki `if close_result:` bloğunda `continue` yanlışlıkla `if tp_id:` içindeki `except` bloğunun içindeydi. Bu durumda:
  - `close_result` truthy + `tp_id` boş → `continue` çalışmıyor, alt bloktan "ACIL KAPANIS BASARISIZ" kritik logu atılıyor ve trade `active_trades`'e tekrar ekleniyor.
  - `close_result` truthy + `tp_id` var + TP cancel başarılı → `continue` çalışmıyor, aynı şekilde yanlış log + re-add.
  - Sadece TP cancel exception fırlattığında `continue` çalışıyordu.
- **Fix:** `continue` ifadesini `if close_result:` bloğunun en sonuna, `if tp_id:` bloğundan bağımsız taşındı. Artık acil kapanış başarılı olduğu her durumda (TP cancel başarılı/başarısız/farketmeksizin) döngü devam ediyor, trade tekrar eklenmiyor.
- **Test:** 3 yeni regresyon testi eklendi:
  1. `test_emergency_close_success_without_tp_skips_critical_log` — `tp_id` boş, close_result truthy.
  2. `test_emergency_close_success_with_tp_cancel_skips_critical_log` — `tp_id` var, cancel başarılı.
  3. `test_emergency_close_success_with_tp_cancel_failure_skips_critical_log` — `tp_id` var, cancel başarısız.
- **Doğrulama:** recovery_manager test suite **13 passed / 0 failed**.
- **Not:** Bu bug A4-04 fix'i ile ilişkili — pozisyon doğrulama başarısı artıkca bu bug'ın tetiklenme sıklığı da artıyordu.

---

## Son İşlem: 2026-08-10 — 4 KRİTİK BULGU FIX UYGULANDI (baş mühendis direktifi: gizli-bug-audit-raporu-2026-08-10.md)

- **Kapsam:** A1-01, A1-02, A3-01, A4-01, A4-04 (A2-01/A2-02 kök ailesi A4-04 ile birleştirildi).
- **A1-01 (runtime.protection senkronizasyonu):** `recovery_manager.py`'deki 3 `existing[...]` güncelleme bloğu (213-218, 645-647, 683-685) ve 3 `ActiveTrade(...)` kurulum noktası (222-252, 649-674, 687-714) hepsine `self._order_manager._sync_runtime_protection(...)` çağrısı eklendi. Üç yol (SL/TP mevcut, acil kapanış başarısız, SL/TP yeni kurulum) da runtime.protection.sl_current/tp_current korunuyor.
- **A1-02 (runtime JSON serileştirmesi):** `exit_lifecycle.py:764-775` `**trade` yerine `asdict(trade)` kullanılıyor; `default=str` kaldırıldı. `TradeRuntimeState`/`ProtectionState`/`ProtectionRef` artık dict olarak kaydediliyor, runtime verisi kaybolmuyor.
- **A3-01 (WS/bar-close exit race):** `bot.py:_on_1m_close` ve `user_data_handler.py:_on_order_update_normalized` + legacy handler'daki tüm `pending_exit_*`/`result` mutation'ları `async with self._exit_locks[trade_key]` ile korundu. `exit_lifecycle.py:execute()`'ye atomik `_exit_committed` bayrağı eklendi; stale/exception early-return yollarında reset ediliyor. `UserDataHandler` artık `exit_locks` DI ile alıyor; `bot.py`'den `self._exit_locks` iletilen aynı nesne kullanılıyor.
- **A4-01 (ghost temizliği sessizce yutuluyor):** `reconcile_ghost_positions()` except bloğuna `log.error("[GHOST] state okunamadi...")` eklendi. `_ghost_fail_count` sayaçları eklendi; 5 ardışık hatada `log_event("ghost_check_persistently_failing", ...)` tetikleniyor.
- **A4-04 (place_market_order/stop/tp boş {} dönüyor):** `bot_binance.py`'deki tüm `return {}` yerine `{"_error": True, "_error_code": ..., "_raw": ...}` dönüyor. `recovery_manager.py:555` ve `entry_manager.py:480`'de `_error` varsa `get_positions()` ile pozisyon durumu doğrulanıyor; kapalıysa EXECUTION_CONFIRMED olarak kabul ediliyor, açıksa force-close deniyor.
- **Doğrulama:** recovery_manager 10/10, exit_lifecycle+user_data_handler 75/75, bot_binance 80/80, entry_manager 95/95 geçti. Pre-existing 2 fail (`test_sl_matched_pending_promoted_to_confirmed`, `test_ws_fallback_promotion_still_works`) — `_exit_trade_legacy` kaldırılmasından kaynaklanan eski test kırıkları, bu turla ilgisiz.
- **Sıradaki:** baş mühendis onayı sonrası commit/push + canlı deploy (sunucu `git pull --ff-only` + restart).

---

## Son İşlem: 2026-08-10 — A6 Backtest-Live Parity Denetçisi (araştırma modu, kod değişikliği YOK)

- **Kapsam:** D-2 (trailing/entry kopya kod) senkron denetimi — canlı (`trailing_manager.py`+`bot.py`), BT-1 (`backtest-sniper/analyzer_v5.py`), BT-2 (`simulate.py`) üçlü karşılaştırma. Kanıt protokolü uygulandı: KOD İZİ / TEST KANITI / VERİ KANITI; kanıtlanamayanlar HİPOTEZ listesinde.
- **Yöntem notu:** MCP graf index'i bu repo için kullanışsız (yalnızca index.json düğümleri) → kullanıcı fallback kuralı gereği grep/findstr/read tabanlı satır satır karşılaştırma. `parity_check.py --check` sistem pythonuyla (venv yok) çalıştırıldı → **PARITE_OK** (kapsamı dar: 2 fonksiyon çifti + config sabitleri).
- **Yeni bulgular (bugs.md A6 bölümü, tam tablo orada):**
  - **A6-01 HIGH:** `analyzer_v5.py:418-423` sweep'i TÜKETMİYOR (`bar_index=None` → `is_sweep_used` dedup devre dışı; `sweep_confirmed` hiç `False`'a atanmıyor) — canlı `signal_engine.py:78-93` (2026-08-06 SEIUSDT fix) backtest'e taşınmamış → tek sweep çoklu giriş denemesi.
  - **A6-02 HIGH:** entry çapası — canlı `bot.py:671` `current.close` vs her iki backtest `next_bar.open` (kasıtlı look-ahead karşıtı, uzlaştırılmamış).
  - **A6-03 MEDIUM:** `MIN_SL_DISTANCE_TICKS/PCT` + pre-entry SL-eps guard yalnızca canlıda (`entry_manager.py:223-235,287-343`) — backtest'te yok (bilinen açık, progress.md 08-06 notu).
  - **A6-04 MEDIUM:** tick_size normalizasyonu yalnızca canlı trailing'de (`trailing_manager.py:629-645`) — backtest'ler raw float.
  - **A6-05 LOW:** D-2 Fark 4 AÇIK — `simulate.py:385` `+1`/bar vs canlı+analyzer per-hop.
  - **A6-06 LOW:** D-2 Fark 3 — guard yalnızca canlıda, kapalı-bar verisinde etkisiz.
  - **A6-07 LOW:** ENTRY_VARIANT E1/E2 dalı yalnızca backtest'te; canlıda karşılığı yok (config "A" iken ölü, latent drift riski).
- **Kapananlar:** Fark 1 (PARITE_OK teyidi), Fark 2 (iki tarafta da is_closed yok, AST-identik), Fark 5 (session filter tek kaynak — analyzer_v5/simulate canlı `session_router` import ediyor; analyzer_v5:47 yerel SESSION_HOURS ölü sabit). TP_FIXED config'te yok → False → TP-shift paritesi doğrulandı.
- **Bilinen bayat fail'ler değişmedi:** 18 pre-existing (test_bot 13 + test_state_writer 2 + test_event_log 1 + test_user_data_handler 2) — bu tur kod değişikliği olmadığından test koşulmadı.
- **Sıradaki (karar bekliyor):** A6-01/A6-02 için fix kararları (sweep tüketimini analyzer_v5'e taşı; entry çapasını uzlaştır) baş mühendise sunulacak. A6-03 zaten bilinen açık. Bu tur SADECE rapor — commit/push kapanış protokolü.

---

## Son İşlem: 2026-08-10 — FVG marker index FIX #2 uygulandı (baş mühendis direktifi: fvg-marker-index-fix-directive.md)

- **Kök neden:** `_resolve_fvg_bar_index` Yöntem 1 (fiyat bazlı) sadece entry_bar'dan GERİYE doğru tarıyordu; FVG zonu displacement mumuyla çakışmadığı ve fiyatın zona dönüşü entry'den SONRA olduğu için (PYTHUSDT: entry=22, ilk dokunuş bar 25) hiç eşleşme bulamıyordu → Yöntem 2/3 offset matematiğine düşülüyor, restart sonrası anlamsızlaşan indekslerle YANLIŞ bar işaretleniyordu.
- **Fix (2 parça):**
  - **A)** Yöntem 1 artık TÜM candles'ta arıyor, entry_bar'a EN YAKIN eşleşmeyi seçiyor (önce de sonra da olabilir; eşit uzaklıkta önceki kazanır — Python ilk bulunanı tutar). `ref = entry_bar if entry_bar is not None else 0`; `best_dist` ile O(n) tek geçiş. Restart-proof ve PYTHUSDT senaryosunu çözüyor.
  - **B)** Yöntem 2/3 offset sonucuna **sanity guard** eklendi: `rel` bounds içindeyse `bar_range = |high-low|`, `fvg_mid = (top+bottom)/2`, `dist = min(|high-mid|, |low-mid|)`; `bar_range > 0 and dist <= bar_range*8` değilse **kabul edilmez, heuristic'e (4) düşülür** — chart_template.html satır 66-89'daki "FVG consistency check" ile AYNI formül. `fvg_top`/`fvg_bottom` yoksa guard atlanır (eski davranış: bounds içinde rel kabul).
- **Test (22 passed, 0 failed — test_snapshot):**
  - 5 pre-existing fail **düzeltildi** (yeni mantığa uyarlandı): test 1-2 `_candles_low_16` (zonla çakışmayan mumlar) ile offset yolu izole test ediliyor; `test_no_data_falls_to_heuristic`→`test_no_fvg_data_returns_none` (has_fvg=False → None, kodun mevcut bilinçli davranışı); `test_entry_bar_less_than_2_returns_0` fvg_top/bottom verip heuristic edge'ini koruyor; `test_all_none_returns_0`→`test_all_none_returns_none`.
  - **Yeni 4 test:** `test_pythusdt_price_returns_to_zone_after_entry` (direktifteki senaryo: fvgTop=0.0424, fvgBottom=0.04234, entryBar=22, 30 mumluk `_candles_pyth`, zonla çakışan tek mum bar 25 → sonuç 25 + `c["low"]<=0.0424 and c["high"]>=0.04234` doğrulaması), `test_price_match_prefers_nearest_to_entry` (bar 20 zonuna çekilince 20 döner — en yakın seçimi), `test_offset_math_sane_result_accepted_regression` (restart yok, offset doğru: rel=7 kabul — eski davranış korundu), `test_offset_math_unreasonable_falls_to_heuristic` (rel=6 ama zon ortası 299'a uzak → dist 188 > 80 → heuristic 8).
- **Doğrulama:** test_snapshot **22 passed / 0 failed** (önceki: 13 passed / 5 failed). Tam suite: test_bot 13 fail + test_state_writer 2 + test_event_log 1 + test_user_data_handler 2 = **18 pre-existing** (hepsi bayat refactor testleri: `mark_trade_closed`/`_exit_trade_legacy` yok, `sl_status` değerleri değişmiş, event_log format farklı) — git stash baseline ile birebir, **0 yeni fail**. py_compile + ruff (check/format) temiz. Canlı trade mantığına dokunulmadı — sadece snapshot/analiz doğruluğu (direktif: DÜŞÜK-ORTA öncelik, deploy onayı gerekmez).
- **Sıradaki:** deploy (sunucu `git pull --ff-only` + restart) istenirse; PYTHUSDT gibi eski snapshot'lar YENİ fonksiyonla yeniden üretilirse FVG marker'ı artık gerçek zona oturur.

---

## Son İşlem: 2026-08-10 — ActiveTrade entry_timestamp FIX uygulandı (baş mühendis direktifi: entry-timestamp-fix-directive.md)

- **Kök neden:** `ActiveTrade`'de `entry_timestamp` alanı hiç yoktu → snapshot `_find_bar` her zaman fiyat bazlı fallback'e düşüyordu → aynı fiyatın geçmişte başka bir barda da görüldüğü durumda (ONDOUSDT 0.352, gerçek giriş 08-08 22:15 ama entryBar 08-07 11:00 = 36 saat erken) yanlış bar seçiliyordu.
- **Fix (3 parça):**
  - `src/models.py` — `ActiveTrade.entry_timestamp: int = 0` eklendi (`exit_timestamp` yanına).
  - `src/bot.py:1020` — `ActiveTrade(...)` oluşturmada `entry_timestamp=int(time.time() * 1000)` (fill onayı sonrası an — entry_bar_index ile tutarlı; `time` zaten import'lu).
  - `src/trading/recovery_manager.py` — 3 `ActiveTrade(...)` noktasına (222, 641, 669) `entry_timestamp=int(time.time() * 1000)` + yorum: restore anı, yaklaşık — gerçek fill zamanı `get_all_orders`'tan alınamıyor (açık emirler yalnızca, filled MARKET emri listede yok; positions endpoint'te entry-time yok).
- **Serileştirme:** `dict(trade)` → `{**trade}` (exit_lifecycle jsonl writer) yeni alanı otomatik taşıyor (`__dataclass_fields__`/`__getitem__` üzerinden) — ek alias gerekmedi, testle kanıtlandı.
- **Test (8 yeni, hepsi geçti):** test_models 2 (`entry_timestamp` default 0 + set/attr/dict round-trip), test_snapshot 5 (`_find_bar` ONDOUSDT senaryosu: aynı fiyat iki barda → ts yokken index 0 (bug), ts varken index 2 (doğru); ts'siz fiyat fallback; ts eşleşmezse fiyat fallback; eşleşme yoksa son bar; `normalize_trade` passthrough + yoksa 0), test_bot 1 (`_try_entry` sonrası `entry_timestamp > 0` ve now±10s).
- **Doğrulama:** kapsam testleri **26 passed / 0 failed**; tam suite 18 fail = **5 pre-existing (TestResolveFvgBarIndex — kod fiyat bazlı FVG aramasını öne aldı, eski testler uyumsuz) + 13 pre-existing (test_bot bayat refactor testleri)** — git stash baseline ile birebir, **0 yeni fail**. py_compile temiz.
- **Not:** fix SADECE yeni açılacak trade'leri düzeltir — geçmiş/açık trade kayıtlarında entry_timestamp yok, ONDOUSDT gibi eski snapshot'lar hâlâ yanlış bar gösterebilir (retroaktif düzeltme direktifte istenmedi). Deploy: commit/push bu turun kapanış protokolünde (baş mühendis onayı varsayıldı — risk düşük, default 0 geriye dönük uyumlu).
- **Sıradaki:** deploy kararı kullanıcıda (sunucu `git pull --ff-only` + restart) + pasif izler (SEIUSDT kapanışı, P1-15, P2-8, PRE-ENTRY).

---

## Son İşlem: 2026-08-09 — Snapshot dosya adları ters kronolojik (deploy edildi) + P1-4 CANLI

- **Değişiklik** `src/snapshot/snapshot.py`: `_reverse_sort_key` (9-tümleyen, her rakam 9-rakam) + filename formatı `{sym}_{T9(YYYY-MM-DD_HHMMSS)}_{YYYY-MM-DD_HHMMSS}.html` — alfabetik sıralamada (VS Code Explorer) **en yeni snapshot en üstte**. Tasarım: ters + okunabilir tarih (kullanıcı onayı).
- **Neden string reverse değil:** harf harf ters çevirme alfabetik sırayı tersine döndürmez (09→"90" vs 01→"10" → eski üstte kalır); rakam bazlı 9-tümleyen matematiksel olarak doğru.
- **Sunucu:** 499 snapshot dosyası rename edildi (script `/tmp/rename_snapshots.py`, 0 skip). Deploy `822e39a..8bace28` (snapshot + P1-4 + DYDX + stabilite dokümanları birlikte) → restart **393704.bot / PID 393706**. Sağlık: 0 CRITICAL/ERROR, 28 LEVERAGE OK, 499 trade yüklendi, WS 56 stream reconnect 0.
- **⚠️ P1-4 artık CANLI:** pull aradaki commit'leri de getirdi (P1-4 ghost periyodik dahil) — restart ile birlikte devreye girdi. Deploy kararı bendeydi (baş mühendis) ve test edilmişti (recovery 8 passed).
- **Test:** `tests/test_snapshot.py` +4 (`TestReverseSortKey`) — 7 passed. **5 pre-existing fail** (`TestResolveFvgBarIndex`): kod fiyat bazlı FVG aramasını öne aldığından eski testlerle uyumsuz (`assert 10==6`, `None==8` vb.) — benim değişiklikle ilgisiz, düzeltme ayrı iş.
- **Sıradaki:** pasif izler (SEIUSDT kapanışı → yeni format snapshot + runtime.status teyidi; P1-15 trail; P2-8; PRE-ENTRY).

---

## Son İşlem: 2026-08-09 — Stabilite eşiği RESMİLEŞTİRİLDİ (baş mühendis direktifi, parity check tetikleyicisi)

- **Çıktı:** `memory-bank/progress.md` üstüne kalıcı "Stabilite Eşiği — Parity Check Tetikleyicisi" bölümü: 5 eşik (E1 0 CRITICAL/ERROR × 3 gün, E2 stale ≤5, E3 orphan+ghost+recover ≤5, E4 PnL |sıçrama| ≤100/200, E5 WARNING ≤100 bilinen kaynak hariç) + **N=3 gün önerisi** (baş mühendis onayına sunuldu) + mevcut durum + mesafe tahmini (~08-12/13 parity adaylığı).
- **Veri (dedupe log analizi, sunucu logları):** 08-08: 2 CRITICAL (1× WS_UNMATCHED ARB 01:14 + 1× tick sentinel), stale 1 ✓, orphan+recover 4 ✓, PnL -1.73. 08-09: 9 CRITICAL — 7× tick sentinel (01:00-11:15, aa27b6f fix öncesi) + 2× APTUSDT ACİL KAPANIŞ (P2-8); **deploy 14:22 sonrası 0 CRITICAL/0 ERROR**; stale 12 (RENDER serisi — pozisyonlar kapandı); PnL -0.78. PnL trendi son 4 gün: -46.33 / -1.73 / -0.78 — sıçrama yok.
- **Yeni gözlem:** 08-08 01:14 WS_UNMATCHED_REDUCE_ONLY (ARBUSDT) — P1-7 "kesin harici" sınıfının yeni örneği, kaynak araştırılmadı; bugs.md'ye 📎 eklendi (E1'i bozabilecek bilinen risk).
- **Kod değişikliği YOK** (baş mühendis kısıtı: sadece belge).
- **Karar bekliyor:** N=3 onayı (3 mü 7 mi — 3 önerildi, deploy sıklığı ~1/gün).
- **Sıradaki:** baş mühendis onayı sonrası commit/push; pasif izlere dönüş (SEIUSDT kapanışı, P1-15 trail, P2-8, PRE-ENTRY).

---

## Son İşlem: 2026-08-09 — P1-4 ghost pozisyon periyodikleştirme UYGULANDI (baş mühendis direktifi)

- **Fix:** `src/trading/recovery_manager.py:838-842` — `periodic_check_loop()` içine `reconcile_orphan_orders()` yanına `reconcile_ghost_positions()` eklendi (60sn aralık, orphan ile aynı). Restart çağrısı (`bot.py:1265`) korundu → ghost temizliği artık **periyodik + restart**.
- **Proaktif kontrol (kod öncesi):** ① idempotent — temizlenen state `open=false` olur, sonraki turda elenir (`mark_trade_closed` de idempotent); ② pozisyon açıksa SL/TP kontrolü durum değiştirmez (log-only); ③ aktif trade'ler `sym in _active_trades` ile skip; ④ restart çağrısı senkron + loop `run()` sonunda başlar → çakışma yok; ⑤ `trades_today=0` yalnızca pozisyon-kapalı dalında (canlı trade'te sıfırlama yok); ⑥ SL/TP eksik uyarısı her 60sn tekrarlanır (bilinçli).
- **Test:** `tests/test_recovery_manager.py` +2 (`TestPeriodicLoopGhostReconcile`): loop'un ghost reconcile çağırdığı + `mark_trade_closed`/`ghost_cleaned` temizleme davranışı. Kanıt: recovery **8 passed**; entry_manager 95, integration_lifecycle 12, models 51 — 0 yeni fail; test_bot 32/13 (13 pre-existing, 0 yeni). Ruff temiz.
- **Dokunulmadı:** `reconcile_ghost_positions` mantığı (639-729), orphan sweep, `_known_protection_ids`, `_on_1m_close` sayacı.
- **Sıradaki (pasif izler):** SEIUSDT kapanışında runtime.status canlı teyidi + P2-8 (APT dust-close) + PRE-ENTRY iz — durumları topluca raporlandı (aşağıda).

### Pasif izlerin durumu (baş mühendis talebi üzerine toplu)
1. **SEIUSDT kapanışında runtime.status canlı teyidi — BEKLİYOR (pasif).** `5fd6f11` deploy edildi; SEIUSDT trade hâlâ açık (restart'ta korundu). Kapanışında `trades_history.jsonl` kaydında flat `status` ile `runtime.status` eşleşmesi kontrol edilecek. Not: state_writer runtime'ı JSON'a yazmıyor (BULGU-05); kontrol trades_history writer üzerinden.
2. **P2-8 APTUSDT dust-close — AÇIK (düşük öncelik, not).** `place_market_order` boş `{}` → "ACİL KAPANIŞ BAŞARISIZ" → restart'a kalıyor; minNotional altı dust için strateji yok. bugs.md'ye eklendi, fix yok.
3. **PRE-ENTRY iz — BEKLİYOR (pasif).** Guard canlıda (`5eb2c08`'den beri); ilk ENA/SEI tipi dar-gap sinyalinde `[PRE-ENTRY]` reddi örneği toplanacak. Canlıda henüz gözlemlenmedi.
4. **P1-15 trailing TETİKLENDİ canlı olayı — BEKLİYOR (pasif).** Retrace koşulları canlıda hâlâ oluşmadı; sıradaki FVG kapanış onaylı trail olayında dashboard/snapshot ile doğrulanacak.

---

## Son İşlem: 2026-08-09 — DYDX reconcile köşe durumu KAPANDI (baş mühendis onayı, kapsam_4.md)

- **Fix:** `src/trading/entry_manager.py:520` — Blok B koşulu `not mkt_id and actual_qty <= 0` → `not mkt_id and (actual_qty <= 0 or actual_price <= 0)`. Köşe durumu (qty>0 ama price<=0) artık reconcile'a düşüyor; pozisyon açıksa `_emergency_close`.
- **Çakışma kontrolü (proaktif):** Blok A (`qty>0 AND price>0`) vs yeni Blok B (`qty<=0 OR price<=0`) mantıksal olarak karşılıklı dışlayıcı — çakışma yok, sıra değişmedi.
- **Test:** `tests/test_entry_manager.py` +1 `test_market_qty_no_price_pos_open_emergency_close` (executedQty var, avgPrice/quote yok → parse_market_fill=(qty,0,0), poz açık → SELL emergency close). Kanıt: 3 reconcile testi **3 passed / 92 deselected**; test_entry_manager tamamı **95 passed / 0 failed**; integration_lifecycle+models+recovery **69 passed**; test_bot **13 failed / 32 passed** (13 pre-existing, 0 yeni).
- **Dokunulmadı:** Blok A/C, `_emergency_close` fail senaryosu (baş mühendis: ayrı büyük konu, ileride).
- **Sıradaki:** P1-4 (ghost temizliğini periyodikleştirme — baş mühendis) + SEIUSDT kapanışında runtime.status canlı teyidi + P2-8 + PRE-ENTRY iz.

---

## Son İşlem: 2026-08-09 — DYDX reconciliation KAPSAM RAPORU (kod değişikliği YOK)

- **Nedir:** 08-02 canlı olay (DYDXUSDT entry'de HTTP 408) → bc3f3ff fix'i (08-03): `entry_manager.execute_live_entry()` içinde **MARKET empty_response reconcile guard** (mkt_id yok + qty<=0 + poz açık → `_emergency_close`).
- **Kapsam matrisi:** Blok A (489, orderId yok+qty/price var — eski), Blok B (520-548, empty_response — bc3f3ff), Blok C (550-581, geçikmeli fill denemesi + MARKET BASARISIZ). `parse_market_fill({})→(0,0,0)` doğrulandı (238-240); `_emergency_close` reduce_only karşı taraf emri (345-402).
- **Testler:** test_entry_manager `-k "empty_response or market_order_failure"` → **2 passed / 92 deselected**.
- **Kapsam boşlukları (not, fix yok):** ① canlı teyit yok (fix sonrası DYDX'te yeni 408 görülmedi); ② **köşe durumu** `not mkt_id and qty>0 and price<=0` → Blok A/B ikisi de çalışmaz, pozisyon açık kalabilir (tek nokta fix adayı: Blok B koşulunu `qty<=0 or price<=0` yapmak — bu turda DEĞİŞTİRİLMEDİ); ③ `_emergency_close` fail → pozisyon korumasız, recover_positions 60sn'de korur ama kapatmaz.
- **Rapor:** `reports/dydx_reconciliation_kapsam.md`.
- **Sıradaki:** kullanıcı kararı (köşe durumu fix'i yapılsın mı) + P2-8 + PRE-ENTRY iz + SEIUSDT kapanışında runtime.status canlı teyidi.

---

## Son İşlem: 2026-08-09 — DEPLOY: aa27b6f + 5fd6f11 (baş mühendis onayı, kapsam_3.md direktifi)

- **Pull:** `5eb2c08..822e39a` fast-forward (HEAD 822e39a; kod içerenler aa27b6f + 5fd6f11, aradakiler docs). Kod katmanı: `models.py:591` logger.debug senkronu, `models.py:638` PendingLock symbol.
- **Restart:** SIGINT 390683 → graceful kapanış (~16s). **İlk `screen -dmS` denemesi bot'u başlatmadı** (süreç yok, "No Sockets"); `;` zincirli + `2>&1` yeniden denemede **391750.bot** (PID 391752) ile başladı. Ön planda timeout testi bot'un sağlıklı olduğunu gösterdi (LEVERAGE 28/28 tamamlandı).
- **Canlı (katman 3):** `tick_size olmadan` CRITICAL = **0** (önceki deploy'da 6× idi — sentinel fix doğrulandı); CRITICAL/ERROR yok; WS bağlı; SEIUSDT açık trade restart'ta korundu (tick_size 0.0001, status ACTIVE — recovery reconcile yolu yeni `__post_init__`'i tetiklemedi).
- **runtime.status tarihsel kanıt:** `output/trades_history.jsonl` son kayıtları flat `"status": "CLOSED"` + `runtime=TradeRuntimeState(status=<TradeStatus.ACTIVE>)` — fix ÖNCESİ uyumsuzluk birebir kayıtlı. Yeni kapanışta (SEIUSDT) senkron kontrolü = **pasif iz** (state_writer runtime'ı JSON'a yazmıyor, BULGU-05; trades_history writer'ı yazıyor).
- **Sıradaki:** DYDX reconciliation kapsamı (baş mühendis: kapsam raporu, kod değişikliği yok) + P2-8 + PRE-ENTRY iz.

---

## Son İşlem: 2026-08-09 — runtime.status SENKRONU UYGULANDI (baş mühendis onayı, kapsam_1.md)

- **Onay:** baş mühendis planı onayladı; "except ValueError: pass'i sessiz bırakma — en azından log.debug ile iz bırak" (bot.py:983 dersi) direktifi verdi.
- **Fix 1:** `src/models.py __setitem__` (584-592): `setattr` sonrası `key == "status"` ise `runtime.status = TradeStatus(value)`; `ValueError`'da `logger.debug` (sessiz pass YOK). Enum 9 değerin tamamını kapsıyor (329-338, BROKEN_MANUAL_INTERVENTION_REQUIRED dahil) → tüm üretim değerleri çevrilebilir; `""`/bilinmeyen → debug + runtime ACTIVE'de kalır.
- **Fix 2:** `tests/test_integration_lifecycle.py _trade()` (64, 77): `tick_size=0.001` → CRITICAL log kirliliği temizlendi.
- **Kanıt:** TestExitStateTransitions **3 passed** (FFF→PASS); integration_lifecycle tamamı **12/0** (önce 9/3); test_models **51 passed**; test_bot **32 passed / 13 failed** (13 pre-existing, 0 yeni); test_recovery_manager **6 passed**. Baseline birebir.
- **Dokunulmadı:** state_writer.py, order_manager._sync_runtime_protection (kapsam dışı direktifi).
- **Nüks kontrolü:** P2-4 (runtime.protection) yeşil; PENDING muafiyeti korundu; sentinel davranışı değişmedi.
- **Sıradaki:** P2-8 (APTUSDT dust-close) + PRE-ENTRY canlı iz + kullanıcıya deploy kararı (aa27b6f tick_size fix henüz sunucuda değil).

---

## Son İşlem: 2026-08-09 — runtime.status KAPSAM RAPORU (MD, kod değişikliği yok)

- **Görev:** TestExitStateTransitions 3 fail'in gerçekte neyi kırdığını çıkar + integration_lifecycle ilişkisi + tek nokta düzeltme önerisi.
- **Kök neden (doğrulandı):** "state iki yerde tutuluyor" ailesi — flat `ActiveTrade.status` (models.py:530) vs nested `runtime.status` (TradeRuntimeState, models.py:443). `__setitem__` (models.py:584) flat'e yazar, runtime'ı senkronlamaz. `runtime.status` üretimde HİÇ yazılmıyor (hep default ACTIVE); exit_lifecycle.py:21 docstring "TradeRuntimeState... BAĞLANMADI" teyidi.
- **3 fail:** `trade["status"]=STATUS_X` sonrası `runtime.status.value` ACTIVE'de kalıyor (EXIT_REQUESTED/EXIT_VERIFYING/CLOSED assertion'ları).
- **integration_lifecycle:** tam dosya 9 passed / 3 failed — fail'ler yalnızca TestExitStateTransitions. P2-4 (runtime.protection) yeşil, sadece status tarafı eksik.
- **Tek nokta fix önerisi:** `__setitem__`'e status senkronu (`TradeStatus(value)`, ValueError'da atla). Kanıt: üretimde 15 `trade["status"]=` noktasının tamamı bu geçitten geçiyor (bot 2, order_manager 3, exit_lifecycle 9); attribute yazımı yok. state_writer flat'ten türetiyor → etkilenmez; `""` UNRESTRICTED sette → ValueError guard'ı güvenli.
- **Test fixture notu:** `_trade()` (test_integration_lifecycle.py:68) tick_size'sız ACTIVE kuruyor → savunmacı CRITICAL log kirliliği (fix commit'ine `tick_size` eklenmesi temizler).
- **Rapor:** `reports/runtime_status_senkronizasyon_kapsam.md` — baş mühendise iletildi.
- **Sıradaki:** onay sonrası tek commit uygulaması (models.py `__setitem__` + `_trade()` tick_size) → 3 test yeşil.

---

## Son İşlem: 2026-08-09 — 🔴 tick_size SENTINEL KÖK NEDEN KAPANDI + 🟡 P2-8 dust-close notu

### Kök neden (baş mühendis direktifi: tüm `ActiveTrade(` kuruluş noktalarını tara)
- `grep -rn "ActiveTrade("` → 7 eşleşme: recovery_manager 3 (159/577/605 — daaeeb0 ile **zaten fix'li**, hepsi `tick_size=tick_size` geçiriyor), bot.py:986 (geçiriyor ama 979-984 eski sessiz 0.10 default + `except: pass` deseni), models.py:567 (log'un kendisi), models.py:615 (docstring), **models.py:625 → SUÇLU**.
- **`PendingLock.__enter__` (models.py:625):** `ActiveTrade(status="PENDING")` — **symbol de tick_size de geçmiyor** → `__post_init__` CRITICAL'ı `sym=` **boş sembolle** patlatıyor. Log'daki 6× `[MODELS] ActiveTrade(sym=) ...` (01:00-11:15) bunun birebir çıktısı — her `_try_entry` giriş denemesinde 1 CRITICAL. "recovery dışında en az bir yol" = PENDING placeholder yolu.

### Fix (3 düzenleme)
1. `models.py __post_init__`: `status != "PENDING"` ise CRITICAL logla — PENDING placeholder'lar bilinçli olarak eksik kurulur (geçici kilit işareti, gerçek trade değil); sentinel yine kurulur (tick_size asla None kalmaz). **Gerçek trade kuruluşları (STATUS_ACTIVE) hâlâ gürültülü patlar** — savunma korundu.
2. `models.py PendingLock.__enter__`: `symbol=self._sym` geçiyor — placeholder doğru sembolle kurulur (boş `sym=` log şifresi de çözüldü).
3. `bot.py:983`: sessiz `except: pass` → `log.warning("[TRY_ENTRY] %s tick_size alinamadi (0.10 fallback)", sym)` — recovery deseniyle (warning'li fallback) tutarlı.

### Test
- test_models 2 yeni (`test_pending_placeholder_does_not_trigger_tick_size_critical` caplog ile; `test_real_trade_without_tick_size_still_triggers_critical` — savunma korunduğu kanıtı) + test_bot 1 yeni (`test_pending_placeholder_has_symbol`).
- Yeni + recovery suite: **13 passed / 0 failed**. Fail seti pre-existing 13/13 (test_bot eski refactor testleri: `mark_trade_closed`/`_stage`/`MIN_FVG_SIZE` — exit-lifecycle refactor sonrası bayat; baseline ile birebir, 0 yeni fail).

### 🟡 P2-8 (not, fix yok — baş mühendis direktifi)
- APTUSDT dust-close gap bugs.md'ye eklendi: `place_market_order` boş `{}` → "ACİL KAPANIŞ BAŞARISIZ" loglanıp restart'a kalıyor; minNotional altı dust için strateji yok. Sadece kayıt, düşük öncelik.

### Sonraki adım (kullanıcı sırası)
- **runtime.status senkronizasyonu** (TestExitStateTransitions 3 fail + integration_lifecycle) — tick_size işi kapandığı için artık sırada.
- Pasif izleme sürüyor: ilk ENA/SEI tipi dar-gap sinyalinde `[PRE-ENTRY]` reddi örneği + yeni run'da `tick_size sentinel` üretilmediği doğrulaması.

---

## Son İşlem: 2026-08-09 — GUARD DEPLOY EDİLDİ (3 katmanlı doğrulama) + canlı log incelemesi

### ✅ Deploy — commit `5eb2c08` (kullanıcı onayı: "Deploy — evet, yap. Düşük riskli: sadece pre-entry validasyona yeni red koşulu")
1. **Katman-1 (hash):** sunucu `git pull --ff-only` → `6e99c9b..5eb2c08` fast-forward; HEAD `5eb2c08` = kullanıcının beklediği hash. ✅
2. **Katman-2 (kod):** deploy edilen dosyalarda guard mevcut — `src/bot.py:727` `EntryManager.validate_pre_entry_protection(...)` çağrısı, `src/trading/entry_manager.py:287` tanım, `src/config.py:639` `SL_EPSILON_TICKS = 2`. ✅
3. **Katman-3 (canlı davranış):** restart sonrası 28 sembol INIT + WS 56 stream + USER_DATA bağlı, **0 ERROR/CRITICAL/Traceback**. `[PRE-ENTRY]` reddi henüz gözlemlenmedi — ilk dar-gap (ENA/SEI tipi) sinyalinde bekleniyor (pasif izleme açık).

### 🔧 Restart sırasında düzeltilen engel
- İlk restart denemesi `screen -dmS bot ./venv/bin/python3 bot.py` → **`can't open file '/root/sniper/bot.py'`** — cwd yanlıştı. Bot `/root/sniper/src/` içinde çalışıyor (eski screen cwd'sini hatırlıyordu, yeni screen'i kullanıcı açtığında cwd geçilmediği için sıfırlandı).
- Doğru: `cd /root/sniper/src && TERM=xterm screen -dmS bot /root/sniper/venv/bin/python3 bot.py` → screen **390682.bot**, PID 390683.

### 🔍 Deploy öncesi canlı log incelemesi (trade/event/history — anormal durum taraması)
- **SEIUSDT-555 hâlâ AÇIK** (short, entry 0.04150, SL 0.0420): trailing her dakika `trail_skipped | no_better_trail_candidate` (normal; SL entry'den 5 tick uzakta — eski guard'ın yakalayamadığı ama reddetmediği aralık; yeni FVG guard'ı bundan sonraki benzer sinyallerde devrede).
- **APTUSDT dust pozisyonu (qty 0.1):** 07:25 UTC'de recovery `ACIL KAPANIS BASARISIZ -- MANUEL MUDAHALE GEREKLI` (`place_market_order` boş dict `{}` döndü). Restart'ta `[GHOST] APTUSDT pozisyon kapali, state temizlendi` → borsada kapanmış, risk geçti. Not: dust pozisyonu minNotional yüzünden korumasızdı (her dakika 4× `[MINNOTIONAL] ... qty=0.1 < min_notional=5.00` WARNING + `trail_skipped no_protection_update_required`).
- **6× CRITICAL `[MODELS] ActiveTrade(sym=) tick_size olmadan kuruldu — 0.10 sentinel kullanildi` (bugün 01:00–11:15):** recovery fix'i (08-08) deploy edilmiş olmasına rağmen bu uyarılar bugün de üretildi → recovery akışında hâlâ tick_size'siz ActiveTrade kurulumu yapan EN AZ BİR YOL var (belki farklı bir kurulum sitesi). Ayrı iz olarak açık.
- Bugünkü kapanışlar (events/history): ONDO TP +17.54, APT TP +2.67, ATOM SL -12.07, RENDER #521 SL -9.55, RENDER #522 SL -8.92, TIA SL -12.14, ALGO SL +19.96 (recovered, trailing uygulandı), ARB WS_FALLBACK +18.13, ADA SL -1.50. Balance 4964.35.
- 9 ERROR/CRITICAL (son 20000 satır) = yukarıdaki 6 sentinel + 2 APT recovery + 1 (tam listelendi); hepsi eski run'a ait, yeni run'da 0.

### Sonraki adım
- Pasif izleme: ilk ENA/SEI tipi dar-gap sinyalinde `[PRE-ENTRY]` reddi örneği topla (log + events ile kaynakla).
- Kullanıcının sıradaki işi: **runtime.status senkronizasyonu** (TestExitStateTransitions 3 fail + integration_lifecycle).
- Açık iz: recovery akışındaki tick_size sentinel CRITICAL'ları (6× bugün) — recovery_manager'da kaçırılan kurulum yolu.

---

## Son İşlem: 2026-08-09 — ENA/tüm-sembol PRE-ENTRY SL GUARD genellemesi (tek genel kural)

### Görev (baş mühendis direktifi)
- "FVG sınırı ile SL arası mesafe eps'in altında mı" kontrolü giriş validasyonuna TEK YERDEN taşınsın — SEI/ENA'ya özel koşul yok, tüm semboller için tek genel kural.
- ⚠️ İlk turda direktifi yanlışlıkla Agent Manager oturumuna pasladım (kullanıcı: "3. ajana pasladın?"). Kullanıcı uyarısıyla oturum durduruldu (`ses_01d1460b7ffe4Y2Y3oS4VIiiOA`), görev bana devredilip tamamlandı. Agent Manager = Kilo'nun görünür izole paralel oturum özelliği; "ajan" dediği kişi benim.

### Uygulama
- `src/trading/entry_manager.py:287-343` — yeni `EntryManager.validate_pre_entry_protection(side, entry_price, sl, tp, tick_size, trigger_fvg, epsilon_ticks)`:
  1. SL/TP vs giriş fiyatı epsilon kontrolü (eski `validate_protection_with_actual_fill` davranışı korundu).
  2. **SL vs FVG sınırı:** long `clearance = fvg.bottom - sl`, short `clearance = sl - fvg.top`; `clearance < eps` → sinyal reddi. FVG yok/geçersizse atlanır.
  - `tick_size <= 0` ise her iki kontrol atlanır (fill-sonrası `validate_protection_with_actual_fill` güvenlik ağı duruyor).
- `src/bot.py:718-745` — pre-entry guard artık sembol-bağımsız `validate_pre_entry_protection(..., trigger_fvg=fvg, epsilon_ticks=cfg.SL_EPSILON_TICKS)` çağırıyor; eski `tick_size > 0` sarmalayıcısı fonksiyona taşındı; yorum "SEIUSDT fix" → "tüm semboller, tek genel kural".
- Not: kod + testlerin çoğu Agent Manager'daki ajan tarafından commit edilmeden yazılmıştı (durdurduğumda çalışma ağacındaydı); ben doğruladım ve eksik mock güncellemelerini tamamladım.

### ENA senaryosu (guard'ın yakaladığı vaka)
- ENA tick=0.001, eps=2 tick=0.002. SL, FVG.bottom'a (long) / FVG.top'a (short) 0.0015-0.0018 mesafede → artık PRE-ENTRY'de reddedilir. Eski guard (sadece entry-eps) bunu yakalayamazdı çünkü SL, entry'ye ~10 tick (0.0098) uzaktaydı — kök neden "SL, FVG sınırına anında-tetiklenecek kadar yakın" idi.

### Doğrulama
- `TestValidatePreEntryProtection` 9 yeni test (ENA long/short red, geçer buffer, eski davranış korunumu, no-fvg, tick=0 atlama, clearance==eps sınır durumu) + 3 bot testi mock güncellemesi.
- entry_manager+bot: **125 passed / 13 fail**; integration (v2+lifecycle): **57 passed / 9 fail** — fail'ler git-stash HEAD baseline ile BİREBİR aynı (0 yeni fail; exit-lifecycle wiring + TestExitStateTransitions + TestEntryProtection pre-existing kırıkları).
- Ruff temiz; vulture/mypy yeni uyarı/hata yok. Tam suite tek koşuda ağ/WS testinde 10 dk'da asılıyor → kapsam dosyaları ayrı koşuldu.

### Sonraki adım
- Deploy kararı kullanıcıda: commit main'de; sunucuya `git pull --ff-only` + restart (screen `377433.bot`). Deploy sonrası canlı gözlem: ilk FVG-çapalı sinyalde `[PRE-ENTRY]` reddi veya normal SL kurulumu.

---

## Son İşlem: 2026-08-09 — CANLI KATMAN-3 TEYİDİ (SSH erişimiyle, kod değişikliği YOK)

### Giriş: SSH çözüldü
- Kullanıcı root + şifre verdi; `plink -hostkey SHA256:up718ORLAn+hTH+VBKw1v4e1awNqnG1fO9CTWrKoZNg` ile bağlantı sağlandı (hostkey cache'li değildi → fingerprint ile kabul). Bir önceki turdaki "canlı teyit engeli: SSH reddi" KALDIRILDI.

### Katman-1 (hash) ✅
- Sunucu HEAD `6e99c9b` (docs: D modu kapanış + deployed kaydı) = repo HEAD → deploy `695b2a4` + memory-bank commit canlıda. Screen **377433.bot**, PID **377435** (`/root/sniper/venv/bin/python3 bot.py`), Aug08'den beri kesintisiz. (Gözlem kuralı: dashboard + snapshot + log; canlı gözlem 01:00-04:00 UTC arası.)

### Katman-2/3 (canlı davranış) — "D modu yok" KANITLANDI; "trailing tetiklenme olayı" hâlâ bekliyor
- **Bugünkü trade'ler (snapshot `trades_history.jsonl` + event log `paper_trade.log`):**
  - RENDERUSDT #521: short entry 1.319 → SL 1.324, **-9.55**, close 23:44 UTC, `trail_steps=[]` (trailing tetiklenmedi).
  - RENDERUSDT #522: short entry 1.321 → SL 1.32599681, **-8.92**, close 00:11 UTC, `trail_steps=[]`, `trail_count=0`.
  - ONDOUSDT #515: **AÇIK** — 01:15 UTC entry short @ 0.35200, sl=0.35458 tp=0.34764 qty=4441; 04:00 UTC itibarıyla hâlâ açık, upnl +6.22, `trailing_count=0`, koruma PLACED/OK. 15m bar kapanışları: 0.3527→0.3522→0.3517→0.3520→0.3518→**0.3488** (03:15)→0.3493→0.3500→0.3504.
- **Trail davranışı (event log):** 289× `trail_skipped`, reason **HEPSİ `no_better_trail_candidate`**; `trail_executed`/`trail_updated` = **0**. ONDO'da her 15m bar'da retrace taraması yapıldı, FVG kapanış onaylı daha iyi kandidat oluşmadı → SL/TP güncellenmedi (retrace beklenen davranış).
- **D modu branch kontrolü:** `TRAIL_ACTIVATION`/`atr_chase`/`CONTINUATION_CONFIRM` canlı log'da **0 eşleşme** → activation/ATR-chase yolları canlıda HİÇ çalışmıyor.
- **Diğer event dağılımı (deploy sonrası run `paper-20260808-182107`):** entry_filled 3, trade_closed 2, sl_placed/tp_placed 3'er, protection_validated/normalized 3'er, entry_qty_ready 3, initial_sl_calculated 6, direction_validation_ok 3.

### Sonuç
- Katman-1 ✅ + Katman-2 ✅ + Katman-3 "D modu davranışı yok" kısmı ✅ → **D modu kaldırma 3. katmanı pratikte kapandı** (retrace-only canlıda kanıtlandı).
- ⏳ Tek eksik: "trailing TETİKLENDİ" canlı olayı (retrace koşulları bugün oluşmadı). Bu iz açık kalır ama pasif bekleyişte — sıradaki pozisyon + FVG kapanış onaylı trail olayında dashboard/snapshot ile doğrulanacak.
- Gözlem kuralı uygulandı: sonuçlar `trades_history.jsonl` (snapshot) + `live_state.json` (dashboard) + `paper_trade.log` (event log) üçlüsüyle kaynaklandı; anlık tail yorumu yapılmadı.

---

## Son İşlem: 2026-08-09 — 4 soruluk kanıt-temelli teyit turu (kod değişikliği YOK)

### Sonuç: 3/4 kodda doğrulandı; canlı davranış SSH olmadan DOĞRULANAMADI
1. **TRAIL_MODE=retrace doğru mu?** Kod ✅ (`src/config.py:538` default "retrace"; trailing_manager retrace-only, DENEYSEL yollar ulaşılmaz), deploy kaydı ✅ (`deployed.md:5`, commit `695b2a4`, screen 377433.bot, 0 ERROR). Canlı ❌ — SSH reddi.
2. **P1-15 "08 Ağu RENDER 06:33" tekrarı doğru mu?** Kayıt ✅ (`activeContext.md` 06:33:16 RENDER stale→koruma eksik→-2021→repair atlandı→orphan_sweep; guard'lar doğru çalıştı, aksiyon gerekmedi). Yerel `paper_trade.log`'da `06:33` 4 satır — hepsi 08-06 replay penceresi, canlı değil. Canlı teyit ❌.
3. **ENA pre-entry SL guard uygulandı mı?** Kod ✅ (`src/bot.py:718-731`, `src/config.py:619` `SL_EPSILON_TICKS=2`; guard sembol-bağımsız → koddan ENA'ya uzanıyor). ENA-spesifik commit/log ❌ — koddan çıkarım.
4. **DYDX reconciliation uygulandı mı?** Kod ✅ (`src/trading/entry_manager.py:430-489` — mkt_id yok + poz açık → `_emergency_close`; empty_response + poz açık → `_emergency_close`; commit `bc3f3ff`). Canlı olay ❌ — sunucu teyidi yok.

### 🔴 KRİTİK BULGU — yerel snapshot canlı sunucudan DEĞİL
- `output/paper_trade.log` run_id dağılımı: `paper-20260808-000537` (deploy edilen run) **0 eşleşme**. Mevcut run'lar 08-05/08-06/08-07 replay-test koşuları + `paper-20260808-181527` (sentetik BTCUSDT 50000 verili, `live_state.json`'da REPAIR_REQUIRED/BROKEN sentetik state). Yerel `output/*` canlı davranış kanıtı SAYILMAZ.
- `events_2026-08-08.jsonl` (203 event): entry 12 / exit 96 / sl_reject 39 / tp_reject 10 / orphan_cleaned 6 / ghost_cleaned 2 / force_close 38; tarih aralığı 02:04→21:16; run_id alanı yok — yerel test/replay event'leri, canlı değil.
- Canlı teyit engeli: `169.58.41.73` root + iki anahtar da "Permission denied (publickey,password)"; plink "OpenSSH SSH-2 private key" reddi. Geçerli SSH kullanıcısı/anahtarı gerekli.

### ⏭️ Sonraki adım
1. Kullanıcıdan geçerli SSH kimliği (kullanıcı/anahtar) → sunucuda `git log -1` + run id + TRAIL_MODE env teyidi (3. katman).
2. Onay gelene kadar 1-4 canlı davranışı "doğrulanamadı" olarak işle.

---

## Son İşlem: 2026-08-08 — RECOVERY tick_size FIX DEPLOY EDİLDİ + CANLI DOĞRULAMA + sunucu log incelemesi

### ✅ Deploy (kullanıcı direktifi: "fix'leri yerel yap, sunucuya sadece deploy")
- Sunucuda `git pull --ff-only` (`b9c2d53..daaeeb0`) + restart → screen **`366235.bot`** PID 366237, venv python, run `paper-20260808-000537`, cwd `/root/sniper/src`, HEAD **`daaeeb0`**. Deploy kaydı: `82b8a41`.
- Restart sonrası 0 ERROR / 0 CRITICAL / 0 Traceback; `trade_state.json` `_used_sweeps` 10 kayıt, tüm semboller `open:false`.

### 📈 Canlı doğrulama — trailing fix ÇALIŞTI (kök neden kapandı)
- **ALGOUSDT short SL kapanış +19.96:** entry 0.08993, initial SL 0.09353 → **trail#1 sl=0.08901/tp=0.08217 UYGULANDI** (tick=1e-05, ROUND_CEILING artık iyileşmeyi yutmuyor — fix öncesi tick=0.1'de 0.08901→0.1 dönüp reddedilirdi). Fiyat SL'yi test edip döndü, STOP_MARKET tetiklendi. **Recovery-recovered trade'lerde ilk gerçek trailing güncellemesi = kanıt.**
- **RENDERUSDT short manuel kapanış 0.00:** web emri `web_SS4TvtEAphlO5BKQHeOh` 08:49:11 FILLED, exit 1.323, qty 0.1 (eski pozisyon).
- 4 kapanış (ARBUSDT WS_FALLBACK +18.13, ADAUSDT SL -1.50, ALGOUSDT SL +19.96, RENDERUSDT TP 0.00). Açık pozisyon **yok**.

### 🔍 Sunucu log incelemesi (kullanıcı talebi)
- RENDER anomali (06:33:16): WS-ORDER FILLED → "SL stale event #1" → **koruma eksik (sl=False tp=True)** → -2021 immediately trigger → repair atlandı → orphan_sweep TP `1000000157506320` temizlendi; trailing adayları üretildi ama short SL yerleştirilemez (doğru guard davranışı) → 08:49:11 web emriyle kapanış. Guard'lar doğru çalıştı, aksiyon gerekmedi.
- `trail_skipped` akışı normal: `no_better_trail_candidate` + `identical_invalid_candidate_suppressed` (dedup çalışıyor).
- 08:48'de 731 sembol EXCHANGE_INFO tazelemesi (kendi kendini iyileştiren WS yolu) sağlıklı.

### 📊 Karar arka planı (baş mühendis raporu)
- **Continuation (B) ölü:** K=1.0 N=1/2/3 = -1,207,682/-1,194,755/-1,181,140; 9/9 varyasyon negatif (kaynak: backtest-sniper `reports/trailing_replay_ab_c.md`). A baseline +4,100,540 (PE% 60.9). Canlıya alınmadı.
- **D modu (ATR-chase activation) canlıda:** K=2.0/R=1.5 (commit `42de7d5`), tarama kaynağı `backtest-sniper/reports/trailing_activation_scan.md`.
- Baş mühendis raporu: `backtest-sniper/reports/chief_engineer_rapor_2026-08-08.md`.

### ⏭️ Sonraki adım
1. Baş mühendis raporunun iletimi (push vs doğrudan içerik) — kullanıcıya soruldu, cevap bekleniyor.
2. ATR-chase replay (K=0.5/1.0/1.5) parametre revizyonu — canlıda K=2.0/R=1.5 aktif olduğu için set kararı baş mühendisle.
3. Açık izler: `runtime.status` senkronizasyonu (integration_lifecycle 3 fail), entry/order_manager pre-existing kırıklar, ENA pre-entry SL guard, DYDX reconciliation, RENDER orphan/repair zinciri gözlem altında.

---

## Son İşlem: 2026-08-08 — RECOVERY tick_size PARITY FIX (yerelde yapıldı, deploy bekliyor)

### 🔴 Kök neden (kullanıcı tarafından teyit edildi)
- `recovery_manager.recover_positions` (recovery_manager.py:80) `ActiveTrade(...)`'i **`tick_size` geçirmeden** kuruyordu → `models.py` default'u `0.10` sessizce kullanılıyordu → **170/170 recovered trade** `tick=0.1` ile trailing normalize'u (ROUND_CEILING) her iyileşmeyi yutuyordu (`no_better_trail_candidate`, 214/214 trail_skipped).
- Matematik kanıt: ALGO raw `0.088888` → normalize(tick=0.1) `0.1` → `0.1 < 0.09353` false → skip; RENDER raw `1.320464` → `1.4` → `1.4 < 1.387` false → skip. **Doğru tick ile** (RENDER 0.001 → 1.321 < 1.387 ✓, ALGO 1e-05 → iyileşme ✓) hop üretilir.
- Etki yalnızca restart-recovered trade'ler (ALGO/RENDER) — yeni açılan trade'ler (`_try_entry`) doğru tick_size alıyor.

### 🔧 Fix'ler (kullanıcı direktifi: "fix'leri yerel makinede yap, sunucuya sadece deploy ediyoruz")
1. **`recovery_manager.py`** — 3 `ActiveTrade(...)` kurulumuna (SL/TP mevcut, emergency-close başarısız, SL/TP yeni) `tick_size=self._rest.get_tick_size(sym)` (try/except → 0.10 fallback + warning) eklendi; ayrıca `status=STATUS_ACTIVE`, `trail_count=0` (parity). `existing` güncelleme yollarına `existing["tick_size"] = tick_size` eklendi.
2. **`models.py`** — savunmacı default (madde 5): `tick_size: float = 0.10` → `tick_size: float | None = None` + `__post_init__` → None ise `log.critical` + 0.10 sentinel. "Sessiz yanlış default" sınıfındaki gelecek eksiklikler artık CRITICAL log basar.
3. **`trailing_manager.py`** — tek birim karşılaştırma (madde 3): `_fvg_multihop` opsiyonel `tick_size` parametresi aldı; verildiğinde hop kararı `_normalize_price` (SL kind) ile normalize birimde yapılır (long/short + ATR-chase fallback dahil), `trail_steps` loguna normalize SL yazılır. **Verilmediğinde (backtest `evaluate_trail`) raw davranış birebir korunur** — backtest kopyası kırmızı çizgisine dokunulmadı.
4. **`bot.py`** — `_build_fvg_scan_trail_extractor`: `_fvg_multihop(..., tick_size=trade.get("tick_size"))` geçiyor.
5. **`state_writer.py`** — `active_trade` bloğuna `"tick_size": trade.get("tick_size")` eklendi (bilerek izleme).
6. **`tests/test_recovery_manager.py`** — `TestRecoveredTradeFieldParity`: (a) yeni recovered trade tam şema taşır (`tick_size==0.00001`, `status==STATUS_ACTIVE`, `trail_mode=="fvg"`, `trail_count==0`, SL/TP/order-id doğru), (b) existing trade'de `tick_size` tazelenir.

### ✅ Test sonucu (yeni kırık YOK — baseline worktree ile karşılaştırıldı)
- Kapsam dosyaları: recovery+trailing+models **tamamı geçti**; state_writer 2 fail + test_bot 13 fail **baseline'da da aynı** (HEAD worktree'de kanıtlandı: `sl_status` eski şema, `mark_trade_closed`/`_exit_trade_legacy` yapı değişikliği).
- Diğer suite'lerdeki fail'ler (entry_manager `get_max_qty` mock await, order_manager `update_trail_orders` davranışı, `runtime.status` senkron, initial_protection yön validasyonu) — **tamamı dokunulmayan dosyaların pre-existing kırıkları**.
- Tam suite tek koşuda ~63 test sonrası asıldı (nondeterministic ağ testi) — kapsam dosyaları ayrı ayrı tamamlandı.

### ⏭️ Sonraki adım
1. **Deploy (kullanıcı onayı bekliyor):** sunucuda `git pull` + bot restart → recovery, ALGO/RENDER dahil tüm trade'leri doğru tick_size ile yeniden kurar (RENDER 0.001, ALGO 1e-05).
2. Restart sonrası doğrulama: `trades_history.jsonl` yeni recovered kayıtlarında gerçek tick_size + `live_state.json` `active_trade.tick_size` + trailing `[TRAIL]` hop logları.
3. `runtime.status` senkronizasyonu (integration_lifecycle 3 fail) ve diğer pre-existing kırıklar ayrı iz konusu.

---

## Son İşlem: 2026-08-07 — Continuation-confirm + is_placeable fix CANLI (baş mühendis direktifi)

### 🔧 Yapılan (commit `b9c2d53`, yerelde testli, deploy edildi)
- `trailing_manager.py`: `_fvg_close_confirmed` → **`_fvg_confirm_mode`** — retrace/continuation/invalidation üçlü ayrımı. Yön kontrolü: short `close < fvg.bottom`, long `close > fvg.top` = continuation (lehimize kırılma); aksi yön (`close > fvg.top` short için) = invalidation → None (karıştırılmaz). Continuation SL: short `fvg.bottom + atr_buffer`, long `fvg.top - atr_buffer`.
- `_fvg_multihop` artık **`current_price`** alıyor; hop sonrası is_placeable şartı (long `new_sl < price`, short `new_sl > price`) — fiyata çok yakın aday stale sayılır, üretilmez (ALGO 0.089049 vs fiyat 0.0897 örneği canlıda teyitli).
- `bot.py` `_build_fvg_scan_trail_extractor`: `_fvg_multihop(..., current_price=float(scoped_bars[-1].close))`.
- `tests/test_trailing_manager.py` +169 satır (retrace/continuation/invalidation/stale regression) — **55/55 passed**; pre-commit ruff/vulture temiz; 13 failed / 82 passed baseline ile birebir (ilgisiz).

### ✅ Deploy (3 katmanlı teyit)
1. **Hash:** sunucuda `git log -1` → `b9c2d53` (aac0e3e→b9c2d53 fast-forward).
2. **Grep:** `_fvg_confirm_mode` sunucuda trailing_manager.py:540/623.
3. **Davranışsal:** yeni run `paper-20260806-223127`, 28 sembol init, WS 56 stream, 8 pozisyon envanterde, ilk trailing taraması yeni kodla `no_better_trail_candidate`, LDOUSDT orphan STOP_MARKET temizlendi.

### ⚠️ Restart zorlukları (dokümante)
- `screen -dm` TTY'siz plink'te **sessiz başarısız** oluyor → `plink -t` + `TERM=xterm` şart.
- System `python3` (3.14.4) **dotenv içermiyor** → bot `/root/sniper/venv/bin/python3` ile çalışmak zorunda. Yeni screen `349790.bot` (PID 349791).

### 📊 Pozisyon durumu
- 9→8 (biri downtime ~3 dk'da borsa SL/TP ile kapandı). ALGOUSDT-0 dokunulmadı: trailing_count=0, sl=0.09353/tp=0.08669, uPnL ~+19.3.
- Açık iz: ENAUSDT 08-06 18:00'de SEIUSDT fix'inden (`aac0e3e`) sonra aynı `SL/TP direction fail` — pre-entry guard ENA'ya uzanmıyor.

### Sonraki adım
1. İlk canlı **continuation-trail** olayını gözlemle (şimdilik trail_skipped) — backtest A/B/C replay ile karşılaştır.
2. ENAUSDT direction-fail'ını baş mühendisle görüş (tick/eps kalibrasyonu).

---

## Son İşlem: 2026-08-06 — SEIUSDT direction-fail döngüsü fix (baş mühendis direktifi): PRE-ENTRY SL-EPS GUARD + SWEEP CONSUMPTION

### 🔴 Baş mühendis direktifi — ayrı ve ÖNCELİKLİ konu
- Sorun "trade açılıp zararla kapanıyor" DEĞİL, daha kötüsü: her 15m bar'da aynı ölü sinyali üretip **anında acil kapatma** — sistematik kaynak israfı (emir/fee/slippage her denemede) + izlenmesi zor gürültü.
- Kök neden net: FVG üst sınırı ile SL arası mesafe (0.0001) validasyon epsilon'undan (0.0002) küçük, ama bu **fill sonrası** yakalanıyor (`execute_live_entry` `[SL_TP_VALIDATION]`), entry'den ÖNCE değil.
- Doğru fix konumu: kontrolü **sinyal üretim/entry pipeline'ının başına** taşı — SL mesafesi eps altındaysa sinyali baştan reddet (MARKET emri + acil kapanma yerine).
- İki parçalı direktif: (a) aynı FVG neden her bar'da yeniden aday oluyor — invalidate edilmeli mi? (b) SL-eps kontrolünü entry-öncesi validasyona taşı, pozisyon hiç açılmasın.

### 🧩 KÖK NEDEN (a) — aynı ölü sinyalin her bar'da yeniden üretilmesi
- **`signal_engine.progress_rsm`** (signal_engine.py:78-83) `on_sweep()` çağrısını **`bar_index=None`** ile yapıyordu → `retrace_state.on_sweep` içindeki `is_sweep_used(sweep_id)` dedup'ı (`sweep_id = f"{direction}_{bar_index}"`, retrace_state.py:104-116) **atlanıyordu**; `_mark_sweep_used` de `None` ID ile kalıcı kayıt yapamıyordu.
- **`_try_entry` red yolları** `rsm.reset()` çağırıyor ama `ss.sweep_confirmed` **hiç temizlenmiyordu** → sonraki 15m bar'da `progress_rsm` aynı sweep'i yeniden onaylayıp aynı FVG'yi tekrar aday gösteriyordu → SEIUSDT direction-fail döngüsü.
- FVG scan'i aslında aynı seviyeyi "yeniden sunmuyor" — sorun, **sweep'in her bar'da yeniden tetiklenmesi** (önceki turlarda "cooldown yok" diye gözlemlenen şeyin gerçek kök nedeni buydu).

### 🔧 FIX (2 üretim dosyası + 1 config + 2 test dosyası)
1. **`src/trading/signal_engine.py` `progress_rsm`:** `on_sweep(...)` artık `bar_index=current.index` alıyor (dedup çalışır, sweep_id gerçek bar'a bağlanır) ve çağrı sonrası **`ss.sweep_confirmed = False`** → sweep tüketildi; yeni bir sweep yakalanmadan aynı sinyal bir daha tetiklenemez.
2. **`src/config.py`:** `SL_EPSILON_TICKS = 2` eklendi (borsa "immediately trigger" epsilon'u, `validate_protection_with_actual_fill` default'u ile birebir).
3. **`src/bot.py` `_try_entry`:** `validate_risk` kontrolünün hemen ardına **`1b. PRE-ENTRY SL-eps guard`** eklendi:
   - `tick_size > 0` ise `EntryManager.validate_protection_with_actual_fill(side, entry_price, sl, tp, Decimal(str(tick_size)), epsilon_ticks=cfg.SL_EPSILON_TICKS)`.
   - Geçemezse → `log.warning("[PRE-ENTRY] ... SL/TP eps icinde, sinyal reddedildi")`, `rsm.reset()`, `ss.sweep_confirmed = False`, return — **emir gönderilmez**.
   - `tick_size` 0.0 (bilinmiyor) → guard atlanır, fill-sonrası `validate_protection_with_actual_fill` güvenlik ağı olarak kalır.
4. **`tests/test_bot.py`:** `_setup_minimal_cfg`'ye `SL_EPSILON_TICKS = 2`; yeni `test_pre_entry_sl_eps_guard_rejects_before_order`.
5. **`tests/test_integration.py`:** `test_progress_rsm_idle_consumes_sweep_confirmed_once` (IDLE + sweep_confirmed → on_sweep çalışır, bayrak False olur) + `test_progress_rsm_consumed_sweep_does_not_retrigger` (tüketilen sweep bir sonraki bar'da yeniden tetiklenmez).

### ✅ Test sonucu
- test_bot: **27 passed / 13 fail** — fail'ler bilinen pre-existing (MIN_FVG_SIZE KeyError, TestExitTradeWiring); guard testi dahil TestTryEntry 6/6.
- test_integration + v2 + lifecycle: **47 passed / 19 fail** (önceki 45 passed + 2 yeni test) — 19 fail pre-existing (TestCheckExit API drift, get_max_qty MagicMock await, runtime.status).
- test_entry_manager + test_retrace_state: **119 passed**.
- ruff check temiz.

### Sonraki adım
1. Baş mühendis onayı sonrası Contabo deploy (`git pull` + `screen -S bot` restart).
2. Canlıda SEIUSDT için: `[PRE-ENTRY]` reddi (kötü senaryo) veya normal SL/TP kurulumu (iyi senaryo — tick tabanlı `MIN_SL_DISTANCE_TICKS=4` devrede); aynı sinyalin birden çok bar'da yeniden denenmemesi.
3. İlk gerçek trailing updated olayını canlıda gözlemleyip state-sync fix'ini (P2-4) kesin kapat.

---

## Önceki İşlem: 2026-08-06 — SEIUSDT kök neden + baş mühendis direktifi: TICK-TABANLI SL TABANI (MIN_SL_DISTANCE_TICKS)

### 🔬 Baş mühendis teşhisi (SEIUSDT SL/TP VALIDATION guard'ı)
- **Teşhis:** `MIN_SL_DISTANCE_PCT=0.0015` entry fiyatına göre hesaplanıyor ama Binance'in "immediately trigger" reddi **fill/current price'a göre epsilon** (2 tick, `validate_protection_with_actual_fill` epsilon_ticks=2) kullanıyor. SEIUSDT ~0.0414'te % tabanı ≈ 0.000062 — bu, borsa epsilon'u 0.0002'den küçük. FVG_SIZE_MAP'teki SEIUSDT 0.020 (backtest onaylı, score=1166) SUÇLU DEĞİL.
- **Doğrulama (log):** SEIUSDT 17:00:51/17:15:17 `[SL_TP_VALIDATION] SL=0.0415 <= actual_fill=0.0414 + eps=0.0002` → acil kapanma; risk_dist=7.5e-05 (SL 0.0415 vs fill 0.0414) — % tabanı (6.2e-05) geçiyor ama borsa epsilon'u (2e-04) geçemiyor.

### 🔧 Fix — SL hesap zincirinin SON adımına tick tabanlı mutlak taban (pre-entry + fill-sonrası)
- **config.py:** `MIN_SL_DISTANCE_TICKS = 4` eklendi (epsilon_ticks=2 × 2 güvenlik payı).
- **entry_manager.py:**
  - `apply_min_sl_distance(entry_price, sl, side, tick_size=0.0)`: `min_dist = max(entry_price * MIN_SL_DISTANCE_PCT, tick_size * MIN_SL_DISTANCE_TICKS)` (tick_size>0 ise).
  - `calculate_sl_tp(..., tick_size=0.0)` — imzaya opsiyonel parametre; iki `apply_min_sl_distance` çağrısına geçirildi.
  - `execute_live_entry`: `get_tick_size` tek çağrı (try öncesi), `calculate_sl_tp`'ye geçiriliyor, tick rounding aynı tick_dec kullanıyor.
- **bot.py `_try_entry`:** pre-entry SL/TP hesabında `get_tick_size` (önbellekli, başarısızlıkta 0.0 → eski davranış) alınıp `calculate_sl_tp`'ye geçiriliyor.
- **FVG_SIZE_MAP SEIUSDT 0.020'ye DOKUNULMADI** (baş mühendis direktifi).
- SEIUSDT senaryosunda: SL 0.041475 → tick tabanı 4×0.0001=0.0004 → SL 0.0418; `validate_protection_with_actual_fill` (eps=2 tick) ARTIK GEÇİYOR → acil kapanma yerine normal koruma kurulur.

### ✅ Test sonucu
- test_entry_manager: **86 passed** (5 yeni tick tabanı testi: SEIUSDT uçtan uca `test_short_seiusdt_narrow_fvg_tick_floor_passes_validation` + 4 `TestApplyMinSlDistance` tick testi).
- test_bot (26 pass) + test_integration/integration_v2/lifecycle (45 pass): kırılan YOK — 19 (integration) + 13 (test_bot) fail **pre-existing** (git stash baseline ile birebir doğrulandı: HEAD'de de aynı; `mark_trade_closed`/`_stage`/`MIN_FVG_SIZE` kayıp migration, TestCheckExit API drift, `get_max_qty` MagicMock await, runtime.status).
- ruff check + format temiz; mypy yalnızca dokunulmayan dosyalarda (trailing_manager/exit_lifecycle) pre-existing hata veriyor.

### ⚠️ Backtest parity notu
- Tick tabanlı taban **sadece canlı** `sniper` tarafına eklendi; `backtest-sniper` `analyzer_v5.py` aynı `adaptive_buf` formülünü kullanıyor ama tick tabanlı SL tabanı YOK. Canlıda dar FVG'li düşük fiyat sembollerinde SL itilir, backtest'te itilmez — küçük bir canlı/backtest sapması oluştu. Baş mühendise görüşülmesi gereken parity konusu (FVG_SIZE_MAP'e dokunmadan backtest'e aynı tabanın eklenip eklenmeyeceği).

### 🔍 Bu turun ek bulguları — direktif kapsamı DIŞINDA (baş mühendise gösterilecek)
Direktif yalnızca tick tabanlı tabanı kapsadı; SEIUSDT soruşturmasında bunun DIŞINDA kalan 2 gözlem not edildi:
1. **FVG kenarına giriş filtresi yok:** SEIUSDT sinyali FVG üst sınırından (0.0414) short'a giriyor; SL = FVG top + adaptive_buf → entry ile SL arası yapısal olarak dar. Entry fiyatı FVG kenarındayken SL'nin fill'e yakın kalması kaçınılmaz. Öneri: entry fiyatının FVG kenarına denk gelmesi durumunda ya FVG içi giriş (entry kenardan içeride) ya da bu senaryoda SL itme kuralı (backtest ile doğrulanmadan devreye ALINMAMALI — kullanıcı önceliği "backtest bekliyor").
2. **Başarısız entry sonrası cooldown yok:** SEIUSDT aynı sinyal her 15m bar'da yeniden üretildi (17:00:51 ve 17:15:17) ve her seferinde guard → acil kapanma. Başarısız/filtre-reddi entry sonrası sembole özel kısa cooldown (örn. 1 bar) yok — aynı FVG'li sinyal tekrar tekrar deneniyor.

### Sonraki adım
1. Bu fix deploy edilecekse Contabo'da `git pull` + bot restart; ardından SEIUSDT sinyali 15m bar'ında `[SL_TP_VALIDATION]` reddi yerine normal SL/TP kurulumu görülmeli.
2. Baş mühendis çıkınca: bu turun raporu + ek bulgular (FVG kenarına giriş filtresi, başarısız entry cooldown — direktif kapsamı dışında kalan öneriler, backtest parity notu).
3. ATR-chase replay paralel sürüyor; state-sync fix sonrası canlıya alınması görüşülecek.

---


### 🔬 Hipotez doğrulandı (genişletilmiş hal)
- **Hipotez:** orphan_sweep canlı emirleri tanıma işlemini `runtime.protection.sl_current` gibi state alanına bakarak yapıyorsa, trailing bu alanı güncellemediği için trailed SL emri (0.089049) orphan sayılıp iptal ediliyor; recovery eski/ham değerlerle (0.093530) yeniden kuruyor. NEARUSDT `sl_current=None` + SOLUSDT `no_better_trail_candidate` yanlış reddiyle aynı kök.
- **Doğrulama:** `reconcile_orphan_orders` (recovery_manager.py:744) ve `_known_protection_ids` (:706-742) `runtime.protection.sl_current`'i DEĞİL, trade dict flat alanlarını okuyor: `sl_order_id/tp_order_id/sl_order_id_prev/tp_order_id_prev/pending_*_order_id` + `*_history` (ProtectionLifecycleService varsa `known_ids(t)`'ye delege — protection_lifecycle.py:73-98).
- **Kök neden:** Canlıdaki trailing yolu `replace_protection` → `_replace_one` (order_manager.py:1136) yalnızca `protection_orders[kind]` + `trade["stop_loss"]`/`trade["take_profit"]` yazıyor; `sl_order_id`/`tp_order_id`'yi ve `runtime.protection`'ı güncellemiyordu → trailing sonrası yeni emir `known_ids`'te yok → orphan_sweep iptal ediyor → recovery ham değerlerle yeniden kuruyor. NEARUSDT `sl_current=None` ve SOLUSDT `no_better_trail_candidate` aynı kökün diğer belirtileri (trail karşılaştırması flat alanları okuyor).

### 🔧 Fix 1 — order_manager.py: `_replace_one` state senkronu (atomik)
- Import: `from trading.protection_lifecycle import _HISTORY_MAX` (döngüsel import yok — protection_lifecycle yalnızca models import ediyor).
- `_replace_one` başarı bloğu: `protection_orders[kind]` yazımına ek olarak `trade["sl"]`/`trade["stop_loss"]` (veya `trade["tp"]`/`trade["take_profit"]`) + yeni `_sync_replaced_order_id` çağrısı.
- `_sync_replaced_order_id`: flat `sl_order_id`/`tp_order_id` günceller, eski ID'yi `*_prev` + `*_history`'ye arşivler (`_HISTORY_MAX` cap), `_sync_runtime_protection` ile `runtime.protection.sl_current`/`tp_current`'e `ProtectionRef(slot=CURRENT)` yazar.

### 🔧 Fix 2 — known_ids'e `protection_orders` kaynağı (her iki trailing yolu)
- `ProtectionLifecycleService.known_ids` (protection_lifecycle.py:73-98) + `RecoveryManager._known_protection_ids` (recovery_manager.py:706-742): flat alanlara ek olarak `trade["protection_orders"]["sl"/"tp"].order_id`'yi de topluyor.
- Böylece eski trailing yolu (`update_trail_orders` → `protection_orders`) ile yeni yol (`replace_protection` → flat + protection_orders) birlikte tanınıyor.

### 🧪 Regression testi — tek testte üç semptom (TestStateSyncTrailOrphanRecovery, test_integration_lifecycle.py)
1. `test_trail_syncs_state_and_orphan_recovery_preserve` — ALGOUSDT senaryosu:
   - (a) `replace_protection` sonrası `sl_order_id=="TRAIL_SL"`, `tp_order_id=="TRAIL_TP"`, `runtime.protection.sl_current/tp_current` DOLU (None değil), flat `sl==0.0891`/`tp==0.0879` (tick 0.0001 normalize) → `no_better_trail_candidate` artık doğru karşılaştırır; eski REC_SL arşivden tanınır.
   - (b) `reconcile_orphan_orders`: trailing emirleri (TRAIL_SL/TRAIL_TP) iptal EDİLMEZ; yalnızca FOREIGN_ID iptal edilir.
   - (c) `recover_positions`: borsada trailed emirler açıkken mevcut SL/TP korunur (0.0891/0.0879), ham değerlere (0.093530/0.086690) DÖNMEZ; yeni koruma emri kurulmaz.
- Not: mock `get_order_type`/`get_order_price` lambda'ları gerçek davranışı veriyor (MagicMock MagicMock döndürürdü → recovery filtresi boş kalırdı).

### ✅ Test sonucu
- Regression testi GEÇTİ (1 passed).
- Etkilenen dosyalar (order_manager/protection_lifecycle/integration_lifecycle/recovery/trailing): 20 fail — **hepsi pre-existing** (git stash baseline ile birebir aynı: 20 failed, 144 passed → benimle 145 passed, yeni fail yok). Pre-existing fail'ler: TestCheckExit 17 (eski API drift) + TestExitStateTransitions 3 (runtime.status senkronu — ayrı konu).
- Tam suite: 70 fail — tamamı pre-existing (test_bot 15, trailing 17, session/snapshot/state_writer/user_data_handler/integration_v2/parity 50'lik set — git stash baseline 50 failed/140 passed ile birebir aynı).

### Sonraki adım
1. **ATR-chase replay** (K=0.5/1.0/1.5) paralel devam edebilir: `replay_trailing_v2.py`'ye long `close − K×ATR` / short `close + K×ATR` (SL-only); FVG-only/ATR-only/max-SL karşılaştırması. **Canlıya alınması bu state-sync fix'inden SONRA olmalı** (aksi halde ATR-chase kâr kilidi da recovery/orphan tarafından silinebilir).
2. Deploy: fix'i sunucuya `git pull` + bot restart ile al.
3. Pre-existing test fail'leri (TestExitStateTransitions runtime.status senkronu, TestCheckExit API drift) ayrı iş — bu tur kapsamı dışı.

---



### ✅ Deploy teyidi KAPANDI — üç katmanlı kanıt
1. **Git-hash:** Sunucuda `git log -1 --oneline` → `bc73b5c` (HEAD -> main, origin/main, origin/HEAD) — kod sunucuda `bc73b5c`.
2. **Davranışsal:** Sunucuda `grep -cE "P1-15_DEBUG|POST_ENTRY_DEBUG" output/paper_trade.log` → **0**. `[P1-15_DEBUG]` (bot.py:582) + `[POST_ENTRY_DEBUG]` (bot.py:838, order_manager.py:386) hiçbir canlı seviyede basılmıyor.
3. **Post-entry yolu canlıda:** Restart (00:22:52) sonrası **9/9 entry** `[POST_ENTRY] SL/TP sanity check OK` INFO seviyesinde: NEAR 04:30, APT 05:00+05:15, LDO 05:30, PYTH 06:00, SEI 09:15, TIA 10:00, NEAR 11:30, XRP 11:45. WARNING sıfır → `bc73b5c` fix'i aktif.
- Kullanıcının "deploy edilen kod commit'lediğimizle eşleşmiyorsa emek boşa gider" endişesi giderildi → **ATR-chase replay (K=0.5/1.0/1.5) için yeşil ışık.**

### ✅ Bot çalışıyor (10:12 duraklaması yanlış alarmdı)
- Sunucuda PID 317218 `python3 bot.py` (pts/0, çalışıyor). Log 11:57'ye kadar akıyor: `[TRAIL]` döngüsü canlı (XRPUSDT-545 trail_skipped, LDOUSDT-520 identical_candidate_already_applied). 10:12'deki "duraklama" indirilen kopyanın o anki durumu — bot değil.

### ✅ SOLUSDT koruması kapalı (önceki tur) + yeni gözlemler
- Recovery korumaları yeniden yerleştiriyor (3× `[RECOVER] SOLUSDT`); kullanıcı Binance open-orders teyidi.
- **Yeni entry'ler:** NEAR 11:30 (sl=1000000157984872), XRP 11:45 (sl=1000000157994762).
- **Stale:** 11:27 ALGOUSDT TP stale #1; toplam stale'ler (SOL #1, LINK #2-#8+backstop#9, ARB #1, GMX #1, SEI #1-#4, ALGO #1) hepsi doğru iptal — WS FILLED gecikmesi sürüyor, kök neden açık (P1-15 ikinci öncelik).

### 🔍 State/raporlama bug'ı deseni sürüyor (baş mühendise görev önerisi)
- NEARUSDT (kapanmış): trailing_count=1, `protection_orders` DOLU (sl 1000000157804391, fingerprint `short|1.710|1.665|519|1000`) ama `runtime.protection.sl_current=None` → trailing koruma değiştirdiğinde runtime state'e yazılmıyor (26-trade bulgusuyla tutarlı).

### Sonraki adım
1. **ATR-chase replay** başlat: `replay_trailing_v2.py`'ye long `new_sl = close − K×ATR`, short `new_sl = close + K×ATR` (yalnız SL iyileştirmesi); K=0.5/1.0/1.5 + FVG-only/ATR-only/max-SL karşılaştırması. Deploy teyidi kapandığı için karar verebilir durumdayız.
2. State bug'ı (trailing sonrası `runtime.protection` yazılmıyor) baş mühendise yeni görev olarak önerildi.
3. `[VERSION] git=<hash>` startup log satırı önerisi — deploy teyidini gelecekte tamamen log'dan yapılabilir kılar (opsiyonel, düşük öncelik).

---

## Son İşlem: 2026-08-06 güncel log analizi — deploy teyidi DAVRANIŞSAL DOĞRULANDI; stale backstop çalıştı; state bug deseni sürüyor

Yeni indirilen 3 dosya (paper_trade.log 1.6MB restart 00:22:52 → 10:12, trades_history.jsonl 448 trade, events_2026-08-06.jsonl 32 event) analiz edildi.

### ✅ Deploy teyidi — davranışsal olarak DOĞRULANDI (git-hash için sunucu komutu hâlâ tek kesin yol)
- Restart sonrası **7 yeni canlı entry**: NEAR 04:30, APT 05:00+05:15, LDO 05:30, PYTH 06:00, SEI 09:15, TIA 10:00. Hepsi `[ORDER] SL OK/TP OK` + `[POST_ENTRY] sanity check OK` INFO seviyesinde.
- Post-entry check 7/7 çalıştı; `[POST_ENTRY_DEBUG]` WARNING (bot.py:838 + order_manager.py:386) **sıfır** → `bc73b5c` + `1a439c9` canlıda (önceki turda bu yol "hiç çalışmadı, kanıtlanamaz"dı — artık 7 kez çalıştı).
- `[P1-15_DEBUG]` (bot.py:582) sıfır → `1a439c9` doğrulandı.
- Trail reason'ları `identical_candidate_already_applied` (trailing_manager.py:240) ve `no_protection_update_required` (:312) kaynak kodla birebir eşleşiyor → canlı kod = yerel HEAD davranışı.
- Bot başlangıçta hâlâ commit hash basmıyor (log'da `[VERSION]` yok; yalnızca JSONL `schema_version`). Kesin teyit: sunucuda `git log -1 --oneline` → beklenen `bc73b5c`.

### ✅ SOLUSDT koruması — endişe TAMAMEN kapandı
- events: SOLUSDT art arda `orphan_cleaned` + **`[RECOVER] SOLUSDT icin Binance uzerinde SL/TP emirleri olusturuldu`** (02:01 sl=1000000157641573/tp=...1577, 02:02 ...2493/...2497, 02:15 ...9249/...9255). Recovery, iptal edilen korumaları borsaya YENİDEN yerleştiriyor. Kullanıcının Binance open-orders teyidi + recovery log'ları → korumasız-pozisyon senaryosu geçersiz.

### ✅ P1-15 stale backstop ÇALIŞTI (exit_lifecycle)
- LINKUSDT SL stale #1→#8 (04:42-04:57), **#9 backstop**: "pozisyon kapanmisti, active_trades'ten cikariliyor" — pozisyon zaten kapanmıştı, trade temizlendi; stale zinciri kırıldı.
- ARBUSDT TP stale #1 (05:00), GMXUSDT TP stale #1 (07:17), SEIUSDT SL stale #1→#4 (09:59-10:02), SOLUSDT SL stale #1 (02:16) — hepsi doğru iptal edildi. WS FILLED gecikmesi SÜRÜYOR (P1-15 kök neden açık, mitigasyonlar çalışıyor).

### 🔍 State/raporlama bug'ı deseni SÜRÜYOR
- NEARUSDT trailing yaptı (trail_count=1, trail_steps dolu, `protection_orders` **DOLU**: sl `1000000157804391`/tp `1000000157804396`, `last_applied_fingerprint=short|1.710|1.665|519|1000`) ama `runtime.protection.sl_current=None`. Trailing koruma değiştirdiğinde runtime state'e yazılmıyor → 26-trade bulgusu (2026-07-30→08-05) ile tutarlı, sistemik.
- Yeni trade'lerde (NEAR/APT/LDO/PYTH/SEI/TIA) `entry_order_id` DOLU; recovered'larda (ENA/ARB/TIA/GMX) boş — beklenen.

### Sonraki adım
1. Baş mühendise karar isteği: (a) recovery-state bug'ı (trailing sonrası `runtime.protection` yazılmıyor) yeni görev olarak eklensin mi? — öneri: evet, orta öncelik; (b) ATR-chase replay K=0.5/1.0/1.5 başlatılsın mı? — öneri: EVET, deploy teyidi davranışsal kapandı.
2. Kullanıcıdan sunucuda `git log -1 --oneline` ile kesin hash teyidi (opsiyonel, davranışsal kanıt yeterli).
3. Kalıcı: bot startup'ına `[VERSION] git=<hash>` log satırı önerisi (deploy teyidini log'dan yapılabilir kılar) — onay bekliyor.

---

## Son İşlem: 2026-08-06 canlı teyit — log fix CANLI doğrulandı; SOLUSDT/ONDO korumasız tespit edildi (KRİTİK)

Yeni sunucu dosyaları (paper_trade.log 552 satır run `paper-20260805-212252`, trades_history.jsonl 440, events_2026-08-06.jsonl) analiz edildi.

### ✅ (1) Log seviyesi fix'i CANLI DOĞRULANDI — KAPANDI
- Restart **00:22:52** (`[HISTORY] 440 trade gecmisten yuklendi`) sonrası tüm pencerede (→00:41:00) **sıfır** `[POST_ENTRY_DEBUG]` / `[P1-15_DEBUG]` satırı. `bc73b5c` (bot.py:838 `log.warning`→`log.debug`) sunucuda aktif. Daha önce 00:03:06'da görünen WARNING yok.
- 28 sembol LEVERAGE + PREFILL + WARMUP + INIT normal; `[STATE] reconcile: tüm semboller zaten güncel`.

### 🔴 (2) SOLUSDT teyidi — DÜZELTME: pozisyonlar KORUMALI (çıplak-pozisyon bulgusu GEÇERSİZ)
- **Kullanıcı Binance teyidi (2026-08-06 00:54): "bütün pozisyonların emirleri mevcut".** SOLUSDT için görünen açık korumalar: **TP Market ≥ 74.7600** + **SL Market ≤ 73.8300**, her ikisi 10.48 SOL, GTC, zaman damgası **2026-08-05 18:15:03**. → `SOLUSDT-0` korumalı; "restart sonrası koruma yeniden konmadı / çıplak pozisyon" hipotezi **YANLIŞ**.
- Düzeltilen yorum: `events_2026-08-06.jsonl`'deki 00:04:24 `orphan_cleaned` (SOL SL `1000000157356807` + TP `1000000157356808`) ve 00:03:47 ONDO `orphan_cleaned` iptalleri **güncel koruma çiftini değil, eski/orphan kalmış bir çifti** temizledi (08-03 BULGU 3'e benzer zararsız çift-koruma penceresi temizliği). Restart sonrası log'da `[POST_ENTRY]`/`SL OK` görünmemesi koruma yokluğu değil — korumalar zaten borsada duruyordu.
- **Doğrulanacak (isteğe bağlı, düşük öncelik):** kullanıcıdan SOLUSDT emirlerinin order ID'si alınıp `1000000157356807/1000000157356808` ile karşılaştırılarak 00:04:24 iptalinin ne olduğu kesinleştirilebilir.
- **Gözlem (yeni soru, ayrı konu):** trailing her ~1dk `[TRAIL] trail#1 sl=74.092724 tp=75.092724` kandidat üretiyor ama eşzamanlı `trail_skipped | no_better_trail_candidate` ile UYGULAMIYOR — mevcut SL 73.83/TP 74.76'dan daha iyi görünen kandidat (long'da SL 73.83→74.09) sürekli reddediliyor. Bu, `no_better_trail_candidate` (FVG trailing kandidat değerlendirme) sorununun devamı; koruma varlığıyla ilgisi yok. ATR-chase trailing (K=0.5/1.0/1.5) bu davranışın da iyileştirme adayı.

### 🟡 P1-15 stale hâlâ canlıda
- 00:31:01 `[WARNING] [EXIT] LINKUSDT SL stale event #1 — pozisyon hala acik, exit iptal` — WS FILLED gecikmesi (87–353sn) sürüyor; mitigation'lar devrede (exit iptal doğru).

### Sonraki adım
1. `recovery_manager` / `order_manager` orphan_sweep kodunu doğrula (state'teki order_id'leri temizliyor mu; restart koruma restore ediyor mu).
2. Koruma restore fix'i: restart'ta koruma borsada yoksa (open_orders kontrolü) YENİDEN KOY.
3. ATR-chase trailing replay (K=0.5/1.0/1.5) yerelde devam — SOLUSDT teyidinden bağımsız.

---

## Son İşlem: Log seviyesi düzeltmesi + baş mühendis onaylı öncelik sırası (2026-08-05)

`[P1-15_DEBUG]` (`src/bot.py:582`, check_exit öncesi) ve `[POST_ENTRY_DEBUG]` (`src/trading/order_manager.py:385`, get_open_order_ids) logları `WARNING` → `DEBUG` çekildi (commit `1a439c9`). `trail_skipped` bir log değil — `paper_trade_logger.py` JSONL telemetri event'i, log seviyesi kapsamına girmedi.

**Baş mühendis onaylı öncelik sırası (2026-08-05):**
1. **Log seviyesi** — ✅ YAPILDI (`1a439c9`)
2. **Stale event kök neden araştırması** — WS FILLED gecikme aralığı 87–353 sn'nin kaynağı: Binance push mu, bot event loop tıkanması mı? Dashboard/alert DEĞİL; ölçümle başla (emir zamanı vs WS FILLED delta serisi).
3. **ATR-chase trailing** — K=0.5/1.0/1.5 replay doğrulamalı (2026-08-04 A/B/C replay'den ayrı iş; is_placeable uyumlu K seçimi).
4. **FVG gevşetme** — SIRADA DEĞİL, "backtest bekliyor" kovasında: %87.8 red oranı başlı başına kanıt değil (wrong_direction doğru çalışıyor); gevşetilirse hangi trade'ler kabul edilir + kaçı kârlı çıkar somut backtest kanıtı gelmeden sıraya alınmayacak.

---

## Son İşlem: Trailing yol kalıcılığı (#3) — trail_mode state'e yazıldı (2026-08-04)

Restart sonrası recover edilen trade'lerde `trail_level_extractor` closure'ı kayboluyor ve trailing `_default_level_from_swings` (swing) yoluna düşüyordu — canlı logdaki 3692 `no_better_trail_candidate` + yalnızca 1 güncelleme ile ilgili kök nedenlerden biri. Fix:

- `ActiveTrade.trail_mode: str = "fvg"` (models.py) — JSON-safe string, state/JSONL'ye yazılabilir.
- Entry (bot.py) + `recover_positions` (recovery_manager.py, 2 recovery sitesi) `trail_mode="fvg"` set ediyor.
- `_on_1m_close`'ta `trail_level_extractor` callable değilse FVG extractor yeniden kuruluyor → restart sonrası aynı yol korunuyor.

Test: +2 yeni (test_bot `test_rebuilds_fvg_extractor_when_missing`, test_models `TestActiveTrade::test_trail_mode_defaults_to_fvg`); recovery testine `trail_mode == "fvg"` assert eklendi. TestOn1mClose'daki 2 stale test (eski `evaluate_trail` API, await edilemeyen MagicMock) güncel `orchestrate_trail` API'sine uyarlandı. Baseline 72 fail → 70 (0 yeni); hedefli 58/58 geçti. Kalan 70 fail pre-existing (parity/SOLUSDT, TestCheckExit stale API, TestExitTrade AttributeError vb., #3 ile ilgisiz).

Öncesi A/B/C trailing replay (2026-08-04): close-confirm gevşetmesi 0 ekstra hop → **elendi**; price-based ATR-chase 9/10 trade'de tetikleniyor → K=0.5/1.0/1.5 replay + is_placeable uyumlu K seçimi **ayrı iş** olarak sırada.

---

## Son İşlem: P1-16 notional sürümü CANLI — deploy + restart (2026-08-04 00:00)

Sunucuda `git pull` + bot restart tamamlandı (2026-08-03 23:56:26) → **P1-16 notional-bazlı fix (`1b0b647`) artık canlıda**: `[HISTORY] 367 trade gecmisten yuklendi` (önceki 22:28 koşusunda 364'tü, 3 trade eklendi). İlk floor'lu sürüm (`694b11d`, 22:28 deploy) hiç canlıya alınmamıştı — devreye giren sürüm notional-bazlı.

Canlı doğrulama beklentisi: normal akışta exchange info cache dolu olduğundan gerçek `LOT_SIZE.maxQty` kullanılır (davranış değişmez). Gerçek bir cache miss'inde `[MAX_QTY] <sembol> ... conservative notional tavan` WARNING görülür; fiyat hiç yoksa `MaxQtyUnavailableError` → emir açılmaz (reddedilir). Deploy sonrası logda hata olmaması beklenir (STRKUSDT pencerede CBDR kilitlenip entry üretmediği için -4005 path'i pasif kalabilir).

---

## Son İşlem: P1-16 ek düzeltme — notional bazlı conservative tavan + fiyat yoksa reddet (2026-08-03 23:30)

Doğrulama: **fiyatsız `get_max_qty()` çağrısı MÜMKÜN** — `estimate_market_price()` ticker REST hatasında `0.0` döner ve `BinanceRESTClient`'ta fiyat cache'i yoktu. Çağıranlar: `entry_manager.py:385` (emir clamp), `order_manager.py:598` (parçalı SL/TP), `recovery_manager.py:244` (aynı).

Direktif uyarınca `MAX_QTY_DEFAULT_FLOOR` (sabit quantity 1000) **kaldırıldı** → notional bazlı oldu:

- `_conservative_max_qty()` yeni zincir: sembol override → **canlı fiyat** ile `MAX_QTY_DEFAULT_NOTIONAL/price` → **stale fiyat** (`_last_price_cache`, sembolün en son başarılı ticker fiyatı) ile aynı formül → fiyat hiç yoksa **`MaxQtyUnavailableError`** (emir açılmaz, "conservative default" değil "reddet").
- Sabit 1000 quantity'nin sorunu: yüksek fiyatlı sembollerde (BTC ~100K → 1000×100K = 100M USDT) devasa tavan üretiyordu — conservative değil.
- `entry_manager`: `MaxQtyUnavailableError` → `EntryExecutionResult(success=False)` (emir reddedildi). `order_manager`/`recovery_manager`: parçalı SL/TP atlanır, closePosition akışı korunur.
- `binanceRESTClient`'a `_last_price_cache: dict[str, float]` eklendi (her başarılı fiyat çekiminde güncellenir).

Testler: `TestGetMaxQty` 6 test — `test_returns_max_qty`, `test_notional_cap_when_price_known`, **`test_stale_price_when_fresh_fails`** (yeni), **`test_rejects_when_no_price`** (yeni), `test_override_wins`, `test_missing_symbol_with_price_returns_notional_cap`. Floor testleri kaldırıldı.

**Sonuç:** test_bot_binance + test_entry_manager + test_order_manager + test_recovery_manager = **220 passed**; ruff temiz. `test_bot.py` 15 fail + trailing/session/snapshot vb. 29 fail **pre-existing** (git stash ile doğrulandı, bu işlemle ilgisiz).

**Not:** İlk sürüm (694b11d, floor'lu) 22:28'de deploy edildi; notional-bazlı sürüm **23:56:26'da restart ile canlıya alındı** (`1b0b647`, 367 trade yüklendi). bugs.md'de P1-16 KAPANDI (✅), canlı doğrulama log gözlemiyle yapılacak.

---

## Son İşlem: Deploy sonrası log analizi — trailing fix CANLI, P1-16 henüz değil (2026-08-03 23:11)

`output/paper_trade.log` (1159 satır, 22:28→23:10, run `paper-20260803-192800`) incelendi.

- **Bot 22:28'de restart oldu** → ENAUSDT trailing fingerprint fix'i (9263516) deploy edilmiş durumda ve **CANLI doğrulandı**: tüm pencerede **sıfır** `identical_invalid_candidate_suppressed` ve **sıfır** `candidate_not_placeable`. ENAUSDT her dakika `trail_skipped` reason=`no_better_trail_candidate` ile normal şekilde değerlendiriliyor (SL 0.0906, high 0.0915-0.0920 → meşru "daha iyi aday yok" durumu, suppress kilidi yok).
- **P1-16 fix (694b11d, 23:08 push) HENÜZ CANLI DEĞİL**: 23:08 sonrası restart yok; ayrıca STRKUSDT tüm pencerede CBDR kilitlenmedi (lock=False, sweep=False) → entry denemesi yok. P1-16'nın aktif olması ve doğrulanması için **bot restart'ı gerekiyor**.
- Normal işleyiş: ONDOUSDT 22:31 TP exit +5.34, 22:45 yeni long entry qty=3283 @0.37640. ARBUSDT 23:01:29 TP stale event #1 → "pozisyon hala açık, exit iptal" (P1-15 stale handling doğru).
- Hata yok: `-4005/-2021/-1007/-4130/WS_FALLBACK/orphan/force_close/reconnect` hiçbiri görülmedi. WARNING'ler yalnızca beklenen `[P1-15_DEBUG]` + `[POST_ENTRY_DEBUG]`.

**Sonraki adım:** sunucuda `git pull` + bot restart → P1-16 conservative default canlıya alınacak.

---

## Son İşlem: P1-16 fix — get_max_qty cache miss'te conservative default (2026-08-03)

Baş mühendis kararı netleşti: **emri geciktirmek yerine conservative default max_qty**. Gerekçe: sistemin failure mode'u "biraz küçük pozisyon" olsun, "belirsiz bekleme" veya "clamp'sız aşırı büyük pozisyon" olmasın. Emri geciktirmek fırsat kaybı (STRKUSDT'de yaşanan sorun) ve belirsiz order queue/race riski taşıyordu.

### Değişiklik: `src/bot_binance.py` + `src/config.py`

- **`get_max_qty()`** artık cache miss'te **asla 0.0 dönmez** — `0.0` dönmek entry_manager'daki `max_qty > 0` guard'ını atlayıp emri limitsiz qty ile exchange'e gönderiyordu (STRKUSDT -4005).
- Yeni **`_conservative_max_qty()`** yardımcısı, öncelik sırasıyla:
  1. `cfg.MAX_QTY_DEFAULT_OVERRIDES` — sembol bazlı sabit tavan (opsiyonel).
  2. `cfg.MAX_QTY_DEFAULT_NOTIONAL / fiyat` — fiyat bazlı conservative tavan (sembole özel volatiliteyi fiyat üzerinden hesaba katar). Notional, risk engine tipik notional'ının alt sınırına yakın: `MAX_QTY_DEFAULT_NOTIONAL = 500.0` USDT.
  3. `cfg.MAX_QTY_DEFAULT_FLOOR = 1000.0` — fiyat da alınamıyorsa sabit tavan.
- `MAX_QTY_DEFAULT_OVERRIDES` dict'i config'de boş tanımlandı — düşük fiyatlı semboller için gerektikçe doldurulacak.
- Cache dolduğunda normal akışa döner (gerçek LOT_SIZE.maxQty okunur).

### Testler: `tests/test_bot_binance.py` — `TestGetMaxQty` güncellendi/genişletildi

1. `test_returns_max_qty` (mevcut) — cache doluysa gerçek maxQty döner.
2. `test_returns_floor_on_missing` — LOT_SIZE.maxQty eksik + fiyat yok → floor (> 0).
3. `test_notional_cap_when_price_known` — fiyat varsa notional/price tavanı.
4. `test_override_wins` — sembol override öncelikli.
5. `test_missing_symbol_returns_floor` — sembol hiç yoksa bile 0.0 dönmez.

**Sonuç:** test_bot_binance (79) + test_entry_manager (81) + test_order_manager/recovery (59) tümü pass; ruff temiz. Trailing suite'teki 17 fail pre-existing `TestCheckExit` API drift (bu işlemle ilgisiz, baseline ile aynı).

### bugs.md güncellendi

P1-16 durumu `🐛 AÇIK` → `🔧 FIX YAZILDI — pending deploy`. Fix detayı ve "neden emri geciktir seçilmedi" gerekçesi eklendi.

---

## Son İşlem: ENAUSDT trailing kilitlenme fix'i — fingerprint'e bucket'lı fiyat (2026-08-03)

Baş mühendis onayı ile BULGU 1 fix'lendi. **Zaman bazlı expiry kullanılmadı** — fingerprint fiyat içerecek şekilde güncellendi.

### Değişiklik: `src/trading/trailing_manager.py`

- **`_fingerprint()`** (eski :472) artık fiyat bucket'ı içerir: `f"{side}|{sl}|{tp}|{source_bar_index}|{price_bucket}"`.
  - Bucket = `max(tick_size * epsilon_ticks, abs(price) * 0.001)` — sembolün tick precision'ı ile %0.1 fiyat tabanı arasından büyük olanı (mikro-noise emilir: 0.09231 vs 0.09232 aynı bucket).
  - Fiyat/tick_size verilmezse eski davranış (`-` bucket) korunur.
  - `@staticmethod` → instance method (config.epsilon_ticks erişimi için).
- **`compute_trail_candidate(trade, bars, current_price=None)`** — `current_price` opsiyonel parametre, fingerprint'e geçirilir.
- **`orchestrate_trail()`** — `compute_trail_candidate` çağrısına `current_price=current_price` geçirir.

### Davranış

- Fiyat lehine **bucket atlarsa** yeni fingerprint → `identical_invalid_candidate_suppressed` bypass → candidate yeniden değerlendirilir (ENABUG çözümü).
- Fiyat aynı bucket'ta kalırsa suppress devam eder — gereksiz log/CPU üretilmez (suppress'ün amacı korunur).
- `last_applied_fingerprint` de fiyatlı olduğundan, fiyat bucket'ı değişmedikçe aynı SL/TP tekrar yerleştirilmez.

### Testler: `tests/test_trailing_manager.py` — `TestFingerprintPriceBucket` (3 yeni test)

1. `test_price_inside_same_bucket_shares_fingerprint` — mikro-noise (109.0 vs 109.4) aynı fingerprint.
2. `test_price_crossing_bucket_changes_fingerprint` — 109.0 vs 110.5 farklı fingerprint.
3. `test_suppress_lifted_when_price_moves_favorably` — ENABUG regression: not placeable → suppress → fiyat lehine bucket atlayınca `updated`.

**Sonuç:** trailing manager dosyası 30 passed (17 pre-existing eski API fail — bu işlemle ilgisiz, baseline ile aynı), ruff temiz.

### Eş zamanlı: bugs.md'ye P1-16 eklendi (STRKUSDT -4005)

Baş mühendis önerisi doğrultusunda `memory-bank/bugs.md`'ye **P1-16** eklendi: entry max_qty clamp cache boşken atlanıyor (`get_max_qty` cache miss → `0.0` → clamp guard atlanır). Fix önerisi: emri geciktirip cache'i beklet veya conservative default max_qty. P1-6'nın eksik tamamlayıcısı.

---

## Son İşlem: Restart öncesi paper_trade.log analizi — trailing kilitlenme bug'ı bulundu (2026-08-03)

`output/paper_trade.log.20260803_212142.bak` (restart öncesi sunucu logu, 16.252 satır) analiz edildi.

### 🔴 BULGU 1 (BUG): ENAUSDT trailing kilitlenmesi — `identical_invalid_candidate_suppressed`

- **20:45:00** `candidate_not_placeable`: candidate `sl=0.092235`, fiyat ~0.0923'e yakındı → `is_placeable` (trailing_manager.py:185 `sl < current_price - epsilon`) reddetti → `protection_state["last_invalid_fingerprint"]` set edildi (:260).
- **21:01 → 21:21** (~35 dk): her 1m'de `[TRAIL] trail#1 sl=0.092235 tp=0.095635` üretiliyor ama `identical_invalid_candidate_suppressed` (:244-257) ile reddediliyor.
- **Kök neden:** `_fingerprint()` = `f"{side}|{sl}|{tp}|{source_bar_index}"` (:472) **fiyat içermiyor**. `is_placeable` fiyat-bağımlı, ama `last_invalid_fingerprint` cache fiyatsız → fiyat lehine değişse bile aynı candidate sonsuza dek suppress. SL 0.0906'da kaldı, fiyat 0.0925-0.0929'a çıktı — kâr korunmadı.
- **Öneri:** ya fingerprint'e `current_price` dahil et, ya `candidate_not_placeable` sonrası cache'e fiyat koşulu ekle, ya da placeability fiyat duyarlı olduğu için cache'i bir sonraki bar'da geçersiz kıl.

### 🟠 BULGU 2: STRKUSDT entry -4005 (max quantity) — fırsat kaçtı (21:15:15)

RISK ENGINE `QTY=93116.1146` → MARKET `-4005 "Quantity greater than max quantity"` → trade kaydedilmedi. `entry_manager.py:385-386` max_qty clamp muhtemelen `get_max_qty` cache henüz boşken 0 döndüğü için atlandı (aynı saniyede `[EXCHANGE_INFO] 731 sembol yüklendi`).

### 🟡 BULGU 3: GMXUSDT trail sonrası 13 sn çift koruma penceresi (21:01:14→27-28)

Trail güncellemesinden sonra eski entry SL/TP emirleri (id ...2931/...2933) orphan_sweep ile 13 sn sonra temizlendi. Zararsız; eski+yeni koruma kısa süre çakıştı.

### ✅ Doğru çalışanlar

- GMXUSDT 09:35-09:40 stale event #1-6 iptal edildi → WS FILLED ile nihai exit doğru commit (P1-15 mitigasyonları çalışıyor).
- `no_better_trail_candidate` akışı normal.
- `[P1-15_DEBUG]` WARNING her bar tüm aktif pozisyonlar için basılıyor — log hacmi yüksek (gözlem).

(Önceki işlemler aşağıda.)

## Son İşlem: Backtest parametre optimizasyonu → config default'ları sabitlendi (2026-08-03)

`src/config.py` default değerleri değiştirildi (kullanıcı onayı, paper trade):

- **`TP_RR` = 2.0 → 1.8** (config.py:523)
- **`ATR_TRAIL_MULT` = 0.25 → 0.10** (config.py:534)

Her ikisi de `os.environ.get("SNIPER_TP_RR", ...)` / `os.environ.get("SNIPER_ATR_TRAIL_MULT", ...)` üzerinden okunuyor — env var set edilmezse yeni default'lar geçerli (1.8 / 0.10). Canlı/paper bot restart edildiğinde otomatik yeni değerlerle çalışır.

**Gerekçe (backtest kanıtı):** `backtest-sniper` (root repo'da, gitignore'da) `analyzer_v5.py` ile yapılan sweep'lerde 28 coinin 28'i de iyileşti: toplam net PnL +3582448 → **+4100540 (+14.5%)**, MaxDD tüm coinlerde düştü. Küçük/düşük volatilite coinler en çok kazandı (PYTH, TIA, STRK, SEI, ENA, LDO — Score 1000+). ATR 0.10, 3 coin cross-coin doğrulamasında (GMX/BNB/SOL) monotonik iyileşme gösteren sweep'in daha yüksek ucu; 0.05/0.01 daha kârlıydı ama 15m kapanış bazlı backtest'te canlıda whipsaw riski nedeniyle elendi.

**Not:** Env override canlı botu da etkiler — `SNIPER_TP_RR`/`SNIPER_ATR_TRAIL_MULT` ortamda kalıcı set edilirse bot onları kullanır. Şu an default'lar zaten yeni değerler olduğundan gerek yok.

(Önceki işlemler aşağıda.)

## Son İşlem: CBDR kilit log metni düzeltildi (2026-08-03)

`src/bot.py:465` — `[SKIP] %s CBDR henuz kilitlenmedi — entry engellendi` → `[SKIP] %s CBDR henuz kilitlenmedi — akis baslatilmadi`.

Gerekçe: "entry engellendi" yanıltıcıydı — bu satır `evaluate_trigger`'dan ÖNCE (satır 469), yani ortada engellenecek bir entry adayı yokken basılıyor. Gerçekte olan: CBDR kilitli olmadığı için trigger değerlendirme akışı hiç başlatılmıyor. Kod akışına dokunulmadı — sadece log metni gerçeğe uygun hale getirildi.

Akış netleştirme (kullanıcı ile birlikte): terminaldeki "CBDR ✅ LOCKED → SWEEP → FVG → TRIGGER → ENTRY" sırası kod akışının birebir karşılığı — `cbdr_locked` (464) kapısı geçilmeden `evaluate_trigger` (469) ve `_try_entry` (497) çalışmaz. `display_fvg_status` (437) display'i kapının ÖNÜNDE çalıştığı için "ENTRY BEKLENIYOR" görüntüsü CBDR kapısından bağımsız basılabiliyordu — bu sadece görüntü, gerçek karar kapının arkasında.

(Önceki işlemler aşağıda.)

## Son İşlem (önceki): MARKET empty_response reconcile guard eklendi (2026-08-02)

`src/trading/entry_manager.py` `execute_live_entry()` — HTTP 408 / `-1007` ("Send status unknown; execution status unknown") senaryosu için yeni reconcile kontrolü (satır ~445):

- **Tetikleyici:** `not mkt_id and actual_qty <= 0` — hem orderId hem qty yok, tam belirsiz durum (timeout/empty_response). Mevcut 414. blok (`actual_qty > 0`) bu senaryoda hiç çalışmıyordu.
- **Davranış:** `get_positions()` ile sembolü sorgular; `pos_amt > 0` ise `_emergency_close()` ile pozisyonu güvenle kapatır (`success=False`, "MARKET cevap yok ama pozisyon acik"). Pozisyon yoksa mevcut `MARKET BASARISIZ — empty_response` yoluna düşer.
- **Gerekçe:** 2026-08-02 21:42:30 canlı olay — DYDXUSDT entry'de Binance 408 timeout döndü, bot emri başarısız saydı ama emir sunucuda dolmuş olabilirdi; pozisyon korumasız ve takipsiz kalabilirdi.

**Testler:** `test_entry_manager.py` — `test_market_empty_response_pos_open_emergency_close` (pozisyon açık → emergency close) + `test_market_order_failure` güncellendi (get_positions=[] → MARKET BASARISIZ yolu). 81 entry_manager + 119 infra pass; test_bot.py 15 fail ve test_integration_v2.py 8 fail pre-existing (baseline ile birebir aynı, bu işlemle ilgisiz).

(Önceki işlemler aşağıda.)

## Son İşlem (önceki): config.py ölü sabitler temizlendi (2026-08-02)

`src/config.py`'den 5 ölü sabit silindi (grep + kod doğrulaması ile sıfır kullanım teyit edildi):
- `LOG_LEVEL` (bot.py logging.INFO hardcoded, config okunmuyordu)
- `MAX_SL_DIST_MULT` (kod karşılığı yok)
- `MIN_REL_FVG_THRESHOLD` (`is_high_quality_fvg()` yalnızca memory-bank'ta yazıyordu, session_router.py'de böyle bir fonksiyon yok)
- `BE_RISK_MULT`, `BE_SPREAD_PTS` (breakeven özelliği hiç inşa edilmemiş)

**Korundu:** `EARLY_LONDON_RISK_MULT` — kullanıcının raporunda "ölü" denmişti ama `simulate.py:305`'te `_cfg.EARLY_LONDON_RISK_MULT` aktif olarak kullanılıyor, silinmedi.

(Önceki işlemler aşağıda.)

## Son İşlem (önceki): DD_GUARD + [RISK-DEBUG] log kaldırıldı (2026-08-02)

`src/bot.py` `_try_entry()` — P1-13 DD devre kesici (entry tamamen engelleme) ve `44ee72d` ile eklenen `[RISK-DEBUG]` logu (equity/peak/dd/broken) tamamen kaldırıldı. `is_defense_mode` değişkeni başka yerde kullanılmadığı için o da silindi.

Kalan durum: `RiskManager.get_dynamic_risk_multiplier()` hâlâ devre kesici state machine'ini (trip/reset, histeresis) ve EL/risk çarpanını yönetiyor — sadece entry engelleme guard'ı yok. `risk_manager.py`'de değişiklik yapılmadı, testleri geçiyor (`test_risk_manager.py`).

**Test durumu:** `test_bot.py` dışındaki suite 190 passed (test_bot.py'deki 15 fail pre-existing: `mark_trade_closed`, `_stage`, legacy fonksiyonlar — bu işlemle ilgisiz, kapsam dışı).

(Önceki işlemler aşağıda.)

## Son İşlem (önceki): DD_GUARD öncesi [RISK-DEBUG] log eklendi (2026-08-02)

`src/bot.py:728-733` — `_try_entry()` içinde `is_defense_mode` hesabından hemen sonra, P1-13 DD_GUARD kontrolünden ÖNCE `get_current_dd()` çağrılıp `[RISK-DEBUG]` INFO logu basılıyor:

```
current_dd = self.risk_mgr.get_current_dd(self._available_balance)
log.info("[RISK-DEBUG] %s | equity=%.2f | peak=%.2f | dd=%.2f%% | broken=%s", ...)
```

Amaç: DD_GUARD devreye girip entry'yi engellediğinde equity/peak/dd/broken değerlerinin log'da görünür olması — DD_GUARD tetiklenme anındaki portföy durumunu tespit etmek. `get_current_dd()` ve `peak_equity` zaten `risk_manager.py`'de mevcuttu, yeni API eklenmedi.

(Not: Bu commit daha sonraki "DD_GUARD + [RISK-DEBUG] log kaldırıldı" işlemiyle geri alındı.)

## Son İşlem: 12 BULGU ayrı commit'lerle düzeltildi (2026-08-02)

`sniper_fix_plan_ve_agent_direktifi.md`'deki 12 madde, üç ayrı doğrulama turu sonrası kesinleşen duruma göre tek tek uygulandı. Her madde ayrı commit, her biri kendi testiyle doğrulandı. Baz `e369ddc` üzerine 10 commit.

1. **BULGU-07** (`21be255`) — `exit_lifecycle.py:521` bare `except Exception: pass` → `except Exception as e: log.error(...)` (position verify bloğu)
2. **BULGU-09** (`df14756`) — `entry_manager.py:766` TP emri başarısız olunca `success=True` dönüyordu; artık `_emergency_close` tetikleniyor, `execute_live_entry` kontratı korundu (test güncellendi: `test_tp_failure_still_returns_success`)
3. **BULGU-03** (`fb82685`) — `exit_lifecycle.py:337` execute() içinde gereksiz `self._active_trades.get(sym)` tekrarı silindi; trade parametresi tek referans (WS callback pop'lamışsa accounting kaybolmuyor)
4. **BULGU-04** (`bac575c`) — `exit_lifecycle.py:621` `_commit_confirmed_exit` sym bazlı `_exit_locks` ile korundu (iki exit path aynı anda ulaşınca PnL commit kaybolmuyor)
5. **BULGU-01/10** (`e5d9151`) — `models.py` ActiveTrade'e `pending_exit_price/qty/order_id/timestamp/reason` gerçek dataclass field'ları eklendi (JSONL'e yazılıyor); `__contains__` `hasattr` → `key in self.__dataclass_fields__`; ProtectionState'e `known_ids()` eklendi
6. **BULGU-23** (`54b2ce6`) — `exit_lifecycle.py:177/350` `cfg.BINANCE_API_KEY` guard'ları `self._is_live` ile değiştirildi (paper mode'da key set edilse de gerçek emir gitmez); `__init__`'e `is_live` parametresi, `bot.py`'den `is_live=self._live` geçildi
7. **BULGU-02/06** (`4c7c4c7`) — `bot.py` `_save_fvg_state`/`_load_fvg_state` bare except → log.error + atomik yazma (temp dosya + `os.replace`)
8. **BULGU-08** (`5b9bb7b`) — `exit_lifecycle.py:504` sabit `abs(amt) < 0.0001` eşiği → `amt == 0` (micro-cap token'larda yanlış "kapalı" sayma önlendi)
9. **BULGU-11** (`4006acb`) — `user_data_handler.py:85-86` `order_id` (server orderId `i`) ile `client_order_id` (`c`) ayrı tutuldu, fallback'te server orderId öncelikli
10. **BULGU-21** (`870930e`) — `bot.py` `self._live` tek kaynak; `EntryManager`/`OrderManager` `is_live=self._live`; canlı moda geçerken `_is_live`'lar senkronize edildi (satır 1256 civarı)

**BULGU-17/18** — bug değil; `HTFFVG.bar_index` runtime'da gerçek alan (retrace_state.py doğrulandı). Kod değişikliği yapılmadı, kapatıldı.

**Test durumu:** `test_exit_lifecycle.py` 34, `test_entry_manager.py` 80, `test_models.py` 48, `test_user_data_handler.py` 36 (2 pre-existing `_exit_trade_legacy` fail hariç) — toplam 198 passed. `test_bot.py` 15 pre-existing fail (kaldırılmış legacy fonksiyonlar: `mark_trade_closed`, `_stage`, `_exit_trade_legacy`) — bu turun kapsamı dışı.

(Önceki işlemler aşağıda.)

## Son İşlem (önceki): Cross-context bug fix turu + P0 safety fixes (2026-08-01)

`sniper_cross_context_bug_report_v2.md` + `sniper_fix_plan_ve_agent_direktifi.md` rehberliğinde, baz `03e6eaf8` üzerine uygulandı. 13 bug 12 commit. Ek olarak 2026-08-01: 5 bare `except Exception: pass` hatasını remediation — recovery_manager.py:486 (SL/TP cancel retry), exit_lifecycle.py:521/549 (position verify + FILLED order check), order_manager.py:646/966 (SL placement + repair cancel). state_writer.py: BULGU-05 (protection_health flat field'lardan), BULGU-19 (ws_event_normalization config'den).

1. **BUG-1/7** (`5f08154`) — `_emergency_close` başarılı kapanmada `success=True`; 4 call-site wrapper ile `execute_live_entry` hâlâ `success=False`; `mkt_side` parametresi + `ValueError` guard
2. **BUG-25** (`c776e20`) — risk_manager bozuk/yok/schema-hatalı state'te `initial_equity` fallback; `get_current_dd` peak<=0'da %100 (güvenli taraf)
3. **BUG-23** (`985fca0`) — `should_trade` `cbdr_width_pct=None` iken fail-closed `False`
4. **BUG-5** (`853f6e5`) — ortak `cbdr_day_key` helper (K1=Seçenek B: etiket = döngünün BİTTİĞİ gün); state_manager + session aynı key'i üretir; 22:00/23:59/00:00/01:59/02:00/21:59 parametrize test
5. **BUG-12** (`105b0f7`) — idempotency key `entry_order_id` öncelikli + `entry_bar_index/price/actual_qty` fallback (K2-A pragmatik; paper modda teorik çakışma docstring'de belgeli)
6. **BUG-8/2** (`b90d865`) — `normalize_order_event` `ts_ms` kaynağı `msg["E"]` → `o.T` → `o.O` → local clock; `received_ts_ms` + latency log (K3: legacy handler migrasyonu ayrı ticket)
7. **BUG-21** (`9bcb94f`) — `order_qty` `apply_amount_precision` + `validate_min_amount` ile normalize; min-altı fallback `valid_qty`
8. **BUG-10** (`a633972`) — `_bump_to_min_notional` Decimal `ROUND_CEILING` + bump sonrası notional/step guard
9. **BUG-11** (`06fdd78`) — `pending_exit_*` normalization tek blok; `order_id`/`timestamp` de `is not None`
10. **BUG-3** (`2afd11f`) — trailing `trade["sl"]`/`["tp"]` canonical; `protection_state` ActiveTrade uyumlu (get+None+atama)
11. **BUG-17** (`4c24ab8`) — `CircuitBreaker.is_open_async` lock korumalı; `call()` kullanır
12. **BUG-16** (`d49fb5a`) — `detect_phase` `isinstance(dt, int)` dead code silindi
13. **BUG-29** (`e244165`) — ek: `trade.setdefault` → `trade.get` (order_manager.py:312/337/1088, protection_lifecycle.py:294/314); ActiveTrade canlı path'te AttributeError crash'i (test_trail_state_transitions ile doğrulandı)

**Kararlar:** K1=Seçenek B, K2=Seçenek A, K3=legacy migrasyon bu turda değil, BUG-25 `get_current_dd`=%100 güvenli taraf.

**Test durumu:** 675+ test geçiyor; 78 pre-existing kırık (çoğu `.env`/test-sırası bağımlı, bu turun kapsamı dışı). `test_event_log.py::test_writes_jsonl_line` dosya kirliliğine bağlı sıra-duyarlı — hijyen ticket'ı olarak not düşüldü.

(Önceki işlemler aşağıda.)

## Son İşlem (önceki): Rapor dokümanları push edildi (2026-08-01)

`reports/` altına 6 yeni analiz dokümanı eklendi + eski `backtest_canli_farklari_31_07_2026.md` silindi:

- `Trading_Execution_Simulator.md` — emir yürütme simülatörü tasarımı (delay/spread/slippage/partial-fill/reject/protection lifecycle)
- `entry_decision_tree.md` — ortak canlı/backtest giriş karar ağacı (CBDR→sweep→FVG→TRIGGER_READY→entry) + sembol matrisi
- `fvg_fix_analysis_report.md` — FVG giriş filtreleri backtest hizalaması
- `parity_regression.md` — giriş parity regression spec
- `sniper_cross_context_bug_report.md` + `sniper_cross_context_bug_verification.md` — cross-context bug analizi

Commit: `a6b0667`, push edildi (`7f8c11c..a6b0667 main -> main`).

(Önceki işlemler aşağıda.)

## Son İşlem (önceki): Canlı/backtest giriş parity tamamlandı — 9 sembol core-diff=0 (2026-07-31)

İki düzeltme ile canlı `bot.py` + `signal_engine.py` state transition akışı backtest `analyzer_v5.py` ile birebir hizalandı:

1. **`bot.py:441-459`** — yeni CBDR gününe taşınan TRIGGER_READY state'i kilitsizken `bias_reject` mantığıyla resetlenir (`analyzer_v5.py:276-284` aynı).
2. **`signal_engine.py:126-137`** — session filtresi sembole özel `cbdr_start/end` penceresini kullanır (detect_phase default 22-2 SOL 19-1'de saat 01 farkını kapatır).

Benchmark: 9 aktif sembol, 87.600 bar, core-diff=0, TRIGGER ve sweep-lock backtest/live birebir.
CI testi: `tests/parity/test_parity_regression.py` (9 test, 379s, SHA256 fixture checksum sabitli).
Commit: sniper `a47b8ae`; spec `output/reports/parity_regression.md`.

(Önceki işlemler aşağıda.)

## Son İşlem (önceki): paper_trade_logger — Append-only JSONL for paper trade lifecycle (2026-07-30 16:21)

`src/paper_trade_logger.py` oluşturuldu ve 4 modüle entegre edildi.

**paper_trade_logger.py:**
- `EventType` enum: 15 event type (entry_filled → trade_closed)
- `configure(log_path, run_id)` — bot.py `PaperTrader.__init__`'de çağrılır
- `log_event()` — append-only JSONL, schema v1
- Bloklar: `entry`, `protection`, `fvg`, `validation`, `error`, `result`, `reason`, `latency_ms`, `call_count`, `protected_state_before/after`
- Secret/traceback yazılmaz; error= `{code, message, retry_count}`

**Entegrasyon:**
- `src/bot.py:57` — import + `pt_configure()` (line 228) + PaperTrader._try_entry()'de `INITIAL_SL_CALCULATED` event
- `src/trading/entry_manager.py:34` — `ENTRY_FILLED`, `INITIAL_SL_CALCULATED`, `PROTECTION_NORMALIZED`, `PROTECTION_VALIDATED`, `SL_PLACED`, `TP_PLACED`, `SL_REJECTED`, `TP_REJECTED`, `EMERGENCY_CLOSE_STARTED`, `EMERGENCY_CLOSE_COMPLETED`, `EMERGENCY_CLOSE_FAILED`
- `src/trading/exit_lifecycle.py:49` — `TRADE_CLOSED`, `TRAIL_CANDIDATE`, `TRAIL_SKIPPED`
- `src/trading/trailing_manager.py:11` — `TRAIL_CANDIDATE`, `TRAIL_SKIPPED`

**Commit edilmedi — uncommitted duruyor.**

## Önceki İşlem: Binance rejection failure simulator for execute_live_entry (2026-07-30 15:50)

`tests/failure_simulator.py` + `tests/test_initial_protection_failures.py` eklendi.

**Simülatör (`failure_simulator.py`):**
- `FakeExchange` — REST adapter contract'ını birebir replike eden deterministic test double
- `FailureMode` enum: NONE, SL_2021, SL_GENERIC, TP_2021, MARKET_TIMEOUT, PARTIAL_FILL, CLOSE_FAIL, CANCEL_FAIL
- `BinanceReject` exception class
- `Expected` enum + `Scenario` dataclass + `SCENARIOS` tuple (5 senaryo)
- Yeni API uydurulmadı — sadece mevcut method imzaları (`place_market_order`, `place_stop_order`, `place_tp_order`, `apply_amount_precision` vb.)
- `get_positions()` eklendi (FakeExchange'te eksikti)

**Testler (`test_initial_protection_failures.py`):** 15 test, tümü geçiyor:
- SL -2021 long/short: 1 SL call, emergency close, protected=false, no TP call
- SL generic exception: exception propagates, protected=false
- Partial fill: actual_qty=0.37 used for SL (not requested 1.0)
- Emergency close failure: SL -2021 → close raises (reduce_only=True) → error visible
- Direction validation: long SL too close / short TP too close → emergency close, no SL call
- Protected state only after SL response confirmed
- Parametric scenario runner (5 scenarios × parameterized)
- Invariant: protected=True requires at least one SL call

**Baseline check:** 0 regresyon vs 1cec670 (önceki commit), +15 passing, 74 pre-existing failures unchanged.

**Commit:** `8c5b7f0`, push edildi.

## Önceki İşlem: calculate_sl_tp dead code temizliği + max_risk_dist override kaldırıldı (2026-07-30 14:24)

`calculate_sl_tp()` artık `analyzer_v5.py` backtest SL formülü ile birebir aynı:
- `max_risk_dist = risk_pts * cfg.MAX_SL_DIST_MULT` + 2 override bloğu KALDIRILDI — GMXUSDT gibi geniş FVG'li coinlerde FVG-anchor SL'yi eziyordu.
- `symbol` parametri (MIN_SL_DISTANCE_PCT_MAP için eklenmişti) artık ölü — commit `5c8e4f4`'te map zaten silinmişti.
- `apply_min_sl_distance()`'dan `symbol` parametresi kaldırıldı.
- `bot.py` + `entry_manager.py`'deki `symbol=sym` çağrıları temizlendi.

**Değişiklikler:**
- `src/trading/entry_manager.py`: `calculate_sl_tp()` imzasından `symbol`, `max_risk_dist` satırı, 2 override bloğu silindi. `apply_min_sl_distance()` imzasından `symbol` parametresi kaldırıldı.
- `src/bot.py`: `calculate_sl_tp(..., symbol=sym)` → `calculate_sl_tp(...)`
- Testler: 9 pre-existing failure (london_high/low TP beklentisi, test_entry_manager/test_trailing_manager/test_models vb.) — değişiklikle yeni failure yok.

### Önceki Context:

**Kesin Kanıt**: exec_sim entegrasyonunda guard (ref_price/min_dist) tek başına PF'yi 4.61→0.71'e düşürdü. Diğer tüm değişiklikler (pending_exit block, _commit_trade_exit, _exec_rng, metadata) sıfır etkili — dead code veya pure refactor.

### 3 İzolasyon Testi (SOLUSDT)
| Test | Guard | pending_exit | _commit_trade_exit | PF | PnL |
|------|-------|-------------|-------------------|-----|-----|
| 3 (baseline) | YOK | YOK | VAR | **4.61** | **+$42,347** |
| 2 | YOK | VAR | VAR | **4.61** | **+$42,347** |
| 1 | VAR | YOK | VAR | **0.71** | **-$5,724** |

### Nihai Karar: Guard LIVE ile Birebir Aynı Olacak — would_reject SADECE Kaldırıldı
- `analyzer_v5.py` → **8872bed state** (= guard + exec_sim refactoring, would_reject YOK)
- Guard korundu çünkü **live trailing_manager.py'de guard var** — backtest live'ın birebir aynısı olmalı
- `would_reject_immediately()`: KALDIRILDI — live trailing_manager.py'de hiç olmadı
- Guard'ın backtest PF'yi düşürmesi = **live'ın gerçekçi simülasyonu** (Binance -2021 rejection eşiği)
- Commit: `9a2c0bc`, push edildi

### Ayıklanan exec_sim Artifaktları (sıfır etkili, korundu):
- `_commit_trade_exit` helper — pure refactor (test edildi, PF 4.61)
- `pending_exit` block — dead code (hiçbir yerde `pending_exit=True` set edilmez, PF 4.61)
- `_exec_rng`, `_estimate_tick_size` — exec_sim altyapısı (çağrılmıyor)
- `_last_trailing_bar`/`_last_clamp_bar` tracking + TRAILING_CLAMP report
- `max_dd_usd`, `equity_curve` eklemeleri

### Live trailing_manager.py Guard Push Edildi (294f7e8)
- Guard local'de uncommitted kalmıştı — hiç push edilmemiş
- Sunucuda `git pull => trailing_manager.py'ye 8 satır eklenir:
  - `ref_price = chunk[-1].close`, `min_dist_pct`, `min_dist = ref_price * min_dist_pct`
  - Long: `(ref_price - new_sl) >= min_dist`, Short: `(new_sl - ref_price) >= min_dist`

### Sıradaki (Chief Engineer planı):
1. Sunucuda `git pull` yap (sadece trailing_manager.py değişecek, diğer source'lar aynı)
2. **28-coin backtest çalıştır** (worker ile)
3. Guard'ın backtest'te yarattığı PF kaybı, live'da -2021 rejection oranına denk mi değerlendir

### Önceki Context:
`memory-bank/bugs.md` ikiye bölündü:
- **bugs_archive.md** (436 satır): 19 sabit madde tam içerikle taşındı (P0-2, P0-3, P0-4, P0-5, P1-1, P1-2, P1-3, P1-5, P1-6, P1-8a, P1-9, P1-10, P1-11, P1-13, P1-14, P2-1, P2-4, P2-5 + P1-12 analiz + 25 Tem log analizi)
- **bugs.md** (461 satır, 1017'den %55 küçültüldü): 17 aktif madde + arşiv izleri
- Commit: `8155ada`

## Son İşlem: export_ohlc_1m pozisyonsuz bar'lara taşındı (2026-07-25 23:41)

`src/bot.py:455-460` — `_on_1m_close()` içinde `export_ohlc_1m(current, sym)` çağrısı
`active_trades` guard'inden ÖNCE taşındı. Artık pozisyon olmasa bile her 1m bar'da
CSV birikir — DD tetikleme anları, stale-event soruşturmaları, pozisyonsuz dönemler
kapsanır.

## Son İşlem: P3-2 fiyat formatı fix + P3-5 PARTIALLY_FILLED notu (2026-07-26 00:01)

`bot.py` ve `entry_manager.py`'deki log satırları `_fmt_price()` kullanımına güncellendi:
- `bot.py:795-803` — `[PAPER] ENAUSDT short @ 0.09 sl=0.09 tp=0.09` → `_fmt_price()` ile dinamik ondalık basamak
- `entry_manager.py:486-491` — `entry_log_msg` içinde `est_price:.2f` → `_fmt_price()` ile dinamik format

`bugs.md`'ye P3-5 eklendi: WS-ORDER PARTIALLY_FILLED tekrarları (gözlemlendi, zararsız, aksiyon gerekmiyor).

## Son İşlem: _on_1m_close flush/counter pozisyon bağımsız hale getirildi (2026-07-26 00:50)

`src/bot.py:456-468` — `_on_1m_close()` yeniden yapılandırıldı:
1. `export_ohlc_1m(current, sym)` → en başta (pozisyon bağımsız)
2. `_orphan_check_counter += 1` + `_flush_ohlc_writers()` (mod-10) → trade gate'den önce
3. Orphan sweep (mod-5) → sadece trade varsa

Flusher ve OHLC export artık her 1m bar'da çalışıyor, pozisyon olsun olmasın.

## Son İşlem: P1-15 check_exit teorisi çürütüldü, repr() debug log aktif (2026-07-26 09:25)

- CSV kanıtı: 02:21 bar high=0.0447 < sl=0.044729 → check_exit tetiklenmemeli
- WS handler elendi (05:15-05:22 arası hiç WS-ORDER SEIUSDT yok)
- recovery_manager elendi (trade["result"]'a yazmıyor)
- check_exit tek kalan yol ama CSV ile çelişiyor → paradox
- [P1-15_DEBUG] repr(high) + repr(sl) WARNING log eklendi, stale event bekleniyor
- APTUSDT'te debug log çalışıyor (high > sl, check_exit doğru tetikleniyor)
- bugs.md status: "ARAŞTIRILIYOR — repr() sonucu bekleniyor"

## Son İşlem: D-2 Fark 1 exit_now guard kaldırıldı (2026-07-26 10:38)

`trailing_manager.py:evaluate_trail()`'den `exit_now` guard kaldırıldı — analyzer_v5.py backtest ile uyumlu hale getirildi. Eski guard (new_sl >= current.close → exit_now=True), iyileşme kontrolü yapmadan pozisyonu zararla kapatıyordu (P2-6/P2-7 kök nedeni). 2 regression test eklendi (long + short). 81/81 test geçti.

Bugs.md güncellendi: D-2 Fark 1 ✅, P2-6/P2-7 🔧 (canlı doğrulama bekleniyor).

## Son İşlem: P0-1 FULL FIX (2026-07-26 15:05)
- `EXIT_LIFECYCLE_SERVICE_ENABLED` flag temizliği (kaynak kod + config).
- `_exit_trade_legacy` silindi, artık tüm exit'ler `ExitLifecycleService.execute()` üzerinden.
- Idempotency guard eklenmiş: `_exit_log[ sym ][entry_bar_index+entry_price] = result` — aynı trade+result ikinci kez commit edilemez.
- Per-trade lock: `asyncio.Lock` key `sym_{entry_bar_index}_{entry_price}` — `position_still_open()` dahil tüm execute() gövdesini korur, farklı trade'ler birbirini bloklamaz.
- Test: 3 yeni P0-1 senaryo (stale→real PnL tek, guard engelleme, concurrent lock) + 31/31 suite geçti.
- P0-6/P0-7/P2-2 durumları doğrulandı.
- **Commit:** `440125c`, `6a0154b`, `e6ed18e`
- **Git:** `bfd4ae7..e6ed18e main -> main`

## Son İşlem: P1-15 Stale Event Kök Neden Analizi Tamamlandı (2026-07-27 15:00)

### Bulgular
- **14 stale event** bugün, 5 cluster — 13 exit'ten 5'i (%38.5) etkilendi
- **Kök neden**: Binance STOP_MARKET fiziksel olarak dolduruyor ama WS FILLED event'i 87-353s (1.4-5.9dk) gecikmeli geliyor
- **Reconnect korelasyonu YOK**: Log'da tek WS reconnect (03:25), stale event'ler saatlerce sonra oluşuyor
- **GMXUSDT orantısız**: 14 stale'ten 10'u (%71.4) GMXUSDT. Max gecikme 306s vs diğer semboller max 86s
- **Tarihsel uyumlu**: trades_history'de 290 trade, 99 WS_FALLBACK (%34.1). Bugün %38.5 — artış yok, kronik
- **STOP_MARKET reject = en erken kanıt**: HTTP 400 -2021 "Order would immediately trigger" Binance fill zaman damgası. WS FILLED bundan 87-353s sonra geliyor

### Latency Tablosu
| Cluster | Sembol | SL Reject (Binance fill) | WS FILLED | Gecikme |
|---------|--------|--------------------------|-----------|---------|
| 1 | GMXUSDT | 05:54:15 | 06:00:08 | 353s (5.9dk) |
| 2 | UNIUSDT | N/A (TP) | 07:48:04 | 3s |
| 3 | GMXUSDT | 11:46:14 | 11:50:15 | 241s (4.0dk) |
| 4 | ONDOUSDT | 13:00:08 | 13:01:35 | 87s (1.4dk) |
| 5 | DOGEUSDT | 13:16:00 | 13:18:24 | 144s (2.4dk) |

### Önerülen Durum
- P1-15 **hâlâ açık** — kök neden Binance WS teslimat gecikmesi, client-side fix mümkün değil
- Yeni aksiyon: STOP_MARKET reject (HTTP -2021) fill kanıtı olarak kullanılabilir

## Son İşlem: FVG Fibo Matched Pair Filtresi — Backtest Tamamlandı (2026-07-27 18:03)

`retrace_state.py`'ye fibonacci zone kontrolu eklendi (`a2eade1`):
- Swing high/low 100 bar 15m'den hesaplaniyor
- FVG midpoint'inin fibo seviyesi compute ediliyor (0.236/0.382/0.5/0.618/0.786)
- Matched pair: bullish+0.236, bearish+0.786 (backtest PF 6.99 vs 1.75 mismatched)
- Unmatched FVG adaylari reject ediliyor

**Backtest Sonuclari (2026-07-27 18:03):**
- Toplam trade: 103,048 → 29,982 (**-71% filtreleme**)
- Net PnL: +3,666,917 → +1,845,884
- **PnL/Trade: 35.6 → 61.6 (+73% iyilesme)**
- Ortalama PF: ~3.4 → ~6.5 (+91% iyilesme)
- Holdout validation: PASSED — Holdout PF 11.16 vs train PF 4.83 (ratio=2.31), WR 73.08% vs 64.83%
- Sembol bazinda en buyuk iyilesme: GMXUSDT (PF 4.19→10.45), PYTHUSDT (4.65→10.91), ENAUSDT (3.96→8.77)
- Sharpe oranlari evrensel olarak iyilesti (0.32-0.44 vs 0.25-0.35)

## Son İşlem: P1-15 Stale Event Mitigation Uygulandı (2026-07-27 15:50)

3 mitigation aksiyonu uygulandı ve push edildi (`ed024c3`):

1. **-2021 immediately trigger sinyali**: `order_manager.py` — STOP_MARKET reject'te `-2021` kodu `_immediately_trigger_rejects` dict'ine kaydediliyor. `exit_lifecycle.py` stale handler'da bu sinyal varsa pozisyon dolmuş kabul ediliyor, döngü kırılıyor.

2. **Stale event cooldown**: `exit_lifecycle.py` — Aynı sembolde 30sn içinde tekrar stale tetiklenirse per-bar retry atlanıyor, WS fill bekleme moduna geçiliyor. `_stale_count` ve `_stale_cooldown` dict'leri `_commit_confirmed_exit`'te temizleniyor.

3. **GMXUSDT SL mesafesi genişletme**: `config.py` — `MIN_SL_DISTANCE_PCT_MAP` eklendi. GMXUSDT default 0.15% → 0.30%. `entry_manager.py` — `calculate_sl_tp` ve `apply_min_sl_distance` `symbol` parametresi aldı.

Test: 558 passed, 24 pre-existing (0 new regression).

## Son İşlem: P1-15 bugs.md Kök Neden Güncellemesi (2026-07-27 15:33)

- bugs.md P1-15 bölümü tamamen yeniden yazıldı: kök neden Binance WS FILLED gecikmesi (87-353s)
- Özet tablosu: 👁️→🐛, status "KÖK NEDEN DOĞRULANDI" olarak güncellendi
- 3 mitigation önerisi eklendi: (-2021 sinyal, bekleme penceresi, GMXUSDT SL genişletme)
- UNIUSDT 3s gürültü notu eklendi (TP path'i farklı kod yolundan geçiyor)
- Commit: `d40caf7`, push edildi

## Son İşlem: would_reject_immediately() Backtest Trailing'den Tamamen Kaldırıldı (2026-07-28 04:45)

### Kritik Bulgu: Çift Standard — Guard 0.15% vs WouldReject 0.30%

**Bug 3 (04:30)**: `would_reject_immediately()` True döndüğünde SL hâlâ uygulanıyordu →else branch fix.
**Bug 4 (04:45)**: Bug 3 fix'i bile yetmedi — canavar kök neden farklı:

| Kontrol | Referans Fiyat | MIN_SL_DISTANCE |
|---------|---------------|-----------------|
| Guard (trailing_manager.py port) | `chunk[-1].close` (önceki bar) | `cfg.MIN_SL_DISTANCE_PCT = 0.0015` |
| would_reject_immediately (execution_sim) | `cur.close` (mevcut bar) | `execution_sim.MIN_SL_DISTANCE_PCT = 0.0030` |

**Sonuç**: Guard 0.15% eşikle geçiyor, would_reject 0.30% eşikle reddediyor. Fiyatın %0.15-0.30 arası SL'ler → guard'dan geçiyor ama reject oluyor → **tüm bu trailing güncellemeleri kaybediliyor**.

**Canlı trailing_manager.py'de `would_reject_immediately()` YOK**: Guard tek mekanizma. Guard geçerse SL uygulanır. Binance reddederse order_manager yakalar → eski SL korunur.

### Fix: would_reject_immediately() Backtest Trailing'den Tamamen Kaldırıldı

```python
# ÖNCEKİ (hatalı — çift kontrol, farklı eşikler):
if upd:
    if would_reject_immediately(sl_price=csl, current_price=cur.close, ...):
        t["_trailing_rejects"] += 1
    else:
        t["sl"] = csl

# SONRAKI (doğru — trailing_manager.py'nin birebir port'u):
if upd:
    t["_last_trailing_bar"] = sb
    t["sl"] = csl
    t["tp"] = ctp
    t["trailing_count"] += ltc
```

- `would_reject_immediately` import'u temizlendi, `clamp_sl_distance` import'u temizlendi
- Backtest trailing artık `trailing_manager.py`'nin birebir port'u
- `_trailing_rejects` stat'ı artık her zaman 0 olacak
- Commit: `8872bed`, push edildi

### Backtest Kanıtları
| Tarih | Config | PF | PnL | TRAILING_CLAMP |
|-------|--------|-----|-----|----------------|
| 27/07 18:03 | Fibo only (exec_sim YOK) | 4.24-10.91 | **+$1,845,884** | 0 |
| 27/07 22:05 | exec_sim (guard yok) | 1.61-5.13 | +$129,411 | N/A |
| 27/07 22:51 | exec_sim + reject | 0.18-0.27 | -$993,753 | N/A |
| 28/07 03:25 | guard + reject (SL uygulanıyordu) | 0.24-0.44 | -$673,174 | 37,067 |
| 28/07 04:40 | guard + reject (SL revert) | ~0.24-0.44 | -$673K benzeri | N/A |
| 28/07 04:45 | **guard SADECE (would_reject kaldırıldı)** | **Bekleniyor** | **Bekleniyor** | **0 olmalı** |

**Sonraki adım**: Full 28-coin backtest çalıştır — PF'nin baseline (2.75-4.65)'e yaklaşıp yaklaşmadığını kontrol et.

## Son İşlem: exec_sim Entegrasyonu — 2 Bug Bulundu + Kritik Mimari Bulgular (2026-07-27 23:00)

### exec_sim Modülü (baş mühendis tarafından oluşturuldu)
- `backtest-sniper/src/execution_sim.py` — `sample_ws_latency()` + `would_reject_immediately()` + `would_reject_immediately()` + `_round_to_tick()` + `sample_ws_latency()` + `would_reject_immediately()` + `_round_to_tick()`. 37/37 test geçti.
- Lognormal dağılım: non-GMX μ=ln(130), σ=0.40; GMX μ=ln(300), σ=0.40 (3x daha yavaş)

### analyzer_v5.py Entegrasyonu — Bug #1: sa.append(t) eksik (DÜZELTİLDİ)
- `analyzer_v5.py`'de `would_reject_immediately()` True döndüğünde trade `pending_exit=True` olup `continue` yapıyordu ama `sa.append(t)` eksikti
- Sonuç: trade active listesinden düşüyor, bir sonraki bar'da kayboluyordu
- Etki: 29,982 → 7,037 trade (-77%), PnL +1,845,884 → +129,411
- **Fix**: Long ve short path'lerde `continue`'dan önce `sa.append(t)` eklendi

### analyzer_v5.py Entegrasyonu — Bug #2: PROFIT_TRAIL misclassification (DÜZELTİLDİ)
- Pending exit'e giren trade'lere `t["result"] = "LOSS"` atanıyordu, ama trailing_count kontrolü yapılmıyordu
- Etki: PTrail% 55→5'e düştü, strateji karlılığı tamamen yok edildi (PF ~0.22, PnL -993,753)
- **Fix**: Pending exit path'inde trailing_count + SL yön kontrolü eklendi (long: sl > entry_price, short: sl < entry_price)

### Kritik Mimari Bulgu: exec_sim Yanlış Senaryoyu Simüle Ediyor
- **Canlı veri** (events_2026-07-27.jsonl): -2021 rejections **SL TRAILING sırasında** oluyor (fiyat yakınlaştırılırken), SL EXIT sırasında değil
- **Backtest**: `would_reject_immediately()` SL **tetiklendiğinde** (bar low/high SL'yi geçtiğinde) çalışıyor → neredeyse tüm SL exit'leri reddediliyor
- **Sonuç**: Backtest过度 pessimistic — strateji canlıda karlı ama backtest'te negatif çıkıyor
- **Çözüm**: exec_sim'i sadece SL trailing/update operasyonuna uygula, SL exit'i muaf tut

### Canlı Paper Trade Analizi (trades_history.jsonl)
- 298 trade, toplam PnL: -$346.50, WR: %23
- SL: 111 trade, -$294.56 | TP: 24 trade, +$122.77
- WS_FALLBACK: 99 trade, -$142.14 | TRAIL_CLOSE: 64 trade, -$32.57
- OPUSDT'de qty=0.1 tespit edildi (minNotional sorunu olabilir)

### Planlanan Aksiyonlar
1. **exec_sim kapsam düzeltmesi**: Sadece SL trailing operation'a uygula (baş mühendis onayı bekleniyor)
2. **REST API fallback**: WS 300ms'de gelmezse REST ile teyit → WS_FALLBACK kayıplarını azaltır
3. **Lock/Pending**: Zaten çalışıyor, dokunulmayacak

## Aktif Görev: P1-8 post_entry_check %100 fail soruşturması

- **Soru 1 cevaplandı:** 7 vaka P0-5 deploy'undan SONRA (23 Tem 14:32 → 24 Tem 14:45+)
- **Soru 2 cevaplandı:** TIAUSDT canlı debug — SL hızlı doldu, false positive tespit edildi
- **Soru 3 bekliyor:** Diğer vakalar (NEARUSDT, ONDOUSDT vb.) için `raw_orders_count` bekleniyor
- **Yapılanlar:** `_fmt_price()` eklendi (fiyat formatlama düzeldi), debug log canlıda aktif
- **Bir sonraki adım:** Diğer vakalarda `raw_orders_count=0` mı `>0` mu çıktığını bekliyoruz

## Tamamlanan Fixler (25 Tem)

| Fix | Commit | Açıklama |
|-----|--------|----------|
| P1-13 DD guard | d62df19 | `bot.py`'de `is_circuit_broken` → entry tamamen engelleniyor |
| P1-14 stale retry | d62df19 | `exit_lifecycle.py` cross-validation: SL/TP open_orders'ta yoksa 400ms retry |
| `_fmt_price()` | bb1b350 | Dinamik ondalik basamak, OPUSDT gibi coinlerde SL/TP ayirt edilebiliyor |
| POST_ENTRY_DEBUG | ded89ce | `log.warning` level, canlıda görünüyor |

## Mevcut Durum (Görev 10 — Post-deploy doğrulama tamam)

- **Bot çalışıyor mu?**: Testnet'te, canlı emir gönderimi aktif.
- **Testnet bakiyesi**: ~5,000 USDT
- **Sembol sayısı**: 28 (18 eski + 10 yeni: TIA/SEI/ONDO/PYTH/RENDER/ENA/STRK/GMX/DYDX/LDO)
- **Kaldıraç**: 5x
- **Strateji**: CBDR → Sweep → FVG Wick Rejection → Primary Entry → Trailing → Exit (V3 — retrade/LHR kaldırıldı)

## Kritik Yapılan Değişiklikler

| # | Değişiklik | Açıklama |
|---|-----------|----------|
| 1 | **Retrade/LHR tamamen silindi** | `RetradeEngine`, `_check_retrade()`, `execute_lhr_entry()`, `SYMBOL_RISK_MAP`, `is_retrade`, `save_retrade_arm`/`load_retrade_arm`/`clear_retrade_arm`, `rsms_retrade`, `retrade_engines` — tümü kaldırıldı. |
| 2 | **Sweep infinite loop fix** | `unmark_sweep_used()` silindi. `mark_sweep_consumed(level)` + `is_sweep_consumed(level)` level-based ID (ör: `bullish_1.2345`) ile eklendi. Token restart-proof JSON lock file. |
| 3 | **`_exit_trade()` rewrite** | Sıra: `cancel_all_open_orders()` → `reduceOnly=True` market → 5-attempt position verify loop → `mark_sweep_consumed()` + `rsm.reset()`. |
| 4 | **Double exit guard** | `_exit_trade()` başında `active_trades.pop(sym, None)` ile trade alınır, `None` dönerse erken return. `pop` çağrısı en üste taşındı — artık hem guard hem atomik silme. |
| 5 | **Orphan cleanup geniş** | `reconcile_orphan_orders()` tüm order türlerini temizler (LIMIT dahil). |
| 6 | **FVG trailing close teyidi** | `_fvg_close_confirmed()` — trailing sadece 15m close'u FVG içinde olan FVG'leri kullanır. |
| 7 | **Trail prev ID geçiş fix** | `update_trail_orders()` eski SL/TP id'sini `*_order_id_prev` olarak saklar, WS fill eşleşmesi hem güncel hem prev id'leri kontrol eder. CANCELED callback'te prev id'ler sessizce yok sayılır. — WS_FALLBACK sayısını azaltır. |
| 8 | **Backtest trailing → live bot port** | `analyzer_v3.py` trailing bloğu `_fvg_close_confirmed()` + ATR buffer + TRAIL_MIN_MOVE_MULT + break-even ile güncellendi. `coins_config.py`'a trailing sabitleri eklendi. |
| 9 | **Entry wick ratio guard kaldırıldı (sweep bar'da yanlıştı)** | `signal_engine.py`'daki sweep barı wick ratio guardı silindi. Doğru kontrol `fvg.py/_wick_ratio_ok()` ile FVG tespiti sırasında yapılıyor. `is_closed` close guard korundu. |
| 10 | **FVG marker fix** | `_save_fvg_state()` içinde `fvg_bar_index: max(0, current.index-3)` → `fvg.bar_index` (restart sonrası marker yanlış yere düşüyordu). |
| 11 | **BE chart bar index fix** | `TrailingManager.evaluate_break_even()`'de `"bar": current.index` → `"bar": bar_index_15m` (15m bar index'i ile skala uyumu). `bars_15m` BE öncesi çekildi, dublikat silindi. |
| 12 | **Sweep level ActiveTrade'de** | `models.ActiveTrade`'e `sweep_level: float\|None` field'ı eklendi, `_try_entry()`'de `sweep_level=ss.sweep_level` ile dolduruluyor. |
| 13 | **on_sweep_confirmed rewrite** | 3 değişiklik: (a) sweep invalidation gate — ters kırılırsa IDLE, (b) FVG yoksa reset yok — bekle, (c) unconditional reset kalktı — SWEEP_DETECTED'de kal. |
| 14 | **output/ gitignore** | `output/*` exception'lar kaldırıldı, tüm output dizini ignore. Mevcut dosyalar `git rm --cached` ile indexten çıkarıldı. |
| 15 | **Snapshot pad & fetch limit** | `_PAD_BARS=8→20`, `_FETCH_LIMIT=120→160` — daha geniş pencere. |
| 16 | **Legend konum fix** | `bottom:14px` → `top:54px` — chart altına düşmesin. |
| 17 | **Entry line canvas overlay'e taşındı** | `createPriceLine()` silindi, `rangedHLine()` ile SL/TP yanına eklendi — chart'a entegre. |
| 18 | **ActiveTrade cbdr_high/cbdr_low** | models.py'ye eklendi, `_try_entry()`'de `ss.cbdr_body_high/low` ile dolduruluyor. |
| 19 | **fvg = rsm.trigger_fvg taşındı** | `_try_entry()` sonundan en başa alındı. |
| 20 | **update_trail_orders signature değişikliği** | `new_sl`, `new_tp`, `new_trail_count` parametreleri eklendi. Paper modda da `trade["sl"]`/`trade["tp"]`/`trade["trailing_count"]` güncellenir. `apply_price_precision()` çağrısı fonksiyon içine alındı — caller'da tekrar yok. |
| 21 | **Trailing partial success fix** | `sl_ok or tp_ok` durumunda `trailing_count` güncellenir. Sadece ikisi de başarısız olursa `False` döner (eski: biri başarısız → hep `False`). Log'da artık `trade.get("sl")` kullanılıyor — key hatası yok. |
| 22 | **_exit_trade() active_trades.pop taşındı** | `pop(sym, None)` çağrısı fonksiyon sonundan (`_write_trade_jsonl` sonrası) başına alındı — çift exit'te ikinci çağrı trade bulunmadığı için hemen return eder. |
| 23 | **max_wick_ratio parametresi kaldırıldı** | `TrailingManager.evaluate_trail()` imzasından `max_wick_ratio: float = 1.0` silindi. `find_fvgs()` çağrısındaki `max_wick_ratio` kwarg da kaldırıldı — kullanılmıyordu. |
| 24 | **Wick ratio guard doğru katmana taşındı** | `signal_engine.py:100-115` sweep bar wick guardı kaldırıldı (yanlış bar). `bot.py` RSM init'e `max_wick_ratio=cfg.FVG_WICK_RATIO_MAX` (0.75) eklendi — artık `fvg.py/_wick_ratio_ok()` impulse mother barını kontrol eder, FVG tespiti sırasında. Trailing'deki `max_wick_ratio` önceki commit'te zaten silindi (23). |
| 28 | **ATR refactor (indicators.py)** | Sahte ATR (`max(range, close*0.0001)`) → gerçek Wilder's smoothing 14-periyot ATR (`_atr_state`, `_atr_prev_close`). `bot.py`: `_warmup_cbdr`, `_on_15m_close`, `_on_1m_close` 3 yerine entegre. `recovery_manager.py`'de de kullanılıyor. `__init__` sıralama bug'ı düzeltildi (`_atr_state` artık RecoveryManager'dan önce tanımlı). |
| 29 | **Dinamik FVG eşiği** | Statik `FVG_SIZE_MAP` (`$ değerleri`) → `FVG_MIN_SIZE_ATR_MULT × atr_val` (dinamik). Hem entry hem trailing aynı formül. MULT taraması (0.02-0.30, 195 run) → `FVG_MIN_SIZE_ATR_MULT = 0.06` seçildi (0.02-0.08 arası PnL farkı gürültü seviyesinde, 0.06 en sağlam/orta nokta). |
| 25 | **FVG bar index restart fix** | `snapshot.py:_resolve_fvg_bar_index()` öncelik sırası değiştirildi: fiyat bazlı arama (#1) artık bar offset formülünden (#2) ÖNCE gelir. Restart sonrası `bars_15m` indeksleri sıfırlandığında formül yanlış bar'ı işaret ediyordu (FVG ~81 seviyesi / indeks 8'de ~77-78 barı). `snapshot.py:166-195`. |
| 26 | **Chart FVG uyuşmazlık uyarısı** | `chart_template.html`'e JS tutarlılık kontrolü eklendi: FVG marker bar'ının high/low'u ile fvgTop/fvgBottom arasındaki mesafe bar range'inin 8 katını geçerse kırmızı uyarı bandı basar. |
| 27 | **console_reporter syntax fix** | `display_fvg_status()`'ta `TRIGGER_READY` bloğundaki iki `self.emit()` yanlış indentasyon seviyesindeydi (if dışında), `elif` yetim kalıp SyntaxError veriyordu. |
| 30 | **RiskManager + Erken London risk çarpanı** | `risk_manager.py` (filelock thread-safe). EL çarpanı 1.5x (02-08 UTC). Histeresizli devre kesici: DD≥%15 patla, DD≤%10 reset. Backtest: 13/13 coin EL avantajı doğrulandı. Config: `EARLY_LONDON_RISK_MULT=1.5`. |
| 31 | **Session Router (yeni modül)** | `session_router.py` — `get_cbdr_multiplier()`, `should_trade()`, `is_high_quality_fvg()`, `is_fvg_valid()`, `get_session_hours()`. Coin bazlı CBDR risk çarpanı + zehirli bölge filtresi + ATR-bazlı FVG kalite kontrolü. |
| 32 | **CBDR Risk Matrisi + 3 katmanlı risk** | `config.py`'de `CBDR_RISK_MATRIX` (13 coin × 6 bucket × 6 çarpan kademesi: 1.5x/1.2x/1.0x/0.8x/0.5x/0.0x). 3 katman: Zaman(EL) × Kurulum(CBDR bucket) × Portföy(devre kesici). |
| 33 | **Defense mode** | Devre kesici aktifken (DD > %15) EL ve Elite CBDR çarpanları iptal: `final = 1.0 × min(cbdr_mult, 1.0)`. Log'da `[DEFENSE]` etiketi. |
| 34 | **Coin bazlı SessionState + midnight crossover** | Her coin `CBDR_RISK_MATRIX['session']` üzerinden kendi optimal session saatlerini alır. Midnight crossover session_router'da handle edilir. |
| 35 | **NaN fix + BOT_SESSION sil + MIN_FVG_SIZE temizlik** | `BOT_SESSION` sabiti kaldırıldı (artık coin bazlı). `FVG_SIZE_MAP` kullanımdan kalktı (ATR-bazlı dinamik eşik). NaN koruması eklendi. |
| 36 | **Dinamik ATR bazlı FVG filtresi** | `is_high_quality_fvg()` — FVG/ATR oranı `MIN_REL_FVG_THRESHOLD=0.50` altındaki FVG'leri reddeder. Tüm checklist tamamlandı. |
| 37 | **P0-5: STRKUSDT -4005 max quantity kısır döngüsü fix** | `place_stop_order/place_tp_order`: `close_position=True` parametresi eklendi (qty'siz emir). `_parse_error_code()` ile -4005 ayrımı. `get_max_qty()` helper. `repair_protection()`: -4005'te closePosition→parçalı dene, diğer hatalarda fiyat-bazlı retry aynen kalır. `recover_positions()`: aynı yaklaşım. `place_market_order_priority()`: CB bypass'li acil kapanış. `place_force_close_order()`: CB bypass. `_emergency_post()`: CB'sız POST. Backoff: 3 başarısız denemeden sonra 5dk bekle + CRITICAL uyarı. |
| 38 | **P0-6: `place_market_order()` `{}` dönmeme sorunu** | `place_market_order()` ve `place_market_order_priority()` hard failure'da `{"_status":...}` yerine `{}` döner. Caller'daki `if not close_result:` artık çalışır, `place_force_close_order()` tetiklenir. |
| 37 | **P0-5: STRKUSDT -4005 max quantity kısır döngüsü fix** | `place_stop_order/place_tp_order`: `close_position=True` parametresi eklendi (qty'siz emir). `_parse_error_code()` ile -4005 ayrımı. `get_max_qty()` helper. `repair_protection()`: -4005'te closePosition→parçalı dene, diğer hatalarda fiyat-bazlı retry aynen kalır. `recover_positions()`: aynı yaklaşım. `place_market_order_priority()`: CB bypass'li acil kapanış. `place_force_close_order()`: CB bypass. `_emergency_post()`: CB'sız POST. Backoff: 3 başarısız denemeden sonra 5dk bekle + CRITICAL uyarı. Test: 76+37 yeni test, 620+ geçiyor. |
| 37 | **FVG expiry filter** | `GLOBAL_FVG_EXPIRY_BARS=45` — 45 bar'dan eski FVG'ler 'ölü' kabul edilir. `is_fvg_valid()` session_router'da. Entry öncesi uygulanır. |
| 38 | **Session assignment** | 13 coin 3 session: **DEFAULT** (8: ADA, AVAX, DOT, NEAR, SOL, XRP, ETH, SUI), **REAL_CBDR** (2: ATOM, BTC), **ASIA_RANGE** (3: APT, BNB, LINK). ETH/SUI DEFAULT'a atanarak geri eklendi. |
| 39 | **CBDR_RISK_MATRIX final** | 13 coin bucket eşikleri + çarpanları backtest verisiyle dolduruldu. Her bucket WR/BE+/PnL baz alındı. Zehirli bölgeler (mult=0.0) işaretlendi. |
| 40 | **bot.py _session_label ASIA fix — backtest uyumu** | `_session_label()` 22-02'yi "ASIA" olarak etiketleyip blokluyordu. Bu REAL_CBDR coin'lerde (19-01) 01:00-02:00 arası hatalı bloka sebep oluyordu. Kaldırıldı. Artık coin bazlı CBDR penceresi blokajı (`cbdr_locked`) backtest'le birebir aynı. |
| 41 | **ExitLifecycleService extraction (Patch Set 2)** | `_exit_trade()`'den `ExitLifecycleService` (557 satır) ayrı modül olarak çıkarıldı (`src/trading/exit_lifecycle.py`). `bot.py`'da `EXIT_LIFECYCLE_SERVICE_ENABLED = cfg.EXIT_LIFECYCLE_SERVICE_ENABLED` flag + DI `exit_service` ile `_exit_trade()` wrapper (flag→execute, flag→legacy). Rollback guard: flag module-level const olarak yakalandığı için `@patch("bot.cfg", autospec=True)` interference'ı yok. 24 unit test + 3 wiring test. |
| 42 | **_round_step floor division fix** | `_round_step()`'de `value // step` kayan nokta hatasıyla 1 step eksik hesaplıyordu (7275.8 // 0.1 = 72757 → 7275.7). `int(value / step)` ile düzeltildi. OPUSDT'de her market close 0.1 OP kalıntı bırakıyordu. |
| 43 | **P1: State split model tanımları (192b6b6)** | `models.py`: `TradeStatus` enum, `TradeRuntimeState`, `TradeConfirmedState`, `ProtectionRef`, `ProtectionSlot`, `ProtectionState`, `PendingExitContext`, `NormalizedOrderEvent`. Henüz `ActiveTrade`'e bağlanmadı — sadece tip tanımları. |
| 44 | **P3: Protection lifecycle extraction (3935a51)** | `protection_lifecycle.py` (+265 satır): `ProtectionLifecycleService` — policy kararları OrderManager/RecoveryManager'dan ayrıldı. `ProtectionCheckResult` (tuple yerine dataclass). `CleanupPlan`. Rollout: `PROTECTION_LIFECYCLE_SERVICE_ENABLED` (env, default False). OrderManager + RecoveryManager delegate calls. |
| 45 | **P4: WS normalization — pending writes (007983b)** | `user_data_handler.py` (+238 satır): WS FILLED/TRIGGERED event'i artık confirmed alanlara direkt yazılmaz. `pending_exit_price/qty/order_id/timestamp`'e yazılır → `_exit_trade()` veya `ExitLifecycleService` promote eder. `normalize_order_event()` pipeline. Rollout: `WS_EVENT_NORMALIZATION_ENABLED` (env, default False). |
| 46 | **P5: bot.py orchestration cleanup (29ffd98)** | `_on_1m_close` yeniden yapısı: orphan sweep artık status'ten **bağımsız** (her 5 bar'da çalışır), ATR hesaplama `if unrestricted` bloğu içine taşındı, UPNL+state writer **her bar'da** (frozen trade'lerde bile). |
| 47 | **P1-1: repair_protection stale SL fallback** | `order_manager.py:repair_protection()` — SL/TP basarisizsa (fiyat coktan gecti, immediately trigger) mevcut mark_price + risk_pts ile yeniden hesapla. `recover_positions()` ile ayni retry mantigi. Eski: `trade["sl"]` dogrudan kullanilip reddedilir, sessizce yutulurdu. |
| 48 | **P1-4: periodic orphan sweep** | `recovery_manager.py:periodic_check_loop()` — `reconcile_orphan_orders()` periyodik olarak da calistirilir. Portfolio flat iken `_on_1m_close` tetiklenmez, sayac durur, orphan sweep calismaz. Artik 60sn'de bir her sey calisiyor. |
| 49 | **P0-4: restart REPAIR_REQUIRED cleanup** | `bot.py:run()` — recover_positions sonrasi REPAIR_REQUIRED/EXIT_REQUESTED trade'leri kontrol et. SL/TP saglikliyse STATUS_ACTIVE'e dondur. Eksi: onceki session'dan kalan bozuk trade sonsuza kadar REPAIR_REQUIRED'da kilitli kaliyordu. |
| 47 | **P6: Operator visibility (6df2134)** | `state_writer.py`: Her trade için `frozen` (status not in UNRESTRICTED) + global `feature_flags` (3 rollout flag'ın JSON durumu). |
| 48 | **B1: ActiveTrade runtime bağlantısı (bd234d4)** | `models.py` (+36): `TradeRuntimeState` → `ActiveTrade.runtime` field. `__getitem__`/`__setitem__` 3 key'i runtime'a yönlendirir: `status`, `frozen`, `pending_events`. `__post_init__` flat→runtime sync. |
| 49 | **B2: ProtectionState → runtime.protection (f2f15f1)** | `models.py` (+101): 6 flat protection alanı (`sl_order_id`, `tp_order_id`, `*_prev`, `pending_*`) → `runtime.protection` object üzerinden okunur/yazılır. `_PROTECTION_MAP` yönlendirme dict'i. `ProtectionState._get_ref/_set_ref` + `known_ids()`. |
| 50 | **B3: ProtectionCheckResult tuple yerine (35ac290)** | `order_manager.py`: `verify_protection()` dönüş tipi `(bool,bool)` → `ProtectionCheckResult` (`sl_present`, `tp_present`, `sl_healthy`, `tp_healthy`, `needs_repair`, `detail`). `__iter__` backward compat. REST fallback path de aynı dataclass'ı döndürür. |
| 51 | **fix: HTFFVG bar_index (2e73ae3)** | `bot.py`: `current.index - tf.real_index` → `current.index - tf.bar_index`. FVG expiry kontrolünde yanlış index kullanılıyordu. |
| 52 | **D1: ProtectionState lifecycle status (dbdab53)** | `models.py` (+32) + `state_writer.py` (+4): `sl_status(sl_price)`, `tp_status(tp_price)` → "NOT_REQUIRED"/"ACTIVE_CONFIRMED"/"PENDING_CREATE"/"EXPECTED". `health` → "HEALTHY"/"DEGRADED"/"BROKEN". State writer'a `sl_status`, `tp_status`, `protection_health` alanları eklendi. |
| 53 | **C(53): Explicit lifecycle states (9d0e72b)** | 3 yeni status: `EXIT_REQUESTED` (trail/exit tespitinde), `EXIT_SUBMITTED` (market order öncesi), `CLOSED` (commit'te string'den enum'a). `update_trail_orders()` replace sırasında `TRAIL_REPLACING`, success'te `ACTIVE`. bot.py + exit_lifecycle.py aynı state machine. |
| 54 | **P0-1: false position closed fix (c11c785)** | `exit_lifecycle.py:_submit_and_verify_market_close()` — verify loop'da adapter belirsizken (REQUEST_SENT/ORDER_ACKNOWLEDGED) `for-else` (sembol listede yok) ilk denemede `pos_closed=True` veriyordu. Binance gecikmeli donebilir, ilk `get_positions()` bos donebilir. Artik: (1) `is_ambiguous` flag, (2) belirsiz durumda `for-else` sadece son denemede kabul, (3) `get_all_orders()` fallback ile FILLED reduceOnly/emir kontrolu. UNIUSDT restart dongusu kok nedeni. |
| 54 | **E(54): Chaos/edge-case tests (9d0e72b)** | 4 test: delayed fill (4. attempt), REST timeout → REPAIR_REQUIRED, force close fallback (market REJECTED), state transition doğrulama. |
| 55 | **fix: close 3 system review findings (594f6f3)** | Review bulguları kapatıldı — detaylar commit'te embedded. |
| 56 | **P0-3: repair_protection per-symbol asyncio.Lock** | `order_manager.py`: `import asyncio` eklendi, `__init__`'e `_repair_locks: dict[str, asyncio.Lock]` eklendi. `repair_protection()` wrapper + `_repair_protection_locked()` rename. Aynı sembol için eşzamanlı çağrılar `lock.locked()` ile tespit edilip sessizce atlanır. Farklı semboller bloklanmaz. Test: 3 concurrency test (`TestRepairProtectionConcurrency`). |
| 57 | **P2-4: self-exit race guard** | `user_data_handler.py`: unmatched-reduceOnly fill, trade EXIT_SUBMITTED/EXIT_VERIFYING durumundayken WS_FALLBACK'e çevriliyordu (market-close emri SL/TP ID setinde yer almaz). `_SELF_EXIT_IN_PROGRESS_STATUSES` guard eklendi — hem normalized hem legacy handler'da. Legacy docstring güncellendi. raise → log_event + log.critical'e çevrildi (ACTIVE senaryosunda). WSFallbackError import kaldırıldı. |
| 58 | **P2-5: update_trail_orders -4005 fallback + backoff** | `order_manager.py`: SL/TP placement -4005 hatasında closePosition → split_qty fallback eklendi (repair_protection ile aynı desen). `error_code` log_event'a eklendi. `_trail_failures` backoff mekanizması: 3 ardışık başarısızlığın ardından 5dk backoff + CRITICAL uyarı. 8 yeni test. |
| 59 | **P1-6: Entry sizing max_qty kontrolü yok (kök neden)** | `entry_manager.py:calculate_qty()` — Binance LOT_SIZE.maxQty kontrolü yok. Sadece buying power tavanı var. Risk formulü çıkış qty'si maxQty sınırını aşabilir → SL/TP -4005 döngüsü. P2-5 semptom tedavisi, bu kök neden. **DURUM: DÜZELTİLDİ** — `execute_live_entry()`'e clamp eklendi. |
| 61 | **client_order_id traceability** | `place_market_order()` + `place_market_order_priority()`'ye `client_order_id` parametresi eklendi. Tüm callers (entry_manager, order_manager, bot, exit_lifecycle, recovery_manager) artık traceable clientOrderId ile market emri gönderiyor (entry=X, exit=X, sl-fail=X, reconcile=X, recover=X). |
| 60 | **P1-7: Harici kapanışlar (forensic) — DÜZELTİLDİ** | 26 WS_FALLBACK — 9 bot trailing / 9 kesin harici / 5 muhtemel harici / 3 log dışı. Önceki sayım tutarsızlıkları düzeltildi (8→9 trailing, 5→9 kesin, 13→3 log dışı). Event JSONL'den force_close + ws_unmatched_reduce_only ile tek tek doğrulandı. |
| 62 | **Görev 3: Post-entry sanity check** | `_try_entry()`'de successful entry sonrası ~2.5s bekleme + `get_open_order_ids()` ile SL/TP doğrulaması. Eksikse CRITICAL log + `post_entry_check_failed` event log. Sadece gözlem amaçlı — repair/recover tetiklemez. |
| 63 | **Görev 4: FVG invalidation exit_intent log** | `_on_1m_close()`'deki FVG kirildi→market close path'ine `log_event("exit_intent", reason="fvg_invalidated")` eklendi. Artık events_*.jsonl'den trail_close'lar raw log'a inmeden tespit edilebilir. |
| 64 | **Görev 5: test_entry_manager 8 test — PRE-EXISTING** | `72d06d9`'a kadar olan eski testler de aynı şekilde kırık. Testler eski london_high/low TP fallback beklentileriyle yazılmış, kod 1:2 R:R sabit TP'ye geçmiş. Regresyon değil — P1-3 backlog notu eklendi. |

## Aktif Kararlar

- **LEVERAGE=5**: 5x kaldıraç, margin = notional / 5.
- **RSM (RetraceStateMachine)**: IDLE → SWEEP_DETECTED → TRIGGER_READY. Sadece 3 state.
- **Max 1 trade/gün/sembol** (retrade kalktı).
- **CBDR penceresi içinde işlem yasak**: Backtest'le birebir aynı. CBDR body tracking penceresinde (DEFAULT 22-02, REAL_CBDR 19-01, ASIA_RANGE 01-05) trade alınmaz — sadece body tracking + bias üretimi. CBDR kilitlenince trade serbest. Eski `_session_label` "ASIA" blokajı kaldırıldı.
- **Erken London risk çarpanı (1.5x)**: 02-08 UTC'de pozisyon boyutu %50 artırılır.
- **CBDR bucket çarpanı**: 6 kademe (1.5x/1.2x/1.0x/0.8x/0.5x/0.0x). Coin bazlı, CBDR genişliğine göre.
- **3 katmanlı risk**: Zaman (EL 1.5x) × Kurulum (CBDR bucket) × Portföy (devre kesici). Defense mode'da EL ve Elite CBDR iptal.
- **Devre kesici**: DD ≥ %15 → defense mode (EL çarpanı kapanır, CBDR elite iptal). DD ≤ %10 → reset.
- **RiskManager**: `sniper/src/risk_manager.py`, filelock ile thread-safe, state `output/risk_state.json`.
- **Session Router**: `sniper/src/session_router.py` — coin bazlı CBDR çarpanı + zehirli bölge filtresi + FVG kalite/zaman aşımı kontrolü.
- **FVG expiry filter**: `GLOBAL_FVG_EXPIRY_BARS=45` — 45 bar'dan eski FVG'ler kullanılmaz.
- **Dinamik FVG filtresi**: `MIN_REL_FVG_THRESHOLD=0.50` — FVG/ATR oranı bu değerin altındaysa red.
- **Backtest doğrulaması**: 13/13 coin'de erken London WR > geç London/NY, tutarlılık %100. EL PF=4.35 vs non-EL PF=2.52. CBDR bucket matrisi backtest ile dolduruldu.
- **Explicit exit state machine**: `EXIT_REQUESTED` (trail/exit tespiti) → `EXIT_SUBMITTED` (market order gönderildi) → `EXIT_VERIFYING` (position verification) → `CLOSED` (commit). `update_trail_orders()` replace sırasında `TRAIL_REPLACING`, success'te `ACTIVE`.
- **Backtest metodu**: Parquet'ten linear PnL skalalama — exit koşulları price-based, qty skalası lineer taşınır. Gerçek portföy MaxDD günlük birleştirilmiş equity eğrisinden hesaplanır.
- **RISK_PER_TRADE=0.003**: Elle güncellendi (%0.3).
- **FVG_BUFFER_MULT=0.50**: Canlı ve backtest artık aynı.
- **MAX_SL_DIST_MULT=2.0**: FVG bazlı SL max `risk_pts × 2`.
- **CBDR gövde bazlı (open/close)**: High/low değil.
- **Backtest trailing live bot ile uyumlu**: `_fvg_close_confirmed()`, ATR buffer (`0.25×ATR`), `TRAIL_MIN_MOVE_MULT=0.2`, break-even (`1R` sonrası SL→entry).

## P0-7 — Deploy Edildi (cc6e48d, 2026-07-23)

**P0-7: `update_trail_orders()` TP iptal fix + precision-residual churn.**
- `order_manager.py`: `tp_ok and not tp_unchanged` guard — TP fiyatı değişmediğinde eski geçerli TP emri iptal edilmiyor, `tp_order_id` boş string ile ezilmiyor.
- `order_manager.py`: Precision-sonrası `sl_really_unchanged and tp_really_unchanged` erken return — tick-altı rezidüde emir atılmıyor/iptal edilmiyor, sonsuz churn önlendi.
- 4 regresyon testi: `TestTpUnchangedNoChurn` (2 test: long/short) + `TestPrecisionResidualNoChurn` (2 test: guard + yanlış pozitif guard).
- Patch `p0-7-tp-unchanged-and-precision-churn.patch`'ten apply edildi, commit: `cc6e48d`.

## Görev 10 — Post-Deploy Doğrulama (2026-07-23 13:11)

**P0-5 fix (7e50331): DEPLOY EDİLDİ + DOĞRULANDI.** Sunucu main branch üzerinde, tüm ilgili dosyalarda mevcut:
- ✅ `bot_binance.py:646` — `get_all_orders()`: `openAlgoOrders` hatasında exception fırlatıyor (sessiz yutma kaldırıldı).
- ✅ `order_manager.py:363/369/403` — `get_open_order_ids()`: REST hatasında `None` dönüyor, fail-safe çalışıyor.
- ✅ `bot.py:708` — `post_entry_check_failed` bloğu: `open_ids is None` durumunda check atlanıyor.
- ✅ `protection_lifecycle.py:130` — `verify_protection()`: `open_ids is None` durumunda `needs_repair=False` dönüyor.

**Deploy sonrası events_2026-07-23.jsonl durumu (son kontrol 14:32 UTC+3):**
- Toplam event: 157 (sl_reject: 98, tp_reject: 58, exit: 43, entry: 19, force_close: 14, post_entry_check_failed: 11, exit_intent: 9, ws_unmatched_reduce_only: 7)
- **STRKUSDT ghost loop:** Son event 1784784617356 (sl_reject -4005, ~13:10 UTC+3) — sonrasında hiç event, loop kapanmış.
- **SEIUSDT ghost loop:** Son event 1784799001314 (exit, TRAIL_CLOSE, trailing_count=10, ~13:30 UTC+3) — sonrasında yeni entry yok, loop kapanmış.
- 3 orphan pozisyon (NEARUSDT, LDOUSDT, APTUSDT) bot tarafından takip ediliyor: 2'si ACTIVE (LDOUSDT, APTUSDT), 1'i kapatıldı (NEARUSDT exit 1784803228413).
- Bot sağlıklı: pid 83179 çalışıyor, 15m barlar akıyor, WS bağlantısı aktif.
- **⚠️ ACİL DURUM (15:33):** APTUSDT pozisyonu Binance'de HÂLÂ açık (short 1024.5 @ 0.62290, upnl=+5.12 USDT) ama bot 3 kez yanlışlıkla `EXIT: SL | PRICE: 0.62 | PNL: +1.62` logladı. P0-6 fix yazılıyor — deploy edilene kadar manuel izleme gerekli.

**Sıradaki açık iş:** P0-6/Görev 12 — `_exit_already_closed` SL/TP result'larında position_still_open() kontrolü genişletme. exit_lifecycle.py + bot.py'da WS_FALLBACK guard'ı SL/TP için de aktif edilecek. APTUSDT canlı izleme altında.
- **P1-11 (yeni, 2026-07-23 analizi):** `EXIT_REQUESTED` runtime dead-end — `_exit_trade()` pozisyon doğrulaması False döndüğünde status ACTIVE'ye resetlenmiyor. Bot per-bar döngüsü (`UNRESTRICTED_STATUSES`) ve orphan sweep (`should_skip_reconcile`) EXIT_REQUESTED'leri atlıyor → sadece restart kurtarıyor. SEIUSDT (~17 dk) ve UNIUSDT (~6 dk) etkilendi. TRAIL_REPLACING ise kısmi başarı durumunda ACTIVE'ye resetleniyor → dead-end değil.

## Sıradaki / Açık Konular

- ~~**P1-2 fix:**~~ ~~`update_trail_orders()` TRAIL_REPLACING stuck — DÜZELTİLDİ (Görev 11, order_manager.py:117-315)~~
- Canlı testte `_exit_trade()` cancel_all + reduceOnly flow'un Binance ile çalışması gözlemlenecek.
- Backtest trailing port'u sonrası WR/DD değişimi canlı ile karşılaştırılacak.
- WS_FALLBACK sayısı trail prev ID fix sonrası takip edilecek.
- **P1-7 forensic turu kapatıldı (2026-07-23):** 26 vaka doğrulandı (9 bot trailing / 9 kesin harici / 5 muhtemel harici / 3 log dışı). #22/#26 ONDOUSDT trail=3 doğrulandı — `_oid_matches_trade` tam history kullanıyor, fill bot SL'inden değil dış kaynaktan. Kalan adım: Binance API ile #19/#26 clientOrderId kaynağını teyit (insan kararı).
- **FVG marker konum bug'ı** (chart'ta gördüğümüz, 3 örnek: SOLUSDT aynı gün) — kök neden araştırılıyor.
- **CBDR_RISK_MATRIX** canlı performansı gözlemlenecek — bucket çarpanlarının gerçek PnL'e uyumu kontrol edilecek.
- **Session assignment** sonrası DEFAULT/REAL_CBDR/ASIA_RANGE geçişlerinde FVG bulunamama sorunu tekrarlarsa analiz edilecek.
- **FVG_SIZE_MAP güncellemesi (2026-07-17):** Tüm 28 coin best session FVG Size değerleriyle yenilendi.
- **CBDR_RISK_MATRIX session sync (2026-07-17):** 9 coin'in session'ı best session analizine göre güncellendi. ATOM:ASIA→REAL, AVAX:REAL→DEFAULT, DOT:DEFAULT→REAL, INJ:ASIA→REAL, LINK:REAL→ASIA, NEAR:ASIA→REAL, OPUS:DEFAULT→REAL, UNI:DEFAULT→REAL, XRP:REAL→DEFAULT.
- **FVG_MIN_SIZE_ATR_MULT güncellendi (2026-07-15):** 0.08→0.06 (analyze_cbdr_thresholds.py ile aynı).
- **SYMBOLS listesi genişletildi (2026-07-15):** 10 yeni coin eklendi (toplam 28).
- **ict_cbdr_thresholds.md** — geçersiz (sahte ATR ile koşmuş), yeniden koşulacak (sırada bekliyor).
- **v3_window_comparison.md** — geçersiz çıktı, yeniden koşulacak (sırada bekliyor).
- **[FVG_SCAN] log formatı** — 16 haneli float basıyor, `.6f` ile sınırlanması istendi, teyit edilmedi.
- **Wiring test scope fix**: `test_flag_true_delegates_to_exit_service` ve `test_flag_false_calls_legacy`'de `with patch("bot.EXIT_LIFECYCLE_SERVICE_ENABLED", ...)` bloğu `_exit_trade` çağrısını kapsamıyordu — patch revert olup flag kayboluyordu. Tüm akış `with` içine alındı, 3/3 wiring testi geçiyor.
- Coin bazlı pencere kararı (real_cbdr/asia_range) — CBDR_RISK_MATRIX içinde session assignment çözüldü, artık v3_window_comparison.md'ye bağımlı değil.
- Dün gece FVG bulunamama şikayeti (23:00'a kadar hiçbir coinde FVG yok, 1-2 sweep) — MULT=0.06 + ATR-bazlı FVG filtresi sonrası düzelip düzelmediği kontrol edilecek.
- **Backtest altyapısı entegrasyonu**: 5 dosya (session.py, retrace_state.py, fvg.py, models.py, coins_config.py) silindi — artık `sniper/src`'ten import ediliyor. `SNIPER_OUTPUT_DIR` env var ile production output/ klasöründen izolasyon. Determinism doğrulandı (in-memory state sızıntısı yok). `mult_scan.py`'de checkpoint/resume mekanizması var.
- **Rollout flag takibi**: 3 rollout flag — `EXIT_LIFECYCLE_SERVICE_ENABLED` (default False), `PROTECTION_LIFECYCLE_SERVICE_ENABLED` (default False), `WS_EVENT_NORMALIZATION_ENABLED` (default False). Hepsi şu an **kapalı**. Feature_flags state_writer'da JSON'a yazılıyor (P6).
- **Backfill** — P1 modelleri henüz ActiveTrade'e bağlı değilken B1/B2 bağlanmıştı. `TradeConfirmedState` field'ları `ActiveTrade` flat alanlarına henüz bağlanmadı — hala kullanılmıyor.
- **Sprint C** `EXIT_REQUESTED` + `pending_exit_price` yazma mekanizması eklendi (bot.py:478, trail/exit tespitinde). Ancak `pending_exit_*` → confirmed promotion (`_close_trade_pending_exit()`) henüz implemente edilmedi — P4 WS normalization için gerekli.

## Hatırlatmalar

- sweep_direction mapping: yukarı sweep = bearish = SHORT, aşağı sweep = bullish = LONG.
- `mark_sweep_consumed()` level-based ID kullanır — bar_index değil.
- `rsm.reset()` artık `_exit_trade()` sonunda çağrılır, `_try_entry()` içinde değil.
- Trailing güncellemede eski order id `*_order_id_prev` olarak saklanır, geçiş penceresinde WS fill'leri prev id ile de eşleşebilir.

---

## Son İşlem: 2026-08-09 — TRADE STATUS KİLİTLENMESİ FIX (Prompt 3 — RENDERUSDT -8.92%)

### 🔴 Kök neden
1. `_on_1m_close` → trailing + check_exit aynı kapıya bağlı (`status in UNRESTRICTED`). Status dışında takılırsa TP/SL kontrolü bir daha çalışmaz.
2. `update_trail_orders` → protection lifecycle çağrıları try/except dışında → exception durumunda status `TRAIL_REPLACING`'de kalır.
3. `orchestrate_trail` try/except yok → üst katmanda düzeltme mekanizması yok.

### Fix A — order_manager.py `update_trail_orders` "ikisi başarılı" bloğu
- SL/TP state güncellemesi + protection lifecycle (`begin_replace_sl/promote_sl`, `begin_replace_tp/promote_tp`) + eski emir iptal + log + status reset → tümü `try` içinde.
- `except` → `log.critical` + `_fail_and_reset_status()` (status ACTIVE + backoff artır). Yeni emirler borsada açık kalabilir → `reconcile_orphan_orders` süpürüyor (log'da not edildi).

### Fix B — bot.py `_on_1m_close` orchestrate_trail try/except
- `orchestrate_trail` exception → `log.critical` + `trade["status"] = STATUS_ACTIVE` zorla.
- Amaç: `check_exit()` (hemen altındaki satır) HER 1m barında çalışsın; TP/SL kontrolü trailing'e bağımlı olmasın.

### Fix C — Watchdog (fail-safe)
- `_orphan_check_counter % 5` orphan sweep'inde: status ACTIVE/"" dışında + `status_since` 90s'den eski → `log.critical` + ACTIVE'e zorla geri çek.
- `STATUS_TRAIL_REPLACING` atarken `trade["status_since"] = time.time()` yazılıyor (order_manager.py:159).

### Testler
- `test_update_trail_orders_protection_exception_resets_status` (Fix A): protection lifecycle exception → status ACTIVE.
- `test_orchestrate_trail_exception_sets_status_active_and_continues` (Fix B): orchestrate_trail exception → status ACTIVE + check_exit çağrıldı.
- `test_watchdog_resets_stuck_trail_replacing_after_90s` (Fix C): TRAIL_REPLACING 100s sonra → ACTIVE.
- **order_manager: tümü geçti / test_bot: 13 pre-existing fail aynı (0 yeni).**

### Dokunulmadı
- state_writer `sl_order_id_present`/`tp_order_id_present` boolean (backlog).
