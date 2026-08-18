# IFVG Guard-Semantik Fix — Direktif (sıfırdan, bağımsız ajan için)

## Arka plan (kısaca)

`sniper` (canlı bot) ve `backtest-sniper` (backtest motoru) ortak bir dosyayı
paylaşıyor: `retrace_state.py`. Bu dosyada `RetraceStateMachine` sınıfı,
mevcut sweep+FVG stratejisine ek olarak yeni bir "IFVG" (Inversion FVG)
sinyal yolu içeriyor (`_inverted_candidates`, `_register_inverted()`,
`check_ifvg_retest()`). IFVG şu an flag ile kontrol ediliyor
(`config.IFVG_ENABLED`, default `False`) ve **canlı sunucuda henüz deploy
edilmedi** — sadece yerelde ve backtest'te var. Yani bu fix'i uygularken
canlıda risk yok, tüm çalışma yerel/backtest tarafında.

IFVG mantığı: bir FVG'nin body'si kırılırsa (`body_broke_down=True`),
normal yolda bu FVG unutulup geçilirdi. IFVG ile artık bu kırılan FVG
**ters yönde** bir "inverted candidate" olarak kaydediliyor
(`_register_inverted`) ve sonraki barlarda fiyat o bölgeye geri gelip
(wick-touch) ters yönde tepki verirse (`check_ifvg_retest`) yeni bir
trade tetikleniyor.

## Bulunan bug — guard semantik uyumsuzluğu

Sistemde bir "FVG hâlâ geçerli mi" kontrolü var (`fvg_is_alive`): bir FVG
oluştuktan sonra fiyat ters tarafa (far-side) kapanırsa, o FVG geçersiz
sayılır ve trigger iptal edilir. Bu kontrol iki yerde ayrı ayrı implement
edilmiş:

- **Canlı** — `bot.py:560`, `fvg_is_alive()`: FVG **oluşumundan (formation
  bar + 2) itibaren TÜM barları** tarar, herhangi birinde far-side close
  varsa FVG'yi öldürür.
- **Backtest** — `analyzer_v5.py:239`, `get_fvg_status()`: sadece **mevcut
  (trigger) barın** close'una bakar, geçmiş barları taramaz.

NORMAL (sweep+FVG) yolunda bu fark önemli değil çünkü `retrace_state.py`
zaten `on_bias_fvg()` içinde (satır ~347-356) `fvg_is_alive` all-bars +
`_fvg_touched_between` kontrollerini trigger'dan ÖNCE uyguluyor — trigger'a
ulaşan bir FVG zaten canlılığı kanıtlanmış oluyor, backtest'teki cur-bar
kontrolü orada sadece ikincil bir güvenlik katmanı.

**IFVG yolunda ise bu fark gerçek bir sorun.** `_register_inverted`,
kaydedilen adaya orijinal FVG'nin **formasyon bar_index'ini** veriyor
(kırılım/break barının index'ini değil). Bu yüzden:

1. Canlıdaki `fvg_is_alive` all-bars taraması, formasyon+2'den itibaren
   taradığı için **kırılım barının kendisini de** kapsıyor.
2. Kırılım barı, tanım gereği flipped (ters) yönün far-side'ına close
   yapıyor (çünkü kırılma = body'nin FVG'yi delip geçmesi).
3. Sonuç: canlı guard, IFVG adayını **doğduğu anda** (kırılım barının
   kendisinde) ölü sayıyor — IFVG canlıda yapısal olarak hiçbir zaman
   trade üretemez.
4. Backtest'in cur-bar kontrolü ise sadece trigger (retest) barına
   baktığı için bu erken-ölüm durumunu hiç görmüyor — bu yüzden backtest
   koşularında IFVG binlerce trade üretebiliyor, ama bu rakam canlıya
   taşınamaz, guard farkının bir artefaktı.

## Fix — yapılacaklar

### 1) `retrace_state.py` — `_register_inverted`'e break bar index ekle

Şu an (yaklaşık):
```python
def _register_inverted(self, fvg: HTFFVG) -> None:
    flipped_dir = "bearish" if fvg.direction == "bullish" else "bullish"
    self._inverted_candidates.append(
        HTFFVG(fvg.top, fvg.bottom, flipped_dir, fvg.bar_index)  # <- orijinal formasyon bar_index
    )
```
Değişecek: adayın taşıdığı bar_index, **kırılımın gerçekleştiği barın
index'i** olmalı (formasyon barı değil). Bu, `_register_inverted`'i çağıran
yerde (`on_sweep_confirmed`/`on_bias_fvg`'deki `body_broke_down` bloğu)
mevcut bar'ın index'i olarak elde edilebilir — çağıran yer zaten "şu an
işlenen bar" bilgisine sahip, onu parametre olarak geçirin:

```python
def _register_inverted(self, fvg: HTFFVG, break_bar_index: int) -> None:
    flipped_dir = "bearish" if fvg.direction == "bullish" else "bullish"
    self._inverted_candidates.append(
        HTFFVG(fvg.top, fvg.bottom, flipped_dir, break_bar_index)
    )
```
Çağrı noktalarını (`self._register_inverted(fvg)` → `self._register_inverted(fvg, current.bar_index)` gibi, mevcut kod neyse ona göre) güncelleyin.

### 2) Canlılık taraması: `formation+2..current` yerine `break_bar+1..current`

Hem canlı (`bot.py:560` civarı, `fvg_is_alive()` çağrısı) hem backtest
(`analyzer_v5.py:239`, `get_fvg_status()`) tarafında, **IFVG kaynaklı
trigger'lar için** taramanın başlangıç noktası düzeltilmeli: kırılım
barının kendisi taramaya dahil edilmemeli (o bar zaten kırılmayı
tanımlıyor, bir "ölüm" değil), tarama `break_bar_index + 1`'den
başlamalı.

Bunu uygularken NORMAL path'e dokunmayın — sadece `trigger_fvg`'in IFVG
kaynaklı olduğu durum (`rsm._last_trigger_source == "IFVG"` gibi bir
ayırt edici zaten kod tabanında olmalı, yoksa `fvg.bar_index`'in şimdi
break-bar anlamına geldiğini bilen bir yol/parametre ekleyin) için
başlangıç noktasını değiştirin. Hedef: her iki tarafta (canlı + backtest)
**aynı semantik** — tutarlılık, gevşetme değil.

### 3) Parity contract güncellemesi

Mevcut parity contract testi (`test_parity_regression.py` civarı) IFVG
senaryosunu hiç kapsamıyor — IFVG-off davranışını bile test etmiyor. Buna
bir **IFVG-on senaryosu** eklenmeli (flag açıkken canlı ve backtest'in
aynı IFVG trigger/reddi ürettiğini doğrulayan bir test).

Ayrıca, ayrı bir bulgu olarak: bu contract testinin benchmark'ı
2026-07-31'de dondurulmuş, live flow o tarihten sonra değişmiş olabilir —
bu fix'i yaparken aynı contract dosyasına dokunacağınız için, benchmark'ı
da güncel duruma göre tazeleyin (ayrı bir konu ama aynı PR'da halledilmesi
mantıklı, iki kez aynı dosyayı açmayın).

### 4) Test

- `IFVG_ENABLED=False` iken davranış **bit-bit önceki haliyle aynı**
  kalmalı (regresyon garantisi).
- Yeni testler: break_bar_index'in doğru kaydedildiğini, canlılık
  taramasının `break_bar+1`'den başladığını, kırılım barının kendisinin
  artık ölüm sebebi sayılmadığını doğrulayan case'ler.
- Var olan tüm test suite'leri (`test_retrace_state.py`,
  `test_signal_engine.py`, backtest tarafındaki testler) geçmeli.

### 5) Yeniden koşu

Fix tamamlanınca **28-coin tam backtest koşusu** tekrar yapılmalı
(`--ifvg` flag'i ile), önceki (guard bug'lı) rakamlarla karşılaştırmalı.
Önceki koşuda 14,899 IFVG trade / +782,552 IFVG-only PnL çıkmıştı — bu
sayının fix sonrası **düşmesi bekleniyor** (bazı IFVG adayları artık
doğru şekilde erken elenecek). Asıl geçerli rakam bu yeni koşudur, eski
rakamlar referans alınmayacak.

Rapor şunları içermeli:
- Toplam trade sayısı, net PnL, NORMAL/IFVG kırılımı (fix öncesi/sonrası
  karşılaştırmalı)
- 28 coin bazında IFVG PnL dağılımı (tek-coin bağımlılığı var mı kontrolü)
- Parity A/B sonucu (fix'in canlı ve backtest'i gerçekten aynı semantiğe
  çektiğinin kanıtı)

## Kesin kırmızı çizgi

Bu fix + yeniden koşu tamamlanıp rapor baş mühendise sunulmadan
**`IFVG_ENABLED=True` hiçbir şekilde canlıya deploy edilmeyecek.** Şu an
zaten canlıda IFVG kodu yok (config'de flag bile tanımlı değil) — bu
durum, bu görev tamamlanana kadar böyle kalacak.
