# Test Plan — sniper

## Priority Order

| Tur | Dosya | Tip | Test Sayısı |
|-----|-------|-----|-------------|
| 1 | `test_models.py` | UNIT | ~15 |
| 2 | `test_fvg.py` | UNIT | ~20 |
| 3 | `test_session.py` | UNIT | ~15 |
| 4 | `test_retrace_state.py` | UNIT+MOCK | ~15 |
| 5 | `test_state_manager.py` | MOCK | ~15 |
| 6 | `test_bot_infra.py` | UNIT+MOCK | ~6 |
| 7 | `test_bot_binance.py` | MOCK | ~18 |
| 8 | `test_websocket.py` | MOCK | ~15 |
| 9 | `test_bot.py` | MOCK | ~30 |

**Total: ~150 tests**

## Tools
- `pytest`
- `pytest-asyncio` (async tests)
- `unittest.mock` / `pytest-mock`

---

## 1. `test_models.py` — Data Classes (UNIT)

All tests are pure logic — no mocking, no IO.

| Test | Prio |
|------|------|
| Bar validates `high >= low`, `open/close in [low, high]` | HIGH |
| `body`, `upper_wick`, `lower_wick`, `range` for bullish/bearish | HIGH |
| Invalid Bar raises `ValueError` | HIGH |
| FVG validates `top > bottom` | HIGH |
| `size`, `midpoint`, `is_active` | HIGH |
| `mark_filled()` returns True when price crosses threshold | HIGH |
| CHoCH validates `bar_index >= pivot_bar_index` | HIGH |
| SwingPoint `mark_mitigated()` (both directions) | HIGH |
| FVGQuality validates fields in `[0.0, 1.0]` | HIGH |
| `is_valid` property | MEDIUM |
| `AnalysisResult.expected_choch_direction` | HIGH |
| `AnalysisResult.is_valid_signal` | HIGH |
| Frozen dataclass — cannot modify fields | MEDIUM |
| `age_bars()` | MEDIUM |
| `summary()` formatting | LOW |
| `tf_params()` lookup/miss | MEDIUM |

---

## 2. `test_fvg.py` — FVG Detection (UNIT)

| Test | Prio |
|------|------|
| `detect_fvgs` finds bullish gap | HIGH |
| `detect_fvgs` finds bearish gap | HIGH |
| `detect_fvgs` returns empty when no gap | HIGH |
| `detect_fvgs` filters by `min_fvg_size` | HIGH |
| `detect_fvgs` filters by `since_index` | MEDIUM |
| `detect_fvgs` skips unclosed bars | HIGH |
| `detect_fvgs` skips inside bars (no gap) | HIGH |
| `update_fvg_states` marks FVG filled when close enters gap | HIGH |
| `update_fvg_states` marks FVG invalidated when close crosses beyond | HIGH |
| `update_fvg_states` uses `_next_check_abs` | MEDIUM |
| `find_latest_unfilled_fvg` returns max `real_index` | HIGH |
| `find_latest_unfilled_fvg` returns None when none match | HIGH |
| `find_latest_unfilled_fvg` filters direction + size | HIGH |
| `is_retesting_fvg` wick enters buffer, body stays safe | HIGH |
| `is_retesting_fvg` returns False for None/inactive | HIGH |
| `is_retesting_fvg` bullish vs bearish logic | HIGH |
| `cleanup_fvgs` removes old/stale entries | MEDIUM |
| `refresh_fvg_list` deduplicates existing indices | MEDIUM |
| `refresh_fvg_list` periodic cleanup trigger | MEDIUM |

---

## 3. `test_session.py` — Session State Machine (UNIT)

| Test | Prio |
|------|------|
| `detect_phase` maps each hour to correct phase | HIGH |
| `detect_phase_from_timestamp` valid/invalid input | MEDIUM |
| `SessionState.update` tracks CBDR body | HIGH |
| CBDR locks at correct hour | HIGH |
| Range type switches to ASIA/DEAD when CBDR < 0.5% | HIGH |
| London high/low tracking | HIGH |
| NY tracking (inherits London if 0) | HIGH |
| `_check_cbdr_sweep` bullish sweep detection | HIGH |
| `_check_cbdr_sweep` bearish sweep detection | HIGH |
| `_check_cbdr_sweep` no sweep for normal bars | HIGH |
| `_reset_for_new_cbdr_cycle` resets all fields + `trades_today` | HIGH |
| CBDR day key at 22:00 UTC boundary | HIGH |
| Asia tracking between hours 2-8 | MEDIUM |
| Retrade state fields persist across updates | MEDIUM |

---

## 4. `test_retrace_state.py` — RSM (UNIT + MOCK)

| Test | Prio |
|------|------|
| Starts in `IDLE` | HIGH |
| `on_sweep` → `SWEEP_DETECTED` | HIGH |
| `on_sweep` ignored when not IDLE | HIGH |
| `on_sweep` deduplicates via state_manager (mock `is_sweep_used`) | HIGH |
| `on_sweep_confirmed` → `TRIGGER_READY` with matching FVG | HIGH |
| `on_sweep_confirmed` resets when no FVG found | HIGH |
| Bullish: wick touches FVG top, body doesn't break below | HIGH |
| Bearish: wick touches FVG bottom, body doesn't break above | HIGH |
| Skips FVGs with wrong direction | HIGH |
| Skips FVGs after sweep bar | HIGH |
| `can_trigger()` only in TRIGGER_READY | HIGH |
| `reset()` clears everything, unmarks sweep (mock) | HIGH |
| `scan_htf_fvgs` returns sorted HTFFVGs | MEDIUM |
| `scan_htf_fvgs` empty for <5 bars | MEDIUM |
| `scan_htf_fvgs` limits to 10 | MEDIUM |

---

## 5. `test_state_manager.py` — File-backed State (MOCK)

| Test | Prio |
|------|------|
| `can_open_trade` returns True for new day | HIGH |
| `can_open_trade` returns False when count >= 1 today | HIGH |
| `mark_trade_opened` writes to JSON | HIGH |
| `mark_trade_closed` sets `open=False` | HIGH |
| `is_sweep_used` true/false | HIGH |
| `mark_sweep_used` writes + cleans old entries | HIGH |
| `unmark_sweep_used` removes entry | HIGH |
| `reconcile_from_active` writes new entries | HIGH |
| `reconcile_from_active` skips already-recorded | HIGH |
| `get_trade_count_today` returns 0 unknown/new-day | HIGH |
| `get_trade_count_today` returns count for today | HIGH |
| `save_retrade_arm` / `load_retrade_arm` / `clear_retrade_arm` round-trip | HIGH |
| `_today()` correct at 22:00 UTC | HIGH |
| `_load` handles corrupt/missing JSON gracefully | MEDIUM |

---

## 6. `test_bot_infra.py` — Helpers & Rate Limiter (UNIT + MOCK)

| Test | Prio |
|------|------|
| `get_lock` returns same lock per symbol | MEDIUM |
| `_round_price` various tick sizes | HIGH |
| `_round_price` zero/negative tick | MEDIUM |
| `_RateLimiter.acquire` waits when needed | HIGH |
| `_RateLimiter.acquire` no wait when interval passed | HIGH |
| `export_ohlc_15m` / `export_ohlc_1m` writes CSV | LOW |

---

## 7. `test_bot_binance.py` — REST Client (UNIT + MOCK)

| Test | Prio |
|------|------|
| `_round_to_tick` / `_round_step` | HIGH |
| `get_order_type` normal + algo | HIGH |
| `get_order_price` triggerPrice vs stopPrice | HIGH |
| `get_order_timestamp` safe extraction | HIGH |
| `get_symbol_info` caches exchange info | HIGH |
| `get_tick_size` / `get_step_size` / `get_min_qty` filter extraction | HIGH |
| `apply_price_precision` / `apply_amount_precision` / `validate_min_amount` | HIGH |
| `get()` HMAC-SHA256 signing | HIGH |
| `get()` retries on HTTP errors, raises after max | HIGH |
| `post()` signs and sends body | HIGH |
| `get_open_orders` / `get_all_orders` | HIGH |
| `get_balance` extracts USDT | HIGH |
| `get_positions` filters non-zero | HIGH |
| `place_market_order` validates + fallback on demo API | HIGH |
| `place_stop_order` algo STOP_MARKET | HIGH |
| `place_tp_order` algo TAKE_PROFIT_MARKET | HIGH |
| `cancel_order` normal + algo (handles Unknown Order) | HIGH |
| `get_listen_key` / `renew_listen_key` | MEDIUM |

---

## 8. `test_websocket.py` — WS Hub (UNIT + MOCK)

| Test | Prio |
|------|------|
| `is_cooldown_active` / `register_trade` | HIGH |
| `_BarBuffer._kline_to_bar` conversion | HIGH |
| `_BarBuffer.feed` appends closed bar + calls callbacks | HIGH |
| `_BarBuffer.feed` skips duplicate timestamp | HIGH |
| `_BarBuffer.feed` does not call on unclosed bars | HIGH |
| `_BarBuffer` max_bars eviction | MEDIUM |
| `_build_url` constructs combined stream | HIGH |
| `_dispatch` routes kline to correct buffer | HIGH |
| `_dispatch` parses combined stream format | HIGH |
| `get_bars` / `prefill_bars` | HIGH |
| `_heartbeat_monitor` detects stale connections | MEDIUM |
| `run()` reconnect loop with exponential backoff | MEDIUM |
| `on_bar` / `register_callback` registration | MEDIUM |
| `stop()` sets event + cancels tasks | MEDIUM |
| `_BarBuffer` next_index management | HIGH |
| `_dispatch` handles invalid JSON | MEDIUM |
| `_dispatch` ignores non-kline events | MEDIUM |

---

## 9. `test_bot.py` — Main Orchestrator (MOCK)

| Test | Prio |
|------|------|
| `__init__` sets up symbols, states, rsms, cfgs | HIGH |
| `_session_label` | HIGH |
| `_on_15m_close` skips when trade active | HIGH |
| `_on_15m_close` skips ASIA session | HIGH |
| `_on_15m_close` skips when CBDR not locked | HIGH |
| `_on_15m_close` sweep → RSM → entry flow | HIGH |
| `_on_15m_close` bias filter (no counter-bias entry) | HIGH |
| `_on_15m_close` session filter (only LONDON/NEWYORK) | HIGH |
| `_on_15m_close` retrade-armed skips primary RSM | HIGH |
| `_try_entry` SL/TP calc long/short | HIGH |
| `_try_entry` risk_dist >= min | HIGH |
| `_try_entry` qty from risk budget | HIGH |
| `_try_entry` live order flow (mkt → SL → TP) | HIGH |
| `_try_entry` rollback on market failure | HIGH |
| `_try_entry` emergency close on SL failure | HIGH |
| `_on_1m_close` FVG trailing logic | HIGH |
| `_on_1m_close` trailing rollback on order failure | HIGH |
| `_on_1m_close` exit on SL/TP hit | HIGH |
| `_exit_trade` PnL calc | HIGH |
| `_exit_trade` retrade arms on first exit | HIGH |
| `_exit_trade` skips arm on retrade exit | HIGH |
| `_check_retrade` sweep detection in recent bars | HIGH |
| `_check_retrade` LHR fallback after 3 failed FVG attempts | HIGH |
| `_update_orders` new SL/TP + cancel old | HIGH |
| `_update_orders` partial failure handling | HIGH |
| `_recover_positions` restore from Binance | HIGH |
| `_reconcile_ghost_positions` clean stale state | HIGH |
| `_warmup_cbdr` feeds all bars to SessionState | MEDIUM |
| `_prefill_bars` loads REST klines | MEDIUM |
| `run()` full init → prefill → warmup → WS | HIGH |
| `_register_user_data_callbacks` ORDER_TRADE_UPDATE | HIGH |
| `_register_user_data_callbacks` ACCOUNT_UPDATE | HIGH |
| `_repair_protection` recreates missing SL/TP | HIGH |
