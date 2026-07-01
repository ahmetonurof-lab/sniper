# Active Context — Sniper Bot

## Mevcut Durum (Temiz Başlangıç)

- **Bot çalışıyor mu?**: Testnet'te, canlı emir gönderimi aktif.
- **Testnet bakiyesi**: ~5,000 USDT
- **Sembol sayısı**: 13 (BTC/ETH/BNB/SOL/AVAX/LINK/XRP/ATOM/ADA/SUI/APT/DOT/NEAR)
- **Kaldıraç**: 5x
- **Strateji**: CBDR → Sweep → FVG Wick Rejection → Primary Entry → Trailing → Exit (V3 — retrade/LHR kaldırıldı)

## Kritik Yapılan Değişiklikler

| # | Değişiklik | Açıklama |
|---|-----------|----------|
| 1 | **Retrade/LHR tamamen silindi** | `RetradeEngine`, `_check_retrade()`, `execute_lhr_entry()`, `SYMBOL_RISK_MAP`, `is_retrade`, `save_retrade_arm`/`load_retrade_arm`/`clear_retrade_arm`, `rsms_retrade`, `retrade_engines` — tümü kaldırıldı. |
| 2 | **Sweep infinite loop fix** | `unmark_sweep_used()` silindi. `mark_sweep_consumed(level)` + `is_sweep_consumed(level)` level-based ID (ör: `bullish_1.2345`) ile eklendi. Token restart-proof JSON lock file. |
| 3 | **`_exit_trade()` rewrite** | Sıra: `cancel_all_open_orders()` → `reduceOnly=True` market → 5-attempt position verify loop → `mark_sweep_consumed()` + `rsm.reset()`. |
| 4 | **Double exit guard** | `_exit_trade()` başında `if sym not in self.active_trades: return`. `del` → `pop(sym, None)`. |
| 5 | **Orphan cleanup geniş** | `reconcile_orphan_orders()` tüm order türlerini temizler (LIMIT dahil). |
| 6 | **FVG trailing close teyidi** | `_fvg_close_confirmed()` — trailing sadece 15m close'u FVG içinde olan FVG'leri kullanır. |
| 7 | **Trail prev ID geçiş fix** | `update_trail_orders()` eski SL/TP id'sini `*_order_id_prev` olarak saklar, WS fill eşleşmesi hem güncel hem prev id'leri kontrol eder. CANCELED callback'te prev id'ler sessizce yok sayılır. — WS_FALLBACK sayısını azaltır. |

## Aktif Kararlar

- **LEVERAGE=5**: 5x kaldıraç, margin = notional / 5.
- **RSM (RetraceStateMachine)**: IDLE → SWEEP_DETECTED → TRIGGER_READY. Sadece 3 state.
- **Max 1 trade/gün/sembol** (retrade kalktı).
- **ASIA kapalı**: 22:00-02:00 UTC.
- **FVG_BUFFER_MULT=0.50**: Canlıda 0.50, backtest'te 0.25.
- **MAX_SL_DIST_MULT=2.0**: FVG bazlı SL max `risk_pts × 2`.
- **CBDR gövde bazlı (open/close)**: High/low değil.

## Sıradaki / Açık Konular

- Canlı testte `_exit_trade()` cancel_all + reduceOnly flow'un Binance ile çalışması gözlemlenecek.
- FVG trailing close teyidi sonrası WR değişimi takip edilecek.
- `output/` dizinindeki JSON/JSONL dosyaları `.gitignore` kontrolü.
- WS_FALLBACK sayısı trail prev ID fix sonrası takip edilecek.

## Hatırlatmalar

- sweep_direction mapping: yukarı sweep = bearish = SHORT, aşağı sweep = bullish = LONG.
- `mark_sweep_consumed()` level-based ID kullanır — bar_index değil.
- `rsm.reset()` artık `_exit_trade()` sonunda çağrılır, `_try_entry()` içinde değil.
- Trailing güncellemede eski order id `*_order_id_prev` olarak saklanır, geçiş penceresinde WS fill'leri prev id ile de eşleşebilir.
