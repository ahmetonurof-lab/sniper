# Tech Context — Sniper Bot

## Kullanılan Teknolojiler

| Kategori | Teknoloji | Amaç |
|----------|-----------|------|
| Dil | Python 3.12+ | Ana geliştirme |
| Async | asyncio | WS + REST |
| HTTP | aiohttp | Binance REST API + WS |
| Format | ruff (linter + formatter) | Kod kalitesi |
| Tip | mypy (optional) | Type checking |
| Ölü kod | vulture | Kullanılmayan kod tespiti |
| Pre-commit | pre-commit hooks | Otomatik kalite gates |
| Config | Python module (config.py) | Tüm parametreler |

## Proje Yapısı

```
sniper/
├── memory-bank/          # Session'lar arası kalıcı bağlam
├── output/               # live_state.json (dashboard beslemesi)
├── src/
│   ├── bot.py            # PaperTrader orchestrator
│   ├── risk_manager.py   # Dinamik risk çarpanı + devre kesici (filelock)
│   ├── config.py         # Tüm sabitler, risk map, semboller
│   ├── session.py        # SessionState (CBDR + Range + TradeDay)
│   ├── models.py         # Bar, FVG, ActiveTrade, Result, PendingLock, TradeRuntimeState, TradeConfirmedState, ProtectionState, ProtectionRef, NormalizedOrderEvent
│   ├── retrace_state.py  # RetraceStateMachine (IDLE→SWEEP→TRIGGER)
│   ├── fvg.py            # FVG detection engine
│   ├── bot_binance.py    # BinanceRESTClient (aiohttp)
│   ├── bot_infra.py      # RateLimiter, CircuitBreaker, helpers
│   ├── websocket.py      # BinanceWSHub (multi-symbol)
│   ├── state_manager.py  # Disk-persistent trade state
│   ├── state_writer.py   # live_state.json writer
│   ├── indicators.py     # Wilder's ATR (14-periyot, rolling state)
│   └── trading/
│       ├── entry_manager.py        # Entry validation + order placement
│       ├── signal_engine.py        # Primary RSM + trigger filters
│       ├── trailing_manager.py     # 1m FVG trailing + exit
│       ├── order_manager.py        # SL/TP update + repair
│       ├── recovery_manager.py     # Startup position recovery
│       ├── user_data_handler.py    # WS callback handler
│       ├── exit_lifecycle.py       # ExitLifecycleService (557 satır, Extract)
│       ├── protection_lifecycle.py # ProtectionLifecycleService (265 satır, Extract)
│       └── console_reporter.py     # Dedup'd TR-time console
```

## Çalıştırma

```bash
cd sniper/src
python bot.py
```

## Bağımlılıklar
- aiohttp, python-dotenv, numpy

## Harici Servisler
- Binance Futures Testnet: `wss://fstream.binancefuture.com` / `https://demo-fapi.binance.com`

## Ortam Değişkenleri (.env)
```
TESTNET_API_KEY=xxx
TESTNET_API_SECRET=xxx
TESTNET=True
```

## Önemli Config Parametreleri

| Param | Value | Açıklama |
|-------|-------|----------|
| LEVERAGE | 5 | 5x kaldıraç (margin = notional / 5) |
| RISK_PER_TRADE | 0.003 | Risk (%0.3) — elle güncellendi |
| SL_ATR_MULT | 1.5 | SL = ATR × 1.5 |
| TP_RR | 2.0 | Risk/ödül oranı |
| FVG_BUFFER_MULT | 0.50 | FVG buffer çarpanı |
| FVG_WICK_RATIO_MAX | 0.75 | FVG wick ratio (impulse mother bar kontrolü) — eskisi: 0.90 |
| FVG_MIN_SIZE_ATR_MULT | 0.06 | Dinamik FVG eşiği: min_fvg = atr_val × 0.06 |
| RETRADE_FVG_MAX_ATTEMPTS | 3 | (kullanılmıyor — retrade silindi) |
| LHR_RETEST_PCT | 0.003 | (kullanılmıyor — retrade silindi) |
| EXIT_LIFECYCLE_SERVICE_ENABLED | False (env) | ExitLifecycleService rollout flag |
| PROTECTION_LIFECYCLE_SERVICE_ENABLED | False (env) | ProtectionLifecycleService rollout flag |
| WS_EVENT_NORMALIZATION_ENABLED | False (env) | WS normalization rollout flag |

## Semboller (28 adet)
BTC, ETH, BNB, SOL, AVAX, LINK, XRP, ATOM, ADA, SUI, APT, DOT, NEAR, TIA, SEI, ONDO, PYTH, RENDER, ENA, STRK, GMX, DYDX, LDO + 5 eski — tümü USDT-margined futures.

## Risk Haritası
| Sembol | Primary | Retrade | Özel Not |
|--------|---------|---------|----------|
| BTCUSDT | 1.2% | 1.0% | — |
| LINKUSDT | 1.0% | 0.8% | DD %13.6, WR %52.7 |
| DOTUSDT | 1.2% | 0.9% | DD %12.0, WR %70.2 |
| AVAXUSDT | 1.5% | 1.0% | En yüksek risk katsayısı |
| NEARUSDT | 1.2% | 1.0% | — |
| Diğer | 1.0% | 1.0% | Standart risk |
