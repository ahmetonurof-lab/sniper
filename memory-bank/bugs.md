# Bug Registry — sniper/src/

> **KAPANIŞ NOTU (2026-08-08): D MODU + CONTINUATION-CONFIRM DENENDİ, KALDIRILDI.** TRAIL_MODE geri `retrace`'e çekildi. Tam evren backtest'inde: continuation (B) 9/9 varyasyonda derin negatif (K=0.1/0.3/1.0 × N=1/2/3 → -1.18M ila -1.53M; A: +4.10M); D modu (activation ATR-chase K=2.0/R=1.5) hiçbir kombinasyonda A'yı geçemedi (PYTH+SEI: -0.26%; SUIUSDT R-grid: -1.5% ila -4.9%, MaxDD de kötü). Sonuç: **A/retrace kanıtlanmış davranış, canlıda kalıcı.** Config'teki `CONT_BUFFER_MULT`/`TRAIL_ACTIVATION_R_MULT`/`ATR_TRAIL_MULT_CONTINUATION`/`CONTINUATION_CONFIRM_BARS` artık DENEYSEL-KULLANILMIYOR (değerler silinmedi — trailing_manager bunlara cfg.X ile erişiyor; TRAIL_MODE=retrace iken ilgili branch'ler çalışmaz). Gelecekte "bunu denedik mi?" sorusuna cevap: **evet, denendi, kaldırıldı** — detay: `backtest-sniper/reports/trailing_replay_ab_c.md` + `trailing_activation_scan.md`.
> **Son güncelleme:** 2026-08-08 — ✅ **P1-17 KAPANDI + CANLI** (recovery tick_size parity fix `daaeeb0` — recovered trade'ler doğru tick ile trailing üretiyor; ALGO trail#1 +19.96 canlı kanıt). ✅ **P2-6/P2-7 KAPANDI** (D-2 Fark 1 canlı doğrulandı: gir-çık döngüsü yok, trailing çıkışları pozitif). ✅ **P3-4 KAPANDI** (tick tabanlı SL tabanı canlıda). P1-15 canlıda tekrar gözlemlendi (RENDER 06:33) — mitigasyonlar doğru çalıştı.
> Dosya referansları `sniper/src/` olarak güncellendi.

---

## 🔴 AKTİF BUG ÖZETİ (08 Ağu 2026)

> 🆕 = yeni keşfedildi | 🐛 = açık/henüz fix yok | 🔧 = fix yazıldı/pending deploy | ✅ = fix deploy edildi | 📎 = mevcut bug'a veri eklendi

| ID | Durum | Başlık | Aciliyet |
|---|---|---|---|
| **P1-15** | 🐛 | Stale event loop — Binance WS FILLED gecikmesi 87-353sn, GMXUSDT orantısız etkileniyor (%71) | KÖK NEDEN DOĞRULANDI (27 Tem), client-side fix mümkün değil — mitigation aksiyonları öneriliyor. **08 Ağu RENDER 06:33'te tekrar görüldü; mitigasyonlar (cooldown + -2021 + repair atla) doğru çalıştı, aksiyon gerekmedi** |
| **D-2** | 🔧 | Trailing/entry formülleri kopya kod — exit_now Fark 1 DÜZELTİLDİ | FARK 1 DÜZELTİLDİ (canlı doğrulandı), FARK 2-5 AÇIK |
| **P1-4** | ✅ | Ghost/temizlik sadece restart'ta | KISMEN DÜZELTİLDİ — reconcile_orphan_orders periyodik (5×1m), reconcile_ghost_positions hâlâ restart'ta |
| **P1-7** | 📎 | Harici kapanışlar (26 WS_FALLBACK) + ONDOUSDT fix | KISMEN AÇIKLANDI — 22 Temmuz 9 kesin-harici vaka kaynağı açık; mainnet'te reassess |
| **P1-8** | 📎 | POST_ENTRY check %100 başarısız — iki kök neden tespit edildi | KÖK NEDEN AYRIMINDA (08-06: restart sonrası 9/9 sanity check OK — muhtemelen kapandı, resmi kapanma baş mühendis onayına bağlı) |
| **P3-3** | 🐛 | except Exception yaygın | HÂLÂ GEÇERLİ |
| **🆕 P1-17** | ✅ | Recovery tick_size parity — recovered trade'ler tick_size'sız kuruluyordu (models default 0.10), trailing normalize tüm iyileşmeleri yutuyordu | KAPANDI + CANLI (daaeeb0, ALGO trail#1 +19.96) |
| **🆕 P2-8** | 🐛 | `place_market_order` boş `{}` → "ACİL KAPANIŞ BAŞARISIZ" loglanıp restart'a kalıyor; dust-close için strateji yok (APTUSDT 08-09 07:25 UTC canlı) | DÜŞÜK (not) |

---

### Arşiv İzleri — bugs_archive.md'e taşınan maddeler

- **D-2 Fark 1:** `exit_now` guard kaldırıldı — live, analyzer_v5 backtest ile aynı (2026-07-26). P2-6/P2-7 kök nedeni giderildi. 2 regression test eklendi (trailing_manager.py).

- **P0-1:** STRKUSDT çift-exit/çift-PnL — ✅ TAMAMEN DÜZELTİLDİ (440125c — flag temizliği, legacy silme, idempotency guard, per-trade lock, 3 test), detay: bugs_archive.md
- **P0-2:** `_exit_already_closed` fast-path'i REST ile pozisyon doğrulamıyor — ✅ DÜZELTİLDİ, detay: bugs_archive.md
- **P0-3:** `_check_position()` transition-guard'sız, lock'sız — ✅ KALDIRILDI, detay: bugs_archive.md
- **P0-4:** OPUSDT — 2. pozisyon exit event'i hiç yazılmamış — ✅ DÜZELTİLDİ, detay: bugs_archive.md
- **P0-5:** `get_all_orders()` openAlgoOrders hatasını sessizce yutuyor — ✅ DÜZELTİLDİ + DOĞRULANDI, detay: bugs_archive.md
- **P0-6:** `_exit_already_closed` SL/TP result'larında pozisyon doğrulaması yok — ✅ DÜZELTİLDİ (exit_lifecycle.py:124-131 P0-6 EXPANDED guard + P1-14 cross-val), detay: bugs_archive.md
- **P0-7:** TP unchanged iptal + precision-residual churn — ✅ DÜZELTİLDİ (order_manager.py:136-138 sl/tp_really_unchanged guard + line 159 tp_unchanged guard), detay: bugs_archive.md
- **P1-1:** `repair_protection()` fiyatı yeniden hesaplamıyor — ✅ DÜZELTİLDİ, detay: bugs_archive.md
- **P1-2:** `update_trail_orders()` reject sonrası retry/backoff yok — ✅ DÜZELTİLDİ, detay: bugs_archive.md
- **P1-3:** SL/TP tahmini fiyatla hesaplanıyor — ✅ DÜZELTİLDİ, detay: bugs_archive.md
- **P1-5:** qty=0.1 dust exit — ✅ DÜZELTİLDİ, detay: bugs_archive.md
- **P1-6:** Entry sizing max_qty kontrolü yok — ✅ DÜZELTİLDİ, detay: bugs_archive.md
- **P1-8a:** POST_ENTRY type mismatch (int vs str set check) — ✅ DÜZELTİLDİ + DOĞRULANDI, detay: bugs_archive.md
- **P1-9:** SEIUSDT ghost loop 4+ saat — ✅ DÜZELTİLDİ, detay: bugs_archive.md
- **P1-10:** STRKUSDT 49x consecutive -4005 rejection — ✅ DÜZELTİLDİ, detay: bugs_archive.md
- **P1-11:** EXIT_REQUESTED runtime dead-end — ✅ DÜZELTİLDİ, detay: bugs_archive.md
- **P1-12:** 24 Temmuz post_entry_check_failed analizi — ✅ P1-8/P0-5 kök nedeni ile aynı, detay: bugs_archive.md
- **P1-13:** DD circuit breaker bypass — ✅ DÜZELTİLDİ (d62df19), detay: bugs_archive.md
- **P1-13b:** P1-13 DD guard sonrası ölü kod — ✅ DÜZELTİLDİ (b0f2408, dead code silindi), detay: bugs_archive.md
- **P1-14:** SL stale event → exit gecikmesi — ✅ DÜZELTİLDİ (d62df19 cross-val), detay: bugs_archive.md
- **P1-14b:** _exit_trade_legacy'de P1-14 cross-val eksik — ✅ DÜZELTİLDİ (b0f2408, legacy path cross-validation eklendi), detay: bugs_archive.md
- **P2-1:** `ProtectionLifecycleService.maybe_repair()` ölü kod — ✅ DOĞRULANDI, detay: bugs_archive.md
- **P2-2:** `CleanupPlan` eksik — prev/history/pending ID'leri iptal etmiyor — ✅ DÜZELTİLDİ (protection_lifecycle.py:cleanup_after_confirmed_exit prev/pending/history ID'leri de topluyor, dedup'lu, 4 yeni test), detay: bugs_archive.md
- **P2-3:** `promote_sl/tp()` dokümantasyon/niyet uyuşmazlığı — ✅ DÜZELTİLDİ (begin_replace_sl/tp() docstring'leri gerçek çağrı desenini yansıtıyor — aynı senkron blokta atomik replace), detay: bugs_archive.md
- **P2-4:** user_data_handler kendi exit'ini WS_FALLBACK sanıyor — ✅ DÜZELTİLDİ, detay: bugs_archive.md
- **P2-5:** update_trail_orders -4005 fallback yok — ✅ DÜZELTİLDİ, detay: bugs_archive.md
- **P3-2:** `entry_log_msg` + `[PAPER]` logları fiyat formatı sorunu — ✅ DÜZELTİLDİ (_fmt_price() kullanımı, bot_infra.py), detay: bugs_archive.md

<!-- P0-1 moved to archive -->

## 🟠 P1 — High Risk

### P1-4: Ghost/temizlik sadece restart'ta çalışır, periyodik değil
**Kaynak:** 2. baş mühendis analizi — OPUSDT event log ile kanıtlı
- `reconcile_ghost_positions()` sadece `run()` içinde bot başlangıcında **BİR KEZ** çağrılır (bot.py:1443).
- Periyodik `reconcile_orphan_orders()` portföy flat'ken **çalışmaz** (sayacı artıracak bar kapanışı yok — bot.py:455-458).
- Arızalı exit'in yetim SL/TP'si sadece sonraki restart'ta temizlenir — teorik olarak sınırsız süre asılı kalabilir.
- **⚠️ DURUM: KISMEN DÜZELTİLDİ** — `reconcile_orphan_orders()` artık periyodik (her 5 × 1m bar), ama `reconcile_ghost_positions()` hala sadece restart'ta.

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
  - **9/26 bot-initiated trailing** — `force_close` event JSONL'de mevcut.
  - **9/26 kesin harici** — `ws_unmatched_reduce_only` event JSONL'de doğrulanmış.
  - **5/26 muhtemel harici** — Log kapsamında, ne `force_close` ne `ws_unmatched_reduce_only` event'i var.
  - **3/26 log dışı** — Ne log ne de event doğrulaması yok. #1,#2,#5.
- **ADAUSDT vakası (#19, en net kanıt):**
  - 13:30:16: Entry @ 0.1737, SL/TP algo ID ile yerleştirildi
  - 13:30:27: DOLDURMA emri geldi — ne SL ne TP tetiklendi
  - Entry→kapanış arası 11 saniye
  - `ws_unmatched_reduce_only` event'i doğruladı: external fill, bot-dışı kaynak
- **Olası kök nedenler:**
  1. **Testnet/demo API tuhaflığı:** `demo-fapi.binance.com` paylaşımlı hesap davranışı
  2. **Aynı API key ile birden fazla instance:** Farklı makine/eski process/test script'i
  3. **Loglanmayan bir kod yolu:** Tüm exit path'leri incelendi, hepsi logluyor — olasılık düşük
- **Düzeltilen aksiyonlar:**
  - Görev 3: `post_entry_check_failed` event logu — entry sonrası ~2.5s sanity check
  - Görev 4: FVG invalidation path'ine `log_event("exit_intent", reason="fvg_invalidated")` eklendi
  - `client_order_id` traceability — tüm market order callers'a semantic prefix
- **Forensic aksiyon:** `ylOu3i0T6KRNJfKMA3T18s` clientOrderId'ine ait emrin tam detayı Binance API'den çekilmeli (`/fapi/v1/allOrders` veya `/fapi/v1/userTrades`). Eğer bu emir MARKET + reduceOnly ise ve botun hiçbir yerinde bu ID üretilmemişse, kaynak bot dışıdır.
- **Testnet güvenliği (2026-07-23):** API key yenilendi ama `web_1FJn4hMop8dxxQeYCcLi` ile web arayüzünden emir gelmeye devam etti. Doğrulandı: kullanıcı Brave'de eski session ile kilitli kalmış, diğer browser'dan login olup bot pozisyonunu görmüş — `web_` order kendi diğer browser'ından kaynaklanıyor.
- **⚠️ 22 TEMMUZ vs 23 TEMMUZ AYRIMI (Görev 10.3):** 23 Temmuz'daki external fill'ler (`web_` prefix OID'ler, NEARUSDT ve SEIUSDT) doğrudan browser session'ına bağlandı. Ancak **22 Temmuz'daki 9 kesin-harici vakanın kaynağı BUNDAN FARKLI OLABİLİR** — 22 Temmuz'da `web_` prefix'li hiçbir OID yok. Forensic aksiyon (`ylOu3i0T6KRNJfKMA3T18s` clientOrderId sorgusu) hâlâ geçerli.
- **⚠️ DURUM: KISMEN AÇIKLANDI** — 26 vaka tamamı doğrulandı (9 bot trailing / 9 kesin harici / 5 muhtemel harici / 3 log dışı). 5 muhtemel harici (#7,#8,#9,#12,#15) için deeper analiz gerekli. **22 Temmuz'daki 9 kesin-harici vaka 23 Temmuz'daki browser session'ından AYRI değerlendirilmeli** — testnet paylaşımı veya diğer instance hâlâ olası. Mainnet'e geçişte reassess edilecek.

- **📎 ONDOUSDT FIX (nexus-mcp, 2026-07-24):** `user_data_handler.py`'ye REST cross-validation eklendi. WS'ten gelen unmatched reduceOnly FILLED event'inde, `WS_FALLBACK`'e geçmeden önce `get_open_order_ids()` ile SL/TP algo ID'leri sorgulanıyor: SL eksikse → `result="SL"`, TP eksikse → `result="TP"`, ikisi de eksikse → fill result'a göre çözümleme. Bu fix P1-7'deki "kesin harici" vakaların çoğunu aslında genuine SL/TP trigger olarak yeniden sınıflandırabilir.

- **📎 08 AĞU GÖZLEM (WS_UNMATCHED_REDUCE_ONLY tekrarı):** 08-08 01:14:42 `[CRITICAL] WS_UNMATCHED_REDUCE_ONLY ARBUSDT reduceOnly FILLED ID eslesmedi (oid=RHuqr1nPOZgTWw6leYUTaf, beklenen_sl=1000000159580657, beklenen_tp=1000000159580667, onceki_status=ACTIVE) — trade kapatildi, kaynak arastirilmali`. Bu, P1-7 "kesin harici" sınıfıyla aynı olay tipi — 22 Temmuz'daki 9 vakanın kaynağı hâlâ açıkken yeni bir örnek. Tek olay, aksiyon alınmadı; **stabilite eşiği E1'i bozabilecek bilinen risk** (bkz. progress.md Stabilite Eşiği). Kaynak araştırması parity check öncesi önerilir.

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
P0-5 fix deploy edilmesine rağmen 25/Jul'da **17+ post_entry_check_failed** kaydedildi (events_2026-07-25.jsonl). `debug` log seviyesi `warning`'e çevrilerek canlıya deploy edildi, ilk debug sonucu yakalandı:

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

### 🔧 Önerilen aksiyonlar (devam eden)
4. `post_entry_check_failed` → `ws_unmatched_reduce_only` zincirindeki PnL kayıpları quantify edilmeli
5. `fvg_invalidated` → `force_close` → `WS_FALLBACK` pattern'i için SL/TP recalculation after actual fill doğrulaması eklenmeli

---

## 🟡 P2 — Medium Risk

### 🆕 P2-6: TIAUSDT Her Bar Close'da Gir-Çık Döngüsü
**Severity:** MEDIUM
**Status:** ✅ KAPANDI (2026-08-08 — canlı doğrulama)
**Date:** 2026-07-25
**Evidence:** `paper_trade.log` — 4 consecutive entries, all TRAIL_CLOSE within 1 min

TIAUSDT 4 bar üst üste her 15 dk'da bir entry almış, her biri ~1 dk içinde TRAIL_CLOSE ile kapanmış:

```
07:45:07  entry @ 0.3420  →  07:46:01  TRAIL_CLOSE  pnl=-0.99  (54s)
08:00:07  entry @ 0.3414  →  08:01:01  TRAIL_CLOSE  pnl=-0.99  (54s)
08:15:07  entry @ 0.3412  →  08:16:01  TRAIL_CLOSE  pnl=-0.70  (54s)
09:30:03  entry @ 0.3384  →  açık (log sonu)
```

Toplam zarar: -$3.26. D-2 ile ilişkili olabilir — live trailing formülü backtest'ten farklıysa optimizasyon yanlış çalışıyordur.

**✅ D-2 FARK 1 FIX (2026-07-26):** `exit_now` guard kaldırıldı.
**✅ CANLI DOĞRULAMA (2026-08-08):** D-2 Fark 1 + trailing fix'leri canlıda — TIAUSDT gir-çık döngüsü tekrar üretilmedi; trailing artık kâr kilitliyor (ALGOUSDT trail#1 0.08901 uygulandı → +19.96 SL kapanış). KAPANDI.

### 🆕 P2-7: Tüm TRAIL_CLOSE Çıkışları Negatif (5/5)
**Severity:** MEDIUM
**Status:** ✅ KAPANDI (2026-08-08 — canlı doğrulama)
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

**✅ D-2 FARK 1 FIX (2026-07-26):** `exit_now` guard kaldırıldı.
**✅ CANLI DOĞRULAMA (2026-08-08):** trailing çıkışları artık pozitif — ALGOUSDT short trail#1 (sl=0.08901/tp=0.08217) uygulandı, STOP_MARKET ile **+19.96** kârla kapandı (fix öncesi recovered trade'lerde tick=0.1 yüzünden hiçbir iyileşme uygulanamıyordu). KAPANDI.

---

## 🔵 P3 — Low Risk

### P3-3: Genel — `except Exception` çok yaygın
**Dosya:** `sniper/src/` geneli
- Spesifik exception tipleri kullanılmalı.
- Type hinting var ama runtime kontrol zayıf.
- **⚠️ DURUM: HÂLÂ GEÇERLİ** — exit_lifecycle.py, recovery_manager.py, bot.py'de yaygın `except Exception` kullanımı var.

### 🆕 P3-5: WS-ORDER PARTIALLY_FILLED tekrarları (gözlemlendi, zararsız)
**Severity:** LOW
**Status:** OBSERVED — aksiyon gerekmiyor
**Date:** 2026-07-25

`entry-enausdt-1785008701386` order ID için `status=PARTIALLY_FILLED` WS event'i 19 kez arka arkaya loglandı (22:45:01.656 → 22:45:01.864). Ardından `status=FILLED` geldi.

**Analiz:**
- Handler bu status'ta hiçbir aksiyon almıyor — sadece log üretiyor
- Entry sonrası POST_ENTRY check ve SL/TP yerleşimi normal çalışıyor
- Binance WS tarafından aynı event'in tekrar gönderilmesi veya handler'daki dedup eksikliği olabilir
- **Zarar:** Yok — sadece log hacmini artırır, fonksiyonel etkisi yok
- **Önerilen aksiyon:** Yok

### 🆕 P3-4: NEARUSDT SL Çok Dar (0.055%)
**Severity:** LOW
**Status:** ✅ KAPANDI (2026-08-08 — tick tabanlı taban canlıda)
**Date:** 2026-07-25
**Evidence:** `paper_trade.log` line 1523-1548

```
Entry @ 1.807  →  SL @ 1.806  (0.001 = 0.055% mesafe)
```

SL neredeyse entry fiyatında. Her küçük wick tetikliyor → 4 stale event (07:15-07:18) → sonunda TP @ 1.807 ile ($0.99 fee zararıyla) çıkıyor. P1-14 stale event sorununu daha da kötüleştiriyor.

**Fix:** `config.py:MIN_SL_DISTANCE_PCT = 0.0015` (%0.15) eklendi. `EntryManager.calculate_sl_tp()` sonunda `apply_min_sl_distance()` ile SL mesafesi garanti altına alınır:
- Round-trip komisyon: %0.10 (2 × %0.05)
- Tipik slippage: %0.05
- Toplam eşik: **%0.15**
- ATR bazlı SL primary kalır; bu sadece taban guard.
- Formül: long'da `min(sl, entry - min_dist)`, short'ta `max(sl, entry + min_dist)` — ikisi de SL'i entry'den uzaklaştırır, asla yaklaştırmaz.

**✅ CANLI DOĞRULAMA (2026-08-08):** `MIN_SL_DISTANCE_PCT` (P3-4) + tick tabanlı taban `MIN_SL_DISTANCE_TICKS=4` (2026-08-06 `aac0e3e` öncesi tur) canlıda — NEARUSDT dar SL tekrar üretilmedi, SL/TP VALIDATION reddi görülmedi. KAPANDI.

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

**✅ DÜZELTİLDİ (2026-07-26):** `trailing_manager.py:evaluate_trail()`'den `exit_now` guard'ı kaldırıldı (D-2 fix). Artık analyzer_v5.py (backtest) ile aynı davranışı gösteriyor: hesaplanan seviye mevcut SL'den daha iyi değilse (veya fiyat çoktan geçmişse) o FVG için trail sessizce atlanıyor, pozisyon normal `check_exit()` akışıyla yönetiliyor. 2 yeni regression test eklendi (long + short taraf). P2-6 (TIAUSDT gir-çık döngüsü) ve P2-7 (5/5 TRAIL_CLOSE negatif) kök nedeni bu fix ile giderildi — canlı doğrulama bekleniyor.

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
3. ~~`exit_now` guard'ı backtest'lere de ekle~~ ✅ KALDIRILDI — live, backtest ile aynı (2026-07-26)
4. Session filter'ı tek bir yere indir

---

<!-- P1-13b, P1-14b moved to archive -->

## 🐛 P1-15: SEIUSDT+ARBUSDT+GMXUSDT+ONDOUSDT+DOGEUSDT Stale Event Loop — WS Event Latency Kök Nedeni DOĞRULANDI

**Severity:** MEDIUM
**Status:** 🐛 KÖK NEDEN DOĞRULANDI — client-side fix mümkün değil, mitigation aksiyonları bekliyor
**Date:** 2026-07-25 (keşif), 2026-07-26 (SEIUSDT doğrulama), 2026-07-27 (kök neden kesinleşti)
**File:** `src/trading/exit_lifecycle.py:225`, `src/bot.py:505-512`, `src/trading/trailing_manager.py:148-162`, `src/bot_infra.py:136-142`, `src/trading/order_manager.py` (`verify_protection`)

### Problem

ARBUSDT/SEIUSDT vakalarında olduğu gibi, pozisyon SL'e değdikten sonra Binance
tarafında STOP_MARKET fiilen tetikleniyor ama WS `FILLED` event'i gecikmeli
geliyor. Bu pencerede her 1m bar close'da `check_exit()` tekrar SL tetikliyor →
`verify_protection()` pozisyonu hâlâ açık buluyor → stale event basılıyor →
`result=None` reset → sonraki bar'da aynı döngü.

### 27 Temmuz Doğrulama Sonuçları

**Kanıtlanan teori:** Kök neden **Binance WS event delivery latency**. STOP_MARKET
order Binance'te fiziksel olarak dolduğunda (HTTP 400 `-2021` "Order would
immediately trigger" hatası bunun kanıtı — order zaten tetiklenmiş olduğu için
yeni bir SL/TP emri "immediately trigger" ile reddediliyor), WS `FILLED` event'i
**87-353 saniye (1.4-5.9 dakika)** gecikmeli geliyor.

**Çürütülen teoriler:**
- CSV precision: Daha önce (26 Tem, SEIUSDT) çürütülmüştü, 27 Tem verisiyle tekrar doğrulandı — `csv.writer` yuvarlama yapmıyor.
- WS reconnect gap: **Kesinlikle YOK.** Log'da tek bir WS reconnect var (03:25:17, bot başlangıcı). Stale event'ler 05:55-13:18 arasında, reconnect'ten 2.5-10 saat sonra dağılmış. Listen key yenilemeleri (30dk aralıkla, 22 adet) düzgün çalışıyor.

**Latency dağılımı (cluster bazında, SL reject → WS FILLED):**

| Cluster | Sembol | Tip | Binance fill (SL reject log'u) | WS FILLED | Gecikme |
|---|---|---|---|---|---|
| 1 | GMXUSDT | SL | 05:54:15 | 06:00:08 | 353s (5.9dk) |
| 2 | UNIUSDT | TP | N/A | 07:48:04 | 3s (gürültü, aşağıda not) |
| 3 | GMXUSDT | SL | 11:46:14 | 11:50:15 | 241s (4.0dk) |
| 4 | ONDOUSDT | SL | 13:00:08 | 13:01:35 | 87s (1.4dk) |
| 5 | DOGEUSDT | SL | 13:16:00 | 13:18:24 | 144s (2.4dk) |

İstatistik (UNIUSDT gürültü hariç, n=4 cluster): min=87s, medyan=192s (3.2dk), max=353s.

UNIUSDT 3s'lik "gecikme" gürültü olarak sınıflandırıldı: UNIUSDT tp_hit tipinde (SL değil), TP path'i `execute_tp` üzerinden farklı bir kod yolundan geçiyor, WS FILLED doğrudan handler'dan geliyor — SL path'indeki `verify_protection()` döngüsüne girmiyor.

**Sembol bazında kümelenme:** GMXUSDT orantısız etkileniyor — 14 stale event'in
10'u (%71.4), 35 STOP_MARKET reject'in 24'ü (%68.6) GMXUSDT'ye ait. GMXUSDT'nin
max gecikmesi (353s) diğer sembollerin ~3 katı — düşük hacim/likidite kaynaklı
WS teslimat yavaşlığına işaret ediyor.

**Seans bazında kümelenme:** Yok. Stale event'ler hem London (05:55-07:48) hem
NY (11:46-13:18) seansına dağılmış.

**Bugünkü artış değerlendirmesi:**

| Metrik | Değer |
|---|---|
| Bugünkü (27 Tem) trade exit sayısı | 13 |
| Bugünkü stale event cluster | 5 |
| Etkilenen exit oranı | 5/13 (%38.5) |
| Tarihsel WS_FALLBACK oranı | 99/290 (%34.1) |

%38.5 oranı tarihsel %34.1 ile istatistiksel olarak tutarlı — **mutlak bir
kötüleşme yok**, kronik bir sorun. Bugün GMXUSDT ağırlıklı trade dağılımı
olduğu için görünürlük arttı, sorunun kendisi değişmedi.

### Önerilen Aksiyonlar (yeni)

1. **`verify_protection()`'a `-2021` sinyali entegre et:** STOP_MARKET reject
   yanıtında `-2021` (immediately trigger) kodu görülürse, bu emrin zaten
   fiziksel olarak dolmuş olduğunun kanıtıdır — pozisyon kapanmış kabul edilip
   stale event döngüsü kırılabilir, WS FILLED'i pasif beklemek yerine.
2. **Alternatif/tamamlayıcı:** SL reject sonrası per-bar tekrar deneme yerine,
   WS FILLED için tek seferlik ~60-90sn bekleme penceresi eklenebilir
   (gözlenen p95 gecikmeye göre kalibre edilmeli — 353s max göz önüne
   alındığında 60sn yetersiz kalabilir, ~120-180sn daha güvenli).
3. **GMXUSDT özelinde:** SL mesafesi biraz açılabilir — mevcut çok dar mesafe,
   her küçük tick'te tetiklenip düşük likidite/WS gecikmesiyle birleşince
   stale event riskini artırıyor orantısız şekilde.

### Durum

**MITIGATION UYGULANDI (2026-07-27, `ed024c3`):**
1. ✅ `-2021` sinyal entegrasyonu — `order_manager.py` + `exit_lifecycle.py`
2. ✅ Stale event cooldown (30sn) — `exit_lifecycle.py`
3. ✅ GMXUSDT SL 0.15%→0.30% — `config.py` + `entry_manager.py`

**📎 08 AĞU CANLI GÖZLEM (RENDERUSDT 06:33) — mitigasyonlar doğrulandı:**
- 06:33:16 WS-ORDER FILLED → `SL stale event #1 — pozisyon hala acik, exit iptal` (cooldown devrede)
- 06:33:19 `koruma eksik (sl=False tp=True) — onariliyor` → SL STOP_MARKET `-2021 immediately trigger` (fiziksel olarak dolu olduğu kanıtı) → `[REPAIR] -2021 — pozisyon zaten dolmus, repair atlaniyor` (P1-15 mitigasyonu + P1-16 tarzı savunma birlikte çalıştı)
- orphan_sweep TP `1000000157506320` temizlendi; pozisyon 08:49:11 web emriyle kapatıldı (kullanıcı manuel, qty 0.1).
- **Sonuç:** stale döngüsü doğru kırıldı, çift emir kalıntısı temizlendi, yeni -2021 reject üretilmedi. Mitigasyon aksiyonlarının canlı teyidi olarak kaydedildi.

Kök neden Binance WS teslimat gecikmesi olduğu için saf
client-side bir "fix" yok; mitigasyonlar stale event sayısını ve süresini
azaltmayı hedefliyor. Canlı testte doğrulama bekleniyor. P1-14
(stale event → exit gecikmesi, cross-val ile düzeltilmişti) ile karıştırılmamalı
— P1-14 exit'in doğruluğunu garantiliyordu, P1-15 exit'in *gecikmesinin*
kaynağını açıklıyor.

---

## 🆕 P1-16: Entry max_qty clamp cache boşken atlanıyor — STRKUSDT -4005 (teorik risk: limitsiz pozisyon)

**Severity:** HIGH (P1)
**Status:** ✅ KAPANDI — fix + ek düzeltme commit'lendi, notional-bazlı sürüm **CANLI** (23:56:26 restart, `[HISTORY] 367 trade gecmisten yuklendi`)
**Date:** 2026-08-03 (restart öncesi log analizi, `paper_trade.log.20260803_212142.bak`)
**File:** `src/trading/entry_manager.py:385-394`, `src/bot_binance.py:241-280`, `src/config.py`

### Olay

STRKUSDT entry `RISK ENGINE QTY=93116.1146` → MARKET emri Binance'ten
`-4005 "Quantity greater than max quantity"` ile reddedildi → trade kaydedilmedi
(21:15:15). Aynı saniyede `[EXCHANGE_INFO] 731 sembol yüklendi` loglandı.

### Kök neden

`entry_manager.py:385` `max_qty = await self._rest.get_max_qty(sym)` çağırır;
`bot_binance.py:get_max_qty()` (241-280) `LOT_SIZE.maxQty`'i cache'ten okur,
cache'te yoksa **`0.0` döner**. `if max_qty > 0 and valid_qty > max_qty` guard'ı
(max_qty=0) atlanır → clamp yapılmaz → emir limitsiz qty ile exchange'e gider.

Restart sonrası cache henüz yüklenmemişken (731 sembolün exchange info'su
asenkron gelir) ilk entry'ler bu duruma düşebilir.

### Risk

Gözlenen vaka sadece entry kaçırılmasıyla sonuçlandı (exchange emri reddetti).
**Teorik ters yön:** clamp atlanır, qty gerçekten LOT_SIZE.maxQty üzerinde kalır,
exchange emri reddetmezse (farklı filtre/koşul) → pozisyon riski istenenin
üzerinde büyür — risk yönetimi bypass edilmiş olur.

### ✅ Fix (2026-08-03) — conservative default max_qty

Baş mühendis kararı: **emri geciktirmek yerine conservative default** — sistemin
failure mode'u "biraz küçük pozisyon" olsun, "belirsiz bekleme" veya "clamp'sız
aşırı büyük pozisyon" olmasın.

`get_max_qty()` artık cache miss'te **asla 0.0 dönmez**; `_conservative_max_qty()`
şu öncelik sırasıyla çalışır:
1. `cfg.MAX_QTY_DEFAULT_OVERRIDES` — sembol bazlı sabit tavan (opsiyonel).
2. `cfg.MAX_QTY_DEFAULT_NOTIONAL / fiyat` — CANLI fiyat ile fiyat bazlı
   conservative tavan. Notional, risk engine'in tipik notional'ının alt sınırına
   yakın tutulur (tipik ~5-10K USDT; `MAX_QTY_DEFAULT_NOTIONAL = 500.0`) —
   sembole özel volatiliteyi fiyat üzerinden hesaba katar.
3. Aynı notional tavan, son bilinen fiyat (`_last_price_cache`, stale) ile —
   canlı fiyat alınamıyorsa.
4. Fiyat gerçekten yoksa → `MaxQtyUnavailableError` fırlatılır; `entry_manager`
   emri REDDEDER, `order_manager`/`recovery_manager` parçalı SL/TP atlar
   (closePosition akışı korunur). Sessizce sabit quantity tavanı kullanılmaz.

Cache dolduğunda normal akışa döner (gerçek `LOT_SIZE.maxQty` okunur).

**İlk sürüm (commit `694b11d`, deploy edildi 22:28):** fiyat yoksa sabit
`MAX_QTY_DEFAULT_FLOOR = 1000.0` quantity tavanı dönerdi.

**Ek düzeltme (commit sonrası, notional-bazlı sürüm):**
- Doğrulama: fiyatsız çağrı MÜMKÜN — `estimate_market_price()` ticker REST
  hatasında `0.0` döner; `BinanceRESTClient`'ta fiyat cache'i yoktu.
- `MAX_QTY_DEFAULT_FLOOR` (sabit quantity 1000) **kaldırıldı** — quantity yerine
  notional bazlı: hem fiyat-bilinen hem fiyat-yok-stale durumlarında aynı
  `MAX_QTY_DEFAULT_NOTIONAL` kullanılır. Sabit 1000 quantity'nin sorunu:
  yüksek fiyatlı sembollerde (ör. BTC ~100K → 1000 × 100K = 100M USDT notional)
  "conservative" değil, tam tersine devasa bir tavan üretiyordu.
- Fiyat hiç yoksa (canlı + stale) → **reddet** (`MaxQtyUnavailableError`):
  fiyatsız pozisyon büyüklüğü hesaplamak zaten yanlış; "conservative default"
  değil "reddet" doğru davranış.
- Yeni testler: `test_stale_price_when_fresh_fails`, `test_rejects_when_no_price`,
  `test_missing_symbol_with_price_returns_notional_cap`; `test_returns_floor_on_missing`
  ve `test_missing_symbol_returns_floor` kaldırıldı (floor artık yok).

**Neden "emri geciktir" seçilmedi:** sinyal anlık, piyasa hızlı hareket ediyor;
cache'in ne zaman dolacağı garantili değil → fırsat kaçırma (STRKUSDT'de yaşanan
sorun) veya belirsiz süre bloklanan order queue + yeni race condition sınıfı
(bekleme süresi/timeout belirsizliği) riski.

### İlişki

P1-6 (entry sizing max_qty kontrolü, ✅ DÜZELTİLDİ) clamp'ın kendisini ekledi;
bu madde clamp'ın **cache-bağımlılık açığını** konu alır — P1-6'nın eksik
tamamlayıcısı. P2-5/P0-5 (-4005 fallback zincirleri) defense-in-depth olarak
kalmalı.

---

## 🆕 P1-17: Recovery tick_size parity — recovered trade'ler doğru tick ile trailing üretmiyordu (KRİTİK, kapatıldı)

**Severity:** HIGH (P1)
**Status:** ✅ KAPANDI + CANLI (commit `daaeeb0`, screen 366235.bot, run `paper-20260808-000537`)
**Date:** 2026-08-08 (keşif + fix + deploy + canlı doğrulama)
**File:** `src/trading/recovery_manager.py:80`, `src/models.py`, `src/trading/trailing_manager.py`, `src/bot.py`, `src/state_writer.py`

### Kök neden (kullanıcı teyidi)

`recovery_manager.recover_positions` `ActiveTrade(...)`'i **`tick_size` geçirmeden**
kuruyordu → `models.py` default'u `0.10` sessizce kullanılıyordu → **170/170
recovered trade** `tick=0.1` ile trailing normalize'u (ROUND_CEILING) her
iyileşmeyi yutuyordu (`no_better_trail_candidate`, 214/214 trail_skipped).

Matematik kanıtı: ALGO raw `0.088888` → normalize(tick=0.1) `0.1` → `0.1 < 0.09353`
false → skip; RENDER raw `1.320464` → `1.4` → `1.4 < 1.387` false → skip.
**Doğru tick ile** (RENDER 0.001 → 1.321 < 1.387 ✓, ALGO 1e-05 → iyileşme ✓) hop üretilir.

Etki yalnızca restart-recovered trade'ler — yeni açılan trade'ler (`_try_entry`)
doğru tick_size alıyor.

### Fix (kullanıcı direktifi: "fix'leri yerel yap, sunucuya sadece deploy")

1. `recovery_manager.py` — 3 `ActiveTrade(...)` kurulumuna `tick_size=self._rest.get_tick_size(sym)` (try/except → 0.10 fallback + warning); `status=STATUS_ACTIVE`, `trail_count=0` parity; `existing["tick_size"]` tazeleme.
2. `models.py` — savunmacı default: `tick_size: float | None = None` + `__post_init__` → None ise `log.critical` + 0.10 sentinel (sessiz yanlış default sınıfı kapatıldı).
3. `trailing_manager.py` — `_fvg_multihop` opsiyonel `tick_size`: hop kararı `_normalize_price` (SL kind) ile normalize birimde; `trail_steps` normalize SL loglar. **Verilmediğinde (backtest `evaluate_trail`) raw davranış birebir korunur.**
4. `bot.py` — extractor `tick_size=trade.get("tick_size")` geçiriyor.
5. `state_writer.py` — `active_trade`'e `"tick_size"` eklendi (izleme).
6. `tests/test_recovery_manager.py` — `TestRecoveredTradeFieldParity` (2 test: yeni recovered trade tam şema; existing trade'de tick_size tazelenir).

### ✅ Canlı doğrulama (2026-08-08)

- **ALGOUSDT short SL kapanış +19.96:** entry 0.08993, initial SL 0.09353 → **trail#1 sl=0.08901/tp=0.08217 UYGULANDI** (tick=1e-05; fix öncesi 0.1'de 0.08901→0.1 normalize olup reddedilirdi). Fiyat SL'yi test edip döndü → STOP_MARKET tetiklendi. `trade_closed` event: `final_sl=0.08901, final_tp=0.08217, trail_count=1, pnl=19.96`.
- 0 ERROR/CRITICAL/Traceback; 5 WARNING (hepsi RENDER 06:33 zinciri, guard'lar doğru çalıştı).
- Test: kapsam dosyaları (recovery+trailing+models) tamamı geçti; diğer fail'ler baseline ile birebir (pre-existing).

---

## 🟡 P2-8: `place_market_order` boş `{}` → dust-close stratejisi yok (DÜŞÜK ÖNCELİK, not)

**Severity:** LOW (P2)
**Status:** 🐛 açık — sadece not düşüldü, fix yok (baş mühendis direktifi: "acil değil ama bugs.md'ye eklensin")
**Date:** 2026-08-09 (canlı gözlem)
**File:** `src/trading/recovery_manager.py` (~satır 550-563, ACIL KAPANIS BASARISIZ yolu)

### Gözlem (08-09 07:25 UTC, APTUSDT dust pozisyon qty=0.1)

`place_market_order` **boş `{}`** döndü (exception atmadı) → recovery
`[RECOVER] APTUSDT ACIL KAPANIS BASARISIZ -- MANUEL MUDAHALE GEREKLI: place_market_order bos dict ({}) dondu`
loglayıp pozisyonu korumasız `active_trades`'e bıraktı. Sonraki restart'ta
`[GHOST] APTUSDT pozisyon kapali, state temizlendi` ile kurtarıldı.

### Gap

- **Kök şüphe:** minNotional altındaki dust (qty=0.1, `[MINNOTIONAL] qty=0.1 < min_notional=5.00`, dakikada 4×)
  için borsa emri reddediyor; kod bunu anlamlı hataya çevirip farklı strateji
  (daha küçük dust-temizleme, en azından net alarm) üretmiyor — boş dict →
  "manuel müdahale gerekli" logu.
- **Risk:** restart olmasaydı pozisyon korumasız kalmaya devam ederdi.
- **Önerilen yön (gelecek iş):** boş `{}` dönüşünü gerçek bir hata türü olarak
  ele al (minNotional kontrolü öncesi büyüklük doğrulaması / dust-temizleme
  alternatifi / escalation alarmı).
- **Kısmi koruma zaten var:** `reconcile_ghost_positions()` restart'ta bu sınıfı
  yakalıyor; periyodik çalışmıyor (bkz. P1-4).
