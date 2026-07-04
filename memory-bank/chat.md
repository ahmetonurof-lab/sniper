# Chat Log

## 2026-06-28

- **trailing_manager.py:** Each trailing step now records `{sl, tp, fvg_top, fvg_bot, bar}` into `trade["trail_steps"]`. New `trail_steps.append(...)` at both long and short trail branches. `trail_steps` is stored on the `ActiveTrade` object with `get()` compatibility.
- **models.py:** `ActiveTrade.trail_steps` field changed from `list | None = None` to `list = field(default_factory=list)` so `.get("trail_steps")` always returns `[]` on first access, never `None`.
- **chart_export.py (full rewrite):** `export_chart(sym, trade, pnl, ss)` generates a Plotly HTML candlestick chart with:
  - CBDR range box (yellow dashed)
  - Sweep candle marker (purple) — first candle that breaks CBDR
  - Trigger FVG zone (green), with "ÜST/ALT/CE" label showing entry's position relative to FVG
  - Entry line (colored by side)
  - Initial SL/TP vs Final SL/TP (if different due to trailing)
  - Trail step vertical lines with tooltip annotations
  - Exit line (red/green based on result)
  - Session label (ASIA/LONDON/NEWYORK based on UTC hour)
  - Title includes sym, direction, session, PnL, trail count
- **bot.py:** `_exit_trade()` reordered: `export_chart` first → sets `trade["chart_file"]` → `export_trade` (so JSONL includes chart_file) → `self.trades.append`. Also `exit_bar` access made safe via `.get("exit_bar", 0)`.
- **user_data_handler.py:** `trade["exit_timestamp"]` now set before calling `_exit_trade()` in both WS exit paths, so chart export has the timestamp.

## 2026-06-29

- **Bug:** MARKET order failed on BTCUSDT (qty=0.5393, notional ~32k USDT, but testnet balance ~5k USDT with 5x leverage = 25k buying power).
- **Root cause:** `calculate_qty` formula (`balance * risk_pct / risk_dist`) didn't cap at available buying power. Resulting notional (`qty × entry_price`) could exceed `balance × leverage`, causing Binance "insufficient balance" error.
- **Fix (commit `83127a7`):** Added `max_qty = (balance * leverage) / entry_price` cap in `calculate_qty`. BTC example: qty now capped at 0.416 (vs uncapped 0.534) — notional 24,947 USDT within 25k limit.
- **Safety margin (commit `90f1b39`):** Changed cap to `max_qty = (balance * leverage * 0.95) / entry_price` with `SAFETY_MARGIN = 0.95` module constant. Provides 5% buffer for open orders, fees, and margin rounding differences. `balance` already comes from Binance `availableBalance` (not walletBalance).

## 2026-06-29

- **Balance refactor (commit `02ce89a`):** Split `self._balance` into two separate variables:
  - `self._wallet_balance`: WS ACCOUNT_UPDATE'ten gelen `wb` (walletBalance). Sadece log/görüntüleme için. Position sizing'i ETKİLEMEZ.
  - `self._available_balance`: REST `/fapi/v2/account` → `assets[].availableBalance`. Position sizing için. Entry öncesi taze çekilir.
- **Entry öncesi REST fetch:** `_try_entry()`'de her primary entry denemesinde `self.rest.get_balance()` çağrılır. Başarısız olursa eski değer korunur.
- **WS handler değişti:** `balance_callback` → `wallet_callback`. Sadece `_wallet_balance`'ı günceller.
- **state_writer.py:** `balance` alanı ikiye ayrıldı: `available_balance` + `wallet_balance`.
- **Kullanım akışı:** WS → `_wallet_balance` | REST → `_available_balance` → `calculate_qty()` → `max_qty = (available_balance × leverage × 0.95) / entry_price`

## 2026-06-30 — V3 Architecture Upgrade

- **Retrade/LHR tamamen kaldırıldı:** `RetradeEngine`, `_check_retrade()`, `execute_lhr_entry()`, `SYMBOL_RISK_MAP` ve tüm retrade/LHR/save_retrade_arm fonksiyonları silindi. `is_retrade` field'ı `ActiveTrade`'den kaldırıldı. `session.py`: 7 retrade alanı temizlendi, `TradeDayState` tek alana (`trades_today`) düştü.
- **Sweep infinite loop fix:** `unmark_sweep_used()` `retrace_state.py`'den silindi (sweep tetiklendiğinde kalıcı işaretlenir). `state_manager.py`'ye `mark_sweep_consumed()` + `is_sweep_consumed()` (level-based ID: `f"{direction}_{level:.4f}"`) eklendi. Token/level artık JSON lock file ile restart-proof.
- **`_exit_trade()` rewrite:** Sıra: `cancel_all_open_orders()` → `reduceOnly=True` market exit → 5-attempt `positionAmt==0` verify loop → `mark_sweep_consumed()` + `rsm.reset()`. `progress_rsm.reset()` → `self.rsms[sym].reset()`.
- **Double exit guard:** `_exit_trade()` başına `if sym not in self.active_trades: return` eklendi. `del` → `pop(sym, None)`.
- **Orphan cleanup genişletildi:** `reconcile_orphan_orders()` artık tüm order türlerini temizler (LIMIT dahil), sadece STOP/TP değil.
- **FVG trailing close teyidi:** `_fvg_close_confirmed()` metodu — trailing sadece 15m mumu FVG içinde kapanmış (close between bottom-top) FVG'leri kullanır. Sadece fitil (wick) yetmez, gövde kapanışı şart. Close ters tarafta kapandıysa FVG geçersiz sayılır.

## 2026-07-03 — ATR Refactor + Dinamik FVG Eşiği

- **indicators.py (yeni dosya):** Gerçek Wilder's smoothing 14-periyot ATR. Eski sahte ATR (`max(range, close*0.0001)`) kaldırıldı. `calculate_true_range()`, `update_atr()`, `build_atr_from_bars()` fonksiyonları. Rolling state: `_atr_state`, `_atr_prev_close`.
- **bot.py entegrasyonu:** `_warmup_cbdr`, `_on_15m_close`, `_on_1m_close` — 3 yerde gerçek ATR kullanılıyor. `__init__` sıralama bug'ı düzeltildi (`_atr_state` artık RecoveryManager'dan önce).
- **recovery_manager.py:** ATR hesaplaması `indicators.py`'den geliyor.
- **Dinamik FVG eşiği:** Statik `FVG_SIZE_MAP` (`$ değerleri`) → `FVG_MIN_SIZE_ATR_MULT × atr_val`. Hem entry hem trailing aynı dinamik formülü kullanıyor.
- **MULT taraması:** 0.02-0.30, 195 run, 2.5 yıllık veri. `FVG_MIN_SIZE_ATR_MULT = 0.06` seçildi (0.02-0.08 arası PnL farkı gürültü seviyesinde, 0.06 en sağlam/orta nokta).
- **FVG_WICK_RATIO_MAX:** 0.90 → 0.75 (config.py + bot.py RSM init).
- **Backtest entegrasyonu:** 5 dosya (session.py, retrace_state.py, fvg.py, models.py, coins_config.py) silindi — `sniper/src`'ten import ediliyor. `SNIPER_OUTPUT_DIR` env var ile izolasyon. Determinism doğrulandı. `mult_scan.py`'de checkpoint/resume.
- **Rapor durumu:** `mult_scan_report.md` tek geçerli rapor. `ict_cbdr_thresholds.md` (sahte ATR ile koşulmuş) ve `v3_window_comparison.md` (stale/cache veri, 26 koşum 2 dakikada imkansız) yeniden koşuluyor.
