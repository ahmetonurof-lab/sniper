# Active Context — Sniper Bot

## Mevcut Durum (Temiz Başlangıç)

- **Bot çalışıyor mu?**: Testnet'te, canlı emir gönderimi aktif.
- **Testnet bakiyesi**: ~5,000 USDT
- **Sembol sayısı**: 13 (BTC/ETH/BNB/SOL/AVAX/LINK/XRP/ATOM/ADA/SUI/APT/DOT/NEAR)
- **Kaldıraç**: 1x (kaldıraçsız)
- **Strateji**: CBDR → Sweep → FVG Wick Rejection → Entry → Trailing → Exit → Retrade

## Kritik Yapılan Değişiklikler (2026-06-27)

| # | Commit | Açıklama |
|---|--------|----------|
| 1 | `fd21f66` | **ICT sweep fix**: Yukarı sweep (`sweep_direction=bearish`) → SHORT; aşağı sweep (`sweep_direction=bullish`) → LONG. Eskiden aynı yönlüydü (bullish→LONG), ICT gereği ters olmalı. |
| 2 | `d1cebaf` | **Risk tuning**: LINK 1.5/1.0 → 1.0/0.8 (%10 DD hedefi), DOT 1.5/1.0 → 1.2/0.9 (%12 DD hedefi). |
| 3 | `15910cf` | **Qty balance cap**: 1x leverage'da notional > balance → `max_qty = balance / entry_price` ile tavanlanır, Binance -2019 hatası önlenir. |
| 4 | `a6a9999` | **state_writer.py**: Her 15m kapanışında `output/live_state.json` yazar — dashboard ve chart_export için. |

## Aktif Kararlar

- **LEVERAGE=1**: Geri dönülemez karar. Tüm pozisyon büyüklükleri buna göre.
- **``kaldıraçsız strateji``**: Ethos — kaldıraç kullanılmaz.
- **RSM (RetraceStateMachine)**: IDLE → SWEEP_DETECTED → TRIGGER_READY. Sadece 3 state.
- **Max 1 primary + 1 retrade/gün/sembol**: trade_state.json ile korunur.
- **ASIA kapalı**: 22:00-02:00 UTC'de trade alınmaz.
- **FVG_BUFFER_MULT=0.50**: Canlıda 0.50, backtest'te 0.25 (fark bilinçli — canlı daha geniş bant).
- **CBDR gövde bazlı (open/close)**: High/low değil, gövde kullanılır.

## Sıradaki / Açık Konular

- LINK WR %52.7 — yapısal sorun mu yoksa Q1 2026'ya özel mi? Multi-period backtest gerekebilir.
- BTC/ETH geniş SL mesafesi → qty cap sık yiyebilir, risk düşürme gerekebilir.
- `LOG_LEVEL` — canlıda DEBUG mi INFO mu kararı.
- Pre-commit hooks çalışıyor (ruff, vulture). Yeni dosyalarda mypy eklenebilir.

## Hatırlatmalar

- `FVG_BUFFER_MULT` canlı (0.50) vs backtest (0.25) farklı — analiz yaparken dikkat.
- sweep_direction mapping: yukarı sweep = bearish = SHORT, aşağı sweep = bullish = LONG.
- Bot restart edilirse pozisyonlar RecoveryManager üzerinden yüklenir.
