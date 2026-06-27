# Project Brief — Sniper Bot (Paper Trade Orchestrator)

## Proje Tanımı
Binance Futures Testnet üzerinde çalışan, ICT/SMC tabanlı otonom trading botu. CBDR → Sweep → FVG Wick Rejection → Entry → Trailing → Exit → Retrade akışını 15m + 1m timeframe'lerde yürütür.

## Temel Gereksinimler
1. **Tam otonom trade**: Sinyal üretimi → emir gönderimi → pozisyon yönetimi → trailing → exit → retrade, tamamen insansız.
2. **ICT tabanlı sinyal**: CBDR likidite seviyesi → sweep → FVG wick rejection → entry.
3. **Multi-symbol**: 13 USDT-margined futures sembolü eşzamanlı.
4. **1x leverage**: Kaldıraçsız strateji, pozisyon notional'ı bakiyeyle sınırlı.
5. **Restart-proof**: Disk-persistent state (trade_state.json) + Binance pozisyon recovery.

## Proje Kapsamı
- 13 Binance Futures sembolü (testnet)
- PaperTrader orchestrator + 6 yardımcı modül (trading/*)
- WebSocket veri akışı + REST API emir gönderimi
- Her 15m'de güncellenen live_state.json (dashboard için)

## Kapsam Dışı
- Backtesting (ayrı repo: backtest-sniper)
- Spot trading
- Grid/DCA stratejileri
- Mainnet canlı trade (testnet aşaması)
