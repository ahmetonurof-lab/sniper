# Active Context — Sniper Bot

## Mevcut Durum (Temiz Başlangıç)

- **Bot çalışıyor mu?**: Testnet'te, canlı emir gönderimi aktif.
- **Testnet bakiyesi**: ~5,000 USDT
- **Sembol sayısı**: 13 (BTC/ETH/BNB/SOL/AVAX/LINK/XRP/ATOM/ADA/SUI/APT/DOT/NEAR)
- **Kaldıraç**: 5x
- **Strateji**: CBDR → Sweep → FVG Wick Rejection → Entry → Trailing → Exit → Retrade

## Kritik Yapılan Değişiklikler (2026-06-27)

| # | Commit | Açıklama |
|---|--------|----------|
| 1 | `d6a8955` | **Quantity precision fix**: `place_market_order`, `place_stop_order`, `place_tp_order` fonksiyonlarında quantity string formatında gönderiliyor. `_get_precision_places()` ile dinamik decimal places hesaplanıyor. BTCUSDT/AVAXUSDT HTTP -1111 hataları çözüldü. |
| 2 | `fd21f66` | **ICT sweep fix**: Yukarı sweep (`sweep_direction=bearish`) → SHORT; aşağı sweep (`sweep_direction=bullish`) → LONG. Eskiden aynı yönlüydü (bullish→LONG), ICT gereği ters olmalı. |
| 3 | `d1cebaf` | **Risk tuning**: LINK 1.5/1.0 → 1.0/0.8 (%10 DD hedefi), DOT 1.5/1.0 → 1.2/0.9 (%12 DD hedefi). |
| 4 | `15910cf` | **Qty balance cap**: 1x leverage'da notional > balance → `max_qty = balance / entry_price` ile tavanlanır, Binance -2019 hatası önlenir. |
| 5 | `a6a9999` | **state_writer.py**: Her 15m kapanışında `output/live_state.json` yazar — dashboard ve chart_export için. |
| 6 | `38436b7` | **availableBalance**: `get_balance()` artık walletBalance değil availableBalance döndürür. |
| 7 | `270ea7f` | **Formül düzeltmesi**: `qty = (balance × risk_pct) / risk_dist / leverage` → `qty = (balance × risk_pct) / risk_dist`. Margin leverage ile ayarlanır, qty'yi etkilemez. LEVERAGE 1→5. |
| 8 | `658e7f6` | **state_writer.py**: `fvg_ready` (sweep sonrası FVG bulundu mu) ve `upnl` (anlık kâr/zarar) alanları eklendi. |
| 9 | `c661283` | **trade_exporter.py**: Kapanan her trade `output/trades_history.jsonl`'a yazılır. Bot okumaz, sadece append eder. İçerik: sym, side, entry/exit, SL/TP, exit_reason, trailing_count, PnL, CBDR, sweep, FVG, timestamp. |
| 10 | `9c01c0a` | **1:2 R:R fix**: `calculate_sl_tp`'de London high/low TP override kaldırıldı. TP artık `risk_dist × tp_rr (2.0)` ile hesaplanır. Trailing'de SL kaydıkça TP aynı orantıda kayar (zaten vardı). |
| 11 | `9d0932b` | **chart_export.py**: Her kapanan trade için `dashboard/charts/SYM_YYYY-MM-DD_HHMM.html` Plotly chart basar — CBDR box, sweep mum, FVG+CE, trail adımları, session damgası. Dashboard'a "Geçmiş Tradeler" paneli + CHART linki. |
| 12 | `f30760f` | **trail_steps kaydı**: Her trailing adımı `trade["trail_steps"]`'e eklenir: `{sl, tp, fvg_top, fvg_bot, bar}`. `trail_steps` field'da `field(default_factory=list)` — hiç `None` dönmez. |
| 13 | `ddd8367` | **chart_export sıralaması**: `_exit_trade`'de `export_chart` → `trade["chart_file"]` → `export_trade` (JSONL chart_file içerir). `exit_bar` `.get("exit_bar", 0)` ile güvenli erişim. WS handler'da `trade["exit_timestamp"]` set edilir. |
| 14 | `83127a7` | **Buying power tavanı**: `calculate_qty` artık `max_qty = balance × leverage / entry_price` ile tavanlanır — market order "insufficient balance" hatası önlenir. |
| 15 | `90f1b39` | **%5 emniyet payı**: `max_qty = balance × leverage × 0.95 / entry_price`. `SAFETY_MARGIN=0.95` — açık emir, ücret, marj farklarına karşı buffer. |
| 16 | `02ce89a` | **Balance ayırımı**: `wallet_balance` (WS'den gelen wb, sadece görüntüleme) ve `available_balance` (REST availableBalance, position sizing). Entry öncesi REST'ten taze `availableBalance` çekilir. WS handler sadece `wallet_balance`'ı günceller, sizing'i etkilemez. |
| 17 | `7c94eca` | **minNotional validation**: `get_min_notional()`, `validate_min_notional()`, `estimate_market_price()` eklendi. Tüm order fonksiyonlarında (market/stop/tp) cap → round → minQty → minNotional → send_order akışı. `execute_live_entry()`'de de erken uyarı için kontrol. |
| 18 | *uncommitted* | **minNotional floor refactor**: `calculate_qty()`'ye `min_notional` parametresi (varsayılan 0.0). `execute_live_entry()` REST'ten min_notional çeker, floor uygular `(min_notional × 1.05 / entry_price)`, eski `validate_min_notional()` çağrısı silindi. `place_market_order()`'dan da minNotional kontrolü kaldırıldı — tekil sorumluluk. |

## Aktif Kararlar

- **LEVERAGE=5**: 5x kaldıraç, margin = notional / 5. Formülde `/leverage` yok — qty = balance × risk_pct / risk_dist.
- **RSM (RetraceStateMachine)**: IDLE → SWEEP_DETECTED → TRIGGER_READY. Sadece 3 state.
- **Max 1 primary + 1 retrade/gün/sembol**: trade_state.json ile korunur.
- **ASIA kapalı**: 22:00-02:00 UTC'de trade alınmaz.
- **FVG_BUFFER_MULT=0.50**: Canlıda 0.50, backtest'te 0.25 (fark bilinçli — canlı daha geniş bant).
- **CBDR gövde bazlı (open/close)**: High/low değil, gövde kullanılır.
- **minNotional floor `execute_live_entry`'de**: REST'ten min_notional çekilip qty'ye floor atılır. `place_market_order` artık minNotional kontrolü yapmaz — tekil sorumluluk.

## Sıradaki / Açık Konular

- **minNotional edge case**: Floor sonrası qty'nin minQty'nin altında kalması durumu canlıda gözlemlenmeli (ör: aşırı düşük fiyatlı coin).
- LINK WR %52.7 — yapısal sorun mu yoksa Q1 2026'ya özel mi? Multi-period backtest gerekebilir.
- `LOG_LEVEL` — canlıda DEBUG mi INFO mu kararı.

## Hatırlatmalar

- `FVG_BUFFER_MULT` canlı (0.50) vs backtest (0.25) farklı — analiz yaparken dikkat.
- sweep_direction mapping: yukarı sweep = bearish = SHORT, aşağı sweep = bullish = LONG.
- Bot restart edilirse pozisyonlar RecoveryManager üzerinden yüklenir.
