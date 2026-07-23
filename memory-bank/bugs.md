# Bug Registry — sniper/src/

> **Son güncelleme:** 2026-07-23 13:11 — Görev 10: SSH post-deploy doğrulama. P1-8/P1-10 DÜZELDİ (P0-5). P1-9 P0-5 yetersiz — P1-2'ye birleşti (TRAIL_REPLACING stuck). P1-7: 22/23 Temmuz ayrımı netleştirildi.
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

### P1-2: `update_trail_orders()` reject sonrası retry/backoff yok + TRAIL_REPLACING stuck vulnerability
**Dosya:** `sniper/src/trading/order_manager.py:64`
- SEIUSDT event log'u ile teyit: aynı `old_id` ile 60sn arayla 2 reject, fiyat yeniden hesaplanmıyor.
- SL trailing durur, pozisyon korumasız kalır.
- **🚨 YENİ BULGU (Görev 10.1/10.2):** `update_trail_orders()`'ta `trade["status"] = STATUS_TRAIL_REPLACING` (line 117) `apply_price_precision()` çağrısından (line 119-120) ÖNCE set ediliyor. `apply_price_precision()` hiçbir try/except kapsamında DEĞİL — `asyncio` timeout veya network hatasında status TRAIL_REPLACING'de kalıcı olarak asılı kalır. `UNRESTRICTED_STATUSES` TRAIL_REPLACING'i içermediği için `_on_1m_close()` trailing'i sonsuza kadar atlar.
- **P1-9 SEIUSDT ghost loop'un devam eden kısmının kök nedeni budur:** P0-5 repair döngüsünü kırdı ama trailing sırasında status TRAIL_REPLACING'de kilitlenen pozisyon hâlâ kurtarılamıyor. `_trail_failures` backoff (line 96-115) sadece WARNING üretiyor, status recovery yok.
- **⚠️ DURUM: HÂLÂ GEÇERLİ** — `update_trail_orders()` reject olduğunda eski SL'yi koruyor (order_manager.py:135) ama:
  - `apply_price_precision()` öncesi try/except yok → TRAIL_REPLACING stuck riski
  - `_on_1m_close()` çağıran tarafta try/except yok → exception event loop'a kadar yayılır
  - Per-symbol lock: **gerekli değil** (`update_trail_orders` sadece `_on_1m_close`'dan çağrılır, eşzamanlılık yok). Asıl ihtiyaç: `try/finally` ile status recovery veya `apply_price_precision` öncesi status set etmeme.
- **Önerilen fix:** `trade["status"] = STATUS_TRAIL_REPLACING` satırını `apply_price_precision()` çıktıktan sonra (line 120 sonrası) veya try/finally bloğu içine taşı.

### P1-3: SL/TP tahmini fiyatla hesaplanıyor, actual fill price ile güncellenmiyor - DÜZELTİLDİ
**Kaynak:** `events_2026-07-23.jsonl` (SEIUSDT 08:48) + `trades_history.jsonl` + SSH ile sunucu kod doğrulaması
- SEIUSDT short entry @ 0.0462, TP @ 0.04625 — TP entry'den ÜSTTE, short'ta TP altta olmalı. Sonuç: hemen tetiklendi, -2.08 PnL (7/23).
- OPUSDT entry'leri de aynı gün ~270-280ms sonra force_close ile kapanmıştı (7/20) — muhtemel aynı kök neden.

**Kök neden (2026-07-23'te SSH ile doğrulandı):**
`calculate_sl_tp()` formülünün kendisi doğru (`tp = entry_price - risk_dist * tp_rr` short'ta). Sorun **bot.py akış sırası** ve **execute_live_entry()** içinde:
1. `bot.py:552` — `entry_price = current.close = 0.0463` (15m bar kapanış fiyatı, tahmini)
2. `bot.py:556` — `sl, tp = calculate_sl_tp(entry_price=0.0463)` → risk_dist=0.000025, tp=0.04625 (0.0463'ten küçük ✓)
3. `entry_manager.py:280` — MARKET order gönderilir, `actual_price=0.0462` ile dolar (0.0001 kayma)
4. `entry_manager.py:369-398` — SL/TP emirleri **actual_price bilinmesine rağmen** eski tahmini sl/tp ile gönderilir
5. `bot.py:688` — `entry_price = actual_entry_price` olarak güncellenir ama sl/tp **yeniden hesaplanmaz**
6. Sonuç: tp=0.04625 > actual_entry=0.0462 → short'ta TP entry üstünde → anında tetiklenir

**Canlı kanıt (trades_history.jsonl):**
```json
"entry_price_estimate": 0.0463,  // current.close (tahmini)
"entry_actual_price": 0.0462,    // Binance fill (gerçek)
"sl": 0.046325, "tp": 0.04625   // 0.0463 bazlı hesaplama
```
- Tahmini entry (0.0463) ile: risk_dist=0.000025, tp=0.04625 < 0.0463 ✓
- Gerçek entry (0.0462) ile: tp=0.04625 > 0.0462 ✗ → immediate trigger

**MIN_STOP_DIST_PCT guard neden yakalamadı:**
- `validate_risk`: min_risk_dist = atr(0.000165) × 0.1 = 0.0000165
- risk_dist(0.000025) >= 0.0000165 → kıl payı PASS
- `calculate_sl_tp`'de risk_dist için alt sınır kontrolü yok (sadece üst sınır: max_risk_dist)

**Sunucu kod doğrulaması:** `entry_manager.py` ve `bot.py` yerel ile sunucu arasında **birebir aynı** (sadece CRLF/LF farkı). `config.py:TP_RR=2.0` da aynı.

**Önerilen fix:** `entry_manager.py:execute_live_entry()` içinde, `actual_price` bilindikten sonra (satır 286), sl/tp actual_price ile güncellenmeli:
```python
if actual_price > 0 and est_price > 0 and actual_price != est_price:
    slippage = actual_price - est_price
    sl += slippage
    tp += slippage
```
Ek olarak `calculate_sl_tp`'ye short'ta `tp >= entry_price` guard'ı eklenmeli.

**İlişki:** Sonraki P0-4 zincirleme olayları (recovery/trailing döngüsü, 11:39 SL exit) bu bug'un sonucudur.

- **⚠️ DURUM: DÜZELTİLDİ (2026-07-23)** — Fix `entry_manager.py:execute_live_entry()` katmanında yapıldı:
  1. Market fill sonrası actual_price ile `calculate_sl_tp()` yeniden çağrılıyor
  2. `calculate_sl_tp()` içinde defense-in-depth guard (tp yön hatası → fallback)
  3. `execute_live_entry()` içinde safety-net guard (tp hatalıysa pozisyon acil kapatılır)
  4. `bot.py:649,737`'den extra parametreler (risk_pts, fvg_buf, tp_rr, trigger_fvg, london_high/low) geçiliyor
- **Ek not:** `test_entry_manager.py`'deki 8 test kırık — pre-existing. Testler eski london_high/low TP fallback beklentileriyle yazılmış, kod sonra 1:2 R:R sabit TP'ye geçmiş. Backlog: test expectations güncellenmeli.

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
- **Testnet güvenliği (2026-07-23):** API key yenilendi ama `web_1FJn4hMop8dxxQeYCcLi` ile web arayüzünden emir gelmeye devam etti. Doğrulandı: kullanıcı Brave'de eski session ile kilitli kalmış, diğer browser'dan login olup bot pozisyonunu görmüş — `web_` order kendi diğer browser'ından kaynaklanıyor.
- **⚠️ 22 TEMMUZ vs 23 TEMMUZ AYRIMI (Görev 10.3):** 23 Temmuz'daki external fill'ler (`web_` prefix OID'ler, NEARUSDT ve SEIUSDT) doğrudan browser session'ına bağlandı. Ancak **22 Temmuz'daki 9 kesin-harici vakanın kaynağı BUNDAN FARKLI OLABİLİR** — 22 Temmuz'da `web_` prefix'li hiçbir OID yok. O günkü WS_UNMATCHED_REDUCE_ONLY event'leri (ADA, ONDO, TIA, SOL, DOGE) farklı bir kaynaktan (testnet paylaşımı, başka API key instance'ı, Binance testnet otomatik reset) gelebilir. 23 Temmuz'un browser açıklaması otomatik olarak 22 Temmuz'a genellenmemelidir. Forensic aksiyon (`ylOu3i0T6KRNJfKMA3T18s` clientOrderId sorgusu) hâlâ geçerli.
- **⚠️ DURUM: KISMEN AÇIKLANDI** — 26 vaka tamamı doğrulandı (9 bot trailing / 9 kesin harici / 5 muhtemel harici / 3 log dışı). Önceki sayım tutarsızlıkları düzeltildi (8→9 trailing, 5→9 kesin, 13→3 log dışı). Görev 3/4 ile gözlemlenebilirlik artırıldı. 5 muhtemel harici (#7,#8,#9,#12,#15) için deeper analiz gerekli. **22 Temmuz'daki 9 kesin-harici vaka 23 Temmuz'daki browser session'ından AYRI değerlendirilmeli** — testnet paylaşımı veya diğer instance hâlâ olası. Mainnet'e geçişte reassess edilecek.

### P1-8: post_entry_check_failed %100 tüm trades — sistematik SL/TP kaybı (2026-07-23 canlı verisi)
**Kaynak:** `events_2026-07-23.jsonl` (241 satır, sunucudan alındı) — SSH ile canlı analiz
- **11/11 post_entry_check_failed** — o gün yapılan TÜM trade'lerde SL/TP 2.5s sonra Binance'te bulunamadı
- **Etkilenenler:** TIAUSDT (x3), SEIUSDT, ENAUSDT (x4), APTUSDT, LDOUSDT, NEARUSDT
- **İkinci grup (geçici):** ENAUSDT × 3 arka arkaya → hepsi `fvg_invalidated` → `force_close` (pattern mi tesadüf mü?)

**Kök neden analizi:**
`entry_manager.py:get_open_order_ids()` (order_manager.py:332-343) şöyle çalışır:
1. `get_open_orders()` → `/fapi/v1/openOrders` (normal limit order'lar)
2. `get("/fapi/v1/openAlgoOrders")` → algo order'lar (STOP_MARKET/TAKE_PROFIT_MARKET)
3. İkisi birleştirilir → `algoId` veya `orderId` ile aranır

SL/TP yerleştirme log'da "SL OK" / "TP OK" dönse de, 2.5s sonra `get_open_order_ids()` bunları bulamıyor. Eğer `/fapi/v1/openAlgoOrders` testnette güvenilir değilse (boş dönüyorsa), `sl_id`/`tp_id` (algo ID) normal openOrders'ta olmadığı için `sl_ok=False, tp_ok=False` döner.

- **İlişkili:** P0-1 (çift exit), P1-7 (harici kapanış), P0-4 (ghost loop) — hepsi aynı kökten besleniyor olabilir
- **⚠️ DURUM: P0-5 İLE DÜZELDİ** — Görev 10.1 (SSH post-deploy doğrulama): deploy sonrası (12:22→12:30 arası) **0 adet** `post_entry_check_failed` event'i. P0-5 fix (openAlgoOrders hatasını None fırlat + fail-safe) P1-8'i tamamen durdurdu. `/fapi/v1/openAlgoOrders` testnet sorgusu gerçekten bozuk dönüyordu, fix sonrası `None` fail-safe koruyor.

### P1-9: SEIUSDT ghost loop 4+ saat — restart + P0-5 deploy SONRASI bile devam etti (2026-07-23)
**Kaynak:** Sunucu canlı log + events_2026-07-23.jsonl + Görev 10.1 SSH post-deploy sorgusu
- SEIUSDT short @ 0.0462 pozisyonu saat 08:47'den itibaren **12:30'a kadar** aktif kaldı
- Restart (12:14) sonrası recovery_manager tarafından yeniden oluşturuldu
- `[ORPHAN] SEIUSDT status=TRAIL_REPLACING — orphan sweep bu sembolde atlaniyor`
- Trailing SL sürekli -2021 (Order would immediately trigger) reject alıyor
- **P0-5 deploy (12:22) SONRASI:** SEIUSDT trailing reject'leri 12:22:15'ten itibaren devam etti (7x sl_reject -2021, 3x tp_reject, tp_price=0.0001'e dejenere oldu). Saat 12:30'da FVG invalidation → force_close ile pozisyon kapatıldı (pnl=-5.32).
- **⚠️ P0-5 YETERSİZ** — P1-9'un kök nedenlerinden 1. yol (verify_protection → sonsuz repair döngüsü) P0-5 ile kapandı ama 2. yol (update_trail_orders TRAIL_REPLACING'de asılı kalma) ayrı bir bug. `extra_trail_failures` backoff sadece warning üretiyor, status'u ACTIVE'e döndürmüyor. P1-9'un devam eden kısmı için **P1-2 ile birleştirildi** (aşağıdaki P1-2 güncellemesine bak).

### P1-10: STRKUSDT 49x consecutive -4005 rejection (2026-07-23 log bulgusu)
**Kaynak:** `events_2026-07-23.jsonl` — SSH ile canlı analiz + Görev 10.1 SSH post-deploy sorgusu
- Aynı `old_id=1000000141695716`, aynı fiyat (`sl_price=0.0301`), 1+ saat boyunca her ~36s'de bir tekrarlanan -4005 hatası
- 49 ardışık `sl_reject` event'i (1784764862643 → 1784784616885 arası)
- `_trail_failures` backoff (P2-5 DÜZELTİLDİ) bu vakada çalışmamış
- **Görev 10.1 doğrulaması:** STRKUSDT -4005 deploy sonrası **0 event** → kesinlikle durdu.
- **⚠️ DURUM: P0-5 İLE DÜZELDİ** — STRKUSDT -4005 ghost döngüsünün kök nedeni openAlgoOrders sessiz yutmaydı. P0-5 fix ile sonsuz repair döngüsü kırıldı. P2-5 fallback/backoff artık gereksiz (P1-6 entry maxQty clamp zaten entry'de -4005'i engelliyor) ama defense-in-depth olarak kalmalı.

### P0-5: `get_all_orders()` openAlgoOrders hatasını sessizce yutuyor — false-negative koruma döngüsü
**Kaynak:** 2026-07-23 12:21 — canlı server log analizi + kod doğrulaması (baş mühendis onayı)
- `bot_binance.py:get_all_orders()` (bot_binance.py:635-647) `/fapi/v1/openAlgoOrders` endpoint'i hata verdiğinde sessizce yutup sadece normal emirlerle dönüyor
- SL/TP emirleri `place_stop_order/place_tp_order` üzerinden **algo ID** ile açıldığı için listede hiç görünmüyor
- Log seviyesi `debug` + "önemsiz" notu — aslında "korumanın varlığını asla doğrulayamıyoruz" demek

**Zincirleme etki (iki ayrı yol):**

1. **`verify_protection()` inline yol** (protection_service kapalıyken): `get_all_orders()` doğrudan çağrılır, algo hatası yutulur → `sl_present=False, tp_present=False` → gereksiz `repair_protection()` tetiklenir → yeni SL/TP (yine algo, yine görünmez) → **sonsuz döngü** (SEIUSDT ghost loop)

2. **`verify_protection()` → `ProtectionLifecycleService.verify()` yolu** (varsayılan, canlıda aktif): `get_open_order_ids()` → `get_all_orders()` exception fırlatmaz (yutuldu), boş küme döner → `needs_repair=True` → **11/11 post_entry_check_failed**

**Tasarım hatası:** "Sorgu başarısız = emirler yok" varsayımı. Doğrusu "sorgu başarısız = bilmiyoruz, dokunma".

**Düzeltme (7e50331):**
1. `bot_binance.py:get_all_orders()` — openAlgoOrders başarısızsa `RuntimeError` fırlatır
2. `order_manager.py:get_open_order_ids()` — hata durumunda `None` (boş küme değil)
3. `protection_lifecycle.py:verify()` — `None` alırsa `needs_repair=False` fail-safe
4. `order_manager.py:verify_protection()` — aynı fail-safe
5. `bot.py:post_entry_check` — None kontrolü

**⚠️ DURUM: DÜZELTİLDİ (7e50331, 12:22)** — Diff baş mühendise onaya gönderildi.

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

### P3-2: execute_live_entry() entry_log_msg tahmini fiyatı gösteriyor
**Dosya:** `sniper/src/trading/entry_manager.py:437`
- `entry_log_msg`'de `PRICE: {est_price:.2f}` yazıyor, `actual_price` değil
- Davranışı etkilemez, sadece kozmetik
- **⚠️ DURUM: HÂLÂ GEÇERLİ** — İstenirse actual_price ile güncellenebilir

### P3-3: Genel — `except Exception` çok yaygın
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
| P0-5 | DÜZELTİLDİ (7e50331) | get_all_orders() openAlgoOrders hatasını yutuyor — SEIUSDT ghost loop + %100 post_entry_check_failed kök nedeni |
| P1-1 | DÜZELTİLDİ | `estimate_market_price()` fallback eklendi |
| P1-2 | HÂLÂ GEÇERLİ | Trail reject sonrası retry yok + TRAIL_REPLACING stuck (apply_price_precision öncesi status set) — P1-9'un devam eden kök nedeni |
| P1-3 | DÜZELTİLDİ (2026-07-23) | execute_live_entry() içinde actual_price ile sl/tp yeniden hesaplanıyor + safety-net guard. |
| P1-4 | KISMEN DÜZELTİLDİ | Orphan periyodik (periodic_check_loop + _on_1m_close), ghost hala restart'ta, restart'ta REPAIR→ACTIVE temizlik var |
| P1-5 | KÖK NEDEN DÜZELTİLDİ | `_round_step` floating-point fix (`int(value/step)`) |
| P1-6 | DÜZELTİLDİ | Entry sizing LOT_SIZE.maxQty kontrolü yok — kök neden |
| P1-7 | KISMEN AÇIKLANDI | 26 vaka doğrulandı: 9 bot trailing / 9 kesin harici (22 Temmuz browser'dan AYRI) / 5 muhtemel harici / 3 log dışı |
| P1-8 | DÜZELDİ (P0-5) | 11/11 → 0/0 post-entry-check post-deploy (SSH doğrulandı) |
| P1-9 | P0-5 YETERSİZ → P1-2 ile birleşti | P0-5 repair döngüsünü kırdı ama trailing TRAIL_REPLACING stuck hâlâ var — deploy sonrası 8dk daha reject devam etti |
| P1-10 | DÜZELDİ (P0-5) | 49x -4005 → 0 post-deploy (SSH doğrulandı) |
| P2-1 | DOĞRULANDI | maybe_repair() ölü kod |
| P2-2 | HÂLÂ GEÇERLİ | CleanupPlan sadece current ID'leri iptal ediyor |
| P2-3 | HÂLÂ GEÇERLİ | promote dokümantasyon uyuşmazlığı |
| P2-4 | DÜZELTİLDİ | self-exit race guard (_SELF_EXIT_IN_PROGRESS_STATUSES) |
| P2-5 | DÜZELTİLDİ | update_trail_orders -4005 fallback + trail backoff |
| P3-1 | HÂLÂ GEÇERLİ | entry_log_msg tahmini fiyat gösteriyor (kozmetik) |
| P3-2 | HÂLÂ GEÇERLİ | except Exception çok yaygın |
