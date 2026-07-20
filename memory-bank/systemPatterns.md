# System Patterns — Sniper Bot

## Sistem Mimarisi

```
PaperTrader (bot.py — orchestrator)
├── SessionState (session.py)
│   ├── CBDRState       — CBDR body tespiti, sweep, bias
│   ├── RangeTracker     — Asia/London/NY range takibi
│   └── TradeDayState    — Günlük trade sayısı + retrade state
├── RetraceStateMachine (retrace_state.py)
│   └── IDLE → SWEEP_DETECTED → TRIGGER_READY
├── indicators.py (Wilder's ATR)
│   └── Rolling 14-periyot ATR, `_atr_state` + `_atr_prev_close`
├── SignalEngine (trading/signal_engine.py)
│   └── RSM progression + trigger filtreleri (bias/session)
├── RiskManager (risk_manager.py)
│   └── Dinamik risk çarpanı: EL 1.5x (02-08 UTC), DD≥%15 devre kesici, filelock thread-safe state
├── EntryManager (trading/entry_manager.py)
│   └── Risk validasyonu → qty hesapla → SL/TP hesapla → API emir
├── TrailingManager (trading/trailing_manager.py)
│   └── 1m FVG trailing + exit check
├── OrderManager (trading/order_manager.py)
│   └── Binance SL/TP order update + repair
├── RecoveryManager (trading/recovery_manager.py)
│   └── Startup pozisyon recovery + ghost cleanup
├── UserDataHandler (trading/user_data_handler.py)
│   └── WS callback: ORDER_TRADE_UPDATE + ACCOUNT_UPDATE
├── ConsoleReporter (trading/console_reporter.py)
│   └── Dedup'lı console output (TR timezone)
└── BinanceWSHub (websocket.py)
    └── Multi-symbol combined stream + bar buffer
```

## 15m Trading Flow (_on_15m_close)

```
1. Load sym config (min_fvg via ATR_MULT, sl_atr, tp_rr, fvg_buf)
2. Get current bar + Wilder's ATR (indicators.py, rolling state)
3. Session detection: ASIA/LONDON/NEWYORK
4. SessionState.update() — CBDR track + sweep check + range type
5. Active trade check → return if open
6. ASIA skip → return
7. Display session status
8. CBDR lock gate → return if unlocked
9. Sweep status: dead→return / waiting→return / detected→continue
10. SignalEngine.progress_rsm() — IDLE→SWEEP_DETECTED→TRIGGER_READY
11. Display FVG status
12. SignalEngine.evaluate_trigger() — bias/session filtresi
13. _try_entry() — validate → qty → SL/TP → API orders → ActiveTrade
14. write_state() — live_state.json
```

## Sinyal Mimarisi

```
IDLE:
  Sweep detected (SessionState.sweep_confirmed)
  → rsm.on_sweep(direction, level)
  → SWEEP_DETECTED

SWEEP_DETECTED:
  New bar → rsm.on_sweep_confirmed(bars)
  → scan_htf_fvgs(lookback=100)
  → wick rejection check (wick touches FVG, body does not break)
  → TRIGGER_READY veya reset(IDLE)

TRIGGER_READY:
  evaluate_trigger() filtreleri:
    ✓ Direction bias (sweep yönü vs daily_bias uyumu)
    ✓ Session (LONDON/NY only)
  → TRIGGER (entry açılır) veya SKIP (reset)
```

## ICT Likidite Mantığı (Sweep Yönü)

```
Price sweeps ABOVE CBDR.high + close below CBDR.high
  → sweep_direction = "bearish"
  → daily_bias = BEARISH
  → SHORT entry

Price sweeps BELOW CBDR.low + close above CBDR.low
  → sweep_direction = "bullish"
  → daily_bias = BULLISH
  → LONG entry
```

## Temel Tasarım Kararları

1. **Tek yönlü bağımlılık**: bot.py → trading/*, döngüsel import yok.
2. **Stateful Session**: Her sembolün kendi SessionState'i, range tracker'ı, RSM'i.
3. **Faz bazlı risk**: SYMBOL_RISK_MAP ile her sembole özel primary/retrade risk.
4. **Restart-proof**: trade_state.json (açık trade, retrade arm, günlük count).
5. **Önce yeni order, sonra eski cancel**: Trailing güncellemesinde order kaybı önlenir.
6. **5x leverage**: Margin = notional / 5. Formülde `/leverage` yok — qty sadece risk+bakiye bazlı.
7. **Quantity string format**: Binance API'ye quantity string olarak gönderilir (`f"{qty:.{decimals}f}"`), float precision hatası önlenir.
8. **Policy/Mekanik ayrımı**: ProtectionLifecycleService policy kararlarını (ne zaman onar, hangi emir iptal) alır; OrderManager/RestClient sadece REST çağrısı yapar.
9. **Rollout flag ile yeni servis**: Her yeni servis `config.py`'de bool flag + env override. `False` iken eski inline logic aynen çalışır.
10. **WS Event → pending → promote pipeline**: WS FILLED event'i confirmed alanlara direkt yazılmaz. `pending_exit_*` alanına yazılır, promotion (pending→confirmed) `ExitLifecycleService` içinde yapılır.
11. **ActiveTrade.confirmed/runtime split**: Flat field'lar backward compat için korunur. `__getitem__`/`__setitem__` belirli key'leri (status, frozen, pending_events, sl_order_id, vb.) `runtime` object'ine yönlendirir.

## Güncel Mimaride Değişiklikler (Patch Set 3-6 + B1-B3 + D1)

```
PaperTrader (bot.py)
├── SessionState / RetraceStateMachine / SignalEngine / RiskManager
├── EntryManager / TrailingManager
│
├── NEW: ExitLifecycleService (exit_lifecycle.py)          ← P2 extraction
│   └── EXIT_LIFECYCLE_SERVICE_ENABLED flag
│
├── NEW: ProtectionLifecycleService (protection_lifecycle.py)  ← P3 extraction
│   ├── verify() → ProtectionCheckResult (B3)               ← B3: tuple→dataclass
│   ├── known_ids() / should_skip_reconcile()
│   ├── maybe_repair() / cleanup_after_confirmed_exit()
│   ├── begin_replace_sl/tp() / promote_sl/tp()
│   └── PROTECTION_LIFECYCLE_SERVICE_ENABLED flag
│
├── CHANGED: OrderManager
│   ├── Delegate protection policy → ProtectionLifecycleService
│   └── verify_protection() → ProtectionCheckResult (B3)
│
├── CHANGED: RecoveryManager
│   └── _known_protection_ids / reconcile → delegate to ProtectionLifecycleService
│
├── CHANGED: UserDataHandler (P4)
│   ├── normalize_order_event() pipeline
│   ├── pending_exit_* writes (no direct confirmed mutation)
│   └── WS_EVENT_NORMALIZATION_ENABLED flag
│
├── CHANGED: bot.py _on_1m_close (P5)
│   ├── Orphan sweep status'tan bağımsız (her 5 bar)
│   ├── ATR → unrestricted bloğu içinde
│   └── UPNL + state writer her bar (frozen dahil)
│
├── CHANGED: ActiveTrade models (B1, B2, D1)
│   ├── .runtime: TradeRuntimeState        ← B1
│   │   ├── .status / .frozen / .pending_exit
│   │   ├── .protection: ProtectionState   ← B2
│   │   │   ├── sl_current/pending/previous
│   │   │   ├── tp_current/pending/previous
│   │   │   ├── history
│   │   │   └── sl_status / tp_status / health  ← D1
│   │   └── .pending_events
│   └── Dict redirect via __getitem__/__setitem__
│
└── CHANGED: StateWriter (P6, D1)
    ├── frozen (boolean, trade bazlı)       ← P6
    ├── feature_flags (3 rollout flag)      ← P6
    ├── sl_status / tp_status               ← D1
    └── protection_health                   ← D1
```

## Eklenen Servisler

### ExitLifecycleService (`exit_lifecycle.py`)
- WS-FALLBACK guard, paper-mode skip, adapter ambiguity (stop-loss vs take-profit fill ayrımı)
- Verification loop (5-attempt position size check)
- `_commit_confirmed_exit()` — PnL hesaplama + cleanup
- REPAIR_REQUIRED status detection
- `EXIT_LIFECYCLE_SERVICE_ENABLED` flag ile rollout

### ProtectionLifecycleService (`protection_lifecycle.py`)
- `known_ids(trade)` — tüm oid kaynaklarını toplar
- `should_skip_reconcile(trade)` — transition state'lerinde orphan sweep engeller
- `verify(trade, open_order_ids)` — SL/TP emirlerinin durumunu kontrol eder
- `maybe_repair(trade, check)` — onarım gerekli mi kararı
- `cleanup_after_confirmed_exit(trade, result)` — exit sonrası temizlik planı
- `begin_replace_sl/tp()`, `promote_sl/tp()` — pending→current lifecycle
- `PROTECTION_LIFECYCLE_SERVICE_ENABLED` flag ile rollout

## Veri Modeli (ActiveTrade)

### Confirmed/Runtime Split
```
ActiveTrade
├── .runtime: TradeRuntimeState
│   ├── .status: TradeStatus           (enum: ACTIVE/PENDING/EXIT_REQUESTED/CLOSED/vb.)
│   ├── .frozen: bool
│   ├── .pending_exit: PendingExitContext | None
│   ├── .protection: ProtectionState
│   │   ├── .sl_current / .sl_pending / .sl_previous: ProtectionRef | None
│   │   ├── .tp_current / .tp_pending / .tp_previous: ProtectionRef | None
│   │   ├── .history: list[ProtectionRef]
│   │   ├── .sl_status(sl_price) / .tp_status(tp_price) / .health
│   │   └── .known_ids() → set[str]
│   └── .pending_events: list[NormalizedOrderEvent]
├── (flat backward-compat fields: sl_order_id, tp_order_id, status, vb.)
└── Dict redirect: __getitem__/__setitem__ → runtime keys + protection keys
```

### ProtectionState Lifecycle Status
| Status | Anlamı |
|--------|--------|
| `NOT_REQUIRED` | SL/TP fiyatı 0 (gerekmiyor) |
| `ACTIVE_CONFIRMED` | Emir gönderilmiş ve onaylanmış |
| `PENDING_CREATE` | Replacement emir beklemede (replace sırasında) |
| `EXPECTED` | Fiyat > 0 ama emir yok — olması gerekir |

### Exit State Machine (Sprint C)
```
Trail/Exit detected → trade["status"] = EXIT_REQUESTED  (pending_exit_price yazılır)
                           ↓
_exit_trade() / ExitLifecycleService.execute()
  → trade["status"] = EXIT_SUBMITTED        (cancel_all + reduceOnly öncesi)
  → market order sent
  → trade["status"] = EXIT_VERIFYING         (position verification loop)
  → success → commit → trade["status"] = CLOSED
  → fail    → trade["status"] = REPAIR_REQUIRED

update_trail_orders():
  → trade["status"] = TRAIL_REPLACING        (replace sırasında)
  → success → trade["status"] = ACTIVE
  → fail    → trade["status"] = ACTIVE       (ikisi de başarısız olursa)
```

### WS Normalization Pipeline
```
raw WS payload → normalize_order_event() → NormalizedOrderEvent
→ _oid_matches_trade() → eşleşen oid → pending_exit_* alanlarına yaz
→ _exit_trade() / ExitLifecycleService.execute() → promote pending→confirmed
```
