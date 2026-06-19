# Tech Context — Sniper Bot

## Kullanılan Teknolojiler

| Kategori | Teknoloji | Amaç |
|----------|-----------|------|
| Dil | Python 3.12+ | Ana geliştirme |
| Async | asyncio | WS + REST yönetimi |
| HTTP | httpx | Binance REST API |
| WS | websockets | Binance stream |
| Loglama | logging | Hiyerarşik log |
| Config | Python module | config.py |

## Çalıştırma
```bash
cd sniper/src
set PYTHONIOENCODING=utf-8
python bot.py
```

## Bağımlılıklar
- httpx, websockets, python-dotenv, aiohttp
- numpy (bot_infra.py)

## Harici Servisler
- Binance Futures Testnet: https://testnet.binancefuture.com
- Dashboard: http://localhost:8080

## Ortam Değişkenleri (.env)
```
TESTNET_API_KEY=...
TESTNET_API_SECRET=...
TESTNET=True
BASE_URL=https://testnet.binancefuture.com
```

## Önemli Config Parametreleri
| Param | Value | Açıklama |
|-------|-------|----------|
| LEVERAGE | 20x | Kaldıraç |
| RISK_PER_TRADE | 1% | Risk oranı |
| LOG_LEVEL | INFO | Log seviyesi |
| CHoCH_ATR_PERIOD | 14 | ATR periyodu |
| ADX_THRESHOLD | 20.0 | ADX eşiği |
