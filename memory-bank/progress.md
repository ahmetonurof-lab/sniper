# Progress — Sniper Bot

## Çalışanlar ✅

| Bileşen | Durum |
|---------|-------|
| PaperTrader orchestrator (`bot.py`) | ✅ Testnet emir gönderimi aktif |
| CBDR → Sweep → FVG → Entry flow | ✅ ICT fix uygulandı |
| SignalEngine (primary entry) | ✅ Bias + session filtresi ile |
| ~~RetradeEngine (retrade entry + LHR fallback)~~ | ❌ Silindi (V3) |
| TrailingManager (1m FVG trailing) | ✅ Close-teyitli FVG trailing |
| EntryManager (live order placement) | ✅ Market + SL(StopMarket) + TP(TakeProfitMarket) |
| OrderManager (trailing update + repair) | ✅ Önce yeni order, sonra eski cancel |
| OrderManager (cancel_all_open_orders) | ✅ Exit öncesi tüm emirleri iptal |
| RecoveryManager (startup recovery) | ✅ Pozisyon import + tüm türlerden orphan cleanup |
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
| trail_steps kaydi | ✅ Her trailing adimi trade dict’ine {sl, tp, fvg_top, fvg_bot, bar} |
| ConsoleReporter (TR time, dedup) | ✅ Şeffaf console çıktısı |
| Pre-commit hooks | ✅ ruff (linter + formatter), vulture |
| event_log (yapısal JSONL log) | ✅ `src/event_log.py` — `log_event()` + `cleanup_old_event_logs()` |
| backupCount=7→14 | ✅ `TimedRotatingFileHandler`'da 14 gün saklama |
| event log noktaları | ✅ entry/exit/force_close (bot.py), orphan/ghost (recovery_manager.py), sl_reject/tp_reject (order_manager.py) |
| Backtest → live bot trailing portu | ✅ `_fvg_close_confirmed()`, ATR buffer, TRAIL_MIN_MOVE_MULT, break-even `analyzer_v3.py`'a eklendi |

## Kalan İşler 🔧

| Görev | Öncelik | Açıklama |
|-------|---------|----------|
| Canlı test: _exit_trade() flow | 🟠 Yüksek | cancel_all + reduceOnly + verify loop |
| Backtest trailing port WR/DD canlı karşılaştırması | 🟡 Orta | Live WR vs backtest WR farkı analiz edilecek |
| Mainnet canlı test | 🟢 Düşük | URL + API key değişikliği |
| Performance benchmark | 🟢 Düşük | CPU/memory profil |
| README güncelleme | 🟢 Düşük | Sadece ihtiyaç halinde |

## Bilinen Sorunlar 🐛

| Sorun | Durum |
|-------|-------|
| HTTP -4130 (açık SL/TP emri çakışması) | 🟡 Precision fix sonrası gözlemlenmeli |
| ~~FVG_BUFFER_MULT canlı/backtest farkı (0.50 vs 0.25)~~ | ✅ Backtest 0.50'ye güncellendi, trailing portu ile uyum tam |
| ~~Trail prev ID penceresinde WS_FALLBACK~~ | ✅ Fix: `*_order_id_prev` geçiş id'si saklanıyor, WS fill eşleşmesi genişletildi |

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
