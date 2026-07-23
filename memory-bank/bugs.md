# Bug Registry — sniper/src/

> **Son güncelleme:** 2026-07-22 — kod taranarak doğrulandı.
> Dosya referansları `sniper/src/` olarak güncellendi.

## 🔴 P0 — Finance Risk

### P0-1: STRKUSDT çift-exit/çift-PnL (event log'dan tespit)
**Kaynak:** `events_2026-07-20.jsonl` replay
```
14:59:00 exit STRKUSDT short entry=0.029 exit=0.0287 qty=17593 pnl=4.77 result=SL
18:47:15 exit STRKUSDT short entry=0.029 exit=0.0287 qty=17593 pnl=4.77 result=SL  ← AYNI trade!
```
- **Senaryo:** WS "SL FILLED" event'i ile `_exit_already_closed` fast-path'i çalışır, REST doğrulaması OLMADAN pozisyonu kapatır. Ama pozisyon borsada açık kalır.
- **60sn'lik `_check_position`** trade'i `active_trades`'te bulamayınca `_recover_unknown_position` ile geri ekler.
- 3.5 saat sonra SL gerçekten tetiklenir, PnL **tekrar** +4.77 yazılır.
- **Risk:** Balance çift PnL ile şişer → position sizing yanlış. VEYA pozisyon 3.5 saat izlemesiz kalır.
- **⚠️ DURUM: KISMEN DÜZELTİLDİ** — `ExitLifecycleService.execute()` (exit_lifecycle.py:122) WS_FALLBACK için REST `position_still_open()` kontrolü ekledi. Ama legacy `_exit_trade_legacy` (bot.py:782) hala REST doğrulamasız. `EXIT_LIFECYCLE_SERVICE_ENABLED=True` (varsayılan) olduğu için yeni yol aktif. `reconcile_orphan_orders()` artık periyodik (her 5 × 1m bar'da), ama `reconcile_ghost_positions()` hala sadece restart'ta.

### P0-2: `_exit_already_closed` fast-path'i REST ile pozisyon doğrulamıyor
**Dosya:** `sniper/src/trading/exit_lifecycle.py` (yeni) + `sniper/src/bot.py` (legacy)
- `trade.get("result") in ("SL","TP","WS_FALLBACK")` → direkt çık, `_submit_and_verify_market_close` çağrılmaz.
- **⚠️ DURUM: YENİ YOLDA DÜZELTİLDİ** — `exit_lifecycle.py:122`'de WS_FALLBACK için `position_still_open()` REST sorgusu var. Legacy path (bot.py:881) hala REST doğrulamasız ama `EXIT_LIFECYCLE_SERVICE_ENABLED=True` ile devre dışı.

### P0-3: `_check_position()` transition-guard'sız, lock'suz
**Dosya:** `sniper/src/bot.py` — 60sn'lik `_periodic_position_check`
- `should_skip_reconcile()` kontrolü TAMAMEN YOK.
- `TRAIL_REPLACING`, `EXIT_VERIFYING`, `REPAIR_REQUIRED` state'lerinde tetiklenebilir.
- Üç yerden eşzamanlı `repair_protection()` tetiklenebilir: (a) bu 60sn döngü, (b) WS handler, (c) ExitLifecycleService — **aralarında hiçbir lock/mutex yok**.
- Çift SL/TP emri riski.
- **⚠️ DURUM: KALDIRILDI** — `_check_position()` ve `_periodic_position_check` fonksiyonları artık yok. Orphan sweep `recovery_manager.reconcile_orphan_orders()` ile yapılıyor ve `should_skip_reconcile()` guard'ı var (protection_lifecycle.py:102).
- **🔒 P0-3 LOCK EKLENDİ (2026-07-22):** `order_manager.py:repair_protection()`'a per-symbol `asyncio.Lock` eklendi. Aynı sembol için eşzamanlı çağrılar (`lock.locked()` ile tespit) sessizce atlanır. Wrapper + `_repair_protection_locked()` rename pattern'i ile mevcut mantık değişmedi. (Test: `tests/test_order_manager.py::TestRepairProtectionConcurrency`)

### P0-4: OPUSDT — 2. pozisyon exit event'i hiç yazılmamış (event log kanıtlı)
**Kaynak:** `events_2026-07-20.jsonl` — 2. baş mühendis analizi
```
03:45:04 entry OPUSDT short qty=7261.9
03:45:04 force_close success=true
-- 2 saat 46 dakika BOYUNCA hiçbir "exit" event'i gelmiyor --
06:31:26 ghost_missing_sltp OPUSDT has_sl=true has_tp=false
06:31:33 orphan_cleaned OPUSDT STOP_MARKET
```
- `_submit_and_verify_market_close()`'daki 5×200ms doğrulama başarısız → trade `REPAIR_REQUIRED`'da kilitli.
- REPAIR_REQUIRED'de **otomatik retry yok** (P0-2 ile aynı kök neden).
- SL emri Binance'te 2 saat 46 dakika yalnız/yetim kaldı.
- **Ghost-position temizliği sadece bot restart'ında çalışır** (`run()` içinde bir kez — bot.py:1443), periyodik eşdeğeri yok.
- **Portföy flat'ken orphan-sweep sayacı durur** — `_on_1m_close` tetiklenmez, sayaç ilerlemez.
- O gün en az 2 bot restartı olmuş (ghost_missing_sltp çifti ×2).
- **⚠️ DURUM: KISMEN DÜZELTİLDİ** — `reconcile_ghost_positions()` (state-file temizliği) gerçekten hâlâ sadece `run()` içinde bir kez çalışıyor. Ama artık `RecoveryManager.periodic_check_loop()` her 60sn'de `recover_positions(quiet=True)` + `reconcile_orphan_orders()` çalıştırıyor; `recover_positions()` Binance'teki pozisyonları doğrudan sorgulayıp `active_trades`'te olmayan/korumasız pozisyonları tekrar SL/TP ile donatıyor — "SL 2 saat 46 dk yalnız kalır" senaryosu artık ~60sn içinde yakalanır. Ayrıca `bot.py:run()`'a restart'ta `REPAIR_REQUIRED`/`EXIT_REQUESTED` trade'leri SL/TP sağlıklıysa `ACTIVE`'e döndüren temizlik eklenmiş. REPAIR_REQUIRED'e özel bir retry döngüsü hâlâ yok ama pratik risk periyodik `recover_positions` ile büyük ölçüde azalmış.

---

## 🟠 P1 — High Risk

### P1-1: `repair_protection()` fiyatı yeniden hesaplamıyor
**Dosya:** `sniper/src/trading/order_manager.py:503`
- `trade["sl"]` / `trade["tp"]`'deki eski değerleri kullanır.
- Piyasa o değerleri geçmişse emir reddedilir (immediately trigger), sessizce yutulur.
- `recovery_manager.recover_positions()`'daki "mevcut fiyata göre yeniden hesapla" fallback'i burada yok.
- **⚠️ DURUM: DÜZELTİLDİ** — `repair_protection()` artık SL/TP reddedilirse `estimate_market_price()` ile mevcut fiyata göre yeniden hesaplama yapıyor (aynı `recover_positions()`'daki fallback mantığı).

### P1-2: `update_trail_orders()` reject sonrası retry/backoff yok
**Dosya:** `sniper/src/trading/order_manager.py:64`
- SEIUSDT event log'u ile teyit: aynı `old_id` ile 60sn arayla 2 reject, fiyat yeniden hesaplanmıyor.
- SL trailing durur, pozisyon korumasız kalır.
- **⚠️ DURUM: HÂLÂ GEÇERLİ** — `update_trail_orders()` reject olduğunda eski SL'yi koruyor (order_manager.py:135) ama retry veya backoff mekanizması yok.

### P1-3: OPUSDT — entry'den ~280ms sonra sistematik force_close
**Kaynak:** `events_2026-07-20.jsonl` (1. analiz)
- 2 ayrı OPUSDT entry'si de ~270-280ms sonra force_close ile kapanıyor.
- Olası neden: entry anındaki SL/TP mesafesi borsadaki gerçek fiyatla uyuşmuyor, emir "immediately trigger" reddi.
- entry_manager.py'de precision/fiyat hesaplama hatası olabilir.
- **⚠️ DURUM: EVENT LOG'A BAĞLI** — Doğrulama için event log gerekli. Kodda belirgin bir hata görünmüyor ama `sniper/src/trading/entry_manager.py` incelenmeli.
- **Ek not (2026-07-23):** `test_entry_manager.py`'deki 8 test (TestCalculateQty + TestCalculateSlTp) `calculate_sl_tp`/`calculate_qty` beklentileriyle uyuşmuyor. `72d06d9`'a kadar olan eski testler de aynı şekilde kırık — pre-existing. Testler eski london_high/low TP fallback beklentileriyle yazılmış, kod sonra 1:2 R:R sabit TP'ye geçmiş. Backlog: test expectations güncellenmeli.

### P1-4: Ghost/temizlik sadece restart'ta çalışır, periyodik değil
**Kaynak:** 2. baş mühendis analizi — OPUSDT event log ile kanıtlı
- `reconcile_ghost_positions()` sadece `run()` içinde bot başlangıcında **BİR KEZ** çağrılır (bot.py:1443).
- Periyodik `reconcile_orphan_orders()` portföy flat'ken **çalışmaz** (sayacı artıracak bar kapanışı yok — bot.py:455-458).
- Arızalı exit'in yetim SL/TP'si sadece sonraki restart'ta temizlenir — teorik olarak sınırsız süre asılı kalabilir.
- **⚠️ DURUM: KISMEN DÜZELTİLDİ** — `reconcile_orphan_orders()` artık periyodik (her 5 × 1m bar), ama `reconcile_ghost_positions()` hala sadece restart'ta.

### P1-5: qty=0.1 dust exit — muhasebe kirliliği
**Kaynak:** `events_2026-07-20.jsonl` — OPUSDT force_close sonrası
```
exit OPUSDT WS_FALLBACK exit=0.0949 qty=0.1 pnl=-0.0
```
- stepSize/precision nedeniyle ana pozisyon tam kapanmaz, 0.1 birim artık kalır.
- Ayrı bir reduceOnly WS fill olarak gelir, ikinci bir "exit" kaydı oluşturur.
- `mark_sweep_consumed()`'ı o anki (farklı) RSM durumuyla tetikler — sweep seviyesi yanlış işaretlenebilir.
- **⚠️ DURUM: KÖK NEDEN DÜZELTİLDİ** — OPUSDT log örneğindeki 0.1 kalıntının sebebi `_round_step()`'teki floating-point floor-division hatasıydı (`7275.8 // 0.1` → 1 step eksik hesaplıyordu). `bot_binance.py`'de artık `int(value/step)` kullanılıyor. Genel "dust guard" yok ama bu spesifik tekrar üretilebilir senaryo artık oluşmaz.

### P1-6: Entry sizing max_qty kontrolü yok — trailing'de -4005 döngüsüne yol açıyor
**Dosya:** `sniper/src/trading/entry_manager.py:calculate_qty()` + `execute_live_entry()`
- `calculate_qty()` sadece `buying_power = balance * MAX_MARGIN_PCT * leverage / entry_price` ile tavan kontrolü yapıyor. Binance LOT_SIZE.maxQty kontrolü YOK.
- Risk formulü (balance * risk_pct / risk_dist) çıkış qty'si maxQty sınırını aşabilir — özellikle yüksek kaldıraç + düşük fiyat sembolleri (STRKUSDT benzeri).
- Sonuç: (1) market entry hatta geçer (Binance market order'ı kısmen accept eder), (2) trade["qty"] maxQty'den büyük kaydedilir, (3) SL/TP emirleri `place_stop_order()`/`place_tp_order()` ile atılırken -4005 alır, (4) `update_trail_orders()` -4005 fallback zincirine girer (closePosition → split_qty), (5) bir sonraki trailing'de aynı -4005 tekrarlanır — sonsuz WARNING spam.
- **DURUM: DÜZELTİLDİ** — `execute_live_entry()`'e LOT_SIZE.maxQty clamp eklendi (calculate_qty() değil, çünkü sync/pre-network). `get_max_qty()` zaten mevcuttu, sadece entry path'ine bağlanmamıştı.
- **İlişki notu:** P2-5 (update_trail_orders -4005 fallback) artık bu kök neden için gereksiz olmalı (entry qty zaten max_qty'yi asamaz) ama başka -4005 senaryoları için (borsa filtre güncellemesi, restart-recovery path'i vb.) defense-in-depth olarak kalmalı — kaldırılmasın.

### P1-7: Harici kapanışlar — botun bilmediği pozisyon kapatmaları (2026-07-22 events_2026-07-22.jsonl)
**Dosya:** Event log analizi — botun başlatmadığı market close emirleri
- **Olay:** 2026-07-22'de 26 WS_FALLBACK çıkışı tespit edildi. Event JSONL'den tek tek doğrulandı.
- **Doğrulanmış vaka listesi (26/26):**

  | # | Saat  | Symbol     | Trail | PnL   | Kova            | Kanıt                      |
  |---|-------|------------|-------|-------|-----------------|----------------------------|
  | 1 | 02:43 | AVAXUSDT   | 0     | -0.83 | Log dışı        | Log yok, force_close yok   |
  | 2 | 02:56 | SUIUSDT    | 0     | -1.85 | Log dışı        | Log yok, force_close yok   |
  | 3 | 05:31 | PYTHUSDT   | 0     | -0.97 | Bot trailing    | force_close var (JSONL)    |
  | 4 | 05:46 | PYTHUSDT   | 0     | -0.47 | Bot trailing    | force_close var (JSONL)    |
  | 5 | 07:51 | LDOUSDT    | 0     | -0.62 | Log dışı        | Log yok, force_close yok   |
  | 6 | 08:46 | AAVEUSDT   | 0     | -0.64 | Bot trailing    | force_close var (JSONL)    |
  | 7 | 10:16 | ONDOUSDT   | 1     | -0.37 | Muhtemel harici | FC yok, UM yok, log var   |
  | 8 | 10:28 | PYTHUSDT   | 0     | +1.76 | Muhtemel harici | FC yok, UM yok, log var   |
  | 9 | 10:38 | LDOUSDT    | 1     | -0.57 | Muhtemel harici | FC yok, UM yok, log var   |
  |10 | 10:46 | GMXUSDT    | 0     | -0.12 | Bot trailing    | force_close + FVG kirildi  |
  |11 | 10:46 | PYTHUSDT   | 0     | -0.37 | Bot trailing    | force_close + FVG kirildi  |
  |12 | 11:23 | ENAUSDT    | 0     | +0.41 | Muhtemel harici | FC yok, UM yok, log var   |
  |13 | 11:30 | RENDERUSDT | 0     | -0.32 | Bot trailing    | force_close + FVG kirildi  |
  |14 | 12:01 | PYTHUSDT   | 0     | -0.25 | Bot trailing    | force_close + FVG kirildi  |
  |15 | 12:30 | ADAUSDT    | 0     | -0.40 | Muhtemel harici | FC yok, UM yok, log var   |
  |16 | 12:46 | ADAUSDT    | 0     | -0.40 | Bot trailing    | force_close + FVG kirildi  |
  |17 | 13:01 | ADAUSDT    | 0     | -0.26 | Bot trailing    | force_close + FVG kirildi  |
  |18 | 13:19 | ONDOUSDT   | 0     | +5.34 | Kesin harici    | WS_UNMATCHED_REDUCE_ONLY   |
  |19 | 13:30 | ADAUSDT    | 0     | -0.53 | Kesin harici    | WS_UNMATCHED_REDUCE_ONLY   |
  |20 | 14:54 | TIAUSDT    | 0     | -1.98 | Kesin harici    | WS_UNMATCHED_REDUCE_ONLY   |
  |21 | 18:21 | ONDOUSDT   | 0     | +1.15 | Kesin harici    | WS_UNMATCHED_REDUCE_ONLY   |
  |22 | 20:21 | ONDOUSDT   | 3     | -1.84 | Kesin harici    | WS_UNMATCHED_REDUCE_ONLY   |
  |23 | 21:30 | ADAUSDT    | 0     | -1.07 | Kesin harici    | WS_UNMATCHED_REDUCE_ONLY   |
  |24 | 21:31 | SOLUSDT    | 0     | -1.90 | Kesin harici    | WS_UNMATCHED_REDUCE_ONLY   |
  |25 | 21:32 | DOGEUSDT   | 0     | -1.78 | Kesin harici    | WS_UNMATCHED_REDUCE_ONLY   |
  |26 | 23:36 | ONDOUSDT   | 3     | -1.16 | Kesin harici    | WS_UNMATCHED_REDUCE_ONLY   |

- **Kova dağılımı (26 = 9 + 9 + 5 + 3):**
  - **9/26 bot-initiated trailing** — `force_close` event JSONL'de mevcut. #3,#4,#6 paper_trade.log kapsamı dışında ama event log doğruluyor. #10,#11,#13,#14,#16,#17 paper_trade.log'da `[TRAIL] FVG kirildi -> aninda market close` ile teyitli.
  - **9/26 kesin harici** — `ws_unmatched_reduce_only` event JSONL'de doğrulanmış. #22 ve #26'da trail=3 var (bot aktif trailing yapıyordu ama SL fill'i algo ID ile eşleşmedi).
  - **5/26 muhtemel harici** — Log kapsamında, ne `force_close` ne `ws_unmatched_reduce_only` event'i var. #7,#9'da trail=1 var ama FVG kirildi logu yok — kesin sınıflandırma için deeper analiz gerekli.
  - **3/26 log dışı** — Ne log ne de event doğrulaması yok. #1,#2,#5.
- **Önceki hatalar (düzeltme nedeni):**
  - Eski "8/26 trailing" yanlıştı — #3,#4,#6 (log dışı dönem) atlanmıştı. Gerçek sayı 9.
  - Eski "13/26 log dışı" yanlıştı — toplama hatalıydı (26-8-5=13). Gerçek log-dışı: 3. #20 TIAUSDT WS_UNMATCHED ile doğrulandı, log-gap'te olmasına rağmen kesin harici.
  - Eski "5/26 kesin harici" yanlıştı — sadece paper_trade.log'daki CRITICAL satırlara bakılmıştı, event JSONL'deki `ws_unmatched_reduce_only` event'leri atlanmıştı. Gerçek sayı: 9.
  - Eski "3/20 muhtemel harici" satırı stale kalmıştı — silindi, 5/26 muhtemel harici ile değiştirildi.
- **ADAUSDT vakası (#19, en net kanıt):**
  - 13:30:16: Entry @ 0.1737, SL/TP algo ID ile yerleştirildi
  - 13:30:27: DOLDURMA emri geldi — ne SL ne TP tetiklendi
  - Entry→kapanış arası 11 saniye
  - `ws_unmatched_reduce_only` event'i doğruladı: external fill, bot-dışı kaynak
- **Olası kök nedenler:**
  1. **Testnet/demo API tuhaflığı:** `demo-fapi.binance.com` paylaşımlı hesap davranışı, otomatik reset — bilinen kalite sorunu
  2. **Aynı API key ile birden fazla instance:** Farklı makine/eski process/test script'i
  3. **Loglanmayan bir kod yolu:** Tüm exit path'leri incelendi, hepsi logluyor — olasılık düşük
- **Düzeltilen aksiyonlar:**
  - Görev 3: `post_entry_check_failed` event logu — entry sonrası ~2.5s sanity check (SL/TP Binance'te açık mı?)
  - Görev 4: FVG invalidation path'ine `log_event("exit_intent", reason="fvg_invalidated")` eklendi — artık events_*.jsonl'den trail_close'lar raw log'a inmeden tespit edilebilir
  - `client_order_id` traceability — tüm market order callers'a semantic prefix (entry-, exit-, sl-fail-, reconcile-, recover-)
- **Forensic aksiyon:** `ylOu3i0T6KRNJfKMA3T18s` clientOrderId'ine ait emrin tam detayı Binance API'den çekilmeli (`/fapi/v1/allOrders` veya `/fapi/v1/userTrades`). Eğer bu emir MARKET + reduceOnly ise ve botun hiçbir yerinde bu ID üretilmemişse, kaynak bot dışıdır.
- **⚠️ DURUM: KISMEN AÇIKLANDI** — 26 vaka tamamı doğrulandı (9 bot trailing / 9 kesin harici / 5 muhtemel harici / 3 log dışı). Önceki sayım tutarsızlıkları düzeltildi (8→9 trailing, 5→9 kesin, 13→3 log dışı). Görev 3/4 ile gözlemlenebilirlik artırıldı. 5 muhtemel harici (#7,#8,#9,#12,#15) için deeper analiz gerekli.

---

## 🟡 P2 — Medium Risk

### P2-1: `ProtectionLifecycleService.maybe_repair()` ölü kod
**Dosya:** `sniper/src/trading/protection_lifecycle.py:157`
- `tests/test_protection_lifecycle.py` dışında HİÇBİR YERDEN çağrılmıyor.
- `is_sweep_consumed()` ile aynı kader.
- Asıl repair kararları inline veriliyor.
- **⚠️ DURUM: DOĞRULANDI** — `maybe_repair()` sadece tanımlı, hiçbir yerden çağrılmıyor.

### P2-2: `CleanupPlan` eksik — prev/history/pending ID'leri iptal etmiyor
**Dosya:** `sniper/src/trading/protection_lifecycle.py:171`
- `cleanup_after_confirmed_exit()` sadece `sl_order_id`/`tp_order_id` iptal ediyor.
- `sl_order_id_prev`, `tp_order_id_prev`, `pending_*`, `*_history` atlanıyor.
- **Telafi:** `order_manager.cleanup_on_exit()` sonunda `cancel_all_open_orders()` broad-sweep var — canlı modda risk düşük ama CleanupPlan başlı başına yanıltıcı.
- **⚠️ DURUM: HÂLÂ GEÇERLİ** — cleanup_after_confirmed_exit (protection_lifecycle.py:196-208) sadece current ID'leri topluyor.

### P2-3: `promote_sl/tp()` dokümantasyon/niyet uyuşmazlığı
**Dosya:** `sniper/src/trading/protection_lifecycle.py:230`
- Doküman: "pending bekler, eski ID hemen silinmez."
- Gerçek: `begin_replace_*` + `promote_*` aynı senkron blokta çağrılır, pending state anlık.
- Şu an zararsız ama ileride yanıltıcı.
- **⚠️ DURUM: HÂLÂ GEÇERLİ** — begin_replace + promote aynı akışta (order_manager.py:139-141).

### P2-4: user_data_handler unmatched-reduceOnly, kendi exit'ini WS_FALLBACK sanıyor
**Dosya:** `sniper/src/trading/user_data_handler.py` (_on_order_update_normalized + _on_order_update_legacy)
- Trade EXIT_SUBMITTED/EXIT_VERIFYING durumundayken gelen kendi market-close fill'i, SL/TP ID setinde olmadığı için "unmatched" sayılıp WS_FALLBACK'e çevriliyordu; result üzerine yazılıyor, _exit_trade ikinci kez tetikleniyor, yakalanmamış WSFallbackError fırlatılıyordu.
- **DURUM: DÜZELTİLDİ** — status guard eklendi (_SELF_EXIT_IN_PROGRESS_STATUSES).
- Ek not: iki farklı tetikleyici senaryo tespit edildi:
  (a) self-close race (trade zaten EXIT_SUBMITTED/VERIFYING iken) — guard ile engellendi
  (b) legitimate external/unmatched fill (trade ACTIVE iken, örn. ENAUSDT olayı) — bu durumda exit doğru çalışıyordu, tek sorun exception'ın commit sonrası gereksiz raise edilmesiydi. raise → log_event'e çevrildi, davranış (trade kapatma) değişmedi, sadece gürültülü ERROR/traceback kaldırıldı.

### P2-5: update_trail_orders -4005 fallback yok + backoff yok
**Dosya:** `sniper/src/trading/order_manager.py:update_trail_orders()`
- SL/TP placement bloğunda -4005 (max qty) hatası aldığında hiçbir fallback denenmiyordu; `repair_protection()`'da olan closePosition → split_qty deseni burada eksikti.
- `sl_reject`/`tp_reject` `log_event` çağrılarına `error_code` alanı eklenmedi.
- Ardışık trailing başarısızlıkları için backoff mekanizması yoktu — -4005 hatası dakikada bir sonsuza kadar WARNING spam'i üretiyordu.
- **DURUM: DÜZELTİLDİ** — SL/TP placement'a closePosition fallback eklendi, `error_code` log_event'a eklendi, `_trail_failures` backoff (3 başarısızlık → 5dk + CRITICAL).

---

## 🔵 P3 — Low Risk

### P3-1: Genel — `except Exception` çok yaygın
**Dosya:** `sniper/src/` geneli
- Spesifik exception tipleri kullanılmalı.
- Type hinting var ama runtime kontrol zayıf.
- **⚠️ DURUM: HÂLÂ GEÇERLİ** — exit_lifecycle.py, recovery_manager.py, bot.py'de yaygın `except Exception` kullanımı var.

---

## ✅ Verified Correct (analizlerde doğrulanan)

### V1: SEIUSDT sl_reject×2 — eski SL korunuyor ✓
- `events_2026-07-20`: aynı `old_id` ile 60sn arayla 2 reject.
- `order_manager.update_trail_orders()` yeni SL reddedilince eski SL'yi değiştirmiyor (order_manager.py:135).
- Sonuç: trailing_count=3 ile orijinal SL tetiklendi, pozisyon korumasız kalmadı.

### V2: GMXUSDT force_close + WS_FALLBACK — beklenen davranış ✓
- Unmatched reduceOnly fill → `INCIDENT_WS_UNMATCHED_REDUCE_ONLY` → `WSFallbackError`.
- `user_data_handler.py`'deki tasarlanmış yol, doğru çalışıyor.

### V3: `execute()` çift tetiklenme koruması — atomic pop ✓
- `_commit_confirmed_exit()` içinde `pop()`, öncesinde `await` yok → GIL/single-thread event loop'da atomic.

### V4: `recovery_manager.reconcile_orphan_orders()` transition-aware ✓
- `should_skip_reconcile()` kontrolü doğru çalışıyor (protection_lifecycle.py:102).
- `_known_protection_ids()` current+prev+history+pending'in tamamını topluyor (protection_lifecycle.py:73).
- 60sn `_check_position()`'ın aksine, burada guard var.

---

## 📊 Özet

| Bug | Durum | Not |
|-----|-------|-----|
| P0-1 | KISMEN DÜZELTİLDİ | Yeni exit_service REST doğrulama ekledi |
| P0-2 | YENİ YOLDA DÜZELTİLDİ | Legacy path devre dışı (flag=True) |
| P0-3 | KALDIRILDI | `_check_position` fonksiyonu yok, orphan sweep guard'lı |
| P0-4 | KISMEN DÜZELTİLDİ | `periodic_check_loop` ~60sn'de yakalar, ghost hala restart'ta, restart'ta REPAIR→ACTIVE temizlik var |
| P1-1 | DÜZELTİLDİ | `estimate_market_price()` fallback eklendi |
| P1-2 | HÂLÂ GEÇERLİ | Trail reject sonrası retry yok |
| P1-3 | İNCELENMELİ | entry_manager.py precision kontrolü gerekli |
| P1-4 | KISMEN DÜZELTİLDİ | Orphan periyodik (periodic_check_loop + _on_1m_close), ghost hala restart'ta, restart'ta REPAIR→ACTIVE temizlik var |
| P1-5 | KÖK NEDEN DÜZELTİLDİ | `_round_step` floating-point fix (`int(value/step)`) |
| P1-6 | DÜZELTİLDİ | Entry sizing LOT_SIZE.maxQty kontrolü yok — kök neden |
| P1-7 | KISMEN AÇIKLANDI | 26 vaka doğrulandı: 9 bot trailing / 9 kesin harici / 5 muhtemel harici / 3 log dışı. Önceki sayım tutarsızlıkları düzeltildi. |
| P2-1 | DOĞRULANDI | maybe_repair() ölü kod |
| P2-2 | HÂLÂ GEÇERLİ | CleanupPlan sadece current ID'leri iptal ediyor |
| P2-3 | HÂLÂ GEÇERLİ | promote dokümantasyon uyuşmazlığı |
| P2-4 | DÜZELTİLDİ | self-exit race guard (_SELF_EXIT_IN_PROGRESS_STATUSES) |
| P2-5 | DÜZELTİLDİ | update_trail_orders -4005 fallback + trail backoff |
| P3-1 | HÂLÂ GEÇERLİ | except Exception yaygın |
