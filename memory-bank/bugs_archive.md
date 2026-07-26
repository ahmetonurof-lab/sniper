# Bug Archive — sniper/src/

> **Oluşturulma:** 2026-07-25 22:15 — bugs.md'den taşındı (yeniden bölme).
> Bu dosyadaki maddelerin tamamı DÜZELTİLDİ, KALDIRILDI veya DOĞRULANDI durumundadır.
> Önceki bölme denemesi yarım kalmıştı; bu dosya orijinal içeriğin tamamını içerir.

---

## 🔴 P0 — Finance Risk (Arşiv)

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
- **⚠️ DURUM: DÜZELTİLDİ** — `reconcile_ghost_positions()` (state-file temizliği) gerçekten hâlâ sadece `run()` içinde bir kez çalışıyor. Ama artık `RecoveryManager.periodic_check_loop()` her 60sn'de `recover_positions(quiet=True)` + `reconcile_orphan_orders()` çalıştırıyor; `recover_positions()` Binance'teki pozisyonları doğrudan sorgulayıp `active_trades`'te olmayan/korumasız pozisyonları tekrar SL/TP ile donatıyor — "SL 2 saat 46 dk yalnız kalır" senaryosu artık ~60sn içinde yakalanır. Ayrıca `bot.py:run()`'a restart'ta `REPAIR_REQUIRED`/`EXIT_REQUESTED` trade'leri SL/TP sağlıklıysa `ACTIVE`'e döndüren temizlik eklenmiş. REPAIR_REQUIRED'e özel bir retry döngüsü hâlâ yok ama pratik risk periyodik `recover_positions` ile büyük ölçüde azalmış.

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

---

## 🟠 P1 — High Risk (Arşiv)

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

### P1-8a: POST_ENTRY Type Mismatch — `int in set[str]` Her Zaman False
**Severity:** HIGH
**Status:** ✅ FIX DEPLOY + DOĞRULANDI (e45d0a9) — `extract_order_id()` str cast + bot.py:732-733 str() cast. 21:02 SEIUSDT temiz TP çıkışı (pnl=+4.11) ile canlı doğrulandı. `sl_ok=False` false positive artık yok.
**Date:** 2026-07-25
**File:** `src/bot_infra.py:85`, `src/bot.py:732-733`

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

### P1-14: SL stale event → exit 27dk'ya kadar gecikiyor
**Severity:** HIGH
**Status:** ✅ DÜZELTİLDİ (d62df19)
**Date:** 2026-07-25
**Evidence:** `paper_trade.log` — 10 stale event, 3 symbol

#### Problem

SL emri exchange'de tetiklendiğinde bot WS'ten `stale event` alıyor ve "pozisyon hala açık" diyerek exit'i iptal ediyor. Gerçek SL/TP fill ignore ediliyor.

| Symbol | Stale Sayısı | Stale Aralığı | Gerçek Exit | Gecikme | Ek Zarar |
|---|---|---|---|---|---|
| NEARUSDT | 4 | 07:15→07:18 | TP @ 1.807 | ~4 dk | -0.99 (fee) |
| ONDOUSDT | 3 | 11:07→11:09 | SL @ 0.3749 | **~27 dk** | fiyat SL(0.3759) altına düşmüş |
| ARBUSDT | 3 | 11:55→12:15 | TRAIL @ 0.0827 | **~21 dk** | pozisyon açık kalmış |

#### Kök Neden

SL/TP emirleri `clientOrderId` (Binance rastgele string'i) WS event'indeki `c` field'ı ile eşleşmiyor. Bot kendi numeric algo ID'leriyle karşılaştırıyor, her zaman fail oluyor. P1-7 ile ilişkili ama stale event mekanizması ayrı bir katman.

#### İlişkili

- P0-6 (_exit_already_closed pozisyon doğrulama yok)
- P1-7 (harici kapanışlar / WS_UNMATCHED_REDUCE_ONLY)
- P1-11 (EXIT_REQUESTED dead-end — stale event bu status'a sokuyor)

#### Düzeltme (d62df19)

`exit_lifecycle.py:execute()` içinde `position_open=True` çıktığında, cross-validation eklendi:
1. `get_open_order_ids(sym)` ile SL/TP ID'lerinin hâlâ open orders'ta olup olmadığı kontrol ediliyor
2. İkisi de yoksa (tetiklenmiş) → 400ms bekleme + `position_still_open()` bir kez daha sorgulanıyor
3. Retry'da pozisyon kapandıysa → exit devam ediyor (false stale engellendi)
4. Retry'da hâlâ açıksa → stale kabul ediliyor (güvenli tarafta)

#### Doğrulama (25 Tem 18:45+)

```
18:45:23  ENAUSDT entry OK (orderId=429731445)
18:45:30  WS SL fill (clientOrderId=Hj017ZT4IfzmvDOe6HYjDo)
18:45:30  raw_orders_count=1 raw_ids=['1000000145978052']  ← SL (1000000145978048) kaybolmuş
18:45:30  [COMMIT] ENAUSDT SL exit=0.0861 pnl=-2.16  ← exit devam etti
```

SL open_ids'den çıktığında P1-14 cross-validation doğru çalışarak exit'i onayladı. ~7sn gecikme kabul edilebilir.

---

## 📊 Destekleyici Analiz

### P1-12: 2026-07-24 paper_trade.log analizi — sistematik WS_FALLBACK + POST_ENTRY check failure zinciri

**Kaynak:** `paper_trade.log` (1803 satır, 2026-07-24 14:12–20:35) + `events_2026-07-24.jsonl` (100 satır)

#### Event dağılımı (events_2026-07-24.jsonl)
| Event Type | Count |
|---|---|
| post_entry_check_failed | 23 |
| exit | 23 |
| entry | 23 |
| ws_unmatched_reduce_only | 21 |
| force_close | 5 |
| exit_intent | 5 |

#### Paper_trade.log'daki kritik olaylar
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

#### Tespit edilen pattern'ler

**Pattern 1: POST_ENTRY → WS_UNMATCHED_REDUCE_ONLY zinciri**
- Her `post_entry_check_failed` sonrası, aynı sembol için 5–60dk içinde `ws_unmatched_reduce_only` event'i geliyor

**Pattern 2: DD Circuit Breaker → POST_ENTRY FAIL**
- DD limiti aktifken entry yapılması ve ardından SL/TP sanity check fail'i — **risk kontrolü bypass**

**Pattern 3: FVG invalidation → force_close → WS_FALLBACK**

**⚠️ Durum: P1-12 bağımsız yeni bug DEĞİL** — P1-8 ile aynı kök neden (P0-5: get_all_orders openAlgoOrders hata yutma). P0-5 fix (7e50331) deploy edildi.

#### 📊 24 Temmuz Event Özeti

| Event Type | Count | Açıklama |
|---|---|---|
| post_entry_check_failed | 23 | SL/TP Binance'te bulunamadı — P0-5/P1-8 zincirinin devamı |
| ws_unmatched_reduce_only | 21 | Binance reduceOnly fill, bot-dışı kaynak |
| force_close | 5 | FVG invalidation → force close |
| exit_intent | 5 | fvg_invalidated reason |
| entry | 23 | Tüm entry'ler |
| exit | 23 | Tüm exit'ler (WS_FALLBACK) |

**PnL Impact:** 24 Temmuz'daki tüm exit'ler WS_FALLBACK veya TRAIL_CLOSE ile gerçekleşti. `ws_unmatched_reduce_only` event'leri bot'un kontrolü dışında kalan pozisyonları gösteriyor — bu, P1-7 (harici kapanış) pattern'in 24 Temmuz'da da devam ettiğini gösteriyor.

### 🔍 25 Tem 2026 — 18:45:23+ Log Analizi (Baş Mühendis Notu İçin)

> Kaynak: `paper_trade.log` (18:45:23 → 19:09) + `events_2026-07-25.jsonl` (son 50 satır)

#### 📋 Olay Özeti (18:45 → 19:09)

| Zaman | Sembol | Olay | Sonuç |
|---|---|---|---|
| 18:45:00 | ARBUSDT | DD DEFENSE → entry | P1-8a: sl_ok=False (type mismatch) |
| 18:45:21 | ENAUSDT | DD DEFENSE → entry | P1-8a: sl_ok=False |
| 18:45:30 | ENAUSDT | SL fill (WS) → exit | ✅ P1-14 cross-val çalıştı, pnl=-2.16 |
| 18:47–19:05 | ARBUSDT | 7x stale event / 18dk | SL/TP open_ids'de → pozisyon gerçekten açık (doğru behavior) |
| 19:00:00 | ENAUSDT | DD DEFENSE → tekrar entry | P1-13 deploy eksik, DD'de tekrar girdi |
| 19:00:20 | ENAUSDT | SL fill (WS) → exit | P1-14 çalıştı, pnl=-2.25 |
| 19:00:37 | OPUSDT | DD DEFENSE → entry | P1-8a: sl_ok=False |

#### P1-14 Cross-Validation ENAUSDT'de Doğrulandı

```
18:45:23  ENAUSDT entry OK (orderId=429731445)
18:45:30  WS SL fill (clientOrderId=Hj017ZT4IfzmvDOe6HYjDo)
18:45:30  raw_orders_count=1 raw_ids=['1000000145978052']  ← SL (1000000145978048) kaybolmuş
18:45:30  [COMMIT] ENAUSDT SL exit=0.0861 pnl=-2.16  ← exit devam etti
```

SL open_ids'den çıktığında P1-14 cross-validation doğru çalışarak exit'i onayladı. ~7sn gecikme kabul edilebilir.

#### 📊 İlişki Tablosu (Yeni Bulgular vs Fix'ler)

| Bulgu | Fix'lerle İlişki | Kök Neden |
|---|---|---|
| P1-13 çalışmıyor | ❌ İlgisi yok | Bot restart edilmemiş, eski kod çalışıyor |
| P1-8a type mismatch | ❌ Ben ortaya çıkardım | Pre-existing bug, debug loglar说esizleştirildi |
| ARBUSDT stale loop | ❌ İlgisi yok | Doğru behavior — P1-14 doğru çalıştı |
| ENAUSDT DD repeat entry | ❌ İlgisi yok | P1-13 deploy eksik, fix deploy edilince çözülür |
| P1-14 ENAUSDT working | ✅ Fix doğru çalışıyor | Cross-validation SL kaybolduğunda exit'i onayladı |

**Sonuç:** Hiçbiri regressyon değil. Ya deploy eksik (P1-13) ya pre-existing (P1-8a) ya da fix'in doğru çalıştığını doğruluyor (P1-14).

### 🔧 Önerilen aksiyonlar (çözülen)
1. ✅ ~~SSH ile sunucuda `get_all_orders()` debug logu alınmalı~~ → P1-8 debug log canlıda aktif, sonuç bekleniyor
2. ✅ ~~P0-5 fix (7e50331) deploy edilip edilmediği doğrulanmalı~~ → ✅ Deploy edildi (7e50331)
3. ✅ ~~DD circuit breaker sonrası entry yapılmasını engelleyin~~ → ✅ DÜZELTİLDİ (P1-13, d62df19)
6. P1-14 stale event cross-validation deploy sonrası ONDOUSDT/ARBUSDT benzeri vakalar için gecikme süresini test et

---

## 🟡 P2 — Medium Risk (Arşiv)

### P2-1: `ProtectionLifecycleService.maybe_repair()` ölü kod
**Dosya:** `sniper/src/trading/protection_lifecycle.py:157`
- `tests/test_protection_lifecycle.py` dışında HİÇBİR YERDEN çağrılmıyor.
- `is_sweep_consumed()` ile aynı kader.
- Asıl repair kararları inline veriliyor.
- **⚠️ DURUM: DOĞRULANDI** — `maybe_repair()` sadece tanımlı, hiçbir yerden çağrılmıyor.

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

## 🔴 P0 — Finance Risk (Arşiv)

### P0-1: STRKUSDT çift-exit/çift-PnL — TAMAMEN DÜZELTİLDİ (440125c)
**Senaryo:** WS "SL FILLED" event'i ile `_exit_already_closed` fast-path'i pozisyonu kapatır ama borsada açık kalır → `_check_position` geri ekler → gerçek SL tetiklenince PnL tekrar yazılır.
**Yapılan değişiklikler:**
1. `EXIT_LIFECYCLE_SERVICE_ENABLED` flag temizliği
2. `_exit_trade_legacy` silindi
3. Idempotency guard: `_exit_log[ sym ][entry_bar_index+entry_price] = result`
4. Per-trade lock: `asyncio.Lock` key `sym_{entry_bar_index}_{entry_price}`
5. 3 yeni test (stale→real PnL tek, guard engelleme, concurrent) + 31/31 suite pass

---

## 🟡 P2 — Medium Risk (Arşiv)

### P2-2: `CleanupPlan` eksik — DÜZELTİLDİ
**Dosya:** `protection_lifecycle.py:cleanup_after_confirmed_exit()`
- `cancel_ids` set'i sadece tetikleyen tarafın ID'sini içeriyordu; prev/pending/history ID'leri eklenmiyordu.
- **DURUM: DÜZELTİLDİ** — tüm prev/pending/history ID'leri cancel_ids'e dedup'lu olarak ekleniyor. 4 yeni test.

### P2-3: `promote_sl/tp()` dokümantasyon/niyet uyuşmazlığı — DÜZELTİLDİ
**Dosya:** `protection_lifecycle.py:begin_replace_sl/tp()`
- Docstring "asynchronous replace" izlenimi veriyordu ama aynı senkron blokta atomik replace yapılıyor.
- **DURUM: DÜZELTİLDİ** — docstring gerçek çağrı desenini yansıtıyor.

---

## 🟢 P3 — Low Risk (Arşiv)

### P3-2: entry_log_msg + [PAPER] log fiyat formatı — DÜZELTİLDİ
**Dosya:** `entry_manager.py:437`, `bot.py:795-803`
- Küçük fiyatlı coinlerde (OPUSDT ~0.09) tüm fiyatlar aynı görünüyordu.
- **DURUM: DÜZELTİLDİ** — `_fmt_price()` kullanımına güncellendi.

---

## 🔴 Açık Buglar — Baş Mühendis Notu (Arşiv)

### P1-13b: P1-13 DD guard sonrası ölü kod — DÜZELTİLDİ (b0f2408 → 440125c)
**Dosya:** `bot.py:_on_15m_close()`
- P1-13 fix'inde erken return eklendikten sonra 7 satır aşağıda ölü `if is_defense_mode:` bloğu kalmıştı.
- **DURUM: DÜZELTİLDİ** — P0-1 fix kapsamında bot.py yeniden yapılandırıldı.

### P1-14b: `_exit_trade_legacy`'de P1-14 cross-validation eksik — DÜZELTİLDİ (440125c)
**Dosya:** `bot.py` (_exit_trade_legacy silindi)
- P1-14 cross-validation sadece `ExitLifecycleService.execute()`'da mevcuttu, legacy path'te yoktu.
- **DURUM: DÜZELTİLDİ** — P0-1 fix kapsamında `_exit_trade_legacy` ve `EXIT_LIFECYCLE_SERVICE_ENABLED` silindi. Cross-validation zaten yeni yolda mevcut.
