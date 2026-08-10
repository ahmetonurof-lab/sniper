# Direktif — YÜKSEK Bulgular Fix Sırası (baş mühendis → yerel ajan)

**Kaynak:** `gizli-bug-audit-raporu-2026-08-10.md` (KRİTİK 4/4 fixlendi + push'landı, commit `8e0fc8c` + `007bdc8`)
**Kapsam:** Kalan 9 YÜKSEK bulgu (A1-02 zaten kritik turunda fixlendi)
**Kural:** Her fix ayrı commit. Her fix'ten önce ilgili test dosyasını çalıştır, fix sonrası aynı dosyayı + `test_recovery_manager.py`'yi tekrar çalıştır. Regresyon testi eklemeden commit YOK.

---

## Sıra 1 — A4-05 (recovery SL/TP yerleştirme tek try'da yutuluyor)

**Dosya:** `src/trading/recovery_manager.py:368-517`

**Sorun:** SL ve TP yerleştirme denemelerinin TAMAMI tek bir `try/except Exception as e: log.warning(...)` bloğunda. Herhangi bir adımda (SL place, TP place, retry) exception patlarsa döngü sessizce devam ediyor ve pozisyon SL/TP'siz `active_trades`'e giriyor — ama bu A4-08'in "acil kapanış" dalına hiç girmiyor çünkü kod `sl_id` boş değilse oraya düşmüyor, sadece except sonrası akış TP/SL kısmi kurulu şekilde devam ediyor.

**Yapılacak:**
1. Tek try bloğunu SL yerleştirme ve TP yerleştirme için **ayrı** try/except'lere böl. Her birinin kendi except'i kendi `sl_id`/`tp_id`'sini `""` bırakmalı, diğerini etkilememeli.
2. Except sonrası, eğer `sl_id` hâlâ boşsa mevcut `if not sl_id:` acil-kapanış dalına düşmesini sağla (şu an bu dal sadece "SL retry mekanizması hiç denemedi" durumunu değil "hiç place edilemedi" durumunu yakalıyor — kontrol et, muhtemelen zaten öyle çalışıyor, sadece except bloğunun sl_id'yi boş bırakmasını garanti et).
3. TP tarafında except sonrası `tp_id=""` ile devam serbest (TP eksikliği kritik değil, log + not yeterli — mevcut `protection_note` mekanizması zaten bunu işaretliyor).

**Test:** `test_recovery_manager.py`'ye SL place exception, TP place exception (ayrı ayrı) senaryoları ekle — SL exception'da acil kapanış dalına düştüğünü, TP exception'da pozisyonun SL'li ama TP'siz active_trades'e girdiğini doğrula.

---

## Sıra 2 — A4-08 (acil kapanış başarısız → retry/escalation yok)

**Dosya:** `src/trading/recovery_manager.py:519-669`

**Not:** Bu bulgu A4-04 (place_market_order artık `{"_error":...}` dönüyor) ve `recover_emergency_close continue` fix'i (007bdc8) ile kısmen iyileşmiş olabilir — **önce mevcut davranışı gözden geçir**, hâlâ eksik olan sadece "retry/escalation" kısmı.

**Yapılacak:**
1. `close_result` alınamadığında (market order + closePosition fallback ikisi de başarısız) tek deneme yerine **1 kez backoff'lu retry** ekle (örn. `await asyncio.sleep(1.0)` + tekrar `place_market_order_priority`). Zaten SL retry pattern'i dosyada var (TP cancel retry, satır ~587), aynı stili kullan.
2. Retry de başarısız olursa mevcut `log.critical` + `self._pl(...)` yeterli — bunun ötesine (dış alarm sistemi vb.) **geçme**, kapsam dışı.
3. Pozisyonu korumasız `active_trades`'e ekleme davranışını DEĞİŞTİRME (bu bilinçli tasarım — periodic loop tekrar dener).

**Test:** Mevcut `test_position_stays_when_both_close_methods_fail` testini çalıştır, retry eklendikten sonra hâlâ geçtiğini doğrula + yeni bir "retry sonrası başarılı" test senaryosu ekle.

---

## Sıra 3 — A2-01 + A2-02 (MIN_NOTIONAL dust pozisyon — acil kapanış ve trailing SL)

**Dosyalar:** `src/bot_binance.py:1155-1214` (place_market_order_priority), `src/bot_binance.py:853-891` (place_stop_order)

**Sorun:** Aynı kök neden iki yerde: minNotional altı qty'lerde (`APTUSDT qty=0.1` örneği, bugs.md P2-8) hem acil kapanışta hem trailing SL yeniden yerleştirmede closePosition/qty'siz moda düşülmüyor, sadece `{}`/error dönüyor.

**Yapılacak (ikisi birlikte, aynı kök fix):**
1. `place_market_order_priority`: minNotional reddi (`_error_code` MIN_NOTIONAL ile eşleşen Binance hata kodu, örn. `-4164`) alındığında **closePosition=true qty'siz modu** dene (dosyada zaten -4005/max-qty için bu fallback var — satır 853-877 civarı — aynı closePosition çağrısını minNotional koduna da ekle).
2. `place_stop_order`: `validate_min_notional` 0.0 döndüğünde (satır 888-891) direkt `{}`/error dönme — closePosition modunu (qty'siz SL) dene.
3. Binance hata kodu sabitini bir yerde (örn. `bot_binance.py` üstünde) tanımla, iki yerde de aynı sabiti kullan — magic number tekrar etme.

**Test:** `test_bot_binance.py`'ye APTUSDT tarzı dust qty (notional < 5 USDT) için hem `place_market_order_priority` hem `place_stop_order`'ın closePosition moduna düştüğünü gösteren test ekle.

---

## Sıra 4 — A3-02 (active_trades dict'e lock'suz yazma)

**Dosya:** `src/trading/recovery_manager.py:222, 645, 676` + `bot.py`/`user_data_handler.py`'nin aynı dict'e eriştiği yerler

**Yapılacak:**
1. `RecoveryManager`'a per-symbol `asyncio.Lock` sözlüğü ekle (muhtemelen `bot.py`'deki `self._exit_locks` ile aynı `dict[str, asyncio.Lock]` pattern'i, DI ile paylaşılabilir — A3-01 fix'inde zaten `exit_locks` DI olarak taşınmıştı, **aynı lock nesnesini** kullanmayı değerlendir, ayrı bir lock seti YARATMA, race'i iki farklı lock arasında bölüştürmüş olursun).
2. `recovery_manager.py`'deki üç `self._active_trades[sym] = ActiveTrade(...)` ve `existing[...]` mutation bloklarını `async with self._exit_locks[sym]:` (veya eşdeğeri) içine al.
3. **Dikkat:** `recover_positions()` başlangıçta (bot henüz `run()` çağırmadan) çalışıyorsa `_exit_locks` dict'i o sembol için henüz oluşmamış olabilir — `defaultdict(asyncio.Lock)` kullan ya da lock'u lazy oluştur.

**Test:** Var olan `test_recovery_manager.py` testlerinin lock eklenince hâlâ geçtiğini doğrula (asyncio.Lock testlerde sorun çıkarmaz ama mock rest client'ların await sırasını bozmadığından emin ol).

---

## Sıra 5 — A3-03 (senkron `requests.get` event loop'u blokluyor)

**Dosya:** `src/snapshot.py:49` (`_fetch_ohlc`), çağıran: `exit_lifecycle.py:758`

**Yapılacak:**
1. `requests.get(...)` çağrısını `asyncio.to_thread(requests.get, ...)` ile sar (en düşük riskli, aiohttp'ye geçiş kapsam dışı — sadece thread'e taşı).
2. `_fetch_ohlc`'ı `async def` yap, çağıran yer (`exit_lifecycle.py:758`) zaten async context'te olduğu için `await` ekle.
3. timeout=15sn korunsun, davranış değişmesin — sadece event loop'u artık bloklamasın.

**Test:** `snapshot.py`'nin mevcut testlerini çalıştır (varsa); yoksa `_fetch_ohlc` çağrısının event loop'u bloklamadığını gösteren basit bir test ekle (örn. paralel bir `asyncio.sleep(0)` task'ının snapshot çağrısı sırasında da tick'lediğini doğrulayan test).

---

## Sıra 6 — A4-02 (orphan emir taraması sembolü sessizce atlıyor)

**Dosya:** `src/trading/recovery_manager.py:873` (`reconcile_orphan_orders`)

**Yapılacak:**
1. `except Exception: continue` → A4-01 fix'indeki pattern'i tekrarla: `log.error("[ORPHAN] %s sorgu hatasi, sembol atlandi: %s", sym, e)` ekle.
2. Ghost temizliğindeki gibi ardışık hata sayacı (`_orphan_fail_count`) ekle, eşik aşılırsa `log_event("orphan_check_persistently_failing", ...)`.
3. Davranışı DEĞİŞTİRME — hâlâ o sembolü atla, sadece sessizlik kalksın.

**Test:** `get_all_orders` mock'ta RuntimeError fırlattığında log.error çağrıldığını ve fail counter'ın arttığını doğrulayan test.

---

## Sıra 7 — A4-03 (demo mod order-id bulunamama sessizce yutuluyor)

**Dosya:** `src/bot_binance.py:817, 927, 1023` (place_market/stop/tp_order demo fallback)

**Yapılacak:**
1. Üç yerdeki `except Exception: pass` → en azından `log.warning(...)` ekle (A4-01/A4-02 ile aynı prensip: sessizlik kalksın, davranış aynı kalsın — hâlâ `ORDER_ACKNOWLEDGED` dönebilir ama artık loglanarak).
2. Kapsam dışı: demo mod davranışını sağlamlaştırmak (retry vs.) — sadece görünürlük ekleniyor.

**Test:** Demo modda `get_open_orders` exception fırlattığında log.warning çağrıldığını doğrulayan 3 test (market/stop/tp).

---

## Sıra 8 — A6-01 (backtest sweep tüketimi yok) — **AYRI REPO: `backtest-sniper`**

**Dosya:** `backtest-sniper/src/analyzer_v5.py:418-423`

**Not:** Bu repo `sniper` değil — local ajanın `backtest-sniper` reposuna da erişimi olmalı, kontrol et.

**Yapılacak:**
1. Canlı fix'i referans al: `sniper/src/signal_engine.py:78-93` — `on_sweep(bar_index=current.index)` çağrısı ve `ss.sweep_confirmed=False` ataması (satır 88 ve IDLE dalında 93).
2. `analyzer_v5.py:418-423`'teki `on_sweep(..., bar_index=None)` çağrısını `bar_index=current.index` (veya backtest'in bar index eşdeğeri) ile değiştir.
3. `sweep_confirmed`'ı canlıdaki gibi uygun noktalarda `False`'a çek — `retrace_state.py:106-109`'daki `is_sweep_used` dedup mantığının backtest tarafında da aynı şekilde çalıştığından emin ol.

**Test:** SEIUSDT fixture'ı ile (canlıdaki 08-06 fix testine karşılık gelen) tek-sweep → tek-giriş senaryosunu backtest'te doğrulayan test ekle. Fix öncesi/sonrası trade sayısını karşılaştır.

---

## Sıra 9 — A6-02 (entry çapası: canlı close vs backtest next_bar.open) — **AYRI REPO + KARAR GEREKTİRİYOR**

**Dosyalar:** `sniper/src/bot.py:671` (canlı, `current.close`) vs `backtest-sniper/src/analyzer_v5.py:477-478` + `sniper/simulate.py:235-237` (`next_bar.open`)

**Bu bir "fix" değil, bir mimari karar.** Kod değişikliğine geçmeden önce baş mühendisten karar iste:
- **Seçenek A:** Backtest'i canlıya uydur (`next_bar.open` → `current.close`). Risk: look-ahead bias'a daha yakın olur ama canlı paritesi artar.
- **Seçenek B:** Canlıyı backtest'e uydur (`current.close` → `next_bar.open`, yani sinyal 15m kapanışında değil bir sonraki bar açılışında girilir). Risk: canlı giriş 1 bar gecikir, strateji davranışı değişir — muhtemelen istenmeyen.
- **Seçenek C:** İkisini bilerek ayrık bırak, sadece dokümante et (backtest sonuçlarının canlıdan sistematik saptığı bilinen bir fark olarak `progress.md`'ye not düş).

**Yapılacak:** Karar gelmeden kod dokunma. Bu maddeyi listede en sona bıraktım çünkü diğer 8'i tamamlamak riski daha çok azaltıyor ve bu ikisi zaten "backtest parity" ailesinden — tek celsede karar + fix + A/B replikasyon run'ı gerektiriyor.

---

## Genel kurallar (tüm sıralar için)
- Her madde kendi commit'i olsun, commit mesajında bulgu ID'si geçsin (`fix: A4-05 recovery SL/TP try ayrımı`).
- Fix sonrası ilgili test dosyası + `test_recovery_manager.py` + `test_bot_binance.py` tam koşulsun, pre-existing bilinen 2 fail (`test_sl_matched_pending_promoted_to_confirmed`, `test_ws_fallback_promotion_still_works`) dışında hiçbir kırık test'le commit atma.
- Her fix sonrası `memory-bank/activeContext.md` ve `progress.md`'ye kısa not düş (önceki KRİTİK turundaki formatı takip et).
- Sıra 4 (A3-02, lock) ve Sıra 1/2 (A4-05/A4-08) aynı dosyada (`recovery_manager.py`) çakışabilir — Sıra 1-2'yi bitirip commit'ledikten SONRA Sıra 4'e geç, aynı anda iki fix'i aynı bloklarda karıştırma.
- Sıra 8-9 farklı repo — commit/push oraya ayrı yapılmalı, `sniper` reposundaki commit'lerle karıştırma.
