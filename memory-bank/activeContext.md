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
| 8 | **Backtest trailing → live bot port** | `analyzer_v3.py` trailing bloğu `_fvg_close_confirmed()` + ATR buffer + TRAIL_MIN_MOVE_MULT + break-even ile güncellendi. `coins_config.py`'a trailing sabitleri eklendi. |
| 9 | **Entry wick ratio + close guard** | `signal_engine.py`'a iki filtre eklendi: (a) `current.is_closed` kontrolü, (b) sweep mumu `(lower_wick veya upper_wick) / range > 0.90` şartı. |
| 10 | **FVG marker fix** | `_save_fvg_state()` içinde `fvg_bar_index: max(0, current.index-3)` → `fvg.bar_index` (restart sonrası marker yanlış yere düşüyordu). |
| 11 | **BE chart bar index fix** | `TrailingManager.evaluate_break_even()`'de `"bar": current.index` → `"bar": bar_index_15m` (15m bar index'i ile skala uyumu). `bars_15m` BE öncesi çekildi, dublikat silindi. |
| 12 | **Sweep level ActiveTrade'de** | `models.ActiveTrade`'e `sweep_level: float\|None` field'ı eklendi, `_try_entry()`'de `sweep_level=ss.sweep_level` ile dolduruluyor. |
| 13 | **on_sweep_confirmed rewrite** | 3 değişiklik: (a) sweep invalidation gate — ters kırılırsa IDLE, (b) FVG yoksa reset yok — bekle, (c) unconditional reset kalktı — SWEEP_DETECTED'de kal. |
| 14 | **output/ gitignore** | `output/*` exception'lar kaldırıldı, tüm output dizini ignore. Mevcut dosyalar `git rm --cached` ile indexten çıkarıldı. |
| 15 | **Snapshot pad & fetch limit** | `_PAD_BARS=8→20`, `_FETCH_LIMIT=120→160` — daha geniş pencere. |
| 16 | **Legend konum fix** | `bottom:14px` → `top:54px` — chart altına düşmesin. |
| 17 | **Entry line canvas overlay'e taşındı** | `createPriceLine()` silindi, `rangedHLine()` ile SL/TP yanına eklendi — chart'a entegre. |
| 18 | **ActiveTrade cbdr_high/cbdr_low** | models.py'ye eklendi, `_try_entry()`'de `ss.cbdr_body_high/low` ile dolduruluyor. |
| 19 | **fvg = rsm.trigger_fvg taşındı** | `_try_entry()` sonundan en başa alındı. |

## Aktif Kararlar

- **LEVERAGE=5**: 5x kaldıraç, margin = notional / 5.
- **RSM (RetraceStateMachine)**: IDLE → SWEEP_DETECTED → TRIGGER_READY. Sadece 3 state.
- **Max 1 trade/gün/sembol** (retrade kalktı).
- **ASIA kapalı**: 22:00-02:00 UTC.
- **RISK_PER_TRADE=0.003**: Elle güncellendi (%0.3).
- **FVG_BUFFER_MULT=0.50**: Canlı ve backtest artık aynı.
- **MAX_SL_DIST_MULT=2.0**: FVG bazlı SL max `risk_pts × 2`.
- **CBDR gövde bazlı (open/close)**: High/low değil.
- **Backtest trailing live bot ile uyumlu**: `_fvg_close_confirmed()`, ATR buffer (`0.25×ATR`), `TRAIL_MIN_MOVE_MULT=0.2`, break-even (`1R` sonrası SL→entry).

## Sıradaki / Açık Konular

- Canlı testte `_exit_trade()` cancel_all + reduceOnly flow'un Binance ile çalışması gözlemlenecek.
- Backtest trailing port'u sonrası WR/DD değişimi canlı ile karşılaştırılacak.
- WS_FALLBACK sayısı trail prev ID fix sonrası takip edilecek.

## Hatırlatmalar

- sweep_direction mapping: yukarı sweep = bearish = SHORT, aşağı sweep = bullish = LONG.
- `mark_sweep_consumed()` level-based ID kullanır — bar_index değil.
- `rsm.reset()` artık `_exit_trade()` sonunda çağrılır, `_try_entry()` içinde değil.
- Trailing güncellemede eski order id `*_order_id_prev` olarak saklanır, geçiş penceresinde WS fill'leri prev id ile de eşleşebilir.
