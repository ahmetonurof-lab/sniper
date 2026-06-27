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
| ConsoleReporter (TR time, dedup) | ✅ Şeffaf console çıktısı |
| Pre-commit hooks | ✅ ruff (linter + formatter), vulture |

## Kalan İşler 🔧

| Görev | Öncelik | Açıklama |
|-------|---------|----------|
| LINK multi-period backtest | 🟡 Orta | WR %52.7 — yapısal/Q1 2026 farkı |
| Mainnet canlı test | 🟢 Düşük | URL + API key değişikliği |
| Performance benchmark | 🟢 Düşük | CPU/memory profil |
| README güncelleme | 🟢 Düşük | Sadece ihtiyaç halinde |

## Bilinen Sorunlar 🐛

| Sorun | Durum |
|-------|-------|
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
