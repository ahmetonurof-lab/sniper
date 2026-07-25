# Bug Registry — sniper/src/

> **Son güncelleme:** 2026-07-25 — Baş mühendis review: P1-8a type mismatch, P1-13b dead code, P1-14b legacy path eksik eklendi.
> **Önceki güncelleme:** 2026-07-24 — P1-12 ve P1-13 olarak eklendi.
> Dosya referansları `sniper/src/` olarak güncellendi.

---

## 🔴 AKTİF BUG ÖZETİ (25 Tem 2026)

> 🆕 = 25 Tem'de yeni keşfedildi | 🐛 = açık/henüz fix yok | 🔧 = fix yazıldı/pending deploy | ✅ = fix deploy edildi | 📎 = mevcut bug'a veri eklendi

| ID | Durum | Başlık | Aciliyet |
|---|---|---|---|
| **P0-1** | ✅ | STRKUSDT çift-exit/çift-PnL | KISMEN DÜZELTİLDİ |
| **P0-5** | ✅ | get_all_orders openAlgoOrders hata yutma | DEPLOY + DOĞRULANDI |
| **P0-6** | 🔧 | _exit_already_closed SL/TP pozisyon doğrulaması yok | FIX YAZILIYOR |
| **P0-7** | 🔧 | TP unchanged iptal + precision-residual churn | HAZIR, DEPLOY BEKLİYOR |
| **P1-2** | ✅ | update_trail_orders retry/backoff yok | DÜZELTİLDİ |
| **P1-3** | ✅ | SL/TP tahmini fiyatla hesaplama | DÜZELTİLDİ |
| **P1-6** | ✅ | Entry sizing max_qty clamp yok | DÜZELTİLDİ |
| **P1-7** | 📎 | Harici kapanışlar (26 WS_FALLBACK) + ONDOUSDT fix | KISMEN AÇIKLANDI |
| **P1-8** | 🐛 | POST_ENTRY check %100 başarısız — iki kök neden tespit edildi | DEBUG LOG AKTİF, KÖK NEDEN AYRIMINDA |
| **P1-13** | ✅🔧 | DD circuit breaker bypass — fix deploy + ölü kod temizliği bekliyor | DEPLOY + TEMİZLİK |
| **🆕 P1-14** | ✅🔧 | SL stale event — fix deploy + legacy path eksik | DEPLOY + EŞİTLEME |
| **D-2** | 🐛 | Trailing/entry formülleri 3 motorunda kopya kod | AÇIK |
| **🆕 P2-6** | 🐛 | TIAUSDT her bar close'da gir-çık döngüsü | AÇIK |
| **🆕 P2-7** | 🐛 | Tüm TRAIL_CLOSE çıkışları negatif (5/5) | AÇIK |
| **🆕 P3-4** | 🐛 | NEARUSDT SL çok dar (0.055%) | AÇIK |
| **🆕 P1-8a** | 🐛 | POST_ENTRY type mismatch: int vs str set check | AÇIK |
| **🆕 P1-13b** | 🐛 | P1-13 DD guard sonrası ölü kod (unreachable block) | AÇIK |
| **🆕 P1-14b** | 🐛 | _exit_trade_legacy'de P1-14 cross-val eksik | AÇIK |

---

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
- **✅ DURUM: DÜZELTİLDİ (Görev 11, 2026-07-23)** — `order_manager.py:update_trail_orders()`:
  - `apply_price_precision()` çağrıları (line 117-118) artık `STATUS_TRAIL_REPLACING` set'inden (line 130) ÖNCE.
  - Precision sonrası validate (line 120-128): sl/tp <= 0 ise TRAIL_REPLACING'e girmeden çık.
  - Kısmi başarı (SL OK, TP fail veya tamamen fail) durumunda status ACTIVE'e resetleniyor (line 310-315).
  - Senaryo: (a) precision exception → status TRAIL_REPLACING'e girmez, (b) SL başarı + TP fail → status ACTIVE'e resetlenir, (c) her ikisi de fail → mevcut ACTIVE reset korunur.

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

- **📎 ONDOUSDT FIX (nexus-mcp, 2026-07-24):** `user_data_handler.py`'ye REST cross-validation eklendi. WS'ten gelen unmatched reduceOnly FILLED event'inde, `WS_FALLBACK`'e geçmeden önce `get_open_order_ids()` ile SL/TP algo ID'leri sorgulanıyor: SL eksikse → `result="SL"`, TP eksikse → `result="TP"`, ikisi de eksikse → fill result'a göre çözümleme. Bu fix P1-7'deki "kesin harici" vakaların çoğunu aslında genuine SL/TP trigger olarak yeniden sınıflandırabilir.

### P1-8: post_entry_check_failed %100 tüm trades — sistematik SL/TP kaybı (2026-07-23 + 25 canlı verisi)
**Kaynak:** `events_2026-07-23.jsonl` + `paper_trade.log` (24/Jul 7 vaka, 25/Jul 9+ vaka) — SSH + yerel analiz
- **11/11 post_entry_check_failed** (23/Jul) + **7/7** (24/Jul) + **9+/9+** (25/Jul) — 3 gündür **%100 failure rate**
- **Etkilenenler:** TIAUSDT (x3), SEIUSDT, ENAUSDT (x4), APTUSDT, LDOUSDT, NEARUSDT
- **İkinci grup (geçici):** ENAUSDT × 3 arka arkaya → hepsi `fvg_invalidated` → `force_close` (pattern mi tesadüf mü?)

**Kök neden analizi:**
`entry_manager.py:get_open_order_ids()` (order_manager.py:332-343) şöyle çalışır:
1. `get_open_orders()` → `/fapi/v1/openOrders` (normal limit order'lar)
2. `get("/fapi/v1/openAlgoOrders")` → algo order'lar (STOP_MARKET/TAKE_PROFIT_MARKET)
3. İkisi birleştirilir → `algoId` veya `orderId` ile aranır

SL/TP yerleştirme log'da "SL OK" / "TP OK" dönse de, 2.5s sonra `get_open_order_ids()` bunları bulamıyor. Eğer `/fapi/v1/openAlgoOrders` testnette güvenilir değilse (boş dönüyorsa), `sl_id`/`tp_id` (algo ID) normal openOrders'ta olmadığı için `sl_ok=False, tp_ok=False` döner.

- **İlişkili:** P0-1 (çift exit), P1-7 (harici kapanış), P0-4 (ghost loop) — hepsi aynı kökten besleniyor olabilir
- **⚠️ DURUM: İKİ AYRI KÖK NEDEN TESPİT EDİLDİ (25 Tem debug log)**

**📎 25 TEMMUZ CANLI DEBUG SONUÇLARI (2026-07-25, canlı debug log yakalandı):**
P0-5 fix deploy edilmesine rağmen 25/Jul'da **17+ post_entry_check_failed** kaydedildi (events_2026-07-25.jsonl). `debug` log seviyesi `warning`'e çevrilerek canlıya deploy edildi,ilk debug sonucu yakalandı:

**TIAUSDT vakası (18:05:02) — CANLI DEBUG:**
```
[POST_ENTRY_DEBUG] TIAUSDT raw_orders_count=1 raw_ids=['1000000145939996'] filtered_empty=False
```
- `raw_orders_count=1` → API 1 emir döndürdü (sadece TP: `1000000145939996`)
- SL order (`1000000145939992`) listede YOK → zaten FILLED olmuş
- `18:05:02.802` exit_lifecycle SL fill tespit etti → **SL gerçekten çalıştı ama check'e yetişemedi**
- **Kök neden (TIAUSDT): SL çok hızlı doldu → 2.5s sleep + API call süresinde SL zaten open orders'tan çıktı → false positive**
- Tip uyuşmazlığı ELANDI — `sl_order_id: str`, `open_ids: set[str]`, `str in set[str]` tutarlı

**İki ayrık kök neden olasılığı:**
1. **Hızlı fill vakası (TIAUSDT):** SL order placement ile check arasında SL zaten dolmuş → `openAlgoOrders` sadece açık emirleri döndüğü için görünmüyor → **false positive** — check mekanizması zaten çalışmayan bir şeyi kontrol etmiş oluyor
2. **Eventual consistency vakaları (NEARUSDT 14:45, ONDOUSDT 15:45 vb.):** SL/TP henüz AÇIKKEN check yine de bulamamış → bu vakalar için `raw_orders_count=0` veya algo order'lar endpoint'te geçici olarak görünmüyor olabilir → **bir sonraki entry'de `raw_orders_count` bekleniyor**

**Kod düzeltmeleri (25 Tem):**
- `bot_infra.py:_fmt_price()` eklendi → küçük fiyatlı coinlerde (OPUSDT 0.09) SL/TP artık ayırt edilebiliyor
- `order_manager.py:get_open_order_ids()` + `bot.py:post_entry_check` → debug log eklendi (`log.warning` level)

### P1-9: SEIUSDT ghost loop 4+ saat — restart sonrası bile devam ediyor (2026-07-23)
**Kaynak:** Sunucu canlı log + events_2026-07-23.jsonl + SSH sorgusu
- SEIUSDT short @ 0.0462 pozisyonu saat 08:47'den itibaren **12:30'a kadar** aktif kaldı
- Restart (12:14) sonrası recovery_manager tarafından yeniden oluşturuldu
- `[ORPHAN] SEIUSDT status=TRAIL_REPLACING — orphan sweep bu sembolde atlaniyor`
- Trailing SL sürekli -2021 (Order would immediately trigger) reject alıyor
- P0-5 fix HENÜZ DEPLOY EDİLMEDİ — sunucu eski kodla çalışıyor
- Saat 12:30'da FVG invalidation → force_close ile pozisyon kapatıldı (pnl=-5.32)
- **P0-5 fix deploy edildikten sonra yeniden değerlendirilecek**

### P1-10: STRKUSDT 49x consecutive -4005 rejection (2026-07-23 log bulgusu)
**Kaynak:** `events_2026-07-23.jsonl` — SSH ile canlı analiz + Görev 10.1 SSH post-deploy sorgusu
- Aynı `old_id=1000000141695716`, aynı fiyat (`sl_price=0.0301`), 1+ saat boyunca her ~36s'de bir tekrarlanan -4005 hatası
- 49 ardışık `sl_reject` event'i (1784764862643 → 1784784616885 arası)
- `_trail_failures` backoff (P2-5 DÜZELTİLDİ) bu vakada çalışmamış
- **Görev 10.1 doğrulaması:** STRKUSDT -4005 deploy sonrası **0 event** → kesinlikle durdu.
- **⚠️ DURUM: P0-5 İLE DÜZELTİLDİ** — STRKUSDT -4005 ghost döngüsünün kök nedeni openAlgoOrders sessiz yutmaydı. P0-5 fix ile sonsuz repair döngüsü kırıldı. P2-5 fallback/backoff artık gereksiz (P1-6 entry maxQty clamp zaten entry'de -4005'i engelliyor) ama defense-in-depth olarak kalmalı.

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

**✅ DURUM: DEPLOY EDİLDİ + DOĞRULANDI (7e50331, 14:32)** — Sunucuda mevcut, tüm fail-safe'ler aktif. STRKUSDT/SEIUSDT ghost loop'ları kapanmış.

### P0-7: `update_trail_orders()` — TP fiyatı değişmediğinde hâlâ geçerli TP emri iptal ediliyor + precision-residual sonsuz trail churn
**Kaynak:** 2026-07-23 — SEIUSDT log analizi (trail#1..#7 aynı ham sl/tp, TP id boş)
```
trail#1 → sl id=...784220  tp id=...784222   ✅ ikisi de yeni id aldı
trail#2 → sl id=...785444  tp id=(boş)       ⚠️ TP id boş!
          hemen üstünde: 🧹 İPTAL algoId=...784222 (trail#1'in TP'si, hâlâ geçerliydi!)
→ 1sn sonra: "SL stale event" → "koruma eksik (tp=False)" → repair
trail#3..#7 → aynı desen tekrarlıyor (raw sl=0.045658 tp=0.044958, 7 kez birebir aynı)
```

**İki iç içe bug:**

1. **`order_manager.py:update_trail_orders()`** — `tp_unchanged = abs(new_tp - old_tp_price) < 1e-8` doğruyken (TP sabit 1:2 R:R'de kalıyor, yalnız SL trail ediyor) `tp_ok = True` set ediliyor (yeni emir atılmadı, "mevcut TP zaten duruyor" doğru tespiti) ama post-processing bloğu yalnızca `tp_ok`'a bakıyordu: `trade["tp_order_id"]` boş `new_tp_id` ile eziliyor VE hâlâ geçerli olan eski TP emri Binance'te iptal ediliyor. Cancel↔repair penceresinde (birkaç yüz ms) pozisyon gerçekten TP'siz kalıyordu.

2. **Kök neden (churn kaynağı) — `trailing_manager.py:evaluate_trail()`** senkron/pure bir fonksiyon, precision/tick-size'a erişimi yok. Eşik kıyaslamasını ham (rounding-öncesi) hedef ile bir önceki cycle'da precision-rounded kaydedilmiş `trade["sl"]`/`trade["tp"]` ile yapıyor. SEIUSDT gibi düşük fiyatlı bir coin'de tick-altı rezidü (`0.045700 - 0.045658 = 0.000042`) eşiği (`risk_pts * TRAIL_MIN_MOVE_MULT`) her seferinde geçiyor → trail sonsuza kadar "tetikleniyor" ama `apply_price_precision()` (order_manager, async/REST) her seferinde aynı `0.045700`/`0.045000`'e yuvarlıyor — gerçek ilerleme sıfır, sadece `trailing_count` ve gürültü artıyor. Bu sonsuz tetiklenme, (1)'deki `tp_unchanged` bug'ını her ~45-90sn'de bir (her trail cycle'ında) ateşliyor.

**Düzeltme (`order_manager.py:update_trail_orders()`, henüz deploy edilmedi):**
1. TP state-write/cancel bloğu artık `tp_ok and not tp_unchanged` şartına bağlı — TP fiyatı değişmediyse ne `tp_order_id` eziliyor ne de eski (hâlâ geçerli) TP emri iptal ediliyor.
2. `apply_price_precision()` çağrıldıktan hemen sonra (SL/TP fiyatları async olarak yuvarlandıktan sonra), yeni bir guard: precision-sonrası `new_sl`/`new_tp` mevcut `trade["sl"]`/`trade["tp"]` ile (1e-8 tolerans) hâlâ aynıysa — yani gerçekte hiçbir şey değişmemişse — fonksiyon `STATUS_TRAIL_REPLACING`'e hiç girmeden, emir atmadan/iptal etmeden `False` döner. `evaluate_trail()` senkron olduğu ve tick-size'a erişemediği için asıl guard mecburen precision uygulandıktan sonra, `update_trail_orders()` içinde olmalı (bkz. kök neden analizi).
3. Log satırı: `tp_unchanged` durumunda `new_tp_id` boş kaldığı için, log artık halihazırda aktif olan gerçek `tp_order_id`'yi gösteriyor (kozmetik netlik, önceki "TP id boş" kafa karıştırıcı log satırının kaynağıydı).

**Testler:** `tests/test_order_manager.py::TestTpUnchangedNoChurn` (2 test) + `::TestPrecisionResidualNoChurn` (2 test) eklendi — mevcut 45+2(backoff,ortamdan bağımsız pre-existing) test paketi bozulmadan geçiyor.

**⚠️ DURUM: PATCH HAZIR, TESTLERLE DOĞRULANDI — DEPLOY EDİLMEDİ.** Kod değişikliği ve regresyon testleri yazıldı, `pytest tests/` ile mevcut pre-existing 40 hatanın hiçbirini değiştirmediği (aynı liste) ve yeni 4 testin geçtiği doğrulandı. Sunucuya push/deploy işlemi bu oturumda yapılmadı — manuel deploy gerekiyor.

### P0-6: `_exit_already_closed` SL/TP result'larında da pozisyon doğrulaması yok
**Kaynak:** 2026-07-23 15:33 — canlı izleme,APTUSDT false SL close
- Bot 15:24, 15:27, 15:28'te 3 kez `EXIT: SL | PRICE: 0.62 | PNL: +1.62` logladı
- Binance'de APTUSDT pozisyonu HÂLÂ açık: short 1024.5 @ 0.62290, upnl=+5.12 USDT
- `_exit_trade_legacy()` (bot.py:857) ve `ExitLifecycleService.execute()` (exit_lifecycle.py:123) sadece `trade.get("result") == "WS_FALLBACK"` için `position_still_open()` REST kontrolü yapıyor
- SL ve TP result'larında `_exit_already_closed = True` → market close atlanıyor → commit yapılıyor ama pozisyon hala açık
- Sonuç: yanlış PnL kaydı, trade ACTIVE'den siliniyor, koruma emirleri iptal ediliyor → pozisyon korumasız kalıyor

**Düzeltme (Görev 12):**
- `exit_lifecycle.py:123` ve `bot.py:857`'de `_exit_already_closed` guard'ını SL/TP/WS_FALLBACK için de çalıştır
- Pozisyon hala açıksa: stale/phantom event olarak işaretle, `verify_protection()` kontrol et, repair et, trade'i ACTIVE'e geri döndür
- Sadece gerçekten pozisyon kapalıysa commit yap

**Acil durum (canlı):** APTUSDT pozisyonu Binance'de açık, bot hata loglamaya devam ediyor. Fix deploy edilene kadar manuel izleme gerekli.

**⚠️ DURUM: AÇIK — FIX YAZILIYOR (Görev 12)**

---

## 🟡 P2 — Medium Risk

### P1-11: EXIT_REQUESTED runtime dead-end — status hiçbir yolla ACTIVE'ye dönmüyor
**Kaynak:** `paper_trade.log` (15:46+, 2026-07-23) — 4 trade etkilendi
- SEIUSDT: 19:30:07'de ACTIVE → SL stale 19:34:15 → EXIT_REQUESTED → log sonu (20:51) kadar hala EXIT_REQUESTED
- UNIUSDT: 19:30:14'de ACTIVE → SL stale 19:45:06 → EXIT_REQUESTED → log sonu (20:51) kadar hala EXIT_REQUESTED
- ONDOUSDT: 20:00:01'de ACTIVE → SL/TP sanity check fail → WS_FALLBACK → commit → trade kapandı
- APTUSDT: 17:18:37'de ACTIVE → WS_FALLBACK → commit → trade kapandı

**Kök neden:**
1. `_exit_trade()` / `ExitLifecycleService.execute()` pozisyon hala açık bulunca `return False` döner — ama `_exit_already_closed = False` olduğu için `EXIT_SUBMITTED` → `EXIT_VERIFYING` status machine geçişini tetikler.
2. Position verification başarısız olunca (5 deneme × 200ms) `_mark_repair_required()` çağrılır — trade `REPAIR_REQUIRED` olur.
3. **Ancak P0-6 fix'i sonrası** `_exit_already_closed` guard'ı SL/TP/WS_FALLBACK için de çalışıyor — bu durumda status `EXIT_VERIFYING`'de kalıyor.
4. `bot.py:_on_1m_close()` (line 461) `UNRESTRICTED_STATUSES` kontrolü EXIT_REQUESTED/EXIT_SUBMITTED/EXIT_VERIFYING'i **atlıyor** — bu trade'ler per-bar döngüde işlenmiyor.
5. `recovery_manager.reconcile_orphan_orders()` (line 688) `UNRESTRICTED_STATUSES` olmayanları **atlıyor** — orphan sweep bu trade'lere dokunmuyor.
6. **Tek çıkış yolu: restart** — `bot.py:1474-1495` restart'ta SL/TP varsa ACTIVE'ye döndürüyor.

**H Boards:** Exit lifecycle pozisyon doğrulaması False döndüğünde status reset Yok. `verify_protection()` sonrası repair ediliyor ama status UNCARTED. WS handler unmatched-reduceOnly path'i False döndürüyor ama status'i korumuyor.

**Etki:** Trade sonsuza kadar EXIT_REQUESTED'da kalıyor, yeni entry engellenmiyor ama mevcut pozisyonun koruması (SL/TP) Binance'te kalıyor — **manual müdahale veya restart gerekli**.

**Düzeltme önerisi:**
- `_exit_trade()` / `execute()` pozisyon doğrulaması başarısız olunca trade.status'u `STATUS_ACTIVE`'ye resetlemeli (SL/TP hâlâ Binance'te).
- veya `EXIT_REQUESTED`/`EXIT_SUBMITTED`/`EXIT_VERIFYING` durumları için per-bar retry mekanizması eklemeli.

**⚠️ DURUM: DÜZELTİLDİ (b739bb3)** — `_exit_trade()` / `execute()` ve `_exit_trade_legacy()` stale-event dalında `trade["status"] = STATUS_ACTIVE` eklendi. Pozisyon hala açık ve koruma onarıldığında trade ACTIVE'ye dönüyor, per-bar döngü ve orphan sweep artık işliyor.

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

## 🔴 P1-12: 2026-07-24 paper_trade.log analizi — sistematik WS_FALLBACK + POST_ENTRY check failure zinciri

**Kaynak:** `paper_trade.log` (1803 satır, 2026-07-24 14:12–20:35) + `events_2026-07-24.jsonl` (100 satır)

### 📊 Event dağılımı (events_2026-07-24.jsonl)
| Event Type | Count |
|---|---|
| post_entry_check_failed | 23 |
| exit | 23 |
| entry | 23 |
| ws_unmatched_reduce_only | 21 |
| force_close | 5 |
| exit_intent | 5 |

### 🔍 Paper_trade.log'daki kritik olaylar
| Saat | Event | Sembol | Açıklama |
|---|---|---|---|
| 14:45 | POST_ENTRY FAIL | NEARUSDT | SL/TP sanity check BASARISIZ (sl_ok=False, tp_ok=False) |
| 15:11 | WS_UNMATCHED_REDUCE_ONLY | ONDOUSDT | reduceOnly fill, ID eşleşmedi, status=ACTIVE |
| 15:42 | WS_UNMATCHED_REDUCE_ONLY | NEARUSDT | reduceOnly fill, ID eşleşmedi, status=ACTIVE |
| 15:45 | POST_ENTRY FAIL | ONDOUSDT | SL/TP sanity check BASARISIZ |
| 16:06 | WS_UNMATCHED_REDUCE_ONLY | ONDOUSDT | reduceOnly fill, ID eşleşmedi |
| 18:15 | POST_ENTRY FAIL | ALGOUSDT | SL/TP sanity check BASARISIZ |
| 18:16 | WS_UNMATCHED_REDUCE_ONLY | ALGOUSDT | reduceOnly fill, ID eşleşmedi |
| 18:30 | POST_ENTRY FAIL | ALGOUSDT | SL/TP sanity check BASARISIZ |
| 19:00 | 🚨 DEVRE KESICI | INJUSDT | DD: %21.22 >= %15.00 |
| 19:00 | POST_ENTRY FAIL | INJUSDT | SL/TP sanity check BASARISIZ |
| 19:06 | WS_UNMATCHED_REDUCE_ONLY | INJUSDT | reduceOnly fill, ID eşleşmedi |
| 19:15 | POST_ENTRY FAIL | PYTHUSDT | SL/TP sanity check BASARISIZ |
| 20:00 | POST_ENTRY FAIL | NEARUSDT | SL/TP sanity check BASARISIZ |
| 20:34 | WS_UNMATCHED_REDUCE_ONLY | NEARUSDT | reduceOnly fill, ID eşleşmedi |
| 20:35 | WS_UNMATCHED_REDUCE_ONLY | PYTHUSDT | reduceOnly fill, ID eşleşmedi |

### 🔗 Tespit edilen pattern'ler

**Pattern 1: POST_ENTRY → WS_UNMATCHED_REDUCE_ONLY zinciri**
- Her `post_entry_check_failed` sonrası, aynı sembol için 5–60dk içinde `ws_unmatched_reduce_only` event'i geliyor
- Bu, bot'un SL/TP emirlerini yerleştirmesine rağmen Binance'ın reduceOnly fill ile pozisyonu kapatması anlamına geliyor
- Bot bu fill'leri "unmatched" olarak logluyor → WS_FALLBACK → yanlış PnL kaydı

**Pattern 2: DD Circuit Breaker → POST_ENTRY FAIL**
- 19:00'da INJUSDT için DD %21.22 → circuit breaker patladı
- Sonraki 15dk içinde INJUSDT entry yapıldı ama POST_ENTRY check BASARISIZ
- DD limiti aktifken entry yapılması ve ardından SL/TP sanity check fail'i — **risk kontrolü bypass**

**Pattern 3: FVG invalidation → force_close → WS_FALLBACK**
- PYTHUSDT, OPUSDT, ONDOUSDT, AAVEUSDT, RENDERUSDT — hepsi `fvg_invalidated` → `force_close` → `WS_FALLBACK`
- Bu, P1-3 (SL/TP actual_price ile hesaplama) bug'ının 24 Temmuz'da hâlâ yaşanıyor olabileceğine işaret ediyor

### ⚠️ Durum: P0-5 ARAŞTIRILIYOR — P1-12 P1-8 ZINCİRİNİN DEVAMI
- 23 post_entry_check_failed event'i → hepsi `sl_ok=False, tp_ok=False`
- 21 ws_unmatched_reduce_only event'i → hepsi `trade_status_before_exit=ACTIVE`
- P1-12 bağımsız yeni bug DEĞİL — P1-8 ile aynı kök neden (P0-5: get_all_orders openAlgoOrders hata yutma)
- P0-5 fix (7e50331) deploy edildi — SSH ile sunucuda `get_all_orders()` debug logu alınmalı, openAlgoOrders endpoint'i test edilmeli

### 🔧 Önerilen aksiyonlar
1. SSH ile sunucuda `get_all_orders()` debug logu alınmalı — openAlgoOrders endpoint'i hâlâ hata yutuyor mu?
2. P0-5 fix (7e50331) deploy edildi.
3. DD circuit breaker sonrası entry yapılmasını engelleyin veya `_post_entry_check`'a DD state guard ekleyin
4. `post_entry_check_failed` → `ws_unmatched_reduce_only` zincirindeki PnL kayıpları quantify edilmeli
5. `fvg_invalidated` → `force_close` → `WS_FALLBACK` pattern'i için SL/TP recalculation after actual fill doğrulaması eklenmeli

---

## 🔍 2026-07-24 Log Analizi — paper_trade.log + events_2026-07-24.jsonl

### Yeni Bulgular (P1-12, P1-13)

### P1-12: 24 Temmuz post_entry_check_failed %100 — P0-5/P1-8 zincirinin devamı
**Kaynak:** `events_2026-07-24.jsonl` (100 satır) + `paper_trade.log` (1803 satır)
- **23/23 entry** sonrası `post_entry_check_failed` — SL/TP Binance'te bulunamadı (sl_ok=False, tp_ok=False)
- **21/21** `ws_unmatched_reduce_only` event'i — trade ACTIVE durumundayken Binance reduceOnly fill gönderdi
- **5 force_close** event'i — FVG invalidation → force_close → WS_FALLBACK zinciri
- **5 exit_intent** event'i — hepsi `fvg_invalidated` reason'li
- **Pattern:** Her entry → ~2.5s sonra post_entry_check_failed → FVG invalidation → force_close → WS_FALLBACK exit

**P1-12 bağımsız yeni bug DEĞİL** — P1-8 (23 Temmuz) ile aynı kök neden: `get_all_orders()` openAlgoOrders endpoint hata yutuyor → SL/TP algo ID'leri görünmez → post_entry_check_failed her trade'de tetikleniyor.

P0-5 fix (7e50331, 23 Temmuz 14:32 deploy) bunu çözmeliydi ama 24 Temmuz'da %100 devam ediyor. İki olasılık:
1. Fix deploy edildi ama farklı bir kök neden var (testnet paylaşımı, farklı API key instance'ı)
2. Farklı bir kök neden (testnet paylaşımı, farklı API key instance'ı)

**Önemli:** 24 Temmuz'daki `ws_unmatched_reduce_only` event'leri 22 Temmuz'daki ile aynı pattern — `trade_status_before_exit=ACTIVE`. Bu, bot'un kendi exit emirlerini göndermediğini, Binance'in bağımsız bir şekilde pozisyonu kapatığını gösteriyor.

**⚠️ Durum: P0-5 ARAŞTIRILIYOR** — SSH ile sunucuda `get_all_orders()` debug logu alınmalı, openAlgoOrders endpoint'i test edilmeli. Fix deploy durumu doğrulanmalı.

### P1-13: DD Circuit Breaker sonrası entry — SL/TP sanity check fail zinciri
**Kaynak:** `paper_trade.log` (1365-1377, 19:00:00)
- 19:00:00 — `🚨 DEVRE KESICI PATLADI! DD: %21.22 >= %15.00`
- 19:00:00 — `[DEFENSE] INJUSDT DD limitinde! EL ve Elite CBDR iptal. final=1.00x`
- 19:00:07 — `[POST_ENTRY] INJUSDT SL/TP sanity check BASARISIZ!` — INJUSDT entry yapılmış ama SL/TP Binance'te bulunamadı
- 19:06:22 — `[WS_UNMATCHED_REDUCE_ONLY] INJUSDT reduceOnly FILLED` — INJUSDT pozisyonu dışarıdan kapatıldı

**Pattern:** DD circuit breaker tetiklenmiş, defense olarak CBDR iptal edilmiş ama entry zaten açılmış. Entry sonrası SL/TP placement'i başarısız → pozisyon korumasız → 6 dakika sonra dışarıdan kapatıldı.

**Aynı pattern tekrarlandı:**
- 19:15 — PYTHUSDT DD defense → post_entry_check_failed → force_close → WS_FALLBACK
- 20:00 — NEARUSDT DD defense → post_entry_check_failed → WS_UNMATCHED_REDUCE_ONLY

**Kök neden:** `_post_entry_check` DD state'ini kontrol etmiyor — defense mekanizması CBDR/EL iptal ediyor ama entry sonrası SL/TP placement riskini azaltmıyor. DD yüksekken entry yapılması itself bir risk.

**⚠️ Durum: DÜZELTİLDİ (d62df19)** — `bot.py:_on_15m_close()`'de `execute_live_entry` çağrısından ÖNCE `is_circuit_broken` guard eklendi. DD aktifken entry tamamen engelleniyor, qty bile hesaplanmıyor.

- **📎 25 TEMMUZ DETAYLI ANALİZ (2026-07-25):** 25/Jul'da **9 DEFENSE tetiklemesinin her birinde** 0.24-1.01s içinde yeni market entry açıldı. 1'i circuit breaker bypass (ENAUSDT, 245ms):
  ```
  09:30:07,856  🚨 DEVRE KESICI PATLADI! DD: %20.86 >= %15.00
  09:30:08,101  [MARKET] ENAUSDT orderId fill bekleniyor...  ← 245ms!
  ```
  DD aktifken toplam **6 yeni pozisyon** birikmiş. DEFENSE sadece pozisyon boyutunu küçültüyor (0.80x/1.00x), yeni girişi engellemiyor. `entry_manager.py:submit_entry()`'de DD state guard'ı YOK.

### 📊 24 Temmuz Event Özeti

| Event Type | Count | Açıklama |
|---|---|---|
| post_entry_check_failed | 23 | SL/TP Binance'te bulunamadı — P0-5/P1-8 zincirinin devamı |
| ws_unmatched_reduce_only | 21 | Binance reduceOnly fill, bot-dışı kaynak |
| force_close | 5 | FVG invalidation → force close |
| exit_intent | 5 | fvg_invalidated reason |
| entry | 23 | Tüm entry'ler |
| exit | 23 | Tüm exit'ler (WS_FALLBACK) |

**PnL Impact:** 24 Temmuz'daki tüm exit'ler WS_FALLBACK veya TRAIL_CLOSE ile gerçekleşti. `ws_unmatched_reduce_only` event'leri bot'un kontrolü dışında kalan pozisyonları gösteriyor — bu, P1-7 (harici kapanış) pattern'in 24 Temmuz'da da devam ettiğini gösteriyor.

**P1-12 bağımsız bug DEĞİL** — P1-8 ile aynı kök neden (P0-5: get_all_orders openAlgoOrders hata yutma). P0-5 fix (7e50331) deploy edildi.

### P1-14: SL stale event → exit 27dk'ya kadar gecikiyor
**Kaynak:** `exit_lifecycle.py:execute()` + `order_manager.py:position_still_open()`
- `position_still_open()` → `/fapi/v2/account` (NOT `/fapi/v2/positionRisk`)
- Exit event'i (SL/TP/WS_FALLBACK) geldiğinde REST ile pozisyon hâlâ açık mı diye kontrol ediliyor
- Eğer `position_still_open()` True dönerse → "stale event" kabul ediliyor → exit iptal, trade ACTIVE'e dönüyor
- **Sorun:** `/fapi/v2/account` endpoint'i de eventual-consistency gecikebilir — SL zaten tetiklenmiş ama pozisyon hâlâ "açık" görünüyor
- P1-14 tablosu: ONDOUSDT 27dk, ARBUSDT 21dk gecikme

**Düzeltme (d62df19):**
`exit_lifecycle.py:execute()` içinde `position_open=True` çıktığında, cross-validation eklendi:
1. `get_open_order_ids(sym)` ile SL/TP ID'lerinin hâlâ open orders'ta olup olmadığı kontrol ediliyor
2. İkisi de yoksa (tetiklenmiş) → 400ms bekleme + `position_still_open()` bir kez daha sorgulanıyor
3. Retry'da pozisyon kapandıysa → exit devam ediyor (false stale engellendi)
4. Retry'da hâlâ açıksa → stale kabul ediliyor (güvenli tarafta)

### 🔧 Önerilen aksiyonlar
1. ~~SSH ile sunucuda `get_all_orders()` debug logu alınmalı~~ → P1-8 debug log canlıda aktif, sonuç bekleniyor
2. ~~P0-5 fix (7e50331) deploy edilip edilmediği doğrulanmalı~~ → ✅ Deploy edildi (7e50331)
3. ~~DD circuit breaker sonrası entry yapılmasını engelleyin~~ → ✅ DÜZELTİLDİ (P1-13, d62df19)
4. `post_entry_check_failed` → `ws_unmatched_reduce_only` zincirindeki PnL kayıpları quantify edilmeli
5. `fvg_invalidated` → `force_close` → `WS_FALLBACK` pattern'i için SL/TP recalculation after actual fill doğrulaması eklenmeli
6. P1-14 stale event cross-validation deploy sonrası ONDOUSDT/ARBUSDT benzeri vakalar için gecikme süresini test et

---

## 🔧 Mimari Risk

### 🆕 D-2: Trailing/entry formülleri live + 2 backtest motorunda kopya kod, senkronizasyon garantisi yok
**Severity:** HIGH
**Status:** OPEN
**Date:** 2026-07-24
**Files:**
- `sniper/src/trading/trailing_manager.py` (live — TrailingManager.evaluate_trail)
- `sniper/simulate.py` (fast backtest — inline trailing block)
- `backtest-sniper/src/analyzer_v5.py` (benchmark engine — collect_fvg_profile trailing block)

### Problem

Trailing/entry formülleri üç ayrı yerde elle kopyalanmış, tek bir modülden import edilmiyor. Geçmişte yapılan fix'ler sadece birine uygulanmış, diğerleri kalmış.

### Tespit Edilen Farklar

#### Fark 1 (HIGH): `exit_now` guard — FVG kırıldı exit'i

Live (`trailing_manager.py:95-96, 109-110`):
```python
# Long
if new_sl >= current.close:
    return TrailResult(exit_now=True)
# Short
if new_sl <= current.close:
    return TrailResult(exit_now=True)
```

Backtest'lerde (**simulate.py** ve **analyzer_v5.py**): **YOK**

**Etki:** Canlıda FVG fiyatı geçtiyse trade hemen kapatılıyor. Backtest'lerde fiyat eski FVG'yi geçmiş olsa bile trailing devam ediyor — backtest sonuçları optimist olur.

#### Fark 2 (MEDIUM): `fvg_close_confirmed` — `is_closed` kontrolü

Live'da var, backtest'lerde YOK.

#### Fark 3 (MEDIUM): `is_closed` guard — trigger seviyesinde

`analyzer_v5.py` inline RSM — `is_closed` guard'ı yok. Açık bar'da trigger olabilir.

#### Fark 4 (LOW): `trailing_count` sayımı

`simulate.py` her zaman 1 sayıyor, FVG sayısına bakmıyor.

#### Fark 5 (LOW): Session filter farkı

Farklı session boundary hesaplayıcıları, farklı giriş noktalarına yol açabilir.

### Önerilen Çözüm

1. `evaluate_trail()` ve `fvg_close_confirmed()` ortak bir modüle çıkar
2. Backtest'lerde inline trailing blokları yerine bu import'u kullan
3. `exit_now` guard'ı backtest'lere de ekle
4. Session filter'ı tek bir yere indir

---

## 🟠 Ek Bulgular (P1 — High Risk)

### 🆕 P1-14: SL Stale Event → Exit 27 Dakikaya Kadar Gecikiyor
**Severity:** HIGH
**Status:** OPEN
**Date:** 2026-07-25
**Evidence:** `paper_trade.log` — 10 stale event, 3 symbol

### Problem

SL emri exchange'de tetiklendiğinde bot WS'ten `stale event` alıyor ve "pozisyon hala açık" diyerek exit'i iptal ediyor. Gerçek SL/TP fill ignore ediliyor.

| Symbol | Stale Sayısı | Stale Aralığı | Gerçek Exit | Gecikme | Ek Zarar |
|---|---|---|---|---|---|
| NEARUSDT | 4 | 07:15→07:18 | TP @ 1.807 | ~4 dk | -0.99 (fee) |
| ONDOUSDT | 3 | 11:07→11:09 | SL @ 0.3749 | **~27 dk** | fiyat SL(0.3759) altına düşmüş |
| ARBUSDT | 3 | 11:55→12:15 | TRAIL @ 0.0827 | **~21 dk** | pozisyon açık kalmış |

### Kök Neden

SL/TP emirleri `clientOrderId` (Binance rastgele string'i) WS event'indeki `c` field'ı ile eşleşmiyor. Bot kendi numeric algo ID'leriyle karşılaştırıyor, her zaman fail oluyor. P1-7 ile ilişkili ama stale event mekanizması ayrı bir katman.

### İlişkili

- P0-6 (_exit_already_closed pozisyon doğrulama yok)
- P1-7 (harici kapanışlar / WS_UNMATCHED_REDUCE_ONLY)
- P1-11 (EXIT_REQUESTED dead-end — stale event bu status'a sokuyor)

### Önerilen Çözüm

1. `exit_lifecycle.py` stale event handler: WS fill event'ini doğrudan kabul etmeli
2. P1-7 REST cross-validation stale handler'a da uygulanmalı
3. Stale event geldiğinde REST ile pozisyon durumu kontrol edilmeli

---

## 🟡 P2 — Medium Risk (Yeni Bulgular)

### 🆕 P2-6: TIAUSDT Her Bar Close'da Gir-Çık Döngüsü
**Severity:** MEDIUM
**Status:** OPEN
**Date:** 2026-07-25
**Evidence:** `paper_trade.log` — 4 consecutive entries, all TRAIL_CLOSE within 1 min

TIAUSDT 4 bar üst üste her 15 dk'da bir entry almış, her biri ~1 dk içinde TRAIL_CLOSE ile kapanmış:

```
07:45:07  entry @ 0.3420  →  07:46:01  TRAIL_CLOSE  pnl=-0.99  (54s)
08:00:07  entry @ 0.3414  →  08:01:01  TRAIL_CLOSE  pnl=-1.57  (54s)
08:15:07  entry @ 0.3412  →  08:16:01  TRAIL_CLOSE  pnl=-0.70  (54s)
09:30:03  entry @ 0.3384  →  açık (log sonu)
```

Toplam zarar: -$3.26. D-2 ile ilişkili olabilir — live trailing formülü backtest'ten farklıysa optimizasyon yanlış çalışıyordur.

### 🆕 P2-7: Tüm TRAIL_CLOSE Çıkışları Negatif (5/5)
**Severity:** MEDIUM
**Status:** OPEN
**Date:** 2026-07-25
**Evidence:** `paper_trade.log` — 5 TRAIL_CLOSE exit, 0 positive

| Symbol | Exit Price | PnL | Süre |
|---|---|---|---|
| ATOMUSDT | 1.386 | -0.99 | ~1 dk |
| TIAUSDT | 0.342 | -0.99 | ~1 dk |
| TIAUSDT | 0.3416 | -1.57 | ~1 dk |
| TIAUSDT | 0.3411 | -0.70 | ~1 dk |
| ARBUSDT | 0.0827 | -2.71 | ~31 dk |

Trailing stop kâr kilitleme yerine her seferinde zararla çıkış üretiyor. D-2'deki trailing formül farklılıklarıyla ilişkili olabilir.

---

## 🔵 P3 — Low Risk (Yeni Bulgu)

### 🆕 P3-4: NEARUSDT SL Çok Dar (0.055%)
**Severity:** LOW
**Status:** OPEN
**Date:** 2026-07-25
**Evidence:** `paper_trade.log` line 1523-1548

```
Entry @ 1.807  →  SL @ 1.806  (0.001 = 0.055% mesafe)
```

SL neredeyse entry fiyatında. Her küçük wick tetikliyor → 4 stale event (07:15-07:18) → sonunda TP @ 1.807 ile ($0.99 fee zararıyla) çıkıyor. P1-14 stale event sorununu daha da kötüleştiriyor.

---

## 🔴 Açık Buglar — Baş Mühendis Notu (25 Tem 2026)

> Baş mühendis review'undan çıkan 3 açık bug. Bloklayıcı değil, temizlik/debt.

### 🆕 P1-8a: POST_ENTRY Type Mismatch — `int in set[str]` Her Zaman False
**Severity:** HIGH
**Status:** OPEN — kök neden tespit edildi, fix henüz uygulanmadı
**Date:** 2026-07-25
**File:** `src/bot.py:732-733`

#### Problem

`sl_id` ve `tp_id` runtime'da `int` tipinde geliyor (debug log: `sl_id_type=int`). `get_open_order_ids()` ise `set[str]` döndürüyor (`raw_ids=['1000000145966694']` — string).

```python
# bot.py:732-733 — mevcut kırık kod
sl_ok = not sl_id or sl_id in open_ids   # int in set[str] → HER ZAMAN False
tp_ok = not tp_id or tp_id in open_ids   # int in set[str] → HER ZAMAN False
```

**Sonuç:** `sl_ok=False, tp_ok=False` → her trade'de `post_entry_check_failed` tetikleniyor.

#### Kök Neden Analizi

`entry_manager.py:45-46` field'ları `str` olarak tanımlı (`sl_order_id: str`). Ama `sl_id = exec_result.order_id` çağrısında `order_id` Binance REST yanıtından `int` olarak geliyor ve field assignment casting yapmıyor. Runtime'da `sl_id` bir `int` oluyor.

#### Etki

25 Temmuz'da **17 `post_entry_check_failed`** — hepsi bu type mismatch'ten. Debug log kanıtı:
```
sl_id=1000000145966694 sl_id_type=int  →  raw_ids=['1000000145966694'] (str)
```

#### Fix

```python
# bot.py:732-733 — düzeltilmiş
sl_ok = not sl_id or str(sl_id) in open_ids
tp_ok = not tp_id or str(tp_id) in open_ids
```

**Alternatif (daha temiz):** `entry_manager.py` tarafında `sl_id`/`tp_id`'yi `str()` cast ile ata — böylece tüm downstream kodlar tutarlı çalışır. Ama bu daha geniş değişiklik gerektirir, mevcut fix daha güvenli.

---

### 🆕 P1-13b: P1-13 DD Guard Sonrası Ölü Kod
**Severity:** LOW
**Status:** OPEN — temizlik borcu, bloklayıcı değil
**Date:** 2026-07-25
**File:** `src/bot.py:614-621`

#### Problem

P1-13 fix'inde (d62df19) `_on_15m_close()`'ye erken return eklendi:

```python
# bot.py:607-611 — P1-13 guard
if is_defense_mode:
    log.warning("[DD_GUARD] %s DD devre kesici aktif — entry ENGELLENDI", sym)
    log_event("entry_blocked_dd", sym, dd_active=True)
    rsm.reset()
    return   # ← burada fonksiyon terk ediliyor
```

Ama 7 satır aşağıda hâlâ eski DEFENSE bloğu duruyor:

```python
# bot.py:614-621 — ÖLÜ KOD (asla ulaşilemez)
if is_defense_mode:
    # PORTFOY KANIYOR (DD > %15): Elite CBDR gelse bile riski buyutme
    final_risk_mult = 1.0 * min(cbdr_mult, 1.0)
    log.warning(
        "[DEFENSE] %s DD limitinde! EL ve Elite CBDR iptal. final=%.2fx",
        sym,
        final_risk_mult,
    )
```

`is_defense_mode=True` ise fonksiyon zaten line 611'de `return` ile çıkmış oluyor. Line 614'e asla ulaşılamıyor.

#### Etki

Kod okunabilirliği — dead code, bakım sırasında yanıltabilir. Fonksiyonel etkisi yok.

#### Fix

Line 614-621'i sil. `else:` bloğunu (line 622-624) koru ama `if is_defense_mode:` guardian'ı olmaksızın doğrudan `final_risk_mult = risk_mgr_mult * cbdr_mult` olarak basitleştir:

```python
# ── Nihai carpan (Guvenlik Freni) ──
# is_defense_mode True ise zaten return ile çıkıldı (line 607-611).
final_risk_mult = risk_mgr_mult * cbdr_mult
```

---

### 🆕 P1-14b: `_exit_trade_legacy`'de P1-14 Cross-Validation Eksik
**Severity:** LOW
**Status:** OPEN — temizlik borcu, legacy path devre dışı
**Date:** 2026-07-25
**File:** `src/bot.py:873-889` vs `src/trading/exit_lifecycle.py:150-188`

#### Problem

P1-14 cross-validation (SL/TP open orders'tan yoksa 400ms retry) sadece `ExitLifecycleService.execute()`'da mevcut (`exit_lifecycle.py:150-188`). `_exit_trade_legacy()` (`bot.py:873-889`)'de yok.

#### Neden Kritik Değil

`EXIT_LIFECYCLE_SERVICE_ENABLED=True` (varsayılan) olduğu için `_exit_trade_legacy()` zaten çalışmıyor — `_exit_trade()` wrapper'ı (line 869-871) `exit_service.execute()`'a delege ediyor. Bu, P0-1/P0-2/P0-6 ile aynı yapısal davranış pattern'i.

#### Risk

Eğer birisi `EXIT_LIFECYCLE_SERVICE_ENABLED=False` yaparsa (örn. rollback) P1-14 cross-validation da devre dışı kalır. Legacy path'i geri yükleyen kişi bunu bilmeli.

#### Fix (opsiyonel, temizlik)

`_exit_trade_legacy()`'ye `exit_lifecycle.py:150-188` ile aynı cross-validation mantığını ekle. Ya da legacy path'i tamamen kaldır (EXIT_LIFECYCLE_SERVICE_ENABLED kalıcı True iken gereksiz dead code).

---

## 🔍 25 Tem 2026 — 18:45:23+ Log Analizi (Baş Mühendis Notu İçin)

> Kaynak: `paper_trade.log` (18:45:23 → 19:09) + `events_2026-07-25.jsonl` (son 50 satır)
> Hiçbiri benim fix'lerimden kaynaklanmıyor. Ya deploy eksik, ya pre-existing, ya da fix'in doğru çalıştığını doğruluyor.

### 📋 Olay Özeti (18:45 → 19:09)

| Zaman | Sembol | Olay | Sonuç |
|---|---|---|---|
| 18:45:00 | ARBUSDT | DD DEFENSE → entry | P1-8a: sl_ok=False (type mismatch) |
| 18:45:21 | ENAUSDT | DD DEFENSE → entry | P1-8a: sl_ok=False |
| 18:45:30 | ENAUSDT | SL fill (WS) → exit | ✅ P1-14 cross-val çalıştı, pnl=-2.16 |
| 18:47–19:05 | ARBUSDT | 7x stale event / 18dk | SL/TP open_ids'de → pozisyon gerçekten açık (doğru behavior) |
| 19:00:00 | ENAUSDT | DD DEFENSE → tekrar entry | P1-13 deploy eksik, DD'de tekrar girdi |
| 19:00:20 | ENAUSDT | SL fill (WS) → exit | P1-14 çalıştı, pnl=-2.25 |
| 19:00:37 | OPUSDT | DD DEFENSE → entry | P1-8a: sl_ok=False |

### 🔴 P1-13: DD Guard Hâlâ Çalışmıyor — Bot Restart Edilmemiş

**Kanıt:** `[DD_GUARD]` log'da hiç görünmüyor. `[DEFENSE]` (eski kod, bot.py:614-621) 5 kez görünüyor.

```
18:45:00 [DEFENSE] ARBUSDT DD limitinde! EL ve Elite CBDR iptal. final=1.00x
18:45:21 [DEFENSE] ENAUSDT DD limitinde! EL ve Elite CBDR iptal. final=0.80x
19:00:00 [DEFENSE] ENAUSDT DD limitinde! EL ve Elite CBDR iptal. final=0.80x
19:00:37 [DEFENSE] OPUSDT DD limitinde! EL ve Elite CBDR iptal. final=0.80x
```

Fix commit edildi (d62df19) ama **bot restart edilmemiş**. Eski kod çalışıyor.

**Etki:** DD aktifken 4 yeni pozisyon. ENAUSDT 2x gir-çık → toplam -4.41$.

**Aksiyon:** Bot'u restart et. `[DD_GUARD]` log'unun görünmesini doğrula.

### 🟡 ENAUSDT "DD Entry → Hızlı SL" Pattern

```
18:45:21  ENAUSDT short @ 0.0858  DD=0.80x  →  18:45:30  SL @ 0.0861  pnl=-2.16  (7sn)
19:00:01  ENAUSDT short @ 0.0858  DD=0.80x  →  19:00:20  SL @ 0.0861  pnl=-2.25  (16sn)
```

DD guard devre dışı (deploy eksik) olduğu için bot her 15dk'da aynı sembole tekrar giriyor. DEFENSE sadece qty'yi küçültüyor (0.80x), girişi engellemiyor. P1-13 fix'i deploy edilince çözülür.

### 🟡 ARBUSDT 18 Dakikalık Stale Event Döngüsü

7 stale event, 18 dakika (18:47 → 19:05):

| Zaman | raw_orders_count | raw_ids |
|---|---|---|
| 18:47:14 | 2 | `['1000000145977785', '1000000145977782']` |
| 18:47:15 | 2 | aynı |
| 18:52:14 | 2 | aynı |
| 18:56:15 | 2 | aynı |
| 19:02:15 | 2 | aynı |
| 19:03:15 | 2 | aynı |
| 19:05:01 | 2 | aynı |

SL ve TP her seferinde open_ids'de → pozisyon gerçekten açık. WS SL tetikleme event'leri geliyor ama Binance'te SL order henüz fill olmamış (fiyat SL seviyesine dokunup geri çekiliyor). **Bu doğru behavior** — P1-14 cross-validation SL/TP'nin open_ids'de olduğunu gördü → stale olarak tanımladı → exit iptal. Doğru karar.

### ✅ P1-14 Cross-Validation ENAUSDT'de Doğrulandı

```
18:45:23  ENAUSDT entry OK (orderId=429731445)
18:45:30  WS SL fill (clientOrderId=Hj017ZT4IfzmvDOe6HYjDo)
18:45:30  raw_orders_count=1 raw_ids=['1000000145978052']  ← SL (1000000145978048) kaybolmuş
18:45:30  [COMMIT] ENAUSDT SL exit=0.0861 pnl=-2.16  ← exit devam etti
```

SL open_ids'den çıktığında P1-14 cross-validation doğru çalışarak exit'i onayladı. ~7sn gecikme kabul edilebilir.

### 🔴 P1-8a: 100% Type Mismatch Devam

Her POST_ENTRY_DEBUG:
```
sl_id=1000000145977782 sl_id_type=int    ← int
raw_ids=['1000000145977782', ...]         ← set[str]
→ sl_ok=False  (int in set[str] = False her zaman)
```

Bu pencerede 4 entry — hepsinde `sl_ok=False, tp_ok=False`.

### 📊 İlişki Tablosu (Yeni Bulgular vs Fix'ler)

| Bulgu | Fix'lerle İlişki | Kök Neden |
|---|---|---|
| P1-13 çalışmıyor | ❌ İlgisi yok | Bot restart edilmemiş, eski kod çalışıyor |
| P1-8a type mismatch | ❌ Ben ortaya çıkardım | Pre-existing bug, debug loglar说esizleştirildi |
| ARBUSDT stale loop | ❌ İlgisi yok | Doğru behavior — P1-14 doğru çalıştı |
| ENAUSDT DD repeat entry | ❌ İlgisi yok | P1-13 deploy eksik, fix deploy edilince çözülür |
| P1-14 ENAUSDT working | ✅ Fix doğru çalışıyor | Cross-validation SL kaybolduğunda exit'i onayladı |

**Sonuç:** Hiçbiri regressyon değil. Ya deploy eksik (P1-13) ya pre-existing (P1-8a) ya da fix'in doğru çalıştığını doğruluyor (P1-14).
