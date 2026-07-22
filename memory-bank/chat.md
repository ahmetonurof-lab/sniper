# Chat Log

## 2026-07-22 — P2-4 Self-Exit Race Guard (sniper/src)

- **P2-4: unmatched-reduceOnly WS_FALLBACK race condition fix** (`user_data_handler.py`):
  - **Kök neden:** `_on_order_update_normalized()` ve `_on_order_update_legacy()` içindeki unmatched reduceOnly dalı, gelen emrin ID'sini sadece sl_order_id/tp_order_id (+ prev/history) ile karşılaştırıyor. Botun kendi başlattığı market-close emirleri (TRAIL_CLOSE, force_close, MANUAL_CLOSE) bu setlerde yer almaz (reduceOnly MARKET emri, SL/TP algo emri değil). Trade zaten EXIT_SUBMITTED/EXIT_VERIFYING durumundayken bu event gelirse "orphan fill" sayılıp result WS_FALLBACK'e çevriliyor, _exit_trade ikinci kez tetikleniyor, yakalanmamış WSFallbackError fırlatılıyordu.
  - **Fix:** `_SELF_EXIT_IN_PROGRESS_STATUSES = frozenset({STATUS_EXIT_REQUESTED, STATUS_EXIT_SUBMITTED, STATUS_EXIT_VERIFYING})`. Unmatched-reduceOnly dalına girmeden önce trade status kontrolü — kendi exit sürecindeyse sessizce logla ve return. Hem normalized hem legacy handler'da uygulandı.
  - **Legacy docstring güncellendi:** "orijinal, değiştirilmedi" → "değiştirildi: self-exit race guard eklendi".
  - **4 test:** 3 guard senaryosu (EXIT_REQUESTED/SUBMITTED/VERIFYING) + 1 regression guard (ACTIVE durumunda hâlâ WS_FALLBACK行为 korunur). 32/32 test geçti.
  - **bugs.md:** P2-4 eklendi (P2 section).

## 2026-07-22 — Bug Registry Fix Session (sniper/src)

- **3 bug fix uygulandı** (sonnet bug registry'den sniper/src'ye taşındı):
  - **P1-1: repair_protection stale SL fallback** (`order_manager.py:334`): Eski `trade["sl"]`/`trade["tp"]` fiyatıyla emir veriliyordu, piyasa o fiyatlari gecmisse "immediately trigger" reddi aliniyordu. Artik basarisizlik durumunda `estimate_market_price()` ile mevcut fiyat uzerinden yeniden hesaplaniyor — `recover_positions()`'daki retry mantigiyla ayni.
  - **P1-4: periodic orphan sweep** (`recovery_manager.py:periodic_check_loop()`): Orphan sweep sadece `_on_1m_close` icinde calisiyordu, portfolio flat iken sayac ilerlemiyor, orphan emirler temizlenmiyordu. Artik `periodic_check_loop` her 60sn'de `reconcile_orphan_orders()` de cagiriyor.
  - **P0-4: restart REPAIR_REQUIRED cleanup** (`bot.py:run()`): Onceki session'dan `STATUS_REPAIR_REQUIRED`/`STATUS_EXIT_REQUESTED` ile kalan trade'ler restart sonrasi ayni durumda kilitli kaliyordu. Artik `recover_positions()` sonrasi SL/TP saglikliysa `STATUS_ACTIVE`'e donduruluyor.
- **Commit:** `2e5007a` sniper repo'ya push edildi (https://github.com/ahmetonurof-lab/sniper.git)

## 2026-07-22 — P0-1 UNIUSDT Restart Loop Fix (sniper/src)

- **P0-1: false position closed fix** (`exit_lifecycle.py:_submit_and_verify_market_close()`):
  - **Kök neden:** Verify loop'da adapter belirsizken (REQUEST_SENT/ORDER_ACKNOWLEDGED) `for-else` (sembol listede yok) ilk denemede `pos_closed=True` veriyordu. Binance gecikmeli donebilir, ilk `get_positions()` bos donebilir → bot sahte PnL hesapliyordu → trade active_trades'dan siliniyordu → periodic_check_loop pozisyonu recover ediyordu → dongu.
  - **Fix:** (1) `is_ambiguous` flag eklendi (REQUEST_SENT/ORDER_ACKNOWLEDGED veya bos close_resp). (2) `for-else` belirsiz durumda sadece `attempt >= 4` (son deneme) durumunda `pos_closed=True` veriyor. (3) Döngüden sonra `get_all_orders()` fallback — FILLED reduceOnly/closePosition emir kontrolü.
  - **EXECUTION_CONFIRMED davranisi degismedi:** Fill teyidi varsa hemen kabul et (ilk denemede `pos_closed=True`).
  - **Event log kanıtlama:** `events_2026-07-21.jsonl` UNIUSDT — 12:35:38 market close, 12:35:38 get_positions boş, 12:35:38 `pos_closed=True` (adapter_status REQUEST_SENT), 12:36:00 periodic_check_loop recover.
- **Commit:** `c11c785` sniper repo'ya push edildi (https://github.com/ahmetonurof-lab/sniper.git)

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

## 2026-07-05 — CBDR Risk Matrisi + Session Router + 3 Katmanlı Risk

- **session_router.py (yeni modül):** `should_trade()` — zehirli bölge filtresi + CBDR genişliğine göre risk çarpanı. `get_cbdr_multiplier()` — coin bazlı bucket çarpanı. `is_high_quality_fvg()` — ATR-bazlı FVG kalite filtresi (`MIN_REL_FVG_THRESHOLD=0.50`). `is_fvg_valid()` — 45 bar expiry. `get_session_hours()` — coin'in optimal session'ını döndürür.
- **config.py:** `CBDR_RISK_MATRIX` — 13 coin × 6 bucket × 6 çarpan kademesi (1.5x/1.2x/1.0x/0.8x/0.5x/0.0x). Backtest WR/BE+/PnL verisiyle dolduruldu. `SESSION_HOURS` 3 tipe ayrıldı (DEFAULT/REAL_CBDR/ASIA_RANGE). `BOT_SESSION` kaldırıldı. `MIN_FVG_SIZE` temizlendi. `GLOBAL_FVG_EXPIRY_BARS=45`.
- **bot.py:** 3 katmanlı risk motoru: (1) Zaman — EL 1.5x, (2) Kurulum — CBDR bucket çarpanı, (3) Portföy — devre kesici (defense mode). Defense mode'da EL ve Elite CBDR iptal: `final = 1.0 × min(cbdr_mult, 1.0)`. FVG expiry filter entry öncesi. NaN koruması. Log formatı iyileştirildi.
- **Session assignment:** DEFAULT=8 (ADA, AVAX, DOT, NEAR, SOL, XRP, ETH, SUI), REAL_CBDR=2 (ATOM, BTC), ASIA_RANGE=3 (APT, BNB, LINK). ETH ve SUI geri eklendi.
- **Commit listesi (bugün):** `0643f84` Session Router, `65d36aa` CBDR Risk Matrisi, `c1ade2f` Defense mode, `e4f0ca2` Coin bazlı SessionState, `e979000` NaN fix, `56aee2e` ATR FVG filtresi, `219986a` Expiry filter, `bf7c9c2` Session assignment, `1d73134` CBDR_RISK_MATRIX final.

## 2026-07-03 — ATR Refactor + Dinamik FVG Eşiği

- **indicators.py (yeni dosya):** Gerçek Wilder's smoothing 14-periyot ATR. Eski sahte ATR (`max(range, close*0.0001)`) kaldırıldı. `calculate_true_range()`, `update_atr()`, `build_atr_from_bars()` fonksiyonları. Rolling state: `_atr_state`, `_atr_prev_close`.
- **bot.py entegrasyonu:** `_warmup_cbdr`, `_on_15m_close`, `_on_1m_close` — 3 yerde gerçek ATR kullanılıyor. `__init__` sıralama bug'ı düzeltildi (`_atr_state` artık RecoveryManager'dan önce).
- **recovery_manager.py:** ATR hesaplaması `indicators.py`'den geliyor.
- **Dinamik FVG eşiği:** Statik `FVG_SIZE_MAP` (`$ değerleri`) → `FVG_MIN_SIZE_ATR_MULT × atr_val`. Hem entry hem trailing aynı dinamik formülü kullanıyor.
- **MULT taraması:** 0.02-0.30, 195 run, 2.5 yıllık veri. `FVG_MIN_SIZE_ATR_MULT = 0.06` seçildi (0.02-0.08 arası PnL farkı gürültü seviyesinde, 0.06 en sağlam/orta nokta).
- **FVG_WICK_RATIO_MAX:** 0.90 → 0.75 (config.py + bot.py RSM init).
- **Backtest entegrasyonu:** 5 dosya (session.py, retrace_state.py, fvg.py, models.py, coins_config.py) silindi — `sniper/src`'ten import ediliyor. `SNIPER_OUTPUT_DIR` env var ile izolasyon. Determinism doğrulandı. `mult_scan.py`'de checkpoint/resume.
- **Rapor durumu:** `mult_scan_report.md` tek geçerli rapor. `ict_cbdr_thresholds.md` (sahte ATR ile koşulmuş) ve `v3_window_comparison.md` (stale/cache veri, 26 koşum 2 dakikada imkansız) yeniden koşuluyor.
