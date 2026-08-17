# IFVG (Inversion FVG) Modülü — Implementasyon Direktifi

## Amaç
Mevcut sweep+FVG stratejisine (retrace/"2022 Model") **ek, ikincil bir sinyal yolu**
olarak IFVG ekle. Mevcut yol hiçbir şekilde değiştirilmeyecek — yalnızca şu an
`continue` ile atlanıp unutulan "kırılmış FVG" senaryosu artık ayrıca izlenip
ters yönde bir retest fırsatına dönüştürülecek.

## Dosya
`sniper/src/retrace_state.py` (hem live bot hem backtest-sniper/analyzer_v5.py
bu dosyayı import ediyor — TEK yerde değişiklik iki tarafa da yansır).

## Güvenlik: Feature Flag ZORUNLU
Bu koda dokunulur dokunulmaz canlı bot da aynı davranışı miras alır. Bu yüzden:

```python
# config.py'ye ekle:
IFVG_ENABLED = False   # canlıda False kalacak, backtest'te True ile test edilecek
```

`RetraceStateMachine` içindeki tüm yeni IFVG kodu bu flag'e bağlı çalışacak.
`IFVG_ENABLED=False` iken davranış BUGÜNKÜ davranışla bit-bit aynı olmalı
(regresyon testiyle kanıtlanacak — aşağıda).

## Yapılacak Değişiklikler

### 1) `__init__`'e ekle
```python
self._inverted_candidates: list[HTFFVG] = []
```

### 2) Yeni metodlar ekle (mevcut hiçbir metodu değiştirmeden)
```python
def _register_inverted(self, fvg: HTFFVG) -> None:
    """Body FVG'yi kırdığında (body_broke_down=True) çağrılır.
    FVG'yi ters yönde retest adayı olarak kaydeder."""
    flipped_dir = "bearish" if fvg.direction == "bullish" else "bullish"
    self._inverted_candidates.append(
        HTFFVG(fvg.top, fvg.bottom, flipped_dir, fvg.bar_index)
    )

def check_ifvg_retest(self, current: Bar) -> HTFFVG | None:
    """Her yeni (kapanmış) bar'da çağrılır. Kayıtlı inverted candidate'lardan
    biri ters yönde wick-touch + no-break şartını sağlarsa onu döndürüp
    listeden çıkarır. Şart sağlanmadan ters yönde de tamamen kırılırsa
    (o taraf da delinirse) aday ölü kabul edilip listeden düşürülür."""
    import config as _cfg
    if not getattr(_cfg, "IFVG_ENABLED", False):
        return None
    for fvg in list(self._inverted_candidates):
        if fvg.direction == "bullish":
            wick_touched = current.low <= fvg.top
            body_broke = current.close < fvg.bottom
        else:
            wick_touched = current.high >= fvg.bottom
            body_broke = current.close > fvg.top
        if body_broke:
            self._inverted_candidates.remove(fvg)
            continue
        if wick_touched:
            self._inverted_candidates.remove(fvg)
            return fvg
    return None
```

### 3) `on_sweep_confirmed()` içinde TEK satır ekle
`body_broke_down` bloğunda (mevcut `continue`'dan hemen önce):
```python
if body_broke_down:
    logger.info("%s | reject=body_broke_fvg", _fvg_debug)
    if getattr(_cfg, "IFVG_ENABLED", False):     # _cfg zaten dosyada import'lu (config as _cfg)
        self._register_inverted(fvg)
    continue
```
`on_bias_fvg()` içindeki eşdeğer `body_broke_down` bloğuna da AYNI satır eklenmeli
(iki yerde de FVG reddi var, ikisi de kırılma anını yakalamalı).

### 4) `reset()`'e ekle
```python
self._inverted_candidates = []
```
(`lock_bias()` buna DOKUNMAYACAK — kararımız: inverted candidate'lar yalnızca
tam reset'te temizlenir, sınırsız süre geçerli.)

### 5) Çağırma noktası — bot.py (live) VE analyzer_v5.py (backtest)
Her 15m bar kapanışında, mevcut `on_sweep_confirmed`/`on_bias_fvg` çağrılarının
HEMEN ARDINDAN:
```python
if rsm.state != RetraceState.TRIGGER_READY:  # normal yol zaten tetiklenmediyse
    ifvg_hit = rsm.check_ifvg_retest(current)
    if ifvg_hit is not None:
        rsm.state = RetraceState.TRIGGER_READY
        rsm.direction = ifvg_hit.direction
        rsm.trigger_fvg = ifvg_hit
        # entry_source işaretlemesi için (madde 6'ya bak):
        rsm._last_trigger_source = "IFVG"
```
Normal yol öncelikli — aynı bar'da hem normal hem IFVG tetiklenirse normal
kazanır (zaten yapısal olarak ikisi aynı anda TRIGGER_READY olamaz, ama netlik
için sıralama önemli).

### 6) Entry/SL/TP hesaplaması
**Hiçbir değişiklik yok.** IFVG tetiklediğinde `trigger_fvg` normal FVG'yle
tamamen aynı obje tipi (`HTFFVG`) olduğundan, `entry_manager.py`'deki
`calculate_sl_tp()` zaten olduğu gibi çalışır — ayrı bir SL/TP mantığı YAZILMAYACAK.

### 7) Raporlama — ZORUNLU (backtest tarafı)
Her trade kaydına IFVG kaynaklı olup olmadığını işaretle:
```python
t["entry_source"] = "IFVG" if getattr(rsm, "_last_trigger_source", None) == "IFVG" else "NORMAL"
```
Backtest raporuna (analyzer_v5.py'nin coin tablosu + toplam özet) şu satır eklensin:
```
IFVG entry sayısı: X / toplam trade Y (%Z)
IFVG-only PnL: ±N | NORMAL-only PnL: ±M
```
Bu, D-mode'da (ATR-chase) SUIUSDT'nin tek başına zararı taşıdığı durumu yakaladığımız
coin-bazlı kontrolün aynısı — IFVG trade'lerinin coin dağılımı ve PnL katkısı
ayrıştırılabilir olmalı ki tek-coin kırılganlığı varsa görülsün.

## Test
- `IFVG_ENABLED=False` iken: `tests/test_retrace_state.py` mevcut 46/46 test
  DEĞİŞMEDEN geçmeli (regresyon garantisi — flag kapalıyken davranış birebir aynı).
- `IFVG_ENABLED=True` için yeni testler: `_register_inverted` doğru flip yapıyor mu,
  `check_ifvg_retest` wick-touch/no-break/tam-kırılma senaryolarını doğru
  ayırt ediyor mu, `reset()` listeyi temizliyor mu, `lock_bias()` temizlemiyor mu.

## Süreç (ZORUNLU sıra)
1. Kodu yaz, flag `IFVG_ENABLED=False` default, testleri geçir, commit.
2. `analyzer_v5.py`'de `IFVG_ENABLED=True` ile backtest koş (mevcut 1.6M temiz
   baseline'a karşı, aynı 28 coin, aynı risk ayarı).
3. Sonuç raporunu (toplam Δ + IFVG entry sayısı/oranı + coin bazlı dağılım)
   baş mühendise getir — canlıya deploy kararı ORADA verilecek.
4. Backtest pozitif VE coin dağılımı sağlıklıysa (B_SWING onayındaki gibi tek
   coine bağımlı değilse) `IFVG_ENABLED=True` canlıya alınır. Aksi halde
   iterasyona devam edilir, canlıya HİÇBİR ŞEKİLDE flag açık gitmez.
