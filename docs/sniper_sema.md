sniper/
├── src/
│   ├── bot.py                     # Ana trading loop
│   ├── bot_binance.py             # REST adapter (place_market_order, place_stop_order, vb.)
│   ├── bot_infra.py               # extract_order_id, circuit breaker, rate limiter
│   ├── config.py                  # CBDR_RISK_MATRIX, sabitler
│   ├── models.py                  # ActiveTrade, Bar, FVG, status sabitleri
│   ├── event_log.py               # log_event
│   ├── fvg.py                     # FVG detection
│   ├── indicators.py              # ATR, True Range
│   ├── retrace_state.py           # RetraceStateMachine (sweep → FVG trigger)
│   ├── risk_manager.py            # Peak equity, circuit breaker
│   ├── session.py                 # SessionState (CBDR, London, NY)
│   ├── session_router.py          # CBDR multiplier lookup
│   ├── state_manager.py           # trades_today state file
│   ├── state_writer.py            # live_state.json writer
│   ├── websocket.py               # Binance WS hub
│   ├── debug_balance.py           # Debug aracı
│   │
│   ├── trading/
│   │   ├── order_manager.py       # Trailing, repair, cleanup, cancel
│   │   ├── entry_manager.py       # Entry, SL/TP placement
│   │   ├── trailing_manager.py    # evaluate_trail, check_exit
│   │   ├── recovery_manager.py    # reconcile_orphan_orders, recover_positions
│   │   ├── user_data_handler.py   # WS order/fill event handler
│   │   ├── console_reporter.py    # Terminal log görselleştirme
│   │   └── signal_engine.py       # Sinyal motoru
│   │
│   └── snapshot/
│       ├── snapshot.py            # HTML snapshot oluşturma
│       └── chart_template.html    # TradingView chart template
│
├── tests/
│   ├── test_bot.py
│   ├── test_bot_binance.py
│   ├── test_bot_infra.py
│   ├── test_entry_manager.py
│   ├── test_order_manager.py
│   ├── test_recovery_manager.py
│   ├── test_snapshot.py
│   ├── test_state_writer.py
│   ├── test_trailing_manager.py
│   ├── test_models.py / test_fvg.py / test_session.py / test_retrace_state.py
│   ├── test_integration.py / test_integration_v2.py
│   ├── test_event_log.py / test_state_manager.py / test_websocket.py
│   └── conftest.py
│
└── docs/
    ├── sprint_a_spec.md
    └── kritik_*.md  (baş mühendis raporları)
```

**Özet:** `src/`'de 16 py dosyası, `src/trading/`'de 8, `src/snapshot/`'de 2. Toplam **26 Python dosyası + 1 HTML**. Baş mühendis mevcut dosyalardan birine ekleme yapmalı, yeni dosya açmamalı.
