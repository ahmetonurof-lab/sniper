# Gizli Bug Araştırma Raporu — sniper/src
**Tarih:** 2026-08-10
**Yöntem:** 6 ajanlı hipotez-güdümlü paralel denetim (A1–A6), kanıt protokolü
**Kapsam:** State/veri akışı, büyüklük/matematik, eşzamanlılık, kurtarma/istisna, snapshot/telemetri, backtest-live parity
**Not:** A5 (snapshot/telemetri) rate limit nedeniyle eksik — ileride tamamlanacak.
**Başmühendis değerlendirmesi bekleniyor.**

---

## Metodoloji

- **Hipotez-güdümlü tarama:** Her ajan geçmiş fix'lerin bug sınıfına odaklandı (ör. `entry_timestamp` eksik → state alanı eksikliği sınıfı). Naif "tüm kodu tara" değil.
- **Kanıt protokolü:** Her bulgu şu 3 kanıttan en az biriyle desteklenmeli: (1) kod izi (dosya:satır + alıntı), (2) mevcut test çalıştırma çıktısı, (3) canlı log/veri (`output/`). Kanıtlanamayan iddialar `[HİPOTEZ]` etiketiyle ayrı listeye yazıldı, kesin bulgu sayılmadı.
- **Bayat fail dışlama:** 18 pre-existing fail (test_bot 13 + test_state_writer 2 + test_event_log 1 + test_user_data_handler 2) "yeni bulgu" olarak raporlanmadı.
- **Kod değişikliği yok.** Sadece analiz.
- **Ajanlar:** A1 Big Pickle, A2 DeepSeek V4 Flash, A3 Poolside Laguna S 2.1 (thinking), A4 Tencent Hy3, A5 Gemini 3.5 Flash (eksik), A6 Ling 3.0 Tiny (thinking).

---

## Toplam Özet

| Şiddet | Kanıtlı Bulgu Sayısı |
|--------|----------------------|
| KRİTİK | 4 |
| YÜKSEK | 10 |
| ORTA | 9 |
| DÜŞÜK | 7 |
| **TOPLAM** | **30** |
| HİPOTEZ (kanıtsız) | 12 |

---

## KANITLI BULGULAR (şiddet sırasıyla)

### KRİTİK

#### A1-01 | KRİTİK | `src/trading/recovery_manager.py:222, 645, 676`
**Senaryo:** Restart sonrası `recover_positions()` çalıştığında Binance'de açık pozisyonlar için ActiveTrade yeniden kuruluyor. SL/TP emirleri varsa `sl_order_id`, `tp_order_id` ve `protection_orders` dict güncelleniyor ama `runtime.protection` (ProtectionState) hiç dokunulmuyor.

**Neden bug:** `order_manager.py:1271-1284`'teki `_sync_replaced_order_id` / `_sync_runtime_protection` yalnızca trailing/cancel replace yolunda çalışıyor. Recovery yolunda sadece flat alanlar güncelleniyor; `runtime.protection.sl_current` ve `tp_current` `None` kalıyor.

**Kanıt (kod izi):**
```python
# recovery_manager.py:213-218
existing["sl"] = sl_price
existing["tp"] = tp_price
existing["sl_order_id"] = sl_id
existing["tp_order_id"] = tp_id
existing["protection_orders"] = protection_orders
existing["risk_pts"] = risk_pts
existing["tick_size"] = tick_size
# runtime.protection güncellemesi YOK
```
`order_manager.py:1271-1284`: `_sync_runtime_protection` tanımlı ama recovery_manager tarafından çağrılmıyor.

**Önerilen doğrulama:** `recovery_manager.py`'deki `existing` güncelleme bloklarına `self._sync_runtime_protection(existing, "sl_current", sl_id, sl_price)` ve tp karşılığını eklemek.

---

#### A3-01 | KRİTİK | `bot.py:624-631` + `user_data_handler.py:246-255` + `exit_lifecycle.py:156-174`
**Senaryo:** Aynı sembolde eşzamanlı bar-close exit ve User Data Stream (WS) exit.

**Neden bug:** Market data WS callback'i (`_on_1m_close`) ve user data WS callback'i (`_on_order_update_normalized`) iki ayrı asyncio task'tır. Her ikisi de `trade["pending_exit_*"]` ve `trade["result"]` alanlarını `_exit_locks` edilmeden MUTASYONA uğratıyor. Idempotency guard `trade.get("result")` üzerinden çalıştığı için, bir callback `result="SL"` diye yazarken diğeri `result="WS_FALLBACK"` diye yazarsa ikinci commit'te idempotency guard devreye girmez ve aynı trade için ikinci kez `_commit_confirmed_exit` çalışır.

**Kanıt (kod izi):**
```python
# bot.py:624-631 (bar-close)
trade["pending_exit_price"] = price
trade["result"] = reason
# user_data_handler.py:246-255 (WS FILLED)
trade["pending_exit_price"] = price
trade["result"] = reason
# exit_lifecycle.py:156-174 — lock'tan önce trade referansı okunur
```

**Önerilen doğrulama:** `_on_1m_close` ve `UserDataHandler`'ın her ikisi de `_exit_trade` çağrısından ÖNCE `async with self._exit_locks[trade_key]` ederek trade dict'ini korumalı.

---

#### A4-01 | KRİTİK | `src/trading/recovery_manager.py:722`
**Senaryo:** `reconcile_ghost_positions()` çağrıldığında ilk adım `dump_state()` (state_manager.py:257). Eğer state dosyası kilitli, bozuk veya disk hatası nedeniyle okunamazsa:

```python
try:
    state = dump_state()
except Exception:
    return  # ← SESSİCE YUT, TAMAMEN ATLA
```

**Neden bug:** `except Exception: return` ile hata tamamen yutuluyor. Ghost pozisyon temizliği o döngüde TAMAMEN atlanıyor. P1-4 periyodik yapıldı ancak bu hata periyodik temizliği de etkisizleştiriyor — restart olmadan ghost'lar süresiz temizlenmez.

**Kanıt (kod izi):**
```python
# recovery_manager.py:720-723
except Exception:
    return
# recovery_manager.py:904-916 — periodic_check_loop içinde her 60sn çağrılıyor
# state_manager.py:257-260 — dump_state() FileLock ile okuma yapıyor
```

**Önerilen doğrulama:** `state_manager.py:259`'daki FileLock hatasını simüle eden test yazılabilir.

---

#### A4-04 | KRİTİK | `src/bot_binance.py:770, 780, 796, 885, 891, 981, 987, 1186, 1191, 1208`
**Senaryo:** `place_market_order`, `place_stop_order`, `place_tp_order`, `place_market_order_priority` hata durumunda boş `{}` dict döner.

**Neden bug:** Bu, P2-8 bilinen bayatıdır. `{}` döndüğünde çağıran kod (`extract_order_id({})` → `""`, `{}.get("orderId")` → `None`) emrin başarısız olduğunu anlıyor. Ancak `recovery_manager.py:538` ve `entry_manager.py:480` bu boş dict'i kullanır. `place_market_order_priority` acil kapanış için kullanıldığında, `{}` dönmesi pozisyonun gerçekten kapandığını doğrulamadan "başarısız" olarak işaretlenmesine yol açar.

**Kanıt (kod izi):**
```python
# bot_binance.py:769-796
r = await self.post("/fapi/v1/order", params)
if r.is_err:
    log.warning("[MARKET] %s MARKET hatasi: %s", symbol, r.error)
    return {}  # ← BOŞ DICT
# recovery_manager.py:538-548 — place_market_order_priority çağrısı
# recovery_manager.py:626 — ACIL KAPANIS BASARISIZ sonrası ActiveTrade ekleme
```

**Önerilen doğrulama:** `test_bot_binance.py`'deki `TestPlaceMarketOrderPriority` testleri (875-932) `return {}` davranışını test ediyor.

---

### YÜKSEK

#### A1-02 | YÜKSEK | `src/trading/exit_lifecycle.py:765` + `output/trades_history.jsonl:43-44`
**Senaryo:** Exit lifecycle trade kapama sırasında `{**trade}` ile tüm ActiveTrade alanlarını kopyalıyor. `runtime` alanı `TradeRuntimeState` objesi olduğu için `json.dumps(default=str)` ile `__repr__` stringe dönüşüyor.

**Neden bug:** Yapısal `runtime.protection` (ProtectionState) verisi kayboluyor. JSON parse edildiğinde `runtime` bir dict değil, parse edilemez string oluyor.

**Kanıt (canlı veri):**
```json
// output/trades_history.jsonl satır 43-44
{"runtime": "TradeRuntimeState(status=<TradeStatus.EXIT_VERIFYING: 'EXIT_VERIFYING'>, frozen=True, pending_exit=None, protection=ProtectionState(sl_current=ProtectionRef(...), ...), pending_events=[])", ...}
```
```python
# exit_lifecycle.py:775
json.dumps(record, ensure_ascii=False, default=str)
```

**Önerilen doğrulama:** `exit_lifecycle.py:765`'teki `{**trade}` yerine özel bir `serialize_trade(trade)` fonksiyonu yazmak; `runtime` alanını dict olarak çıkarmak veya hariç tutmak.

---

#### A2-01 | YÜKSEK | `bot_binance.py:1155-1214` + `recovery_manager.py:538` + `exit_lifecycle.py:417`
**Senaryo:** qty=0.1 APTUSDT (fiyat≈0.59 → notional 0.06 USDT < 5.0). `place_market_order_priority` yalnızca `apply_amount_precision` + `validate_min_amount` yapar; minQty 0.1 geçer. MIN_NOTIONAL yerelde hiç kontrol edilmez → Binance reddi → `{}` → `ACİL KAPANIS BAŞARISIZ -- MANUEL MUDAHALE GEREKLI`.

**Neden bug:** `place_market_order`'da minNotional kontrolü bilinçli kaldırılmış ve yalnızca entry'ye (`_bump_to_min_notional`) devredilmiş. Ama recovery ACİL KAPANIŞ ve reduceOnly exit close aynı bilinçsiz yoldan geçiyor; closePosition fallback'i de başarısız olursa pozisyon restart'taki ghost temizliğine kadar korumasız kalıyor.

**Kanıt (kod izi + canlı log):**
```python
# bot_binance.py:1172-1173
# minNotional kontrolü burada YOK
# memory-bank/bugs.md:529-551 (P2-8, 08-09 07:25 UTC)
```

**Önerilen doğrulama:** `place_market_order_priority`'ye reduce-only dust close senaryosu testi.

---

#### A2-02 | YÜKSEK | `bot_binance.py:888-891` + `order_manager.py` trail yolu
**Senaryo:** A2-01'deki dust pozisyonunda her bar trailing SL güncellemesi `place_stop_order` reduceOnly moduna girer; `validate_min_notional` (888) 0.0 döner → `{}` (890-891) → `trail_skipped no_protection_update_required` — SL/TP hiç kurulmaz.

**Neden bug:** minNotional reddinde closePosition moduna (qty'siz, 853-877) geçiş yok — o mod MIN_NOTIONAL'dan muaftır. -4005 (max qty) için closePosition fallback'i var ama minNotional `{}` senaryosu için yok.

**Kanıt (kod izi + canlı log):**
```python
# bot_binance.py:888-891
# validate_min_notional → 0.0 → {} dönüşü
# memory-bank/activeContext.md:163 ("her dakika 4× MINNOTIONAL WARNING + trail_skipped")
```

**Önerilen doğrulama:** minNotional altı SL yeniden yerleştirmede closePosition moduna geçişi test eden test.

---

#### A3-02 | YÜKSEK | `recovery_manager.py:222, 645, 676`
**Senaryo:** Background periyodik kontrol veya başlangıç `recover_positions()` sırasında `active_trades` dict'ine korumasız yazma.

**Neden bug:** `recover_positions()` ve `reconcile_ghost_positions()` hiçbir lock edinmeden `self._active_trades[sym] = ActiveTrade(...)` satırıyla direkt dict'i mutate ediyor. Aynı anda `_on_1m_close` veya `UserDataHandler` aynı sembol için `active_trades`'ı okuyup `pop()` ya da dict-field yazabilir.

**Kanıt (kod izi):**
```python
# recovery_manager.py:222
self._active_trades[sym] = ActiveTrade(...)
# recovery_manager.py:645, 676 — aynı pattern
# Hiçbirinde asyncio.Lock yok
```

**Önerilen doğrulama:** `RecoveryManager` için per-symbol `asyncio.Lock` ekle ve tüm dict mutation'larını lock altında yap.

---

#### A3-03 | YÜKSEK | `snapshot.py:49` + `exit_lifecycle.py:758`
**Senaryo:** Trade kapandığında HTML snapshot çekmek için sync HTTP çağrısı.

**Neden bug:** `snapshot.py:_fetch_ohlc()` içinde `requests.get(...)` (sync, timeout=15sn) kullanılıyor. Bu fonksiyon `exit_lifecycle.py:_commit_confirmed_exit()` satır 758'den async context içinde çağrılıyor. `requests.get` event loop'u 15 saniyeye kadar bloklar. Bu süre boyunca market data WS mesajları, user data WS mesajları, trailing güncellemeleri TÜMÜNÜ askıya alır. Yüksek volatilite döneminde gecikmiş WS FILLED + bloklayan snapshot = stale event döngüsü riski.

**Kanıt (kod izi):**
```python
# snapshot.py:49
r = requests.get(...)  # sync, timeout=15sn
# snapshot.py:5 — "urllib tamamen kaldırıldı" yorumuna rağmen requests kullanılıyor
# exit_lifecycle.py:758 — snap = capture_snapshot(...) async içinden çağrılıyor
```

**Önerilen doğrulama:** `_fetch_ohlc` içini `aiohttp` ile yeniden yaz veya `asyncio.to_thread()` içine al.

---

#### A4-02 | YÜKSEK | `recovery_manager.py:873`
**Senaryo:** `reconcile_orphan_orders()` içinde `get_all_orders(sym)` çağrılırken ağ hatası, timeout veya Binance API hatası olursa:

```python
try:
    orders = await self._rest.get_all_orders(sym)
except Exception:
    continue  # ← SESSİCE YUT, SEMBOLÜ ATLA
```

**Neden bug:** Orphan emir temizliği o sembol için tamamen atlanıyor. Binance'te asılı kalmış STOP/TP emirleri birikmeye devam eder.

**Kanıt (kod izi):**
```python
# recovery_manager.py:871-874
except Exception:
    continue
# bot_binance.py:716-728 — get_all_orders artık RuntimeError fırlatıyor (P0-5 FIXED)
```

**Önerilen doğrulama:** `get_all_orders` mock'ta RuntimeError fırlatıldığında `reconcile_orphan_orders`'ın o sembolü atladığını gösteren test.

---

#### A4-03 | YÜKSEK | `bot_binance.py:817, 927, 1023`
**Senaryo:** Demo API'de MARKET/STOP/TP emri gönderildikten sonra `orderId` bulunamadığında, demo API gecikmesi beklenir ve `get_open_orders` ile tekrar kontrol edilir. Bu kontrol sırasında istisna olursa:

```python
try:
    orders = await self.get_open_orders(symbol)
    for o in orders if isinstance(orders, list) else []:
        ...
        return o
except Exception:
    pass  # ← SESSİCE YUT
```

**Neden bug:** Demo modunda emir gerçekten gittiği halde orderId bulunamadığında, bu `pass` ile sessizce geçiliyor. `result["_status"] = "ORDER_ACKNOWLEDGED"` döndürülüyor ama emrin gerçek durumu bilinmiyor.

**Kanıt (kod izi):**
```python
# bot_binance.py:804-818 — place_market_order demo fallback
# bot_binance.py:914-928 — place_stop_order demo fallback
# bot_binance.py:1012-1024 — place_tp_order demo fallback
```

**Önerilen doğrulama:** Demo modunda `get_open_orders` exception fırlattığında `ORDER_ACKNOWLEDGED` döndüğünü gösteren test.

---

#### A4-05 | YÜKSEK | `recovery_manager.py:512-517`
**Senaryo:** `recover_positions` içinde SL/TP yerleştirme denemesi (try bloğu) başarısız olursa:

```python
try:
    # ... SL/TP yerleştirme denemeleri ...
except Exception as e:
    log.warning(
        "[RECOVER] %s icin Binance koruma emri yerlestirme hatasi: %s",
        sym,
        e,
    )
    # ← Hata yutuldu, döngü devam ediyor ama bu pozisyon için SL/TP kurulmadı
```

**Neden bug:** Tek bir `except Exception` bloğu tüm SL/TP yerleştirme denemelerini kapsar. Hata durumunda log warning atılıyor ama pozisyon `active_trades`'e SL/TP'siz ekleniyor. Bu, o pozisyonun korumasız kalmasına yol açar.

**Kanıt (kod izi):**
```python
# recovery_manager.py:368-517 — Tek try bloğu tüm SL/TP yerleştirme denemelerini kapsıyor
# recovery_manager.py:512-517 — except Exception as e: log.warning
```

**Önerilen doğrulama:** `test_recovery_manager.py` testleri `place_stop_order` exception fırlatıldığında pozisyonun `active_trades`'e eklendiğini doğruluyor (test_force_close_exception_handled).

---

#### A4-08 | YÜKSEK | `recovery_manager.py:519-669`
**Senaryo:** SL kurulamadığında acil market kapanış (`place_market_order_priority`) başarısız olur:

```python
if not close_result or not close_result.get("orderId"):
    # BAŞARISIZ — ne ikinci deneme, ne escalation, ne alarm
    log.critical("[RECOVER] %s ACIL KAPANIS BASARISIZ -- MANUEL MUDAHALE GEREKLI")
    self._pl(sym, "recover_emergency_close_failed", "...")
    # Pozisyonu KORUMASIZ active_trades'e ekle
    self._active_trades[sym] = ActiveTrade(... sl_order_id="", tp_order_id="")
    continue
```

**Neden bug:** Emergency close başarısız olduğunda:
1. İkinci deneme yok (backoff/retry mekanizması yok)
2. Escalation yok (sadece log + konsol mesajı)
3. Pozisyon `active_trades`'e korumasız (SL/TP'siz) ekleniyor
4. Sonraki `periodic_check_loop` (60sn) tekrar deneyecek, ama bu "sessizce kalma" değil "gecikmeli deneme"dir

**Kanıt (kod izi + test kanıtı):**
```python
# recovery_manager.py:626-668 — ACIL KAPANIS BASARISIZ sonrası ActiveTrade oluşturma
# test_recovery_manager.py:109-149 (test_position_stays_when_both_close_methods_fail) — zaten var ve geçiyor
```

**Önerilen doğrulama:** Testi çalıştır: `python -m pytest tests/test_recovery_manager.py::test_position_stays_when_both_close_methods_fail -v`.

---

#### A6-01 | YÜKSEK | `backtest-sniper/src/analyzer_v5.py:418-423`
**Senaryo:** Backtest motorunda sweep'i tüketmiyor (`bar_index=None` → dedup devre dışı, `sweep_confirmed` hiç False'lanmıyor).

**Neden bug:** Canlı 08-06 SEIUSDT fix'i (`signal_engine.py:78-93`) backtest'e taşınmamış → tek sweep çoklu giriş üretebilir. Backtest sonuçları canlıdan sapma gösterebilir.

**Kanıt (kod izi):**
```python
# analyzer_v5.py:418-423 — sweep tüketim yok
# signal_engine.py:78-93 — canlı fix mevcut
```

**Önerilen doğrulama:** `analyzer_v5.py`'e `bar_index=current.index` ile sweep tüketim eklemek.

---

#### A6-02 | YÜKSEK | `bot.py:671` vs `backtest-sniper/src/analyzer_v5.py` + `simulate.py`
**Senaryo:** Entry çapası: canlı `bot.py:671` `current.close` vs her iki backtest `next_bar.open` — kasıtlı look-ahead karşıtı seçim, uzlaştırılmamış.

**Neden bug:** Canlı ve backtest farklı bar seviyelerinde entry fiyatı kullanıyor. Backtest'te `next_bar.open` (look-ahead bias) canlıda `current.close` (gerçek zamanlı). Bu, backtest sonuçlarının canlı performansı yansıtmamasına yol açar.

**Kanıt (kod izi):**
```python
# bot.py:671 — current.close
# analyzer_v5.py / simulate.py — next_bar.open
```

**Önerilen doğrulama:** Entry fiyat seçimini tek standarda indirmek (tüm motorlarda `current.close` veya tümünde `next_bar.open`).

---

### ORTA

#### A1-03 | ORTA | `src/trading/trailing_manager.py:326` vs `src/trading/order_manager.py:112` vs `src/state_writer.py:72`
**Senaryo:** Aktif trade'de iki farklı sayaç alanı var: `trail_count` ve `trailing_count`.

**Neden bug:** `_fvg_multihop` `trail_count`'i artırırken, `order_manager.update_trail_orders` `trailing_count`'i set eder. `state_writer` sadece `trailing_count` okur. Bu durumda trailing gerçekleşse bile state_writer 0 görür.

**Kanıt (kod izi):**
```python
# trailing_manager.py:326
trade["trail_count"] = int(trade.get("trail_count", 0)) + 1
# order_manager.py:112
trade["trailing_count"] = new_trail_count
# state_writer.py:72
"trailing_count": trade.get("trailing_count", 0)
```

**Önerilen doğrulama:** Tek standart alan (`trailing_count`) seçmek ve tüm güncellemeleri o alana yönlendirmek.

---

#### A2-03 | ORTA | `trailing_manager.py:91,491-492` + `bot.py:261,1007-1012`
**Senaryo:** `get_tick_size` exception verirse `bot.py:1007-1012` `tick_size = 0.10` atar (uyarı loglu ama devam eder); `trailing_manager._tick_size` trade'de anahtar yoksa sessizce `default_tick_size=0.10` kullanır.

**Neden bug:** APTUSDT (0.59, gerçek tick 0.0001) için 0.10 grid: long SL 0.5895 → floor(5.895)×0.1 = **0.50** (%15 risk). Sentinel ile gerçek tick arasında plauzibilite kontrolü yok.

**Kanıt (kod izi + test kanıtı):**
```python
# bot.py:1007-1012
tick_size = 0.10  # fallback
# test_models.py:345,358 — 0.10 default'u bug olarak değil davranış olarak assert ediyor
# test_recovery_manager.py:199-201 — bu sınıfın ALGO/RENDER canlı bug'ına yol açtığını belgeliyor
```

**Önerilen doğrulama:** `_normalize_price`'a sentinel plauzibilite guard'ı (tick_size vs `_last_price` büyüklük oranı) ve testi.

---

#### A2-04 | ORTA | `entry_manager.py:442-478`
**Senaryo:** `_bump_to_min_notional` (442, est_price ile) qty'yi artırır → `get_max_qty` clamp'ı (461-469) küçültür → 470'te yalnızca `validate_min_amount` tekrar denenir, `validate_min_notional` değil → son qty minNotional altına düşebilir → exchange reddi → `{}` → entry kaybolur.

**Kanıt (kod izi + test kanıtı):**
```python
# entry_manager.py:442-478
# _bump_to_min_notional → get_max_qty clamp → validate_min_amount (validate_min_notional YOK)
# tests/test_entry_manager.py:1284-1339 — BUG-10 bump testleri geçiyor ama clamp-sonrası re-check senaryosu yok
```

**Önerilen doğrulama:** Clamp sonrası minNotional re-check testi + bump sırasının maxQty öncesi sabitlenmesi.

---

#### A3-04 | ORTA | `state_writer.py:98` + `bot.py:533, 642, 1049-1057`
**Senaryo:** Her 1m ve 15m bar kapanışında `write_state()` çağrılır.

**Neden bug:** `write_state()` sync fonksiyon — `json.dump()` + `open(..., "w")` + dosya yazma. `_on_1m_close` (async) içinden çağrılıyor. 1m bar her dakika kapandığında event loop 1-5ms civarında bloke olur. Çoklu sembolde (10+) bu toplanır.

**Kanıt (kod izi):**
```python
# state_writer.py:98
with open(...) as f: json.dump(...)
# bot.py:533, 642 — write_state(...) çağrıları
# bot.py:110 — f.flush() blocking
```

**Önerilen doğrulama:** `write_state` ve `_save_fvg_state` için `asyncio.to_thread()` veya async file I/O kullan.

---

#### A3-05 | ORTA | `exit_lifecycle.py:160` + `exit_lifecycle.py:647`
**Senaryo:** Exit lifecycle içinde lock anahtarları tutarsız.

**Neden bug:** `execute()` per-trade lock kullanıyor: `trade_key = f"{sym}_{_trade_id_key}"` (satır 159-160). Ama `_commit_confirmed_exit()` per-symbol lock kullanıyor: `lock = self._exit_locks.setdefault(sym, asyncio.Lock())` (satır 647). Sonuç: (1) Aynı sembolde iki farklı trade eşzamanlı exit edilirse, `_commit_confirmed_exit` per-symbol lock ile seri hale getirir — gereksiz darboğaz. (2) `execute()` satır 388'de `trade_key` lock'ını pop ediyor ama `_commit_confirmed_exit` `sym` lock'ını pop etmiyor — lock leak.

**Kanıt (kod izi):**
```python
# exit_lifecycle.py:160
trade_key = f"{sym}_{_trade_id_key}"
# exit_lifecycle.py:388
self._exit_locks.pop(trade_key, None)
# exit_lifecycle.py:647
lock = self._exit_locks.setdefault(sym, asyncio.Lock())
# sym lock'ı hiçbir yerde temizlenmiyor
```

**Önerilen doğrulama:** `_commit_confirmed_exit` de `trade_key` lock'ını kullanmalı, veya `execute` içinde commit edildikten sonra `sym` lock'ını da temizlemeli.

---

#### A3-07 | ORTA | `order_manager.py:311-378` + `bot.py:594-612`
**Senaryo:** Trailing güncellemesi sırasında trade dict'i korumasız mutate edilir.

**Neden bug:** `_on_1m_close` içinde `orchestrate_trail()` → `update_trail_orders()` çağrılır. `update_trail_orders` trade dict'in `sl`, `tp`, `trailing_count`, `sl_order_id`, `tp_order_id` gibi alanlarını await noktaları ARASINDA doğrudan mutate ediyor. Aynı `_on_1m_close` içinde daha sonra `check_exit` ve `_exit_trade` çağrılıyor. Bu sırada WS user data task'ı da aynı trade objesine erişip `pending_exit_*` yazabilir.

**Kanıt (kod izi):**
```python
# bot.py:594-612 — orchestrate_trail → update_trail_orders
# order_manager.py:311-378 — update_trail_orders trade dict'ini mutate ediyor
```

**Önerilen doğrulama:** `_on_1m_close` trail→exit pipeline'ını tek lock altında birleştir.

---

#### A4-06 | ORTA | `bot.py:148, 176`
**Senaryo:** `_setup_logging` içinde eski log arşivleme ve stdout reconfigure hataları:

```python
try:
    shutil.copy2(_log_file, archive_name)
    os.remove(_log_file)
except Exception:
    pass  # ← LOG ARŞİVLEME HATASI SESSİCE YUT
```

**Neden bug:** Log arşivleme hatası (disk dolu, dosya kilitli) sessizce yutuluyor. Eski log dosyası silinemezse, yeni log eski logun üzerine yazılabilir veya log rotate çalışmayabilir.

**Kanıt (kod izi):**
```python
# bot.py:145-149 — except Exception: pass
# bot.py:176-179 — except Exception: _log.debug
```

**Önerilen doğrulama:** Log arşivleme hatasını fail-fast yap veya disk dolu kontrolü ekle.

---

#### A6-03 | ORTA | `entry_manager.py:223-235, 287-343`
**Senaryo:** `MIN_SL_DISTANCE_TICKS/PCT` + pre-entry SL-eps guard yalnızca canlıda — bilinen açık.

**Neden bug:** Backtest motorları (`analyzer_v5.py`, `simulate.py`) bu guard'ı içermiyor. Backtest'te SL mesafe kontrolü atlanıyor → backtest sonuçları canlıdan sapma gösterebilir.

**Kanıt (kod izi):**
```python
# entry_manager.py:223-235, 287-343 — canlı guard
# backtest-sniper/src/analyzer_v5.py — guard YOK
```

**Önerilen doğrulama:** Backtest'lere de `MIN_SL_DISTANCE_TICKS/PCT` guard'ını eklemek.

---

#### A6-04 | ORTA | `trailing_manager.py:629-645`
**Senaryo:** Tick normalizasyonu yalnızca canlı trailing'de; backtest'ler raw float.

**Neden bug:** Canlıda `_normalize_price` ile SL/TP tick_size grid'ine yuvarlanırken backtest'te raw float kullanılıyor. Bu, trailing kararlarının backtest'te farklı sonuç vermesine yol açar.

**Kanıt (kod izi):**
```python
# trailing_manager.py:629-645 — normalize var
# backtest-sniper/src/analyzer_v5.py — normalize YOK, raw float
```

**Önerilen doğrulama:** Backtest'e de `_normalize_price` eklemek veya parametrik mod kullanmak.

---

### DÜŞÜK

#### A1-04 | DÜŞÜK | `src/trading/recovery_manager.py:222, 645, 676` vs `src/bot.py:1029`
**Senaryo:** Canlı trade kurulumunda (`bot.py:1029`) `trail_level_extractor=self._build_fvg_scan_trail_extractor(sym)` geçiliyor. Recovery'deki 3 ActiveTrade kurulum noktasında bu alan `None` kalıyor.

**Neden bug:** İlk trail çağrısında `bot.py:586-587` lazy rebuild yapıyor → çalışır ama ilk trail evaluation'da ekstra overhead. Recovery sonrası ilk trail atlanabilir veya gecikebilir.

**Kanıt (kod izi):**
```python
# bot.py:1029 — trail_level_extractor geçiliyor
# recovery_manager.py:222-247 — trail_level_extractor parametresi YOK
# bot.py:586-587 — lazy rebuild var
```

**Önerilen doğrulama:** Recovery'de de trail_level_extractor'ı kurmak veya lazy rebuild'i zorunlu hale getirmek.

---

#### A3-06 | DÜŞÜK | `entry_manager.py:778-782` + `recovery_manager.py:162`
**Senaryo:** Bazı REST çağrılarında retry/backoff eksik.

**Neden bug:** `_try_entry` içinde `get_balance()` (satır 778) bare `except Exception: pass` ile sessizce düşürülüyor. `recover_positions` içinde `get_all_orders()` (satır 162) da bare `except Exception: continue` ile atlanıyor. Bu çağrılar transient network error sonrası kritik veri kaybına yol açabilir.

**Kanıt (kod izi):**
```python
# entry_manager.py:778-782 — except Exception: pass
# recovery_manager.py:162 — except Exception: continue
# bot_binance.py:165-180 — RetryConfig mevcut ama bu direkt çağrılar onu kullanmıyor
```

**Önerilen doğrulama:** Bu direkt çağrıları `BinanceRESTClient` retry mekanizmasından geçir veya en azından `RetryConfig` ile sarmala.

---

#### A4-07 | DÜŞÜK | `state_manager.py:67-68`
**Senaryo:** `_load()` state dosyasını okurken herhangi bir hata olursa:

```python
def _load() -> dict:
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}  # ← TÜM STATE KAYBOLDU
```

**Neden bug:** JSON parse hatası, disk hatası vb. durumlarda tüm state kaybolur. `mark_trade_opened`, `can_open_trade` gibi fonksiyonlar `_load()`'u çağırır ve boş dict alır — bugünkü işlem kotası sıfırlanır, sweep kayıtları kaybolur.

**Kanıt (kod izi):**
```python
# state_manager.py:64-68 — except Exception: return {}
# state_manager.py:88-96 — can_open_trade _load()'u çağırır
```

**Önerilen doğrulama:** State yükleme hatasında fail-fast veya yedek state dosyasından kurtarma eklemek.

---

#### A6-05 | DÜŞÜK | `simulate.py:385`
**Senaryo:** `trailing_count` sayımı — `simulate.py` her zaman 1 sayıyor, FVG sayısına bakmıyor.

**Neden bug:** Canlı/analyzer per-hop count kullanırken simulate sabit 1. Bu, backtest'te trail sayısının yanlış hesaplanmasına yol açar.

**Kanıt (kod izi):**
```python
# simulate.py:385 — bar başına +1
# trailing_manager.py / analyzer_v5.py — per-hop count
```

**Önerilen doğrulama:** `simulate.py`'de trailing_count'u per-hop olarak sayacak şekilde düzeltmek.

---

#### A6-06 | DÜŞÜK | `trailing_manager.py` + `analyzer_v5.py` + `simulate.py`
**Senaryo:** `is_closed` guard yalnızca canlıda — kapalı-bar verisinde davranışsal etkisi yok.

**Neden bug:** Açık bar'da trigger olabilir (backtest'te). Canlıda guard var, backtest'te yok — kucak farkı.

**Kanıt (kod izi):**
```python
# trailing_manager.py — is_closed guard var
# analyzer_v5.py — guard YOK
# simulate.py — guard YOK
```

**Önerilen doğrulama:** Backtest'lere `is_closed` guard'ını eklemek.

---

#### A6-07 | DÜŞÜK | `backtest-sniper/src/analyzer_v5.py:442-452`
**Senaryo:** `ENTRY_VARIANT` E1/E2 dalı yalnızca backtest'te; canlıda karşılığı yok.

**Neden bug:** Config "A" iken ölü, latent drift riski. Backtest sonuçları canlı ortamda geçersiz olabilir.

**Kanıt (kod izi):**
```python
# analyzer_v5.py:442-452 — ENTRY_VARIANT E1/E2 dalı
# canlı config.py — TRAIL_MODE=retrace, ENTRY_VARIANT yok
```

**Önerilen doğrulama:** Backtest'teki ENTRY_VARIANT dalını kaldır veya canlıya taşı.

---

## HİPOTEZ LİSTESİ (kanıtsız, deneysel)

| ID | Açıklama | Doğrulama yöntemi |
|----|----------|-------------------|
| H-A1-1 | `recovery_manager.py`'da `existing` trade'lere `is_recovered=True` atlanıyor — state'ten yüklenen trade'de korunup korunmadığı bilinmiyor | Integration test gerekli |
| H-A1-2 | `trades_history.jsonl`'deki eski kayıtlar (lines 1-42) `runtime` alanını içermiyor, yeni kayıtlar (lines 43-44) içeriyor — kod değişikliği sonrası şema dönüşümü mü? | Git history + exit_lifecycle trace |
| H-A2-1 | `ActiveTrade.tick_size=None` state'ten yüklenen trade'de `Decimal(str(None))` crash riski | Eski state dosyasıyla yükleme testi |
| H-A2-2 | Bump'ın buying-power tavanı ile `get_max_qty`'nin notional tavanı iki ayrı otorite — küçük sembolde bump 500-USD tavanını aşıp clamp'lanıp minNotional altına düşebilir | Canlı örnek aranmalı |
| H-A2-3 | Trailing hop kararı normalized birimde, SL/TP yerleşimi tick_size grid'iyle ayrı yoldan — sentinel devredeyse karar/yerleşim grid tutarsızlığı | Sentinel devredeyken trailing karşılaştırma |
| H-A3-1 | Loglarda çift `trade_closed` event'leri A3-01 race'i + idempotency guard'un `trade["result"]` overwrite'a karşı korumasızlığından kaynaklanıyor olabilir | Race simulator testi |
| H-A3-2 | `write_state` sync file I/O'su 1m bar close'larını yavaşlatıyor, bu da WS mesajlarının `_dispatch` içinde kuyruğa alınmasına yol açıyor | Event loop latency ölçümü |
| H-A3-3 | `recovery_manager.recover_positions()` `active_trades`'e yazarken, eğer `_on_1m_close` aynı trade objesini referans alıp trailing/exit yapmaya çalışırsa, trade objesi değişebilir | Concurrent stress test |
| H-A4-1 | `bot_binance.py:817, 927, 1023`'teki `except: pass` bloklarının production'da gerçek emir kaybına yol açıp açmadığı | Loglarda bu path'in tetiklendiğine dair kanıt yok |
| H-A4-2 | `websocket.py:158, 329`'teki callback hatalarının tek bar/event kaybına yol açıp açmadığı | Reconnect mekanizması testi |
| H-A4-3 | `bot.py:781`'deki entry öncesi bakiye sorgusu `except: pass` ile geçildiğinde, fallback balance kullanılarak yanlış pozisyon büyüklüğü hesaplanıp hesaplanmadığı | Balance fallback senaryosu testi |

---

## KAPANIS ve SONRAKI ADIMLAR

### Şiddet Dağılımı
- **KRİTİK (4):** A1-01 (runtime.protection boş), A3-01 (WS race exit), A4-01 (ghost temizliği sessizce atlanıyor), A4-04 (place_market_order boş {} döner)
- **YÜKSEK (10):** A1-02 (runtime string kaydı), A2-01/A2-02 (dust/emergency-close MIN_NOTIONAL eksik), A3-02/A3-03 (recovery korumasız yazma + snapshot sync blok), A4-02/A4-03/A4-05/A4-08 (sessiz yutma zinciri + emergency close eksik), A6-01/A6-02 (backtest sweep + entry çapası)
- **ORTA (9):** A1-03 (trail_count asimetrisi), A2-03 (tick_size sentinel), A2-04 (bump-clamp re-check), A3-04/A3-05/A3-07 (sync I/O + lock tutarsız + trailing mutate), A4-06 (log arşivleme), A6-03/A6-04 (backtest guard eksik)
- **DÜŞÜK (7):** A1-04 (trail_level_extractor), A3-06 (retry eksik), A4-07 (state kaybı), A6-05/A6-06/A6-07 (backtest trail count + is_closed + ENTRY_VARIANT)

### Başmühendis Değerlendirmesi İçin Notlar
1. **A4-04 (P2-8)** ve **A2-01/A2-02** aynı kök neden ailesi (dust/emergency-close MIN_NOTIONAL eksik). Tek bir fix ile ikisi de kapanabilir.
2. **A1-01** ve **A1-02** state/JSON writer ailesi — ikisi de `runtime` alanı kaybı ile ilgili.
3. **A3-01** (WS race) ve **A3-03** (sync HTTP blok) en yüksek canlı etki potansiyeline sahip — her ikisi de event loop'u bloke ediyor.
4. **A6-01/A6-02** backtest güvenilirliğini doğrudan etkiliyor — backtest kararları canlıyı yansıtmayacak.

### A5 Eksik
A5 (snapshot/telemetri) raporu rate limit nedeniyle tamamlanamadı. A1/A2/A3/A4/A6 raporlarıyla örtüşen alanlar (BULGU-05, state_writer) zaten A1 ve A4'te ele alındı. A5 tamamlanınca bu rapora eklenecek.

---

**Rapor hazırlama:** Ajanlar tarafından oluşturulan ham çıktılar konsolide edildi, tekrar eden bulgular birleştirildi, şiddet sırasına göre düzenlendi.
**Başmühendis onayından sonra:** Kanıtlı bulgular P1/P2/P3 öncelik sırasına göre fix planına taşınacak.
