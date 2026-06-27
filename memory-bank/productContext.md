# Product Context — Sniper Bot

## Neden Var?
ICT (Inner Circle Trader) konseptlerini — CBDR, likidite sweep, FVG, wick rejection — tam otonom bir botta uygulamak için. Amaç: insan duygusunu ve kararsızlığını denklemden çıkarmak.

## Hangi Sorunu Çözüyor?
1. **Disiplin**: ICT kurallarını 7/24 hatasız uygular — duygusal karar yok.
2. **Hız**: 15m kapanışında milisaniyeler içinde sinyal üretir + emir gönderir.
3. **Çoklu sembol**: Bir insanın aynı anda takip edemeyeceği 13 sembolü tarar.
4. **Restart güvenliği**: Bot kapansa bile trade_state.json + Binance API sayesinde kaldığı yerden devam eder.

## Nasıl Çalışır?
1. **CBDR (Central Bank Dealing Range)**: Londra seansı açılışına kadar gövde bazlı range belirlenir, 02 UTC'de kilitlenir.
2. **Sweep**: CBDR üst/alt sınırının ATR toleransı dahilinde test edilmesi tespit edilir.
3. **FVG Wick Rejection**: Sweep sonrası oluşan FVG'de fitil reddi (wick touches FVG, body does not break through) aranır.
4. **Entry**: Uygun FVG'de market emri ile girilir, SL/TP hemen yerleştirilir.
5. **Trailing (1m)**: 15m FVG'ler kullanılarak SL sürüklenir.
6. **Exit (1m)**: SL veya TP touch'ında çıkılır, retrade kolu kurulur.
7. **Retrade**: İlk trade kapanınca, aynı gün ters yönde ikinci bir sweep + entry aranır (max 1 retrade/gün).

## Kullanıcı Deneyimi Hedefleri
- **Sıfır manuel müdahale**: Bot başlatılır, her şeyi kendi yapar.
- **Şeffaflık**: Her karar console'da açıklamalı olarak gösterilir.
- **Dashboard**: live_state.json üzerinden dışarıya açık JSON beslemesi.
