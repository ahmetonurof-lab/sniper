# Progress — Sniper Bot

## Çalışanlar ✅

| Bileşen | Durum |
|---------|-------|
| PaperTrader orchestrator (`bot.py`) | ✅ Testnet emir gönderimi aktif |
| CBDR → Sweep → FVG → Entry flow | ✅ ICT fix uygulandı |
| SignalEngine (primary entry) | ✅ Bias + session filtresi + close guard + wick ratio > 0.75 |
| ~~RetradeEngine (retrade entry + LHR fallback)~~ | ❌ Silindi (V3) |
| TrailingManager (1m FVG trailing) | ✅ Close-teyitli FVG trailing |
| EntryManager (live order placement) | ✅ Market + SL(StopMarket) + TP(TakeProfitMarket) |
| OrderManager (trailing update + repair) | ✅ Önce yeni order, sonra eski cancel |
| OrderManager (cancel_all_open_orders) | ✅ Exit öncesi tüm emirleri iptal |
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
| output/ gitignore | ✅ exception'lar kaldırıldı, indexten çıkarıldı |
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

## Kalan İşler 🔧

| Görev | Öncelik | Açıklama |
|-------|---------|----------|
| Canlı test: _exit_trade() flow | 🟠 Yüksek | cancel_all + reduceOnly + verify loop |
| Backtest trailing port WR/DD canlı karşılaştırması | 🟡 Orta | Live WR vs backtest WR farkı analiz edilecek |
| CBDR_RISK_MATRIX canlı doğrulaması | 🟡 Orta | Bucket çarpanlarının gerçek PnL'e uyumu kontrol edilecek |
| Session assignment canlı gözlem | 🟡 Orta | DEFAULT/REAL_CBDR/ASIA_RANGE geçişlerinde FVG bulunamama sorunu tekrarlarsa analiz |
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
