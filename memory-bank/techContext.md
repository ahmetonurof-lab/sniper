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
│   ├── config.py         # Tüm sabitler, risk map, semboller
│   ├── session.py        # SessionState (CBDR + Range + TradeDay)
│   ├── models.py         # Bar, FVG, ActiveTrade, Result, PendingLock
│   ├── retrace_state.py  # RetraceStateMachine (IDLE→SWEEP→TRIGGER)
│   ├── fvg.py            # FVG detection engine
│   ├── bot_binance.py    # BinanceRESTClient (aiohttp)
│   ├── bot_infra.py      # RateLimiter, CircuitBreaker, helpers
│   ├── websocket.py      # BinanceWSHub (multi-symbol)
│   ├── state_manager.py  # Disk-persistent trade state
│   ├── state_writer.py   # live_state.json writer
│   └── trading/
│       ├── entry_manager.py    # Entry validation + order placement
│       ├── signal_engine.py    # Primary RSM + trigger filters
│       ├── retrade_engine.py   # Retrade logic + LHR fallback
│       ├── trailing_manager.py # 1m FVG trailing + exit
│       ├── order_manager.py    # SL/TP update + repair
│       ├── recovery_manager.py # Startup position recovery
│       ├── user_data_handler.py # WS callback handler
│       └── console_reporter.py # Dedup'd TR-time console
```

## Çalıştırma

```bash
cd sniper/src
python bot.py
```

## Bağımlılıklar
- aiohttp, python-dotenv, numpy

## Harici Servisler
- Binance Futures Testnet: `wss://testnet.binancefuture.com` / `https://testnet.binancefuture.com`

## Ortam Değişkenleri (.env)
```
TESTNET_API_KEY=xxx
TESTNET_API_SECRET=xxx
TESTNET=True
```

## Önemli Config Parametreleri

| Param | Value | Açıklama |
|-------|-------|----------|
| LEVERAGE | 1 | Kaldıraçsız (1x) |
| RISK_PER_TRADE | 0.001 | Fallback risk (%0.1) |
| SL_ATR_MULT | 1.5 | SL = ATR × 1.5 |
| TP_RR | 2.0 | Risk/ödül oranı |
| FVG_BUFFER_MULT | 0.50 | FVG buffer çarpanı |
| RETRADE_FVG_MAX_ATTEMPTS | 3 | Retrade FVG max deneme |
| LHR_RETEST_PCT | 0.003 | LHR zone genişliği |

## Semboller (13 adet)
BTC, ETH, BNB, SOL, AVAX, LINK, XRP, ATOM, ADA, SUI, APT, DOT, NEAR — tümü USDT-margined futures.

## Risk Haritası
| Sembol | Primary | Retrade | Özel Not |
|--------|---------|---------|----------|
| BTCUSDT | 1.2% | 1.0% | Geniş SL → qty cap yiyebilir |
| LINKUSDT | 1.0% | 0.8% | DD %13.6, WR %52.7 |
| DOTUSDT | 1.2% | 0.9% | DD %12.0, WR %70.2 |
| AVAXUSDT | 1.5% | 1.0% | En yüksek risk katsayısı |
| NEARUSDT | 1.2% | 1.0% | — |
| Diğer | 1.0% | 1.0% | Standart risk |
