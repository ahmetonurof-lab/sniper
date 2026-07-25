# Bug Registry — sniper/src/

> **Son güncelleme:** 2026-07-25 22:15 — bugs.md bölündü. Sabit maddeler: bugs_archive.md'e taşındı.
> Dosya referansları `sniper/src/` olarak güncellendi.

---

## 🔴 AKTİF BUG ÖZETİ (25 Tem 2026)

> 🆕 = 25 Tem'de yeni keşfedildi | 🐛 = açık/henüz fix yok | 🔧 = fix yazıldı/pending deploy | ✅ = fix deploy edildi | 📎 = mevcut bug'a veri eklendi

| ID | Durum | Başlık | Aciliyet |
|---|---|---|---|
| **P0-1** | ✅ | STRKUSDT çift-exit/çift-PnL | KISMEN DÜZELTİLDİ |
| **P0-6** | 🔧 | _exit_already_closed SL/TP pozisyon doğrulaması yok | FIX YAZILIYOR |
| **P0-7** | 🔧 | TP unchanged iptal + precision-residual churn | HAZIR, DEPLOY BEKLİYOR |
| **P1-4** | ✅ | Ghost/temizlik sadece restart'ta | KISMEN DÜZELTİLDİ |
| **P1-7** | 📎 | Harici kapanışlar (26 WS_FALLBACK) + ONDOUSDT fix | KISMEN AÇIKLANDI |
| **P1-8** | 🐛 | POST_ENTRY check %100 başarısız — iki kök neden tespit edildi | DEBUG LOG AKTİF, KÖK NEDEN AYRIMINDA |
| **D-2** | 🐛 | Trailing/entry formülleri 3 motorunda kopya kod | AÇIK |
| **P2-2** | 🐛 | CleanupPlan eksik | HÂLÂ GEÇERLİ |
| **P2-3** | 🐛 | promote_sl/tp() niyet uyuşmazlığı | HÂLÂ GEÇERLİ |
| **🆕 P2-6** | 🐛 | TIAUSDT her bar close'da gir-çık döngüsü | AÇIK |
| **🆕 P2-7** | 🐛 | Tüm TRAIL_CLOSE çıkışları negatif (5/5) | AÇIK |
| **P3-2** | 🐛 | entry_log_msg tahmini fiyat | HÂLÂ GEÇERLİ |
| **P3-3** | 🐛 | except Exception yaygın | HÂLÂ GEÇERLİ |
| **🆕 P3-4** | 🐛 | NEARUSDT SL çok dar (0.055%) | AÇIK |
| **🆕 P1-13b** | 🐛 | P1-13 DD guard sonrası ölü kod (unreachable block) | AÇIK |
| **🆕 P1-14b** | 🐛 | _exit_trade_legacy'de P1-14 cross-val eksik | AÇIK |
| **🆕 P1-15** | 🔍 | ARBUSDT stale event loop — _on_1m_close result reset mi tetikliyor? | ARAŞTIRILIYOR |

---

### Arşiv İzleri — bugs_archive.md'e taşınan maddeler

- **P0-2:** `_exit_already_closed` fast-path'i REST ile pozisyon doğrulamıyor — ✅ DÜZELTİLDİ, detay: bugs_archive.md
- **P0-3:** `_check_position()` transition-guard'sız, lock'sız — ✅ KALDIRILDI, detay: bugs_archive.md
- **P0-4:** OPUSDT — 2. pozisyon exit event'i hiç yazılmamış — ✅ DÜZELTİLDİ, detay: bugs_archive.md
- **P0-5:** `get_all_orders()` openAlgoOrders hatasını sessizce yutuyor — ✅ DÜZELTİLDİ + DOĞRULANDI, detay: bugs_archive.md
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
- **P1-14:** SL stale event → exit gecikmesi — ✅ DÜZELTİLDİ (d62df19 cross-val), detay: bugs_archive.md
- **P2-1:** `ProtectionLifecycleService.maybe_repair()` ölü kod — ✅ DOĞRULANDI, detay: bugs_archive.md
- **P2-4:** user_data_handler kendi exit'ini WS_FALLBACK sanıyor — ✅ DÜZELTİLDİ, detay: bugs_archive.md
- **P2-5:** update_trail_orders -4005 fallback yok — ✅ DÜZELTİLDİ, detay: bugs_archive.md

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

---

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

## 🔵 P3 — Low Risk

### P3-2: execute_live_entry() entry_log_msg + bot.py [PAPER] logları fiyat formatı sorunu
**Dosya:** `sniper/src/trading/entry_manager.py:437`, `sniper/src/bot.py:795-803`
- `entry_log_msg`'de `PRICE: {est_price:.2f}` ve `[PAPER]` logunda `@ %.2f sl=%.2f tp=%.2f` kullanılıyor
- Küçük fiyatlı coinlerde (ENAUSDT ~0.0859, OPUSDT ~0.09) tüm fiyatlar aynı görünür (ör: 0.09)
- `_fmt_price()` zaten `bot_infra.py`'de var ve dinamik ondalik basamak kullanıyor
- **⚠️ DURUM: DÜZELTİLDİ** — `entry_log_msg` ve `[PAPER]` logları `_fmt_price()` kullanımına güncellendi (2026-07-25)

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
**Status:** OPEN
**Date:** 2026-07-25
**Evidence:** `paper_trade.log` line 1523-1548

```
Entry @ 1.807  →  SL @ 1.806  (0.001 = 0.055% mesafe)
```

SL neredeyse entry fiyatında. Her küçük wick tetikliyor → 4 stale event (07:15-07:18) → sonunda TP @ 1.807 ile ($0.99 fee zararıyla) çıkıyor. P1-14 stale event sorununu daha da kötüleştiriyor.

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

## 🔴 Açık Buglar — Baş Mühendis Notu (25 Tem 2026)

> Baş mühendis review'undan çıkan açık bug'lar. Bloklayıcı değil, temizlik/debt.

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

## 🔍 P1-15: ARBUSDT Stale Event Loop — `_on_1m_close` Result Reset mi Tetikliyor?

**Severity:** MEDIUM
**Status:** 🔍 ARAŞTIRILIYOR
**Date:** 2026-07-25
**File:** `src/trading/exit_lifecycle.py:225`, `src/bot.py:505-512`, `src/trading/trailing_manager.py:148-162`

### Problem

ARBUSDT pozisyonu 18:45:01'de açıldı. 18:47:14'ten 19:24:45'e kadar (~37dk) **9 stale event** tekrarlandı. Her stale event'te SL/TP open_ids'de hâlâ mevcut → pozisyon gerçekten açık.

### Zaman Çizelgesi

```
18:45:01  ARBUSDT entry (SL=0.082725, TP=0.083550)
18:47:14  stale #1  (no WS-ORDER before)
18:47:15  stale #2
18:52:14  stale #3
18:56:15  stale #4
19:02:15  stale #5
19:03:15  stale #6
19:05:01  stale #7
19:09:14  stale #8
19:14:16  stale #9   ← bot restart sonrası, WS reconnect
19:24:01  stale #10
19:24:45  GERÇEK WS SL fill → exit devam etti, pnl=-4.43
```

### Tespit

Stale event'lerden **hiçbirinde** `[WS-ORDER]` satırı yok. Ama timing 1m bar close ile hizalı:

| Stale Event | En yakın 1m Bar Close | Fark |
|---|---|---|
| 19:14:16 | 19:14:00 | +16sn |
| 19:24:01 | 19:24:00 | +1sn |

### Kök Neden Hipotezi

`_on_1m_close()` (bot.py:505-512) her 1m bar close'da `TrailingManager.check_exit()` çağırıyor. `check_exit()` (trailing_manager.py:148-162) bar'ın low/high değerlerini SL/TP ile karşılaştırıyor:

```python
# trailing_manager.py:158 — short pozisyon için
if current.high >= sl:
    return ExitDecision(triggered=True, result="SL", exit_price=sl)
```

Eğer ARBUSDT short pozisyonu için 1m bar high'u SL (0.082725) seviyesine değerse → `result="SL"` → `_exit_trade()` → `exit_service.execute()` → `position_still_open=True` → stale event.

**Kritik nokta:** Stale event sonunda `trade["result"] = None` (exit_lifecycle.py:225) ile result reset ediliyor. Ama bir sonraki 1m bar close'da `check_exit()` tekrar `result="SL"` set edebilir — eğer bar high'u SL'e değmeye devam ederse.

### Doğrulama Bekleniyor

1. **`[P_DEBUG]` logu:** `trade["result"] = None` çalışıyorsa stale event'ten hemen önce `[P_DEBUG] ARBUSDT result reset ediliyor, onceki=SL` görünmeli. Bu, `_on_1m_close()`'ün her bar'da kendi result'unu set ettiğini doğrular.

2. **1m OHLC verisi:** `live_ohlc/ARBUSDT_1m.csv`'den 19:14:00 ve 19:24:00 bar'larının high değeri SL (0.082725) ile karşılaştırılmalı. High >= SL ise → **doğru behavior** (fiyat SL'e değiyor, exit tetikleniyor ama pozisyon kapanmıyor).

### Geçici Sonuç

**Henüz kapatılmadı.** `[P_DEBUG]` ve OHLC verisi bekleniyor. Eğer her ikisi de hipotezi doğrularsa → bu doğru behavior (fiyat SL seviyesinde dalgalanıyor), bug değil. Eğer `[P_DEBUG]` çalışmıyorsa → result reset bug'ı var, fix gerekiyor.
