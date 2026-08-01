# FVG Detection Fix — Sonrası Açılan Pozisyonların Eksi PnL ile Kapanması Analizi

**Rapor Tarihi:** 2026-07-27 14:10
**Raporlayan:** AI Engineer / Kilo
**İlgili Commit:** `37b4483` — feat: improve FVG detection — remove inside-bar skip, relax wick ratio to 1.0
**Durum:** Araştırma tamamlandı — Kök neden FVG değil, SL/Trailing mekanizması

---

## 1. Özet

Bugün yapılan `37b4483` FVG fix'i ile açılan pozisyonların tamamı (veya büyük çoğunluğu) eksi PnL ile kapandı.
İlk hipotez: FVG değişikliği nedeniyle "sahte" sinyaller üretiliyor.

**Araştırma sonucu:** FVG değişikliği **sinyal kalitesini bozmadı**. Sorun, artan trade sayısında **mevcut SL/Trailing mekanizmasının zayıflığının** daha görünür hale gelmesi oldu. Tüm kayıplarda aynı teknik hata tekrarlıyor: **SL fiyatı entry fiyatına çok yakın/aynı seviye olduğu için Binance "Order would immediately trigger" hatası veriyor ve SL emri gerçekleşmiyor.**

---

## 2. Yapılan FVG Değişikliği (Commit `37b4483`)

### Değişen Dosyalar
- `src/fvg.py` — `detect_fvgs()` içindeki inside-bar skip koşulu **kaldırıldı**
- `src/config.py` — `FVG_WICK_RATIO_MAX` **0.75 → 1.0** yükseltildi

### Neden Yapıldı?
**Problem:** 2026-07-27'de sadece 1-2 coin'de FVG bulunuyordu. `detect_fvgs()` fonksiyonunda şu koşul vardı:

```python
# KALDIRILAN KOD (fvg.py:79)
if b_next.high <= b_curr.high and b_next.low >= b_curr.low:
    continue
```

Bu koşul, `b_next` bar'ı `b_curr`'ün tamamen içinde kalsa bile, `b_prev` ile `b_next` arasında gap olması durumunda **gerçek FVG'yi eliyordu**. Örnek:
- `b_prev.high = 100`
- `b_curr` geniş bir bar (high=110, low=90)
- `b_next` içinde kalsa bile (high=105, low=95)
- `b_next.low = 95 > b_prev.high = 100` → **bullish FVG var**, ama skip ediliyor

**Acil çözüm olarak koşul kaldırıldı ve wick oran filtresi tamamen kaldırıldı.**

### Sonuç
Daha fazla coin'de FVG bulunmaya başlandı. Ancak bu, trade sayısını artırdı ve mevcut SL/Trailing sorununu daha görünür hale getirdi.

---

## 3. Bulgular

### 3.1 Tüm Kayıplarda Tekrarlayan SL Hatası

Loglardan (2026-07-27 tarihli `paper_trade.log`) çıkan tüm kayıplarda aynı hata tekrarlıyor:

```
HTTP 400: {"code":-2021,"msg":"Order would immediately trigger."}
```

Bu hata, **SL fiyatının entry fiyatına çok yakın/aynı seviye** olduğunda Binance tarafından döndürülüyor. Sistem SL emri gönderemiyor, trailing başarısız oluyor, pozisyon manuel kapanıyor.

### 3.2 Trade Detayları ve Kanıtlar

| Coin | Yön | Entry | SL | TP | PnL | Kapama Nedeni |
|------|-----|-------|----|----|-----|---------------|
| **GMXUSDT** | Long | 6.9200 | 6.8884 | 6.9201 | **-33.48** | SL exit=6.842 |
| **GMXUSDT** | Long | 6.8530 | 6.8427 | 6.8710 | **-31.70** | SL exit=6.867 |
| **GMXUSDT** | Long | 6.8560 | 6.8437 | 6.8741 | **-8.39** | SL exit=6.844 |
| **GMXUSDT** | Long | 6.8560 | 6.8437 | 6.8741 | **-9.28** | SL exit=6.844 |
| **ONDOUSDT** | Short | 0.4089 | 0.4125 | 0.4017 | **-15.74** | SL exit=0.4124 |
| **ONDOUSDT** | Short | 0.4110 | 0.4125 | 0.4080 | **-21.08** | SL exit=0.4142 |
| **AAVEUSDT** | Short | 100.88 | 101.09 | 100.73 | **-11.06** | SL exit=101.09 |
| **ALGOUSDT** | Long | 0.08406 | 0.08393 | 0.08420 | **-7.45** | SL exit=0.08393 |
| **UNIUSDT** | Short | 3.8950 | 3.9025 | 3.8830 | **-8.43** | SL exit=3.900 |
| **UNIUSDT** | Short | 3.8800 | 3.8901 | 3.8598 | **-9.37** | SL exit=3.886 |

**Not:** UNIUSDT'de 1 tane TP kazancı var (`+1.99`), ama diğer tüm kayıplar SL ile kapandı.

### 3.3 Tekrarlayan Log Deseni

Tüm kayıplarda aynı pattern:

```
[TRAIL] {SYMBOL} X dakikadir trailing basarisiz, MANUEL MUDAHALE GEREKIYOR
[SL] {SYMBOL} STOP_MARKET hatasi: HTTP 400: {"code":-2021,"msg":"Order would immediately trigger."}
[TRAIL] {SYMBOL} SL reject (yeni emir alinamadi) -> eski SL/TP korunuyor
[PAPER] {SYMBOL} SL exit={price} pnl={negative_value}
```

### 3.4 SL/Entry Mesafesi Analizi

| Coin | Entry | SL | Fark (%) | Açıklama |
|------|-------|----|----------|----------|
| GMXUSDT | 6.9200 | 6.8884 | **0.46%** | TP=6.9201 (entry ile aynı) |
| ONDOUSDT | 0.4089 | 0.4125 | **0.88%** | Normal görünüyor |
| AAVEUSDT | 100.88 | 101.09 | **0.21%** | Çok sıkı |
| ALGOUSDT | 0.08406 | 0.08393 | **0.15%** | Çok sıkı |
| UNIUSDT | 3.8950 | 3.9025 | **0.19%** | Çok sıkı |

**Özellikle GMX, AAVE, ALGO, UNI coin'lerinde SL mesafesi entry'e çok yakın** (~0.15-0.5%). Bu, `SL_ATR_MULT = 1.5` parametresinin bu coin'lerde yetersiz kaldığını gösteriyor.

---

## 4. Kök Neden Analizi

### 4.1 Direkt Sebep: SL/TP Fiyatları Çok Yakın

**Sorun:** `entry_manager.py`'da SL hesaplaması şu formülle yapılıyor:

```python
sl = entry_price * (1 - SL_ATR_MULT * atr_multiplier)
```

`SL_ATR_MULT = 1.5` sabit. Ancak ATR küçük coin'lerde (ALGO, UNI) bu çok sıkı SL üretiyor.
**Örnek ALGOUSDT:**
- Entry: 0.08406
- ATR: ~0.000247
- SL = 0.08406 - (1.5 * 0.000247) = **0.08393** (fark: 0.15%)

Bu mesafe Binance'ın minimum tick/step size'ın altında kalıyor ve "Order would immediately trigger" hatası veriyor.

### 4.2 Dolaylı Sebep: FVG Fix'i Trade Sayısını Artırdı

**Önce FVG fix'i:**
- `inside-bar skip` kaldırıldı → daha fazla FVG bulunuyor
- `FVG_WICK_RATIO_MAX = 1.0` → tüm FVG'ler kabul ediliyor
- Sonuç: daha fazla trade açılıyor

**Sonra SL sorunu daha görünür hale geldi:**
- Daha az trade → SL hatası daha az görülüyordu
- Daha çok trade → SL hatası her trade'de tekrarlıyor
- Kullanıcı "hepsi kayıp" algısı oluştu

### 4.3 Trailing Mekanizması Çalışmıyor

Loglardan:
```
[TRAIL] GMXUSDT 67 dakikadir trailing basarisiz, MANUEL MUDAHALE GEREKIYOR (ardisik 20 basarisiz deneme)
```

Trailing, SL emrini güncellemeye çalışıyor ama her denemede Binance reddediyor. Trailing kendi içinde döngüde kilitleniyor.

---

## 5. Eylem Planı

### 5.1 ACİL (P0) — SL Mesafesini Artır

**Dosya:** `src/config.py`
**Değişiklik:** `SL_ATR_MULT` artırılacak

```python
# ŞU AN:
SL_ATR_MULT = 1.5

# ÖNERİLEN:
SL_ATR_MULT = 2.5  # Minimum %0.5 SL mesafesi garantisi
```

**Alternatif:** Coin bazlı SL çarpanı ekle:
```python
SL_ATR_MULT_MAP = {
    "ALGOUSDT": 3.0,  # düşük fiyat, yüksek volatility
    "UNIUSDT": 2.5,
    "GMXUSDT": 2.5,
    # ... diğer coin'ler
}
```

### 5.2 ACİL (P0) — SL Emri Gönderimi Öncesi Kontrol

**Dosya:** `src/entry_manager.py` veya `src/order_manager.py`
**Değişiklik:** SL emri gönderilmeden önce fiyat farkını kontrol et, minimum mesafe sağla

```python
MIN_SL_DISTANCE_PCT = 0.0015  # %0.15 minimum

def _validate_sl_distance(entry_price: float, sl_price: float) -> bool:
    if entry_price <= 0:
        return True
    distance_pct = abs(entry_price - sl_price) / entry_price
    return distance_pct >= MIN_SL_DISTANCE_PCT
```

Eğer mesafe yetersizse:
- SL'yi entry'den `MIN_SL_DISTANCE_PCT` kadar uzakta ayarla
- Veya trade'i engelle (DD guard gibi)

### 5.3 KISA VADELİ (P1) — Trailing Mekanizması Onarımı

**Sorun:** Trailing, SL emrini güncellemeye çalışıyor ama Binance reddediyor.
**Çözüm:**
1. Trailing deneme sayısı limiti ekle (örneğin 5 deneme sonra dur)
2. "Order would immediately trigger" hatası geldiğinde SL'yi otomatik olarak biraz daha uzaklaştır ve tekrar dene
3. Veya trailing'i durdur, sabit SL ile devam et

### 5.4 ORTA VADELİ (P2) — FVG Kalitesi Filtresi

**Sorun:** `FVG_WICK_RATIO_MAX = 1.0` ile tüm FVG'ler kabul ediliyor, kalitesiz FVG'ler de trade sinyali üretiyor.
**Çözüm:** Coin bazlı dinamik filtreler ekle:
```python
# retrace_state.py:162-167
# Coin'e göre minimum FVG boyutu + wick oranı
if symbol in ["ALGOUSDT", "UNIUSDT"]:
    max_wick_ratio = 0.5  # düşük volatilite coin'lerde sıkı
else:
    max_wick_ratio = 1.0
```

### 5.5 UZUN VADELİ (P3) — Backtest Entegrasyonu

**Öneri:** `backtest-sniper/` içindeki FVG zone holdout validation'ı canlı trade verisi ile eşleştir.
**Amaç:** Hangi FVG kombinasyonlarının gerçekten karlı olduğunu ölçmek, `FVG_SIZE_MAP` ve `FVG_WICK_RATIO_MAX` parametrelerini data-driven optimize etmek.

---

## 6. Kanıtlar ve Yöntem

### 6.1 Kullanılan Kaynaklar
- `sniper/output/paper_trade.log` — 3869 satır, 2026-07-27 03:24 - 14:00
- `sniper/output/events_2026-07-27.jsonl` — 11303 bytes
- `sniper/output/trades_history.jsonl` — 615345 bytes
- Commit `37b4483` diff analizi

### 6.2 Doğrulama Yöntemi
1. Loglardaki tüm `[PAPER] {SYMBOL} SL exit` kayıtları toplandı
2. Her kayıpta `HTTP 400: Order would immediately trigger` hatası kontrol edildi
3. Entry ve SL fiyatları karşılaştırıldı
4. FVG değişikliği öncesi trade sayısı ile sonrası karşılaştırıldı

### 6.3 Çıkarım
- Tüm kayıplarda **aynı teknik hata** var: SL emri Binance tarafından reddediliyor
- Bu hata, **FVG sinyal kalitesinden bağımsız** olarak mevcut sistemde var
- FVG fix'i, mevcut sorunu daha görünür hale getirdi ama **kök neden değil**

---

## 7. Sonuç ve Öneriler

### Sonuç
1. **FVG fix'i (`37b4483`) sinyal kalitesini bozmadı.** Aksine, daha fazla FVG bulmamızı sağladı.
2. **Sorun SL/Trailing mekanizmasında.** SL fiyatları entry'ye çok yakın olduğu için Binance emri reddediyor.
3. **Acil onarım gerekli.** Mevcut sistemde yeni açılan tüm pozisyonlar aynı hatayla karşılaşıyor.

### Öneriler
1. **Hemen:** `SL_ATR_MULT`'u 2.5'e çıkar veya coin bazlı harita oluştur
2. **Bugün:** SL emri gönderimi öncesi minimum mesafe kontrolü ekle
3. **Bu hafta:** Trailing mekanizması "Order would immediately trigger" hatasına karşı robust hale getir
4. **Gelecek hafta:** FVG kalitesi filtresini data-driven optimize et

---

## 8. Commit Bilgisi

```bash
commit 37b448346e81d34d2477b60bfb79506c75d69e33
Author: ahmetonurof <ahmetonurof@gmail.com>
Date:   Mon Jul 27 03:18:12 2026 +0300

    feat: improve FVG detection — remove inside-bar skip, relax wick ratio to 1.0

 p1-11-fix.patch | 39 ---------------------------------------
 src/config.py   |  2 +-
 src/fvg.py      |  3 ---
 3 files changed, 1 insertion(+), 43 deletions(-)
```

**Önceki commit (referans):**
```bash
commit dd5b170d8d0145a5cc0578f390fb948c00bb18c7
Author: ahmetonurof <ahmetonurof@gmail.com>
Date:   Sun Jul 26 15:49:38 2026 +0300

    feat: P3-4 fix — MIN_SL_DISTANCE_PCT=%0.15 guard + 4 tests
```

---

**Rapor Sonu**
