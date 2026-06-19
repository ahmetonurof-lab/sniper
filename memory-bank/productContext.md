# Product Context — Sniper Bot

## Neden Var?
Sonnet botunun tüm V4 mantığını alıp bağımsız, hafif ve taşınabilir bir paket haline getirmek için oluşturuldu. Kendi repo'suna sahiptir, Sonnet'ten bağımsız deploy edilebilir.

## Hangi Sorunu Çözüyor?
1. **Bağımsız dağıtım**: Sonnet kod tabanına bağımlılık olmadan çalışır.
2. **Taşınabilirlik**: Kendi config, state ve mantık dosyalarıyla her yerde çalıştırılabilir.
3. **V40 Sniper Flow**: Sadeleştirilmiş state machine ile daha hızlı ve güvenilir sinyal üretimi.

## Nasıl Çalışır?
1. WebSocket üzerinden 19 sembolün 4 timeframe bar verisi alınır.
2. `bot_pipeline.py` sinyal zincirini çalıştırır.
3. State machine IDLE → WAIT_RETRACE → WAIT_CONFIRM → READY_TO_ENTER zincirini yönetir.
4. READY_TO_ENTER durumunda emir gönderilir.
5. Açık pozisyonlar trailing stop ile yönetilir.

## Kullanıcı Deneyimi Hedefleri
- **Sıfır manuel müdahale**: Bot başlatılır, kendi kararlarını verir.
- **Şeffaf loglama**: Her event, state geçişi loglanır.
- **Dashboard**: http://localhost:8080 üzerinden canlı takip.
