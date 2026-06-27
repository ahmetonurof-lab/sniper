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
├── SignalEngine (trading/signal_engine.py)
│   └── RSM progression + trigger filtreleri (bias/session)
├── RetradeEngine (trading/retrade_engine.py)
│   └── Retrade sweep detection + RSM + LHR fallback
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
1. Load sym config (min_fvg, sl_atr, tp_rr, fvg_buf)
2. Get current bar + ATR
3. Session detection: ASIA/LONDON/NEWYORK
4. SessionState.update() — CBDR track + sweep check + range type
5. Active trade check → return if open
6. ASIA skip → return
7. Display session status
8. CBDR lock gate → return if unlocked
9. Sweep status: dead→return / waiting→_check_retrade+return / detected→continue
10. Retrade armed bypass → _check_retrade+return
11. SignalEngine.progress_rsm() — IDLE→SWEEP_DETECTED→TRIGGER_READY
12. Display FVG status
13. SignalEngine.evaluate_trigger() — bias/session filtresi
14. _try_entry() — validate → qty → SL/TP → API orders → ActiveTrade
15. _check_retrade()
16. write_state() — live_state.json
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
6. **1x leverage**: Pozisyon notional'ı balance ile tavanlanır (−2019 hatası önlenir).
