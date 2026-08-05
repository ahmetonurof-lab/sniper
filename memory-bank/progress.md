# Progress — Sniper Bot

## Son İşlem

| Tarih | İşlem | Detay |
|-------|-------|-------|
| 2026-08-05 | **Log seviyesi düzeltmesi + öncelik sırası (baş mühendis onaylı)** | `[P1-15_DEBUG]` (`bot.py:582`) + `[POST_ENTRY_DEBUG]` (`order_manager.py:385`) `WARNING` → `DEBUG` — commit `1a439c9`. `trail_skipped` JSONL telemetrisi (`paper_trade_logger.py`), kapsam dışı. py_compile OK; test_trailing_manager baseline ile aynı (30 pass / 17 bayat check_exit imza faili). **Öncelik sırası:** 1) Log seviyesi ✅ · 2) Stale event kök neden araştırması (WS FILLED 87–353 sn: Binance push vs bot event loop — ölçümle başla) · 3) ATR-chase K=0.5/1.0/1.5 replay doğrulamalı · 4) FVG gevşetme "backtest bekliyor" (somut backtest kanıtı gelmeden sırada değil). |
| 2026-08-04 | **Trailing yol kalıcılığı (#3) — trail_mode state'e yazıldı** | Restart sonrası recover edilen trade'lerde `trail_level_extractor` closure'ı kayboluyordu → swing fallback'e düşülüyordu (canlıda 3692 `no_better_trail_candidate` + 1 güncelleme). Fix: `ActiveTrade.trail_mode: str = "fvg"` (models.py), entry + recovery (2 site) `trail_mode="fvg"` set ediliyor; `_on_1m_close` callable değilse FVG extractor yeniden kuruluyor. +2 yeni test (test_bot `test_rebuilds_fvg_extractor_when_missing`, test_models `TestActiveTrade::test_trail_mode_defaults_to_fvg`); recovery testine `trail_mode == "fvg"` assert eklendi. TestOn1mClose'daki 2 stale test (eski `evaluate_trail` API, await edilemeyen MagicMock) güncel `orchestrate_trail` API'sine uyarlandı. Baseline 72 fail → 70 (0 yeni); hedefli 58/58 geçti. Kalan 70 fail pre-existing (parity/SOLUSDT, TestCheckExit stale API, TestExitTrade AttributeError vb., #3 ile ilgisiz). |
| 2026-08-04 | **Stale event backstop (#3 ek) — exit_lifecycle.py** | N=3 ardışık stale event sonrası REST `position_still_open()` backstop eklendi. Pozisyon kapandıysa `active_trades.pop(sym, None)` ile trade temizlenir ve exit True döner (stale döngüsü kırılır). Pozisyon hâlâ açıksa veya REST hata verirse mevcut davranış korunur (trade active_trades'te kalır). 3 yeni regresyon testi (`TestStaleBackstop`): pozisyon kapalı/pop, pozisyon açık/kal, REST exception/güvenlik. ExitLifecycleService suite: 37/37 geçti. |
| 2026-08-04 | **P1-16 notional sürümü CANLI — deploy + restart** | Sunucuda `git pull` + restart (23:56:26): `[HISTORY] 367 trade gecmisten yuklendi` (önceki koşu 364). Devredeki commit `1b0b647` (notional-bazlı tavan + stale fiyat fallback + fiyat yoksa `MaxQtyUnavailableError`). Normal akışta cache dolu olduğundan gerçek LOT_SIZE.maxQty kullanılır; gerçek cache miss'inde `[MAX_QTY]` WARNING beklenir. |
| 2026-08-03 | **P1-16 ek düzeltme — notional bazlı tavan + fiyat yoksa reddet** | Doğrulama: fiyatsız `get_max_qty()` MÜMKÜN (`estimate_market_price` hata → 0.0, `BinanceRESTClient` fiyat cache'siz). `MAX_QTY_DEFAULT_FLOOR` (sabit qty 1000) KALDIRILDI → `_conservative_max_qty()`: override > canlı fiyat notional tavan > stale fiyat (`_last_price_cache`) notional tavan > **`MaxQtyUnavailableError`** (emir açılmaz). Sabit 1000 qty yüksek fiyatlı sembollerde devasa tavan üretiyordu (BTC 1000×100K=100M USDT). `entry_manager` reddeder; `order_manager`/`recovery_manager` parçalı SL/TP atlar. `TestGetMaxQty` 6 test (yeni: `test_stale_price_when_fresh_fails`, `test_rejects_when_no_price`). test_bot_binance+entry+order+recovery = **220 passed**, ruff temiz; diğer suite'lerdeki 44 fail pre-existing (stash ile doğrulandı). bugs.md'de P1-16 KAPANDI. Commit `1b0b647`, push edildi; **CANLI (23:56:26 restart, 367 trade)**. |
| 2026-08-03 | **P1-16 ek düzeltme — notional bazlı tavan + fiyat yoksa reddet** | Doğrulama: fiyatsız `get_max_qty()` MÜMKÜN (`estimate_market_price` hata → 0.0, `BinanceRESTClient` fiyat cache'siz). `MAX_QTY_DEFAULT_FLOOR` (sabit qty 1000) KALDIRILDI → `_conservative_max_qty()`: override > canlı fiyat notional tavan > stale fiyat (`_last_price_cache`) notional tavan > **`MaxQtyUnavailableError`** (emir açılmaz). Sabit 1000 qty yüksek fiyatlı sembollerde devasa tavan üretiyordu (BTC 1000×100K=100M USDT). `entry_manager` reddeder; `order_manager`/`recovery_manager` parçalı SL/TP atlar. `TestGetMaxQty` 6 test (yeni: `test_stale_price_when_fresh_fails`, `test_rejects_when_no_price`). test_bot_binance+entry+order+recovery = **220 passed**, ruff temiz; diğer suite'lerdeki 44 fail pre-existing (stash ile doğrulandı). bugs.md'de P1-16 KAPANDI. Commit `1b0b647`, push edildi; **CANLI (23:56:26 restart, 367 trade)**. |
| 2026-08-03 | **Deploy sonrası log analizi (22:28 restart)** | `paper_trade.log` (1159 satır, 22:28→23:10) incelendi. **Trailing fingerprint fix'i (9263516) CANLI**: sıfır `identical_invalid_candidate_suppressed`/`candidate_not_placeable`, ENAUSDT `no_better_trail_candidate` ile normal değerlendiriliyor. **P1-16 (694b11d, 23:08 push) HENÜZ CANLI DEĞİL** — restart gerekli; STRKUSDT CBDR kilitlenmediği için path test edilmedi. ONDOUSDT TP +5.34 + yeni long entry normal; ARBUSDT TP stale event #1 doğru iptal edildi (P1-15). Hata yok: -4005/-2021/-1007/WS_FALLBACK/orphan/reconnect görülmedi. |
| 2026-08-03 | **P1-16 fix — get_max_qty cache miss'te conservative default** | Baş mühendis kararı: emri geciktirmek yerine conservative default (failure mode "biraz küçük pozisyon"). `bot_binance.py:get_max_qty()` artık cache miss'te **asla 0.0 dönmez** (0.0, entry_manager `max_qty > 0` guard'ını atlayıp limitsiz qty'ye yol açıyordu — STRKUSDT -4005). Yeni `_conservative_max_qty()`: sembol override (`MAX_QTY_DEFAULT_OVERRIDES`) > fiyat bazlı notional tavan (`MAX_QTY_DEFAULT_NOTIONAL=500.0 / price`, sembole özel volatiliteyi fiyat üzerinden hesaba katar) > sabit floor (`MAX_QTY_DEFAULT_FLOOR=1000.0`). Cache dolunca normal akış (gerçek LOT_SIZE.maxQty). `TestGetMaxQty` 5 test (floor, notional cap, override, missing symbol, gerçek maxQty). Test suite pass, ruff temiz. |
| 2026-08-03 | **ENABUG trailing kilitlenme fix'i — fingerprint'e bucket'lı fiyat** | Baş mühendis onayı: zaman bazlı expiry DEĞİL. `trailing_manager.py:_fingerprint()` artık fiyat bucket'ı içeriyor (`max(tick*epsilon_ticks, price*0.001)` ile bucket'lı, mikro-noise emilir). `compute_trail_candidate()` `current_price` parametresi aldı; `orchestrate_trail()` geçiriyor. Fiyat lehine bucket atlayınca suppress kalkıp candidate yeniden değerlendiriliyor. 3 yeni test (`TestFingerprintPriceBucket`) — trailing manager 30 passed, ruff temiz. **Eş zamanlı:** bugs.md'ye **P1-16** eklendi (entry max_qty clamp cache boşken atlanıyor — STRKUSDT -4005; fix önerisi: emri geciktir/cache bekle veya conservative default max_qty). |
| 2026-08-03 | **Restart öncesi log analizi — trailing kilitlenme bug'ı** | `paper_trade.log.20260803_212142.bak` (16.252 satır) incelendi. **BUG:** ENAUSDT `identical_invalid_candidate_suppressed` — `_fingerprint()` (trailing_manager.py:472) fiyat içermiyor, `is_placeable` fiyat-bağımlı; 20:45'te `candidate_not_placeable` sonrası aynı fingerprint ~35 dk sonsuza dek suppress edildi, SL 0.0906'da kaldı. **Gözlem:** STRKUSDT entry -4005 (max_qty clamp cache boşken atlandı), GMXUSDT trail sonrası 13 sn çift koruma, GMX stale event'ler doğru yönetildi. |
| 2026-08-03 | **TP_RR 1.8 + ATR_TRAIL_MULT 0.10 default sabitlendi** | `src/config.py:523` `TP_RR` 2.0→1.8, `:534` `ATR_TRAIL_MULT` 0.25→0.10. Her ikisi env-var override destekli (`SNIPER_TP_RR`/`SNIPER_ATR_TRAIL_MULT`). Backtest (`backtest-sniper/analyzer_v5.py`, root repo) kanıtı: 28/28 coin iyileşti, toplam +3582448→+4100540 (+14.5%), MaxDD ↓. ATR 0.10 GMX/BNB/SOL cross-coin monotonik sweep'in seçilen sınırı; 0.05/0.01 overfit/live-risky elendi. Paper trade'de deneniyor. |
| 2026-08-03 | **CBDR kilit log metni düzeltildi** | `bot.py:465` — "[SKIP] CBDR henuz kilitlenmedi — entry engellendi" → "... — akis baslatilmadi". Log metni yanıltıcıydı: satır 464, `evaluate_trigger` (469) ÖNCESİNDE basılıyor, ortada entry adayı yokken "engellendi" diyordu. Kod akışına dokunulmadı, sadece metin gerçeğe uygun hale getirildi. Akış: CBDR kilit (464) → evaluate_trigger (469) → _try_entry (497). |
| 2026-08-02 | **MARKET empty_response reconcile guard eklendi** | `entry_manager.py` `execute_live_entry()` — HTTP 408/-1007 ("execution status unknown") için yeni kontrol: `not mkt_id and actual_qty <= 0` → `get_positions()` + `pos_amt>0` → `_emergency_close()`. Mevcut 414. blok (`actual_qty>0`) timeout senaryosunda çalışmıyordu. Canlı olay: DYDXUSDT 21:42:30 408 timeout — emir belirsiz, pozisyon korumasız kalabilirdi. 81+119 test pass; 2 yeni/güncellenmiş test. |
| 2026-08-02 | **config.py ölü sabitler temizlendi** | `src/config.py`'den 5 ölü sabit silindi: `LOG_LEVEL`, `MAX_SL_DIST_MULT`, `MIN_REL_FVG_THRESHOLD`, `BE_RISK_MULT`, `BE_SPREAD_PTS` (grep + kod doğrulaması, sıfır kullanım). `EARLY_LONDON_RISK_MULT` korundu — `simulate.py:305`'te kullanılıyor (kullanıcı raporundaki "ölü" iddiası hatalıydı). Bot davranışına sıfır etki. |
| 2026-08-02 | **DD_GUARD öncesi [RISK-DEBUG] log eklendi** | `src/bot.py:728-733` — `_try_entry()`'de `is_defense_mode` sonrası, P1-13 DD_GUARD kontrolünden önce `get_current_dd()` + `log.info("[RISK-DEBUG] ... equity/peak/dd/broken")`. DD_GUARD tetiklenme anındaki portföy durumu log'da görünür. Yeni API yok. (Sonraki işlemle geri alındı.) |
| 2026-08-02 | **12 BULGU ayrı commit'lerle düzeltildi** | `e369ddc` baz üzerine 10 commit: BULGU-07 (`21be255` bare except→log.error), BULGU-09 (`df14756` TP fail→emergency close), BULGU-03 (`fb82685` trade referansı tek kaynak), BULGU-04 (`bac575c` sym lock), BULGU-01/10 (`e5d9151` pending_exit_* fields + __contains__ + known_ids), BULGU-23 (`54b2ce6` _is_live), BULGU-02/06 (`4c7c4c7` atomik yazma), BULGU-08 (`5b9bb7b` amt==0), BULGU-11 (`4006acb` order_id ayrı), BULGU-21 (`870930e` _live tek kaynak). BULGU-17/18 bug değil, kapatıldı. 198 test passed. |
| 2026-08-01 | **P0 safety fixes: bare except remediation** | recovery_manager.py:486, exit_lifecycle.py:521/549, order_manager.py:646/966 bare `except Exception: pass` → `log.error` + retry/fallback. state_writer.py: BULGU-05 (protection_health flat field'lardan) + BULGU-19 (ws_event_normalization config'den). |
| 2026-08-01 | **Cross-context bug fix turu — 13 bug, 12 commit** | `03e6eaf8` baz: BUG-1/7, BUG-25, BUG-23, BUG-5, BUG-12, BUG-8/2, BUG-21, BUG-10, BUG-11, BUG-3, BUG-17, BUG-16, BUG-29. Commitler: `5f08154..e244165`. |
| 2026-08-01 | **Rapor dokümanları push edildi** | `reports/` — `Trading_Execution_Simulator.md`, `entry_decision_tree.md`, `fvg_fix_analysis_report.md`, `parity_regression.md`, `sniper_cross_context_bug_report.md`, `sniper_cross_context_bug_verification.md` eklendi; `backtest_canli_farklari_31_07_2026.md` silindi. Commit: `a6b0667`, push edildi. |
| 2026-07-31 | **Giriş parity tam + CI regression testi** | `bot.py` bias_reject bloğu (kilitsiz TRIGGER_READY reset) + `signal_engine.py` coin-bazlı session penceresi. 9 sembol core-diff=0, TRIGGER/sweep-lock birebir. `tests/parity/test_parity_regression.py` (9 test, 379s). Commit: `a47b8ae`. |
| 2026-07-31 | cbdr_locked bağımlılığı düzeltmesi | `signal_engine.py:77` `on_sweep()` artık `ss.sweep_confirmed` koşuluyla (`analyzer_v5.py:266`); `bot.py:419-443` progress_rsm her bar çağrılır, display_sweep_status entry kapısı değil, cbdr_locked engeli evaluate_trigger öncesi. |
| 2026-07-30 16:21 | paper_trade_logger — append-only JSONL paper trade logger | `src/paper_trade_logger.py` (+135 satır). `EventType` enum (15 tip), `configure()`, `log_event()` schema v1. Bloklar: entry, protection, fvg, validation, error, result, reason, latency_ms, call_count, protected_state. 4 modüle entegre: bot.py, entry_manager.py, exit_lifecycle.py, trailing_manager.py. Mevcut events.json/trades_history.jsonl değişmedi. |
| 2026-07-30 15:50 | Binance rejection failure simulator for execute_live_entry | `tests/failure_simulator.py` + `tests/test_initial_protection_failures.py` eklendi. Deterministic FakeExchange ile SL -2021, SL generic exception, partial fill, emergency close failure, direction validation, protected-state, no-TP-after-SL-fail senaryoları. 15 yeni test, 84/84 entry_manager suite geçiyor. Commit: `8c5b7f0`. |
| 2026-07-30 14:24 | calculate_sl_tp dead code temizliği + max_risk_dist override kaldırıldı | `max_risk_dist = risk_pts * cfg.MAX_SL_DIST_MULT` + 2 override bloğu silindi (GMXUSDT FVG-anchor SL ezmesi). `symbol` parametresi ölüydü (MIN_SL_DISTANCE_PCT_MAP commit 5c8e4f4'te zaten silinmiş). `apply_min_sl_distance()`'dan `symbol` kaldırıldı. `bot.py` + `entry_manager.py` çağrıları temizlendi. |
| 2026-07-28 18:47 | Live trailing_manager guard push — 294f7e8 | Guard local'de uncommitted'ti. Push edildi: ref_price/min_dist check. Sunucuda git pull => trailing_manager'ye 8 satır gelir. Backtest(9a2c0bc) ile live(294f7e8) parity sağlandı. |
| 2026-07-27 23:00 | exec_sim analyzer_v5 entegrasyonu — 2 bug fix + mimari bulgu | Bug #1: sa.append(t) eksik → trades active'dan düşüyordu. Bug #2: PROFIT_TRAIL misclassification → PTrail% 55→5'e düştü. Mimari bulgu: exec_sim SL exit'te değil TRAILING'de uygulanmali. |
| 2026-07-27 22:00 | exec_sim backtest koşusu (buggy) | 26,395 trade, PnL -993,753, PF ~0.22. Bug #2'nin etkisi: PTrail% çöktü, strateji karlılığı yok edildi. |
| 2026-07-27 20:00 | Canlı paper trade log analizi | trades_history: 298 trade, PnL -$346.50, WR %23. 99 WS_FALLBACK (-$142), 111 SL (-$294). OPUSDT qty=0.1 tespit edildi. |
| 2026-07-27 19:00 | Canlı log incelemesi (paper_trade.log) | -2021 rejections SL TRAILING sırasında oluyor (GMXUSDT ağırlıklı). Stale event'ler WS gecikmesi kaynaklı. |
| 2026-07-27 18:03 | Fibo filter backtest tamamlandı | 29,982 trade, PnL +1,845,884, PF ~6.5, holdout PASSED. |
| 2026-07-26 10:38 | D-2 Fark 1: exit_now guard kaldırıldı (P2-6/P2-7 kök neden fix) | `trailing_manager.py:evaluate_trail()` — new_sl >= current.close → exit_now guard'ı analyzer_v5 backtest ile uyumlu kaldırıldı. 2 regression test (long + short). 81/81 test geçti. |
| 2026-07-26 15:05 | P0-1 FULL FIX: flag temizliği, legacy silme, idempotency guard, per‑trade lock | `bot.py`, `config.py`, `state_writer.py`, `exit_lifecycle.py`, `test_exit_lifecycle.py` güncellendi. `_exit_reason_log` (entry_bar_index+entry_price bazlı) + per‑trade asyncio.Lock. 3 yeni P0-1 test + 31/31 suite geçti. Commit: `440125c`. |
| 2026-07-27 17:20 | FVG fibo matched pair filtresi eklendi | `retrace_state.py` — swing high/low'tan fibo level, matched pair (bullish+0.236, bearish+0.786) filtresi. Commit: `a2eade1`. |
| 2026-07-27 18:03 | Fibo filter backtest tamamlandi | Trade 103K→30K (-71%), PnL/trade 35.6→61.6 (+73%), ort PF ~3.4→~6.5 (+91%), holdout PASSED (PF ratio 2.31, WR 73%). |
| 2026-07-27 15:50 | P1-15 stale event mitigation uygulandı | 3 aksiyon: -2021 sinyal (order_manager + exit_lifecycle), stale cooldown (30sn), GMXUSDT SL 0.15%→0.30%. Commit: `ed024c3`. 558 passed, 0 new regression. |
| 2026-07-27 15:33 | P1-15 bugs.md kök neden güncellemesi | bugs.md P1-15 bölümü yeniden yazıldı: WS FILLED gecikmesi 87-353s, GMXUSDT orantısı etkilenmiş, 3 mitigation önerisi. Özet tablosu 🐛'a taşındı. Commit: `d40caf7`. |
| 2026-07-26 15:22 | bugs.md temizliği: ✅ maddeler archive'e taşındı, P0-1/P1-14b detay güncellendi, output/server_bot.py silindi | `bugs.md`, `bugs_archive.md` güncellendi. Git: `e6ed18e`. |
| 2026-07-26 09:25 | P1-15 check_exit teorisi çürütüldü, repr() debug log aktif | CSV high=0.0447 < sl=0.044729 → check_exit tetiklenmemeli. WS handler + recovery_manager elendi. [P1-15_DEBUG] repr(high/sl) WARNING log eklendi, stale event bekleniyor. |
| 2026-07-26 08:15 | P1-15 SEIUSDT stale event kök neden doğrulaması (AŞAĞI ÇEKİLDİ) | csv.writer precision reddedildi ama check_exit teorisi de CSV ile çelişiyor — repr() ile çözülecek. |
| 2026-07-25 23:41 | export_ohlc_1m pozisyonsuz bar'lara taşındı | `bot.py:455-460` — `_on_1m_close()`'da export, trade guard'inden önceye alındı. Her 1m bar CSV'ye düşer. |
| 2026-07-25 22:15 | Bug Registry bölünmesi | `bugs.md` → `bugs.md` (17 aktif) + `bugs_archive.md` (19 sabit). Commit: `8155ada` |

## Çalışanlar ✅

| Bileşen | Durum |
|---------|-------|
| PaperTrader orchestrator (`bot.py`) | ✅ Testnet emir gönderimi aktif |
| CBDR → Sweep → FVG → Entry flow | ✅ ICT fix uygulandı |
| SignalEngine (primary entry) | ✅ Bias + session filtresi + close guard + wick ratio > 0.75 |
| ~~RetradeEngine (retrade entry + LHR fallback)~~ | ❌ Silindi (V3) |
| TrailingManager (1m FVG trailing) | ✅ Close-teyitli FVG trailing + exit_now guard kaldırıldı (D-2 F1, analyzer_v5 uyumlu) |
| EntryManager (live order placement) | ✅ Market + SL(StopMarket) + TP(TakeProfitMarket) |
| OrderManager (trailing update + repair) | ✅ Önce yeni order, sonra eski cancel |
| OrderManager (cancel_all_open_orders) | ✅ Exit öncesi tüm emirleri iptal |
| **client_order_id traceability** | ✅ `place_market_order()` parametre eklendi, tüm callers güncellendi |
| RecoveryManager (startup recovery) | ✅ Pozisyon import + tüm türlerden orphan cleanup |
| RecoveryManager (ATR integration) | ✅ indicators.py Wilder's ATR entegre |
| P0-5: -4005 max quantity infinite loop fix | ✅ closePosition=True SL/TP, CB bypass, qty splitting, backoff |
| UserDataHandler (WS callbacks) | ✅ ORDER_TRADE_UPDATE + ACCOUNT_UPDATE |
| BinanceWSHub (multi-symbol WS) | ✅ Auto-reconnect + heartbeat |
| SessionState (CBDR + Range + TradeDay) | ✅ Gövde bazlı CBDR, retrade alanları temizlendi |
| RetraceStateMachine (IDLE→SWEEP→TRIGGER) | ✅ Sweep dedup (restart-proof), `unmark_sweep_used` silindi |
| state_manager (disk-persistent state) | ✅ trade_state.json + sweep consumption lock |
| state_writer (dashboard JSON) | ✅ live_state.json, her 15m güncellenir |
| trade_exporter (trade geçmişi) | ✅ trades_history.jsonl, bot okumaz |
| trades_history.jsonl yazma | ✅ `_exit_trade`'de append + `_load_history()` restart yükleme |
| Hybrid SL buffer | ✅ `FVG_BUFFER_MIN_FACTOR` aktif, `MAX_SL_DIST_MULT` tavanı |
| chart_export (Plotly HTML chart) | ✅ CBDR box, sweep mum, FVG+CE, trail adimlari, session damgasi |
| trail_steps kaydi | ✅ Her trailing adimi trade dict'ine {sl, tp, fvg_top, fvg_bot, bar} |
| ConsoleReporter (TR time, dedup) | ✅ Şeffaf console çıktısı |
| Pre-commit hooks | ✅ ruff (linter + formatter), vulture |
| event_log (yapısal JSONL log) | ✅ `src/event_log.py` — `log_event()` + `cleanup_old_event_logs()` |
| paper_trade_logger (append-only JSONL) | ✅ `src/paper_trade_logger.py` — `EventType` enum, `configure()`, `log_event()` schema v1, 4 modüle entegre |
| RiskManager (dinamik risk + devre kesici) | ✅ `src/risk_manager.py`, filelock thread-safe, 1.5x EL çarpanı |
| Real CBDR threshold analysis (3 session) | ✅ Parquet tabanlı, `detect_phase()` ile kodun gerçek faz sınırları |
| Erken London avantajı doğrulama | ✅ 13/13 coin, tutarlılık %100, EL PF=4.35 vs non-EL PF=2.52 |
| Portföy MaxDD sweep | ✅ Günlük birleştirilmiş equity eğrisi, 1.0x-5.0x taraması |
| backupCount=7→14 | ✅ `TimedRotatingFileHandler`'da 14 gün saklama |
| event log noktaları | ✅ entry/exit/force_close (bot.py), orphan/ghost (recovery_manager.py), sl_reject/tp_reject (order_manager.py) |
| Backtest → live bot trailing portu | ✅ `_fvg_close_confirmed()`, ATR buffer, TRAIL_MIN_MOVE_MULT, break-even `analyzer_v3.py`'a eklendi |
| FVG marker fix | ✅ `_save_fvg_state()` bar_index hatası düzeltildi |
| BE chart bar index fix | ✅ evaluate_break_even 15m bar index kullanıyor |
| Sweep level ActiveTrade | ✅ `sweep_level` field + `_try_entry()` beslemesi |
| on_sweep_confirmed rewrite | ✅ sweep invalidation gate + no reset on no-FVG + no unconditional reset |
| Binance rejection failure simulator (FakeExchange + 15 test) | ✅ `tests/failure_simulator.py`, `tests/test_initial_protection_failures.py` — SL -2021, exception, partial fill, close fail, direction validation, protected state. 84/84 entry_manager test. |
| P1-8 debug log canlıya deploy | ✅ `log.warning` level, TIAUSDT vakasında ilk sonuç alındı |
| `_fmt_price()` dinamik fiyat formatlama | ✅ `bot_infra.py` — küçük fiyatlı coinlerde SL/TP artık ayırt edilebiliyor |
| OHLC export path sabitleme | ✅ `bot_infra.py` — `export_ohlc_1m`/`export_ohlc_15m` artık `_OUTPUT_DIR` (script dizinine göre) kullanıyor, çalışma dizinine bağlı relative path sorunu düzeltildi |
| `entry_log_msg` + `[PAPER]` log `_fmt_price()` güncellemesi | ✅ `entry_manager.py` + `bot.py` — tahmini fiyat yerine gerçek fiyat, düzgün ondalık basamak |

## Devam Eden Soruşturmalar 🔍

| Soruşturma | Durum | Sonraki adım |
|-----------|-------|-------------|
| **P1-15 stale event kök neden** | ✅ Mitigation uygulandı — -2021 sinyal, stale cooldown, GMXUSDT SL genişletme. Commit: `ed024c3`. | Canlı testte stale event sayısında düşüş bekleniyor. |
| **P1-8 post_entry_check fail** | 🔍 Kök neden #1 (hızlı fill → false positive) tespit edildi | Diğer vakalarda `raw_orders_count` bekleniyor |
| SNIPER_OUTPUT_DIR izolasyon | ✅ Backtest output/ klasörü production'dan ayrı |
| update_trail_orders signature | ✅ `new_sl/tp/trail_count` param + paper mod güncellemesi + `apply_price_precision` içe taşındı |
| Trailing partial success | ✅ `sl_ok or tp_ok` → `trailing_count` güncellenir, tek başarısızlıkta `False` dönme kaldırıldı |
| _exit_trade active_trades.pop | ✅ `pop` fonksiyon başına taşındı — atomik guard + çift exit koruması |
| max_wick_ratio kaldırıldı | ✅ `evaluate_trail()` + `find_fvgs()` çağrısından silindi |
| Wick ratio guard doğru katmana | ✅ signal_engine'dan silindi, RSM init'e `max_wick_ratio=0.75` eklendi — FVG tespitinde impulse bar kontrolü |
| Dinamik FVG eşiği | ✅ `FVG_MIN_SIZE_ATR_MULT × atr_val` (eskiden statik FVG_SIZE_MAP) |
| Session Router (session_router.py) | ✅ `get_cbdr_multiplier()`, `should_trade()`, `is_high_quality_fvg()`, `is_fvg_valid()`, `get_session_hours()` |
| CBDR Risk Matrisi (13 coin × 6 bucket) | ✅ `config.py`'de `CBDR_RISK_MATRIX`, backtest WR/BE+/PnL verisiyle dolduruldu |
| CBDR Risk Matrisi 10 yeni coin (2026-07-15) | ✅ TIA/SEI/ONDO/PYTH/RENDER/ENA/STRK/GMX/DYDX/LDO eklendi. ASIA_RANGE=7, DEFAULT=3. |
| FVG_SIZE_MAP 10 yeni coin (2026-07-15) | ✅ Sweep ile optimum değerler bulundu: DYDX=0.040, ENA/GMX/LDO=0.020, ONDO=0.040, PYTH=0.130, RENDER/SEI/TIA=0.070, STRK=0.060. |
| FVG_MIN_SIZE_ATR_MULT güncellendi (2026-07-15) | ✅ 0.08→0.06 (analyze_cbdr_thresholds.py ile aynı). |
| SYMBOLS 10 yeni coin (2026-07-15) | ✅ 28 sembole genişletildi. |
| 3 katmanlı risk motoru | ✅ Zaman(EL) × Kurulum(CBDR bucket) × Portföy(devre kesici). Defense mode: EL+Elite iptal |
| Coin bazlı SessionState | ✅ Her coin `CBDR_RISK_MATRIX['session']` üzerinden kendi optimal saatlerini alır |
| BOT_SESSION kaldırıldı | ✅ Yerine coin bazlı session assignment |
| NaN fix + MIN_FVG_SIZE temizlik | ✅ Kullanılmayan sabitler silindi, NaN koruması eklendi |
| Dinamik ATR bazlı FVG filtresi | ✅ `is_high_quality_fvg()` — `MIN_REL_FVG_THRESHOLD=0.50` |
| FVG expiry filter | ✅ `GLOBAL_FVG_EXPIRY_BARS=45`, `is_fvg_valid()` |
| Session assignment (13 coin) | ✅ DEFAULT=8, REAL_CBDR=2, ASIA_RANGE=3 → **10 yeni coin eklendi (toplam 28)** |
| ETHUSDT/SUIUSDT geri eklendi | ✅ DEFAULT session'a atandı |
| CBDR_RISK_MATRIX final commit | ✅ 13 coin bucket eşikleri + çarpanları tamamlandı |
| bot.py _session_label ASIA fix (backtest uyumu) | ✅ `_session_label()`'deki 22-02="ASIA" blokajı kaldırıldı. Artık coin bazlı CBDR blokajı backtest'le birebir aynı. REAL_CBDR coin'lerde 01:00-02:00 arası hatalı blok düzeldi. |
| peak_equity geri alma fix (e6ef7fe) | ✅ pos_closed=False'ta hayali PNL ile peak_equity şişmesi engellendi |
| force close trigger yönü fix (31c5e19) | ✅ long→cur_price×1.01, short→cur_price×0.99 (tersi reddedilirdi) |
| dust closePosition fallback (06067c6) | ✅ market close başarısızsa closePosition ile kapanış denemesi (bot.py + recovery_manager.py) |
| Unit test: pos_closed=False revert | ✅ balance revert + peak_equity rollback + peak korunma (2 test) |
| Unit test: dust closePosition fallback | ✅ place_force_close_order çağrısı doğrulandı |
| Unit test: force_close trigger yönü | ✅ long/short yön + zero price + API error (4 test) |
| Unit test: recovery_manager closePosition | ✅ market fail→force close + success passthrough + her ikisi başarısız + exception (4 test) |
| ExitLifecycleService extraction | ✅ `src/trading/exit_lifecycle.py` (557 satır), `EXIT_LIFECYCLE_SERVICE_ENABLED` flag, DI `exit_service`, `_exit_trade_legacy` rename |
| ExitLifecycleService unit tests | ✅ 24 test — WS-FALLBACK guard, paper-mode skip, adapter ambiguity (5 senaryo), verification loop (fail/success), REPAIR_REQUIRED, _commit_confirmed_exit (long/short PnL, cleanup) |
| Wiring tests (bot.py routing) | ✅ 3 test — flag=True→exit_service.execute, flag=False→_exit_trade_legacy, flag default False. Scope fix: patch'in `_exit_trade` çağrısını kapsaması sağlandı. |
| **P1: State split model tanımları** | ✅ `models.py`: 8 yeni tip/container. Henüz ActiveTrade'e bağlı değildi — B1/B2 sonra bağladı. |
| **P3: Protection lifecycle extraction** | ✅ `protection_lifecycle.py` (+265 satır). `ProtectionLifecycleService` + `ProtectionCheckResult` + `CleanupPlan`. `PROTECTION_LIFECYCLE_SERVICE_ENABLED` flag. |
| **P4: WS normalization** | ✅ `user_data_handler.py` (+238). `normalize_order_event()` pipeline. `pending_exit_*` alanlarına yazma. `WS_EVENT_NORMALIZATION_ENABLED` flag. |
| **P5: bot.py orchestration cleanup** | ✅ `_on_1m_close` — orphan sweep status'tan bağımsız, ATR unrestricted içine, UPNL her bar. |
| **P6: Operator visibility** | ✅ `state_writer.py` — `frozen` + `feature_flags` çıktısı. |
| **B1: ActiveTrade runtime bağlantısı** | ✅ `models.py` — `TradeRuntimeState` → `ActiveTrade.runtime`. Dict yönlendirme: status/frozen/pending_events. |
| **B2: ProtectionState → runtime.protection** | ✅ `models.py` — 6 protection field'ı `runtime.protection` object üzerinden. `_PROTECTION_MAP`. |
| **B3: ProtectionCheckResult tuple→dataclass** | ✅ `order_manager.py` — `verify_protection()` dönüş tipi değişti. `__iter__` backward compat. |
| **fix: HTFFVG bar_index** | ✅ `bot.py` — `real_index` → `bar_index` (FVG expiry). |
| **D1: ProtectionState lifecycle status** | ✅ `models.py` + `state_writer.py` — `sl_status`, `tp_status`, `protection_health`. |
| **C: Explicit lifecycle states (9d0e72b)** | ✅ `STATUS_EXIT_REQUESTED`, `STATUS_EXIT_SUBMITTED`, `STATUS_CLOSED` eklendi. `update_trail_orders()` → `TRAIL_REPLACING`/`ACTIVE`. bot.py + exit_lifecycle.py state machine sync. |
| **E: Chaos tests (9d0e72b)** | ✅ 4 edge-case test: delayed fill, REST timeout, force close fallback, state transition verification. |
| **fix: close 3 review findings (594f6f3)** | ✅ 3 system review bulgusu kapatıldı. |
| **P1-1: repair_protection stale SL fallback** | ✅ `order_manager.py` — SL/TP basarisizsa mark_price + risk_pts ile yeniden hesapla (recover_positions ayni mantik) |
| **P1-4: periodic orphan sweep** | ✅ `recovery_manager.py:periodic_check_loop()` — orphan sweep periyodik olarak calistiriliyor (portfolio flat iken sayac duruyordu) |
| **P0-4: restart REPAIR_REQUIRED cleanup** | ✅ `bot.py:run()` — recover_positions sonrasi stuck trade'leri ACTIVE'e dondur eger SL/TP saglikli |
| **P0-3: repair_protection per-symbol lock** | ✅ `order_manager.py`: per-symbol `asyncio.Lock`. Wrapper + `_repair_protection_locked` rename. Eşzamanlı çağrı atlanır, farklı semboller bloklanmaz. 3 concurrency test. |
| **P2-4: self-exit race guard** | ✅ `user_data_handler.py`: `_SELF_EXIT_IN_PROGRESS_STATUSES` guard. EXIT_SUBMITTED/EXIT_VERIFYING durumunda unmatched reduceOnly fill WS_FALLBACK'e çevrilmeyip sessizce loglanıyor. raise → log_event + log.critical'e çevrildi (ACTIVE senaryosunda). 5 test (3 guard + 1 ACTIVE fallback + 1 regression). |
| **P2-5: update_trail_orders -4005 fallback + backoff** | ✅ `order_manager.py`: SL/TP placement -4005 hatasında closePosition → split_qty fallback. `error_code` log_event'a eklendi. `_trail_failures` backoff: 3 ardışık başarısızlık → 5dk bekle + CRITICAL uyarı. 8 yeni test. |
| **client_order_id traceability** | ✅ `place_market_order()` parametre eklendi, tüm callers güncellendi. Semantic prefix'ler: entry-, exit-, sl-fail-, reconcile-, recover-. |
| **Görev 3: Post-entry sanity check** | ✅ `bot.py:_try_entry()` — entry sonrası ~2.5s bekleme + SL/TP Binance'te açık mı doğrulaması. Eksikse CRITICAL log + `post_entry_check_failed` event. |
| **Görev 4: FVG invalidation exit_intent** | ✅ `bot.py:_on_1m_close()` — FVG kirildi→market close path'ine `log_event("exit_intent", reason="fvg_invalidated")`. |
| **P0-7: tp_unchanged TP iptal fix + precision-residual churn (cc6e48d)** | ✅ `order_manager.py`: `tp_ok and not tp_unchanged` guard + precision-sonrasi `sl/tp_really_unchanged` erken return. `evaluate_trail()` tick-altı rezidüde sonsuz tetiklenmeyi durdurur. 4 regresyon testi (TestTpUnchangedNoChurn + TestPrecisionResidualNoChurn). |
| **D-2 Fark 1: exit_now guard kaldırıldı (2026-07-26)** | ✅ `trailing_manager.py:evaluate_trail()` — new_sl >= current.close → exit_now guard'ı kaldırıldı. analyzer_v5 backtest ile aynı davranış. P2-6/P2-7 kök nedeni giderildi. 2 regression test (long + short). 81/81 test. |

## Kalan İşler 🔧

| Görev | Öncelik | Açıklama |
|-------|---------|----------|
| **exec_sim kapsam düzeltmesi** | 🔴 Kritik | SL exit'te exec_sim'i muaf tut, sadece trailing operation'a uygula. Backtest artık gerçekçi karlılık gösterecek. |
| **REST API fallback entegrasyonu** | 🔴 Kritik | WS 300ms'de gelmezse REST ile teyit. 99 WS_FALLBACK → ~$142 kaybın büyük kısmını kurtarır. |
| Canlı test: _exit_trade() flow | 🟠 Yüksek | cancel_all + reduceOnly + verify loop |
| Backtest trailing port WR/DD canlı karşılaştırması | 🟡 Orta | Live WR vs backtest WR farkı analiz edilecek |
| CBDR_RISK_MATRIX canlı doğrulaması | 🟡 Orta | Bucket çarpanlarının gerçek PnL'e uyumu kontrol edilecek |
| Session assignment canlı gözlem | 🟡 Orta | DEFAULT/REAL_CBDR/ASIA_RANGE geçişlerinde FVG bulunamama sorunu tekrarlarsa analiz |
| OPUSDT minNotional qty sorunu | 🟡 Orta | qty=0.1 tespit edildi, minNotional limitinin altında kalabilir |
| Mainnet canlı test | 🟢 Düşük | URL + API key değişikliği |
| Performance benchmark | 🟢 Düşük | CPU/memory profil |
| README güncelleme | 🟢 Düşük | Sadece ihtiyaç halinde |
| FVG marker konum bug çözümü | 🟡 Orta | chart'ta gördüğümüz 3 örnek (SOLUSDT) — kök neden araştırılıyor |
| v3_window_comparison.md yeniden koşumu | 🟡 Orta | Geçersiz çıktı, yeniden çalıştırılacak |
| ict_cbdr_thresholds.md yeniden koşumu | 🟢 Düşük | Sahte ATR ile koşmuş, yeniden koşulacak |
| **Rollout flag aktivasyon planı** | 🟡 Orta | 3 flag => `EXIT_LIFECYCLE_SERVICE_ENABLED`, `PROTECTION_LIFECYCLE_SERVICE_ENABLED`, `WS_EVENT_NORMALIZATION_ENABLED`. Hepsi default False. Sırayla açılacak. |
| **_close_trade_pending_exit() implementasyonu** | 🟠 Yüksek | P4 WS normalization için gerekli — pending_exit promote mekanizması bot.py'de henüz yok. |
| **ProtectionLifecycleService rollout** | 🟡 Orta | P3 default False. Açılmadan önce canlı testte restore edilebilirlik doğrulanmalı. |
| **WS normalization rollout** | 🟡 Orta | P4 default False. Açılmadan önce WS_FALLBACK sayısı baseline alınmalı. |
| **TradeConfirmedState backfill** | 🟢 Düşük | P1'de tanımlanan `TradeConfirmedState` field'ları henüz ActiveTrade flat alanlarına bağlanmadı. |

## Bilinen Sorunlar 🐛

| Sorun | Durum |
|-------|-------|
| HTTP -4130 (açık SL/TP emri çakışması) | 🟡 Precision fix sonrası gözlemlenmeli |
| ~~UNIUSDT restart dongusu (P0-1)~~ | ✅ `exit_lifecycle.py` verify loop fix (c11c785). Belirsiz adapter durumunda son denemeye kadar bekle + `get_all_orders()` fallback. |
| ~~STRKUSDT SL kurulamama (P1-1)~~ | ✅ `repair_protection()` stale SL fallback fix (2e5007a). |
| ~~periodic orphan sweep calismama (P1-4)~~ | ✅ `periodic_check_loop()` orphan sweep fix (2e5007a). |
| ~~REPAIR_REQUIRED restart kilitlenme (P0-4)~~ | ✅ `bot.py run()` restart cleanup fix (2e5007a). |
| ~~FVG_BUFFER_MULT canlı/backtest farkı (0.50 vs 0.25)~~ | ✅ Backtest 0.50'ye güncellendi, trailing portu ile uyum tam |
| ~~Trail prev ID penceresinde WS_FALLBACK~~ | ✅ Fix: `*_order_id_prev` geçiş id'si saklanıyor, WS fill eşleşmesi genişletildi |
| ~~SOLUSDT FVG bar index restart bug~~ | ✅ `_resolve_fvg_bar_index()` fiyat bazlı arama öncelikli yapıldı. Restart sonrası bar indeksleri sıfırlandığında offset formülü (~81 FVG'yi ~77-78 barına işaret ediyordu). |
| ~~console_reporter SyntaxError~~ | ✅ `display_fvg_status()` TRIGGER_READY bloğu indent fix — elif artık if'siz kalmıyor. |
| ~~D-2 Fark 1: exit_now guard (P2-6/P2-7 kök neden)~~ | ✅ `trailing_manager.py:evaluate_trail()` — exit_now guard kaldırıldı. analyzer_v5 backtest uyumlu. 2 regression test. |
| **exec_sim SL exit yanlış uygulama** | 🐛 Backtest'te SL exit'lerde `would_reject_immediately()` neredeyse tüm trade'leri reddediyor → PnL negatif. Çözüm: exec_sim'i sadece trailing operation'a uygula, SL exit muaf. |
| **OPUSDT qty=0.1 minNotional** | 🔍 Canlı paper trade'de qty=0.1 tespit edildi, minNotional limitinin altında kalabilir → entry başarısız olabilir. |

## Test Sonuçları (Backtest — All Coin 2026 Q2)

### Eski (Orijinal SL/TP + trailing)
| Metrik | Değer |
|--------|-------|
| Toplam Trade | 11,355 |
| Toplam PnL | +1,553,539 USDT |
| WR Aralığı | %46.7 - %70.2 (sembole göre) |
| Max DD Aralığı | %5.7 - %19.7 (sembole göre) |
| LINK WR/DD | %52.7 / %13.6 |
| DOT WR/DD | %70.2 / %12.0 |

### Yeni (Live bot SL/TP + trailing port)
| Metrik | Değer |
|--------|-------|
| Toplam Trade | 9,529 |
| Toplam PnL | +1,460,131 USDT |
| WR Aralığı | %38.3 - %62.4 (sembole göre) |
| Max DD Aralığı | %2.0 - %19.1 (sembole göre) |
| BTC WR/DD | %62.4 / %2.0 |
| LINK WR/DD | %38.3 / %19.1 |
| DOT WR/DD | %55.9 / %16.4 |
