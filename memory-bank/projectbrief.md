# Project Brief — Sniper Bot (NEXUS V4)

## Proje Tanımı
Sniper, NEXUS V4 trading botunun bağımsız ve taşınabilir bir dağıtımıdır. Binance Futures Testnet üzerinde SMC (Smart Money Concepts) stratejilerini otonom olarak çalıştırır.

## Temel Gereksinimler
1. **Tam otonom trade**: IDLE → WAIT_RETRACE → WAIT_CONFIRM → READY_TO_ENTER → ENTERED state zinciri.
2. **SMC tabanlı sinyal üretimi**: Sweep → MSS → FVG → Retrace → LTF Confirm.
3. **Çoklu timeframe analizi**: D1 bias, H4 swing, H1 likidite, 15m işlem, 1m onay.
4. **WebSocket bağlantısı**: 19 sembol × 4 timeframe (1m, 15m, 1h, 4h).
5. **V40 Sniper Flow**: 5 state, tek SNIPER GATE.

## Proje Kapsamı
- 19 Binance Futures sembolü (Testnet)
- V40 sadeleştirilmiş state machine
- Canlı emir gönderimi (MARKET + SL/TP)
- Runtime monitoring + performance takibi

## Kapsam Dışı
- Backtesting (Sonnet tarafında yapılır)
- Spot trading
- Grid/DCA stratejileri
