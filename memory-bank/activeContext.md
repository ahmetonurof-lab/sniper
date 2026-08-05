# Active Context — Sniper Bot

## Son İşlem: 2026-08-06 canlı teyit — log fix CANLI doğrulandı; SOLUSDT/ONDO korumasız tespit edildi (KRİTİK)

Yeni sunucu dosyaları (paper_trade.log 552 satır run `paper-20260805-212252`, trades_history.jsonl 440, events_2026-08-06.jsonl) analiz edildi.

### ✅ (1) Log seviyesi fix'i CANLI DOĞRULANDI — KAPANDI
- Restart **00:22:52** (`[HISTORY] 440 trade gecmisten yuklendi`) sonrası tüm pencerede (→00:41:00) **sıfır** `[POST_ENTRY_DEBUG]` / `[P1-15_DEBUG]` satırı. `bc73b5c` (bot.py:838 `log.warning`→`log.debug`) sunucuda aktif. Daha önce 00:03:06'da görünen WARNING yok.
- 28 sembol LEVERAGE + PREFILL + WARMUP + INIT normal; `[STATE] reconcile: tüm semboller zaten güncel`.

### 🔴 (2) KRİTİK BULGU: SOLUSDT-0 ve ONDOUSDT-0 koruma emirleri BORSA'DA YOK (çıplak pozisyon)
- `events_2026-08-06.jsonl`: **00:03:47** ONDOUSDT `orphan_cleaned` (STOP_MARKET `1000000157572041` + TAKE_PROFIT_MARKET `1000000157572047`); **00:04:24** SOLUSDT `orphan_cleaned` (STOP_MARKET `1000000157356807` + TAKE_PROFIT_MARKET `1000000157356808`); 00:03:07 ENAUSDT `entry`.
- Restart (00:22:52) sonrası log'da **hiçbir trade için koruma yerleştirme satırı yok** — `[POST_ENTRY]`, `SL OK`/`TP OK`, `[RECOVER]`, `[MARKET]` hiçbiri yok. 12 restore trade (`[SYNC] ... trades_today: 1`: SOL/LINK/ADA/SUI/OP/ARB/ALGO/TIA/ONDO/RENDER/ENA/GMX) trailing döngüsünde görünüyor ama korumaları borsaya koyulmadı.
- SOLUSDT-0 trailing her ~1dk `[TRAIL] trail#1 sl=74.092724 tp=75.092724` basıyor **ama eşzamanlı** `trail_skipped | no_better_trail_candidate` event'i düşüyor → SL/TP değişmiyor. trailing "daha iyi kandidat" arıyor, **koruma eksikliğini yakalamıyor**. ONDOUSDT-0 için de `trail#1 sl=0.384778 tp=0.356078` + `trail#2` benzer (short, korumasız).
- **Kök neden hipotezi (kod doğrulaması sıradaki adım):** `orphan_cleaned` trade state'teki `sl_order_id`/`tp_order_id`'yi temizlemiyor; restart'ta `recover_positions` state'te ID olduğu için korumayı borsaya YENİDEN KOYMUYOR; trailing de no_better_trail_candidate ile hiçbir şey yapmıyor → pozisyon çıplak.
- **Kullanıcı aksiyonu (acil):** Binance hesabında SOLUSDT ve ONDOUSDT açık emirlerini manuel kontrol et; koruma yoksa manuel koy veya pozisyonu kapat.

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
