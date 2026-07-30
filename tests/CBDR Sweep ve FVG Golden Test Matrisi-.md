# CBDR Sweep ve FVG Golden Test Matrisi

# CBDR Sweep ve FVG Golden Test Matrisi
**Amaç:** Backtest ve live aynı `fvg.py` / `session.py` fonksiyonlarını kullansa bile farklı veri penceresi, timeframe, ATR, filtre ve seçim davranışı nedeniyle ayrışıyor mu kesin olarak ölçmek.
## 1\. Önce kritik ayrım
Aynı fonksiyon kullanımı, aynı sonucu garanti etmez. Sonuç şu beş katmanda değişebilir:

1. **Input bars:** Backtest tüm tarihsel dataframe'i veya kapanmış barları verir; live yalnızca cache'teki son N barı veya kapanmakta olan barı verebilir.
2. **Bar kapanış zamanı:** Backtest kapanmış 15m barla çalışır; live websocket event'i aynı bar kapanmadan tetiklenebilir.
3. **Timeframe/resampling:** Backtest 1m → 15m dönüşümü ile live exchange 15m candle'ı OHLC değerleri farklı olabilir.
4. **ATR ve tolerance:** Aynı `detect_fvgs` veya `check_sweep` fonksiyonu çağrılsa bile ATR'nin hesaplandığı pencere, warmup ve son bar dahil etme kuralı farklı olabilir.
5. **Selection/scope:** Aynı fonksiyon birden fazla FVG döndürür; backtest son FVG'yi, live ilk/geçerli FVG'yi veya trailing yalnızca kendi trade penceresindeki FVG'yi seçebilir.

Bu nedenle soru “fonksiyon aynı mı?” değil, **aynı input snapshot üzerinde aynı sonucu ve aynı seçimi üretiyor mu?**
## 2\. Canlıda neden sorun yaratabilir?
Evet, fark canlıda doğrudan sorun yaratabilir:
*   Farklı FVG seçilirse ilk SL anchor'ı değişir.
*   FVG yüksekliği veya ATR değişirse buffer değişir.
*   Sweep bir bar erken/geç onaylanırsa entry ve `entry_bar_index` değişir.
*   Live kapanmamış barı kullanırsa backtest'te hiç oluşmamış FVG/sweep görülür.
*   Trailing eski veya farklı penceredeki FVG'yi kullanırsa SL ileri/geri zıplayabilir.
*   Aynı aday tekrar tekrar seçilirse cancel/place döngüsü veya `-2021` riski artar.
## 3\. Golden test veri sözleşmesi
Her golden fixture tek bir immutable snapshot olmalı. Fixture içinde mutlaka bulunmalı:

```json
{
  "fixture_id": "g01_valid_bullish_fvg",
  "symbol": "BNBUSDT",
  "timeframe": "15m",
  "bars": [],
  "bar_timestamps": [],
  "closed_bar_count": 0,
  "entry_bar_index": null,
  "atr_period": 14,
  "atr_value": null,
  "config": {
    "CBDR_SWEEP_ATR_TOLERANCE_MULT": 0.5,
    "FVG_MIN_SIZE_ATR_MULT": null,
    "FVG_BUFFER_MIN_FACTOR": null
  },
  "expected": {}
}
```

`bars` hem backtest hem live adapter'a aynı sırada ve aynı tipte verilecek. Live testinde özellikle **kapanmamış son barı dışarıda bırakan** ve **kapanmış son barı dahil eden** iki ayrı fixture kullanılacak.
## 4\. CBDR sweep golden matrisi

| ID | Durum | Veri | Beklenen |
| ---| ---| ---| --- |
| S01 | Bullish sweep | `low < body_low - tolerance`, `close > body_low` | `bullish=True`, `bearish=False` |
| S02 | Bearish sweep | `high > body_high + tolerance`, `close < body_high` | `bearish=True`, `bullish=False` |
| S03 | Tam tolerance sınırı | `low == body_low - tolerance` | Sweep yok, strict eşik korunur |
| S04 | Üst fitil var ama kapanış içeride değil | `high` dışarıda, `close >= body_high` | Bearish sweep yok |
| S05 | Alt fitil var ama kapanış içeride değil | `low` dışarıda, `close <= body_low` | Bullish sweep yok |
| S06 | Body kilitlenmeden event | `body_locked=False` | Sweep yok |
| S07 | ATR sıfır/NaN/sonsuz | tolerance geçersiz | Sweep yok veya açık validation error, sessiz sweep yok |
| S08 | ATR değişimi | Aynı bar, farklı ATR | Tolerance ve sonuç farkı açıkça raporlanır |
| S09 | İki taraf aynı bar | Hem üst hem alt koşul | Repo sözleşmesine göre tek sonuç veya explicit ambiguous; sessiz iki yön yok |
| S10 | Duplicate bar event | Aynı timestamp iki kez | Aynı sweep ikinci kez üretilmez |
| S11 | Out-of-order bar | Eski timestamp yeni event gibi gelir | Event reddedilir veya replay edilmez |
| S12 | Backtest/live snapshot | Aynı kapalı bar + aynı ATR | Sonuç ve sweep timestamp birebir eşit |
| S13 | Açık 15m bar | Aynı bar kapanmadan | Backtest eşdeğeriyle karşılaştırılmaz; live provisional olarak işaretlenir |
| S14 | Session state reset | Yeni CBDR session | Önceki body/sweep state sızmaz |
| S15 | Tolerance config değişimi | `mult=0.5` vs başka değer | Fark beklenen config diff olarak sınıflanır |

### CBDR golden assertion örneği

```python
@pytest.mark.parametrize("fixture_id", ["S01", "S02", "S03", "S04", "S05"])
def test_cbdr_golden(fixture_id, golden_fixture):
    fx = golden_fixture(fixture_id)
    expected = fx["expected"]["sweep"]

    bt = run_backtest_session_snapshot(fx)
    live = run_live_session_snapshot(fx)

    assert bt == expected
    assert live == expected
    assert live == bt
```

Test, sadece boolean değil şunları da karşılaştırmalı:

```plain
sweep_direction
sweep_bar_timestamp
body_high/body_low
atr_value
tolerance
session_id
```

## 5\. FVG golden matrisi

| ID | Durum | Veri | Beklenen |
| ---| ---| ---| --- |
| F01 | Geçerli bullish FVG | Üç bar bullish gap koşulu | Tek beklenen FVG, top/bottom/index sabit |
| F02 | Geçerli bearish FVG | Üç bar bearish gap koşulu | Tek beklenen FVG, yön sabit |
| F03 | Gap tam sınırda | `height == min_size` | Config sözleşmesine göre dahil/dışarıda, explicit |
| F04 | Gap min size altında | `height < min_size` | FVG yok |
| F05 | Sıfır yükseklik | `top == bottom` | FVG yok |
| F06 | Negatif yükseklik | `top < bottom` | FVG yok, exception veya skip sözleşmesi |
| F07 | ATR sıfır | `atr <= 0` | FVG geçersiz, silent accept yok |
| F08 | ATR NaN/sonsuz | invalid ATR | FVG geçersiz |
| F09 | FVG listesi sırası | Aynı snapshotta birden fazla FVG | Backtest/live aynı sıralama |
| F10 | Son FVG seçimi | Eski ve yeni geçerli FVG | Selection policy aynı olmalı |
| F11 | Entry scope | Entry öncesi güçlü FVG, sonrası zayıf FVG | Entry öncesi FVG scope dışı |
| F12 | Trailing scope | Trade sonrası iki FVG | Sadece tanımlı trade penceresi kullanılmalı |
| F13 | Timeframe | 1m resample → 15m | Backtest/live OHLC ve timestamp eşit |
| F14 | Kapanmamış bar | Son bar incomplete | FVG üretilmemeli veya provisional olarak ayrılmalı |
| F15 | Duplicate event | Aynı bar tekrar işlendi | Aynı FVG duplicate oluşmamalı |
| F16 | Out-of-order event | Bar timestamp geriye gider | Eski snapshot yeniden yazılmamalı |
| F17 | Symbol config | Farklı semboller, aynı oranlar | Coin-spesifik gizli branch yok |
| F18 | Direction filter | Long/bearish ve short/bullish uyumsuzluğu | Yanlış yön FVG seçilmez |
| F19 | Analyzer vs retrace | Aynı FVG snapshot | FVG top/bottom/direction/index eşit |
| F20 | Analyzer vs trailing | Aynı trade snapshot | Trailing'in gördüğü FVG listesi ve seçim nedeni raporlanır |

### FVG golden assertion örneği

```python
@pytest.mark.parametrize("fixture_id", ["F01", "F02", "F04", "F05", "F06", "F10", "F11", "F12"])
def test_fvg_golden(fixture_id, golden_fixture):
    fx = golden_fixture(fixture_id)

    expected = fx["expected"]["fvgs"]
    bt = normalize_fvgs(run_backtest_fvg_snapshot(fx))
    live_signal = normalize_fvgs(run_signal_fvg_snapshot(fx))
    live_trailing = normalize_fvgs(run_trailing_fvg_snapshot(fx))

    assert bt == expected
    assert live_signal == expected
    assert live_trailing == expected
```

Normalize edilen alanlar:

```plain
symbol
timeframe
bar_index
timestamp
direction
top
bottom
height
atr
min_size
selection_rank
selection_reason
```

## 6\. Aynı fonksiyon kullanılıyor mu, fark nereden geliyor testi
Ajan önce çağrı matrisi çıkarmalı:

| Consumer | Function | Bars input | Timeframe | ATR source | min\_size/config | Direction | Selection |
| ---| ---| ---| ---| ---| ---| ---| --- |
| analyzer\_v5 | `detect_fvgs` | doldur | doldur | doldur | doldur | doldur | doldur |
| RetraceStateMachine | `detect_fvgs` | doldur | doldur | doldur | doldur | doldur | doldur |
| SignalEngine | `detect_fvgs` | doldur | doldur | doldur | doldur | doldur | doldur |
| trailing\_manager | `detect_fvgs` veya wrapper | doldur | doldur | doldur | doldur | doldur | doldur |

Aynı fonksiyon pointer/source path'i tek başına PASS değildir. Her satır için gerçek runtime snapshot loglanmalı.

Her FVG invocation'a geçici veya kalıcı debug event ekle:

```json
{
  "event_type": "fvg_invocation",
  "consumer": "analyzer|retrace|signal|trailing",
  "symbol": "BNBUSDT",
  "timeframe": "15m",
  "input_first_ts": 0,
  "input_last_ts": 0,
  "input_bar_count": 0,
  "closed_bar_count": 0,
  "atr": 0.0,
  "atr_period": 14,
  "min_size": 0.0,
  "direction_filter": null,
  "selected_fvg": null,
  "all_fvgs": [],
  "data_snapshot_hash": "sha256..."
}
```

Aynı `data_snapshot_hash` üzerinde consumer çıktıları karşılaştırılmalı. Hash farklıysa önce veri farkı çözülmeden algoritma farkı denmemeli.
## 7\. Replay test akışı
Yerel ajan şu sırayla çalışmalı:

1. `fvg.py`, `session.py`, `retrace_state.py`, `analyzer_v5.py`, `trailing_manager.py` gerçek çağrılarını ve wrapper'larını bul.
2. Her consumer için invocation snapshot adapter ekle; üretim hesaplamasını değiştirme.
3. Aynı kapalı bar fixture'ını bütün consumer'lara ver.
4. Aynı ATR, config, timeframe ve direction filter'ı zorla.
5. CBDR sweep çıktısını karşılaştır.
6. FVG listesi ve seçilen FVG'yi karşılaştır.
7. Sonuçları `paper_trade.log` JSONL'a yaz.
8. Farkları şu sınıflardan biriyle etiketle:

```plain
NO_DIFF
DATA_WINDOW_DIFF
TIMEFRAME_DIFF
ATR_DIFF
CONFIG_DIFF
DIRECTION_FILTER_DIFF
SELECTION_DIFF
CLOSED_BAR_DIFF
DUPLICATE_EVENT_DIFF
REAL_ALGORITHM_DIFF
```

1. Sadece `REAL_ALGORITHM_DIFF` varsa fonksiyon mantığı gerçekten ayrışmış sayılır.
2. Her fark için fixture id, consumer, hash ve expected/actual değerleri raporla.
## 8\. Kabul kriterleri
**CBDR için:**
*   S01/S02 pozitif golden testleri geçer.
*   S03-S07 sınır testleri strict ve deterministik davranır.
*   Aynı kapalı snapshotta backtest/live sweep sonucu eşittir.
*   Açık bar backtest'e sızmaz.
*   Duplicate/out-of-order event yeni sweep üretmez.

**FVG için:**
*   F01/F02 pozitif, F04-F08 negatif testleri geçer.
*   Aynı snapshotta analyzer/retrace/signal/trailing FVG listesi eşittir veya fark selection/data sınıfına bağlanır.
*   `top`, `bottom`, `height`, `direction`, `bar_index`, `atr`, `min_size` eşleşir.
*   Trailing farklı FVG seçiyorsa neden ve selection policy açıkça raporlanır.
*   FVG input hash'leri farklıysa önce input parity düzeltilir.

**Rollout:** Golden matrix ve aynı-snapshot replay tamamlanmadan trailing unify edilmez. Fark canlıda SL/TP veya entry timing'i değiştiriyorsa paper-trade gözlemi sürer ve production rollout BLOCKED kalır.
## 9\. Yerel ajana verilecek direktif
> `CBDRState.check_sweep()` ve `detect_fvgs()` için bu golden matrisi uygula. Önce üretim kodunu değiştirme; çağrı matrisi ve runtime input snapshot'larını çıkar. Aynı immutable, kapanmış-bar fixture'ını analyzer, RetraceStateMachine, SignalEngine ve trailing consumer'larına ver. Her consumer için bars hash, timeframe, bar count, closed-bar sınırı, ATR, min\_size, direction filter, tüm FVG listesi ve seçilen FVG'yi kaydet. CBDR'de S01-S15, FVG'de F01-F20 testlerini gerçek fonksiyonlarla çalıştır. Farkları DATA\_WINDOW\_DIFF, TIMEFRAME\_DIFF, ATR\_DIFF, CONFIG\_DIFF, SELECTION\_DIFF, CLOSED\_BAR\_DIFF veya REAL\_ALGORITHM\_DIFF olarak sınıflandır. Aynı fonksiyon kullanılıyor diye PASS deme. Golden test ve replay sonuçları paper\_trade.log JSONL'a yazılsın. `REAL_ALGORITHM_DIFF` veya canlıda farklı SL/entry timing'i doğuran açıklanmamış fark varsa rollout BLOCKED; trailing'i backtest mantığına körlemesine unify etme.\`
