# Sniper Bot - Critical Bug Fixes Implementation Plan

This plan addresses the critical and high-priority bugs identified in the comprehensive code audit report. The goal is to stabilize the system, prevent data loss, eliminate race conditions, and fix type/attribute errors that could completely break trading operations.

> [!IMPORTANT]
> **ActiveTrade Field Formalization (BULGU-01):** I will add `pending_exit_*` fields explicitly to the `ActiveTrade` dataclass. This will allow them to be properly logged into `trades_history.jsonl` for post-mortem analysis.

## Priority Order

Changes are grouped by implementation priority. Items within each group should be done in the listed order.

### P0 — BLOCKER (CRITICAL: positioning / recovery correctness)

1. **recovery_manager.py:486 (bare except — SL/TP cancel)**
   `except Exception: pass` — SL veya TP iptal hatası sessizce yutuluyor → pozisyon korumasız kalabilir.
   **Fix:** `log.error()` + incident kaydı + en az 1 retry.

2. **exit_lifecycle.py:521 + :549 (bare except — position verify)**
   `except Exception: pass` — exit doğrulaması sessizce atlanıyor → `_mark_repair_required` çağrılmaz, pozisyon state'te açık kalır.
   **Fix:** `log.error()` + `_mark_repair_required` fallback'i ekle.

3. **order_manager.py:646 (bare except — SL placement/closePosition repair)**
   `except Exception: continue` — SL parça yerleşimi başarısız olursa sessizce geçiyor → pozisyon SL'siz kalır.
   **Fix:** `log.error()` + eğer hiçbir parça başarılı olmazsa TP'yi de kurma + incident flag.

4. **order_manager.py:966 (bare except — repair cancel)**
   `except Exception: pass` — eski emir iptal hatası sessiz → eski emir aktif kalır, SL/TP override edemez.
   **Fix:** `log.error()` + bu emri `_repair_failed` set'ine ekle ki sonraki repair cycle tekrar dener.

---

### P1 — CRITICAL (race conditions / data loss)

#### FVG bar_index Pre-Fix Validation (item 5)
**ZORUNLU:** `trigger_fvg` parametresine hem `FVG` hem `HTFFVG` tipi gelebiliyor mu, doğrula.
- `FVG.real_index` == `HTFFVG.bar_index` aynı candle index'ini mi temsil ediyor?
- Eşdeğer değilse: `bar_index` property alias ekleme — tüm çağrı noktalarını (bot.py:649/962/986, entry_manager.py:591) doğrudan `real_index`'e çevir.
- Eşdeğerse: FVG dataclass'ına `@property bar_index` ekle.

#### BULGU-03 — Exit Race Condition: Stale Trade Reference (item 6)
**exit_lifecycle.py execute() — per-sym lock güçlendirme:**
- Mevcut `_trade_identity_key` bazlı lock yetersiz (farklı key üretebilir → eşzamanlı giriş).
- `asyncio.Lock`'u `sym` bazlı (ör. `self._sym_locks: dict[str, asyncio.Lock]`) kullan, tüm `execute()` gövdesini lock'la.
- Satır 337'deki tekrar `self._active_trades.get(sym)` çağrısını kaldır — mevcut `trade` referansını kullan.
- WS callback ve 1m-bar-check'in aynı sym için `execute()`'a eşzamanlı girmesini engelle.

#### BULGU-04 — Double-Pop Race: PnL Kaybolması (item 7)
**exit_lifecycle.py _commit_confirmed_exit — _recently_closed cache:**
- `self._recently_closed: dict[str, TradeSnapshot]` — kısa ömürlü cache (TTL ~5sn).
- `_commit_confirmed_exit` içinde `pop(sym, None)` öncesi trade snapshot'ını cache'e yaz.
- İkinci path (pop → None) cache'den PnL verisini alır, çifte muhasebe yapmaz.
- Cache periyodik cleanup (veya `execute()` girişinde stale temizlik).

#### BULGU-03/04 — Regresyon Testleri (item 8)
**Merge öncesi ZORUNLU** — gerçek `ActiveTrade` nesnesiyle:
1. BULGU-03 testi: iki coroutine eşzamanlı `execute()` çağırır → biri promote yapar, diğeri stale referansla karşılaşmaz.
2. BULGU-04 testi: iki coroutine eşzamanlı `_commit_confirmed_exit` çağırır → ikincisi `_recently_closed` cache'inden PnL alır.
3. BULGU-09 testi: TP placement başarısız → `_emergency_close` tetiklenir, `success=False` döner.

---

### P2 — HIGH (corrupted state / incorrect behavior)

#### [MODIFY] [entry_manager.py](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/entry_manager.py)
- **TP Failure Handling (BULGU-09):** In `_execute_live_entry`, if `tp_id` is empty (TP placement failed), log a CRITICAL error, execute an `_emergency_close`, and return `EntryExecutionResult(success=False, error=...)` instead of returning `success=True`.

#### [MODIFY] [exit_lifecycle.py](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/exit_lifecycle.py)
- **Exit Race Condition 1 (BULGU-03) + per-sym lock:** See P1 items 6-7 above.
- **Exit Race Condition 2 (BULGU-04) + _recently_closed cache:** See P1 items 6-7 above.
- **Bare Exception Fixes — position verify:** See P0 item 2 above.
- **Position Size Threshold (BULGU-08):** Change from `abs(amt) < 0.0001` to `abs(amt) < 1e-8` or `amt == 0`.
- **File Handle Leak (BULGU-12):** Wrap `open()` calls in `_commit_confirmed_exit` FVG state write with `with open(...)`.
- **Paper Mode Safety (BULGU-23):** Replace `cfg.BINANCE_API_KEY` with `self._is_live` for API call guards.

#### [MODIFY] [bot.py](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/bot.py)
- **FVG State Corruption (BULGU-02 & BULGU-06):**
  - Add `except Exception as e: log.error(...)` to `_save_fvg_state` and `_load_fvg_state`.
  - Atomic write: `.tmp` → `os.replace`.
- **FVG bar_index fix (BULGU-17/18):** per P1 item 5 validation outcome.

#### [MODIFY] [state_writer.py](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/state_writer.py)
- **Protection Health Mapping (BULGU-05):** Derive `sl_status`/`tp_status` from flat fields (`trade.get("sl_order_id")`, `trade.get("tp_order_id")`).
- **Feature Flag Sync (BULGU-19):** Use `cfg.WS_EVENT_NORMALIZATION_ENABLED` instead of hardcoded `False`.

---

### P3 — MEDIUM (incorrect categorization / type safety)

#### [MODIFY] [user_data_handler.py](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/trading/user_data_handler.py)
- **order_id/client_order_id split (BULGU-11 / item 4):** `normalize_order_event` içinde `order_id`'yi tek alanda birleştirme (`str(raw_order.get("c", "") or raw_order.get("i", ""))`) yerine `client_order_id` (`c`) ve `server_order_id` (`i`) ayrı ayrı taşı. `_resolve_fill_result` ve `_oid_matches_trade` çağrılarında her iki ID'yi bağımsız match'le.

#### [MODIFY] [models.py](file:///c:/Users/Administrator/Desktop/nexus-mcp/sniper/src/models.py)
- **ActiveTrade (BULGU-01):** Add explicit fields: `pending_exit_price: float | None = None`, `pending_exit_qty: float | None = None`, `pending_exit_order_id: str | None = None`, `pending_exit_timestamp: int | None = None`, `pending_exit_reason: str | None = None`.
- **ActiveTrade (BULGU-10):** Modify `__contains__` to only check `self.__dataclass_fields__`.
- **FVG (BULGU-17/18):** Per bar_index validation outcome — add `@property bar_index` or fix call sites.

---

## Bare Except Inventory (P0 — P1 overlap)

All bare `except Exception: pass/continue` locations from audit report:

| # | File | Line | Context | Risk | Action |
|---|---|---|---|---|---|
| 1 | recovery_manager.py | 486 | SL/TP cancel in emergency close | CRITICAL — korumasız pozisyon | log.error + retry + incident |
| 2 | exit_lifecycle.py | 521 | Position verify loop | CRITICAL — exit doğrulaması atlanır | log.error + _mark_repair_required |
| 3 | exit_lifecycle.py | 549 | FILLED order check fallback | HIGH — yanlış REPAIR_REQUIRED | log.error + fallback açık |
| 4 | order_manager.py | 646 | SL placement (closePosition) | HIGH — korumasız pozisyon | log.error + TP kurma |
| 5 | order_manager.py | 966 | Repair cancel loop | HIGH — eski emir aktif kalır | log.error + _repair_failed set |
| 6 | bot.py | 110 | FVG state save | HIGH — veri kaybı | In BULGU-02 fix |
| 7 | bot.py | 120 | FVG state load | HIGH — recovery eksik | In BULGU-02 fix |
| 8 | exit_lifecycle.py | 698-709 | FVG state cleanup | MEDIUM — file handle leak | In BULGU-12 fix |

## Verification Plan

### Manual Verification
- Review the diffs to ensure `except Exception: pass` paths are properly logged.
- Simulate an exit flow by reviewing the code to ensure the `trade` reference is maintained throughout the exit lifecycle.
- Verify `trades_history.jsonl` structure theoretically includes the new `pending_exit_*` fields.

### Automated Tests (ZORUNLU — Madde 8)

Gerçek `ActiveTrade` nesnesiyle yazılacak regresyon testleri:

1. **test_exit_race_concurrent_execute** — iki coroutine eşzamanlı `execute()` çağırır (WS callback + 1m bar check simülasyonu). İlk çağrı promote + submit yapar, ikinci çağrı per-sym lock tarafından engellenir veya `_recently_closed`'dan veri alır.
2. **test_exit_race_double_commit** — iki coroutine eşzamanlı `_commit_confirmed_exit` çağırır. İlki `pop` yapar, ikincisi `_recently_closed` cache'inden trade verisini okur ve PnL muhasebesini tamamlar.
3. **test_tp_failure_emergency_close** — `_execute_live_entry` içinde TP placement mock'lanarak başarısız olur → `_emergency_close` çağrılır → `success=False` döner.
