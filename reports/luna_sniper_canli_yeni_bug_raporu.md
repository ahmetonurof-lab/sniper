# Sniper canlı repo kapsamlı güvenlik, mantık ve akış denetimi

**Tarih:** 2026-08-13
**Kapsam:** `ahmetonurof-lab/sniper` main branch, canlı akış öncelikli.
**Kapsam dışı:** backtest-sniper yalnızca canlı parity bağlamında referanslandı; yeni backtest bulgusu raporlanmadı.

Bu rapor, promptta istenen kanıt standardına göre yalnızca güncel koddan mekanik olarak izlenebilen bulguları içerir. Önceki kayıtlarda kapatılmış sweep reset, FVG giriş-bar invalidation, recovery tick-size ve algo cancel bulguları tekrar edilmedi.

## Yönetici özeti

Canlıdaki düşük işlem sayısını açıklayabilecek üç doğrudan akış kusuru var:

1. Aktif bir RSM setup'ı FVG beklerken SessionState yeni bir CBDR sweep'iyle bias'ı değiştirebiliyor; RSM yönü ile daily_bias ayrışıyor ve trigger sonradan bias filtresinde düşüyor.
2. `scan_htf_fvgs()` yalnızca son 10 FVG'yi döndürüyor; yön filtresi bundan sonra uygulandığı için son 10 FVG ters yöndeyse daha eski ama geçerli bias yönlü FVG hiç görülemiyor.
3. BIAS_LOCKED modundaki FVG taraması daha önce dokunulmuş/doldurulmuş FVG'lerin yaşam döngüsünü kontrol etmiyor; yanlış re-entry üretme riski var.

Bunlara ek olarak exit lock registry kopyalanması P0 seviyesinde, sweep persistence ve sweep consumption zamanlaması da güvenilirlik kusuru.

---

## [P0] Exit lock registry boşken kopyalanıyor

**Dosya:** `src/trading/exit_lifecycle.py:128-137`
**Dosya:** `src/trading/user_data_handler.py:133-143`
**İlişkili:** `src/bot.py` içindeki `self._exit_locks = {}` ve iki servise dependency injection.

### Kanıt kodu

```python
# exit_lifecycle.py
self._exit_log: dict[str, dict[float, str]] = exit_log or {}
# P0-1 per-trade lock: key = sym+entry_timestamp
self._exit_locks: dict[str, asyncio.Lock] = exit_locks or {}
```

```python
# user_data_handler.py
self._exit_trade = exit_callback
self._exit_locks = exit_locks or {}
```

```python
# bot.py
self._exit_locks: dict[str, asyncio.Lock] = {}
```

```python
# bot.py
self.exit_service = ExitLifecycleService(
    ...
    exit_locks=self._exit_locks,
    ...
)
```

### Mekanizma

`bot.py` boş sözlüğü paylaşmak istiyor. Python'da boş dict falsey olduğu için `exit_locks or {}` yeni sözlük üretir. Böylece `ExitLifecycleService`, `UserDataHandler` ve botun kendi 1m exit yolu aynı lock registry'sini paylaşmıyor.

### Tetikleyici

1. 1m bar SL/TP veya trailing exit tetikler.
2. Aynı anda Binance `ORDER_TRADE_UPDATE` FILLED gelir.
3. 1m yolu ve WS yolu aynı trade key için farklı sözlüklerde ayrı `asyncio.Lock` alır.
4. İki lifecycle paralel ilerler.

### Etki

Per-trade mutual exclusion fiilen yoktur. Çift exit, stale state, çift cleanup ve exit muhasebesi yarışı yeniden mümkün hale gelir. Bu, önceki P0 exit lock düzeltmelerini etkisizleştirir.

### Önerilen yön

Her iki yerde de `exit_locks if exit_locks is not None else {}` kullanılmalı. Aynı düzeltme `exit_log` için de yapılmalı. Cross-context test:

```python
locks = {}
service = ExitLifecycleService(..., exit_locks=locks)
handler = UserDataHandler(..., exit_locks=locks)
assert service._exit_locks is locks
assert handler._exit_locks is locks
```

---

## [P1] Aktif RSM setup'ı, sonraki CBDR sweep'iyle bozuluyor

**Dosya:** `src/session.py:80-99, 286-314`
**Dosya:** `src/trading/signal_engine.py:70-90`
**Dosya:** `src/trading/retrace_state.py:177-192`

### Kanıt kodu

```python
# session.py
if cbdr.locked and not cbdr.sweep_confirmed:
    cbdr.check_sweep(high, low, close, atr)
```

```python
# session.py
if high > self.body_high + tolerance:
    if close < self.body_high:
        self.sweep_confirmed = True
        self.sweep_direction = "bearish"
        self.sweep_level = self.body_high
        self.daily_bias = DailyBias.BEARISH
        return

if low < self.body_low - tolerance:
    if close > self.body_low:
        self.sweep_confirmed = True
        self.sweep_direction = "bullish"
        self.sweep_level = self.body_low
        self.daily_bias = DailyBias.BULLISH
        return
```

```python
# signal_engine.py
if self.rsm.state_name == "SWEEP_DETECTED":
    self.rsm.on_sweep_confirmed(bars_15m, current, atr_val, symbol)
    if self.rsm.state_name == "IDLE":
        ss.sweep_confirmed = False
```

```python
# retrace_state.py
if not htf_fvgs:
    logger.info("[FVG-DEBUG] %s no FVG found in last 100 bars", self.direction)
    return # sweep hala gecerli, bir sonraki bar'i bekle — RESET YOK
```

### Mekanizma

RSM `SWEEP_DETECTED` durumunda FVG bulamazsa setup'ı koruyor. Ancak `ss.sweep_confirmed` false kaldığı için sonraki barlarda `SessionState.check_sweep()` tekrar çalışıyor. Yeni ters sweep geldiğinde `daily_bias`, `sweep_direction` ve `sweep_level` değişiyor; RSM ise eski sweep yönünde `SWEEP_DETECTED` olarak kalıyor.

Daha sonra eski yöndeki FVG bulunursa `evaluate_trigger()` RSM yönünü yeni `daily_bias` ile karşılaştırıp setup'ı resetliyor.

### Tetikleyici

1. CBDR sonrası bullish sweep oluşur.
2. Bullish yönde FVG yoktur; RSM `SWEEP_DETECTED`'de bekler.
3. Daha sonra bearish CBDR sweep koşulu oluşur.
4. `daily_bias` BEARISH olur, RSM yönü BULLISH kalır.
5. Sonraki barda bullish FVG bulunursa trigger bias filtresinde reddedilir ve RSM resetlenir.

### Etki

İlk geçerli sweep'in FVG arama penceresi yeni sweep ile sessizce bozulur. Kullanıcının tarif ettiği “BIAS oluştu, o yöne FVG ara” dinamiği yerine bias tekrar değişir ve birçok setup işleme dönüşmeden düşer. Günlük işlem sayısının düşük kalmasını doğrudan açıklayabilecek mantık kusurudur.

### Önerilen yön

Bias oluştuğu ve RSM `SWEEP_DETECTED`/`BIAS_LOCKED` olduğu sürece yeni CBDR sweep'leri mevcut setup'ın `daily_bias` değerini değiştirmemeli. Sweep tespiti tek seferlik günlük bias latch'ine bağlanmalı veya RSM aktifken `SessionState.check_sweep()` devre dışı bırakılmalı. Ters yön ancak yeni CBDR döngüsünde veya açıkça tanımlanmış reset koşulunda kabul edilmeli.

---

## [P1] Son 10 FVG sınırı, bias yönündeki eski FVG'leri görünmez yapıyor

**Dosya:** `src/retrace_state.py:42-58`
**Kullanım:** `src/retrace_state.py:118-158` ve `src/retrace_state.py:177-241`

### Kanıt kodu

```python
def scan_htf_fvgs(
    bars_15m: list[Bar],
    lookback: int = 100,
    min_fvg_size: float = 10.0,
    max_wick_ratio: float = 1.0,
) -> list[HTFFVG]:
    segment = bars_15m[-lookback:] if len(bars_15m) > lookback else bars_15m
    if len(segment) < 5:
        return []

    fvgs = detect_fvgs(
        segment,
        lookback=len(segment),
        timeframe="15m",
        min_fvg_size=min_fvg_size,
        max_wick_ratio=max_wick_ratio,
    )
    levels = [HTFFVG(f.top, f.bottom, f.direction, f.real_index) for f in fvgs]
    levels.sort(key=lambda x: x.bar_index)
    return levels[-10:] if len(levels) > 10 else levels
```

```python
# on_bias_fvg()
for fvg in reversed(htf_fvgs):
    if fvg.direction != self.direction:
        continue
```

```python
# on_sweep_confirmed()
for fvg in reversed(htf_fvgs):
    if fvg.direction != self.direction:
        logger.info("%s | reject=wrong_direction", _fvg_debug)
        continue
```

### Mekanizma

`levels[-10:]` yön filtresinden önce uygulanıyor. Son 10 FVG'nin tamamı ters yöndeyse, daha eski 100-bar lookback içindeki geçerli bias yönlü FVG'ler listeye hiç girmiyor.

### Tetikleyici

1. Bias BULLISH kilitlenir.
2. Son 10 tespit edilmiş FVG bearish olur.
3. 11. sıradaki eski bullish FVG hâlâ dokunulmamış ve geçerlidir.
4. `on_bias_fvg()` yalnızca son 10'u görür; bullish FVG bulunamaz.

### Etki

FVG var olduğu halde canlı “no trigger” davranışı üretir. Bu da günlük 2-3 işlem sınırının önemli bir yapısal nedeni olabilir.

### Önerilen yön

Limit yön filtresinden sonra uygulanmalı: tüm adaylar içinden önce `fvg.direction == self.direction` seçilmeli, ardından en yeni 10 yön-uyumlu aday tutulmalı. Daha iyisi, FVG yaşam döngüsüyle birlikte yalnızca aktif ve taze adaylar tutulmalı.

---

## [P1] BIAS_LOCKED FVG taraması geçmişte doldurulmuş FVG'yi yeniden kullanabiliyor

**Dosya:** `src/retrace_state.py:118-158`

### Kanıt kodu

```python
htf_fvgs = scan_htf_fvgs(
    bars_15m,
    lookback=100,
    min_fvg_size=min_fvg_size,
    max_wick_ratio=self._max_wick_ratio,
)
if not htf_fvgs:
    return

for fvg in reversed(htf_fvgs):
    if fvg.direction != self.direction:
        continue
    if (
        self._locked_from_bar is not None
        and fvg.bar_index <= self._locked_from_bar
    ):
        continue
    if fvg.bar_index >= current.index:
        continue

    if self.direction == "bullish":
        wick_touched = current.low <= fvg.top
        body_broke_down = current.close < fvg.bottom
    else:
        wick_touched = current.high >= fvg.bottom
        body_broke_down = current.close > fvg.top

    if not wick_touched:
        continue
    if body_broke_down:
        continue

    self.state = RetraceState.TRIGGER_READY
    self.trigger_fvg = fvg
    return
```

### Mekanizma

Kod yalnızca FVG'nin kilit barından sonra oluştuğunu ve mevcut barın FVG'ye wick ile dokunduğunu kontrol ediyor. FVG'nin oluşumundan mevcut bara kadar olan ara barlarda gap içine girip girmediğini veya far-side close ile invalid olup olmadığını kontrol etmiyor.

`detect_fvgs()` ham adayları döndürüyor; burada FVG başına consumed/invalidated state tutulmuyor. Bu nedenle geçmişte doldurulmuş bir FVG, sonraki bir bar yeniden temas ettiğinde yeni trigger gibi kabul edilebilir.

### Tetikleyici

1. BULLISH bias lock oluşur.
2. Yeni bullish FVG meydana gelir.
3. Ara bar FVG içine girer ve FVG fiilen tüketilir.
4. Ara bar far-side invalidation yapmadan kapanır.
5. Daha sonraki bar tekrar FVG üstüne wick atar ve close bottom üstünde kalır.
6. Kod eski FVG'yi yeni trigger kabul eder.

### Etki

Yanlış re-entry, aynı yapısal bölgenin tekrar kullanılması ve canlı ile gerçek FVG yaşam döngüsünün ayrışması. Paper/backtest sonuçları yapısal olarak fazla iyimser olabilir.

### Önerilen yön

Her FVG için oluşumdan önceki/current bar aralığını tarayan tek bir `fvg_is_alive()`/`get_fvg_status()` helper'ı kullanılmalı. Gap touch/fill veya far-side close görüldüğünde aday elenmeli; FVG yalnızca ilk geçerli retest'te tüketilmeli.

---

## [P1] Sweep, entry gerçekleşmeden önce tüketiliyor ve başarısız girişte geri alınmıyor

**Dosya:** `src/retrace_state.py:72-85, 220-241`
**Dosya:** `src/trading/signal_engine.py:93-130`

### Kanıt kodu

```python
def _mark_sweep_used(self):
    if self._pending_sweep_id is not None:
        try:
            from state_manager import mark_sweep_used
            mark_sweep_used(self._pending_sweep_id)
        except Exception:
            pass
    self._pending_sweep_id = None
```

```python
# on_sweep_confirmed()
logger.info("%s | ACCEPT=trigger_ready", _fvg_debug)
self.state = RetraceState.TRIGGER_READY
self.trigger_fvg = fvg
self._mark_sweep_used()
return
```

```python
# evaluate_trigger() filtre yollarından biri
if ss.daily_bias == DailyBias.NEUTRAL:
    log.info("[SKIP] trigger — bias NEUTRAL, atlandi (rsm reset)")
    self.rsm.reset()
    return EvalResult(decision="SKIP", reason="bias_neutral")
```

### Mekanizma

Sweep, yalnızca FVG wick rejection sonucu `TRIGGER_READY` olur olmaz disk state'inde tüketilmiş işaretleniyor. Entry henüz açılmamış durumda. Daha sonra session/bias/router/risk/qty filtresi veya canlı order placement reddederse RSM resetleniyor, fakat sweep ID geri alınmıyor.

### Tetikleyici

1. Sweep ve FVG trigger-ready olur.
2. Entry, min risk, CBDR router, min notional, API veya SL/TP validation nedeniyle reddedilir.
3. RSM resetlenir.
4. Aynı sweep'in tekrar denenmesi gerektiğinde `is_sweep_used()` true döner.

### Etki

Sinyal gerçekten geçerli olmasına rağmen trade açılmadan kalıcı olarak kaybedilir. Bu doğrudan düşük işlem sayısını büyütebilir. Sweep “trigger accepted” aşamasında değil, başarılı entry ve protection confirmation sonrasında tüketilmeli; başarısız entry'de pending ID temizlenmeden retry veya kontrollü invalidation yapılmalı.

---

## [P1] Sweep state persistence hatası sessizce sweep kaybettiriyor

**Dosya:** `src/retrace_state.py:72-85`

### Kanıt kodu

```python
def _mark_sweep_used(self):
    if self._pending_sweep_id is not None:
        try:
            from state_manager import mark_sweep_used
            mark_sweep_used(self._pending_sweep_id)
        except Exception:
            pass
    self._pending_sweep_id = None
```

### Mekanizma

Disk lock, JSON okuma/yazma veya rename hatasında exception tamamen yutuluyor. Buna rağmen `_pending_sweep_id` temizleniyor. Persistence başarısız olduğu halde in-memory state sweep'in tüketildiğini varsayıyor.

### Tetikleyici

Disk doluluğu, dosya lock timeout'u, bozuk JSON veya geçici I/O hatası sırasında FVG trigger-ready olur.

### Etki

Sweep tüketim durumu memory ile disk arasında ayrışır. Restart sonrası aynı sweep yeniden çalışabilir veya aynı process içinde retry edilemez. Finansal etkisi senaryoya göre çift sinyal veya kaçırılmış işlem olabilir.

### Önerilen yön

`mark_sweep_used()` başarı/başarısız sonucunu açıkça döndürmeli. Başarısızsa pending ID tutulmalı, event log + critical/warning üretilmeli ve restart reconciliation yapılmalı. `except: pass` kaldırılmalı.

---

## [P2] FVG tarama fonksiyonu canlı stratejinin yaşam döngüsünü tek veri yapısında taşımıyor

**Dosya:** `src/retrace_state.py:42-58, 118-158, 177-241`

### Kanıt kodu

```python
levels = [HTFFVG(f.top, f.bottom, f.direction, f.real_index) for f in fvgs]
levels.sort(key=lambda x: x.bar_index)
return levels[-10:] if len(levels) > 10 else levels
```

`HTFFVG` yalnızca `top`, `bottom`, `direction`, `bar_index` taşıyor; filled/invalidated/consumed state yok.

### Etki

Aynı ham FVG listesi hem ilk sweep confirmation hem de BIAS_LOCKED re-entry için kullanılıyor, fakat iki kullanımın yaşam döngüsü farklı. Bu ayrım olmadan “ilk retest”, “tüketildi”, “invalidated” ve “yeniden aranabilir” durumları test edilemiyor. Bu P2 mimari kusuru, yukarıdaki P1 stale-FVG bug'ını mümkün kılıyor.

---

# Zorunlu cross-context regression testleri

Aşağıdaki testler aynı kod tabanında `tests/test_live_bias_flow.py` olarak eklenmeli. Bunlar tek modül unit test değil, SessionState + SignalEngine + RetraceStateMachine + entry failure davranışını birlikte doğrular.

## Test 1: Aktif sweep setup'ı ters CBDR sweep'inden etkilenmemeli

```python
def test_active_rsm_setup_does_not_change_daily_bias_on_later_sweep():
    ss = SessionState()
    ss.cbdr_body_high = 110
    ss.cbdr_body_low = 100
    ss.cbdr_locked = True
    rsm = RetraceStateMachine()
    engine = SignalEngine(rsm)

    ss.daily_bias = DailyBias.BULLISH
    rsm.on_sweep("bullish", 100, bar_index=10)

    # İlk confirmation'da FVG yok: RSM setup'ı beklemeli.
    no_fvg_bars = [_bar(i, 105, 106, 104, 105) for i in range(20)]
    engine.progress_rsm(no_fvg_bars, no_fvg_bars[-1], ss, atr_val=1.0, symbol="BTCUSDT")
    assert rsm.state_name == "SWEEP_DETECTED"

    # Sonraki ters sweep, eski aktif setup'ın bias'ını ezmemeli.
    ss._check_cbdr_sweep(high=120, low=90, close=105, atr=0.0)
    assert ss.daily_bias == DailyBias.BULLISH
```

## Test 2: Bias lock ters sweep ile resetlenmemeli

```python
def test_bias_locked_direction_survives_later_cbdR_sweep():
    ss = SessionState()
    ss.cbdr_body_high = 110
    ss.cbdr_body_low = 100
    ss.cbdr_locked = True
    ss.daily_bias = DailyBias.BULLISH
    rsm = RetraceStateMachine()
    rsm.on_sweep("bullish", 100, bar_index=10)
    rsm.lock_bias(bar_index=20)
    engine = SignalEngine(rsm)

    ss._check_cbdr_sweep(high=120, low=90, close=105, atr=0.0)
    bars = [_bar(i, 105, 106, 104, 105) for i in range(30)]
    engine.progress_rsm(bars, bars[-1], ss, atr_val=1.0, symbol="BTCUSDT")
    assert rsm.state_name == "BIAS_LOCKED"
    assert rsm.direction == "bullish"
```

## Test 3: Son 10 ters FVG, daha eski uyumlu FVG'yi gizlememeli

```python
def test_direction_filter_happens_before_fvg_cap():
    # 11 aday üret: eski bullish + son 10 bearish.
    # on_bias_fvg() eski bullish adayı görüp TRIGGER_READY olmalı.
    ...
```

## Test 4: Doldurulmuş FVG tekrar trigger olmamalı

```python
def test_bias_fvg_rejects_fvg_touched_by_intermediate_bar():
    # FVG oluşur, ara bar gap içine girer, current bar tekrar wick atar.
    # Beklenen: BIAS_LOCKED kalır, TRIGGER_READY olmaz.
    ...
```

## Test 5: Entry reddedilince sweep consumption geri alınmalı

```python
def test_failed_entry_does_not_permanently_consume_sweep():
    # Trigger-ready -> entry API failure -> same sweep retry.
    # is_sweep_used(sweep_id) retry öncesi False olmalı veya explicit retry state olmalı.
    ...
```

## Test 6: Exit registry identity

```python
def test_empty_exit_lock_registry_is_shared_by_identity():
    locks = {}
    service = ExitLifecycleService(..., exit_locks=locks)
    handler = UserDataHandler(..., exit_locks=locks)
    assert service._exit_locks is locks
    assert handler._exit_locks is locks
```

---

# Sonuç

Canlıdaki “BIAS oluşunca o yönde FVG ara” fikri kodda kısmen mevcut: `BIAS_LOCKED` ve `on_bias_fvg()` bunun için yazılmış. Ancak state katmanı yeni sweep'lerle bias'ı tekrar değiştirebildiği, RSM aktif setup'ı korunmadığı ve FVG adayları yön filtresinden önce son 10 ile kırpıldığı için bu dinamik güvenilir biçimde çalışmıyor.

Öncelik sırası net:

1. **P0:** exit lock registry aliasing.
2. **P1:** aktif RSM setup'ının yeni sweep ile bozulması.
3. **P1:** son-10 FVG yön starvation.
4. **P1:** BIAS_LOCKED stale/filled FVG reuse.
5. **P1:** entry başarısızken sweep'in erken tüketilmesi.
6. **P1:** sweep persistence hatasının sessiz yutulması.

Bu altı madde düzeltilmeden işlem sayısının yalnızca “piyasada FVG yok” diye yorumlanması yanlış olur. Önce bu akışlar log/event seviyesinde ölçülmeli: `sweep_detected`, `rsm_direction`, `daily_bias`, `fvg_candidates_total`, `fvg_candidates_same_direction`, `fvg_rejected_stale`, `entry_rejected_after_trigger`, `sweep_consumed_after_entry`.
