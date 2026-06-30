# Progress — Sniper Bot

## Çalışanlar ✅

| Bileşen | Durum |
|---------|-------|
| PaperTrader orchestrator (`bot.py`) | ✅ Testnet emir gönderimi aktif |
| CBDR → Sweep → FVG → Entry flow | ✅ ICT fix uygulandı |
| SignalEngine (primary entry) | ✅ Bias + session filtresi ile |
| RetradeEngine (retrade entry + LHR fallback) | ✅ Arm → sweep detect → FVG → LHR |
| TrailingManager (1m FVG trailing) | ✅ Progressive FVG iteration |
| EntryManager (live order placement) | ✅ Market + SL(StopMarket) + TP(TakeProfitMarket) |
| OrderManager (trailing update + repair) | ✅ Önce yeni order, sonra eski cancel |
| RecoveryManager (startup recovery) | ✅ Pozisyon import + ghost cleanup |
| UserDataHandler (WS callbacks) | ✅ ORDER_TRADE_UPDATE + ACCOUNT_UPDATE |
| BinanceWSHub (multi-symbol WS) | ✅ Auto-reconnect + heartbeat |
| SessionState (CBDR + Range + TradeDay) | ✅ Gövde bazlı CBDR |
| RetraceStateMachine (IDLE→SWEEP→TRIGGER) | ✅ Sweep dedup (restart-proof) |
| state_manager (disk-persistent state) | ✅ trade_state.json |
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

## Kalan İşler 🔧

| Görev | Öncelik | Açıklama |
|-------|---------|----------|
| Backtest hybrid SL buffer | 🟡 Orta | WR/PF değişimi bekleniyor |
| LINK multi-period backtest | 🟡 Orta | WR %52.7 — yapısal/Q1 2026 farkı |
| Mainnet canlı test | 🟢 Düşük | URL + API key değişikliği |
| Performance benchmark | 🟢 Düşük | CPU/memory profil |
| README güncelleme | 🟢 Düşük | Sadece ihtiyaç halinde |

## Bilinen Sorunlar 🐛

| Sorun | Durum |
|-------|-------|
| HTTP -4130 (açık SL/TP emri çakışması) | 🟡 Precision fix sonrası gözlemlenmeli |
| FVG_BUFFER_MULT canlı/backtest farkı (0.50 vs 0.25) | 🟡 Bilinçli fark, analiz yaparken dikkat |

## Test Sonuçları (Backtest — All Coin 2026 Q2)

| Metrik | Değer |
|--------|-------|
| Toplam Trade | 11,355 |
| Toplam PnL | +1,553,539 USDT |
| WR Aralığı | %46.7 - %70.2 (sembole göre) |
| Max DD Aralığı | %5.7 - %19.7 (sembole göre) |
| LINK WR/DD | %52.7 / %13.6 |
| DOT WR/DD | %70.2 / %12.0 |
