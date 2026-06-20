# Active Context — Sniper Bot (Paper Trade → Live)

## Mevcut Durum
- **Bot calisiyor**: Testnet WS + REST API bagli
- **Testnet bakiyesi**: 4995.96 USDT
- **Strateji**: CBDR → Sweep → FVG Wick Rejection → Trailing → Exit (analyzer_v3)
- **Sembol sayisi**: 7 (BTC/ETH/BNB/SOL/AVAX/LINK/XRP)
- **Emir gonderme**: Aktif — STOP_MARKET + TAKE_PROFIT_MARKET testnete gidiyor
- **Session gate**: ASIA (22:00-02:00 UTC) red, LONDON+NY kabul
- **Kazanc**: +37.42 USDT (LINK SHORT)

## Son Degisiklikler (2026-06-20)
- `bot.py`: CBDR warmup (gecmis barlardan body hesapla) → sweep hemen yakalanir
- `bot.py`: Position recovery (restartta API'den pozisyonlari yukle, cift trade engelle)
- `bot.py`: Testnet emir gonderme (SL=STOP_MARKET, TP=TAKE_PROFIT_MARKET)
- `bot.py`: Trailing guncellemesinde SL/TP emirlerini yenile
- `bot.py`: Logging duzeltildi — paper_trade.log hem sniper.paper hem ws_hub yazar
- `bot.py`: Session/CBDR/Sweep status her bar'da gosterilir
- `bot_binance.py`: get_positions(), place_stop_order(), place_tp_order() eklendi
- `websocket.py`: prefill_bars() eklendi, open_timeout asyncio.wait_for ile
- `config.py`: FVG_SIZE_MAP, TESTNET_API_KEY destegi
- `backtest-sniper/config.py`: FVG_SIZE_MAP eklendi

## Acik Basliklar
- Pre-commit hooks (ruff, mypy, vulture) su an calismiyor — .pre-commit-config.yaml guncellenmeli
- ETH/BTC/XRP icin backtest sonuclari kontrol edilecek
- `pre-commit install` sonucta runner dogru calismali

## Onemli Notlar
- `sonnet/src/` icindeki hicbir dosya degistirilmez veya silinmez
- Veriler mainnet WS'den gelir (testnet WS = mainnet data)
- Emirler testnet'e gider — canliya geciste sadece API url degisecek
- Bot koparsa testnet'te pozisyon kalir, restartta `_recover_positions()` alir
