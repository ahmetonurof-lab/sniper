# Active Context — Sniper Bot (Paper Trade)

## Mevcut Durum
- **Bot durumu**: Paper trade hazir, `bot.py` orchestrator yazildi
- **Strateji**: CBDR -> Sweep -> FVG Wick Rejection -> Trailing -> Exit (analyzer_v3)
- **Sembol sayisi**: 7 (BTC/ETH/BNB/SOL/AVAX/LINK/XRP)
- **FVG esikleri**: config.py FVG_SIZE_MAP'de coin bazli
- **Session gate**: ASIA (22:00-02:00 UTC) red, LONDON+NY kabul

## Son Degisiklikler (2026-06-20)
- `sonnet/src/`'den alinan dosyalar revize edildi:
  - `bot_infra.py`: V4 bagimliliklari temizlendi, sadece sniper'a ozel
  - `websocket.py`: demo bolumu silindi, sadece WS hub kaldi
  - `bot_binance.py`: degisiklik yok (bagimsiz)
- `bot.py` yazildi: Paper trade orchestrator
  - BinanceWSHub + SessionState + RetraceStateMachine baglantisi
  - 7 sembol ayni anda canli data alir
  - Paper entry/trailing/exit/PnL takibi
  - Progresif log (terminalde renkli)
- `config.py`: FVG_SIZE_MAP eklendi, gereksiz V4 parametreleri temizlendi
- `analyzer_v3.py`: SYMBOL_CONFIGS config.py'den okur hale getirildi

## Acik Basliklar
- ETH/BTC/XRP icin backtest sonuclari kontrol edilecek (bazi coinler negatif)
- Paper trade canli test: `cd sniper\src && python bot.py`
- `sniper` repoya push: `git push sniper main`

## Önemli Notlar
- `sonnet/src/` icindeki hicbir dosya degistirilmez veya silinmez
- Tüm revizyonlar `sniper/src/` icinde yapilir
- FVG esikleri per-coin analyzer'lardaki test edilmis degerlerle birebir ayni
