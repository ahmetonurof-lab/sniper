Sniper yeni strateji için BIAS + SWEEP kalıcı kilit fix direktifi
Hedef branch: ahmetonurof-lab/sniper
Referans HEAD: 3a8831937e464b7393e7ce05182c411ce6c17441

1. Kesin strateji sözleşmesi
İstenen canlı davranış aşağıdaki gibidir:

text
22:00      yeni CBDR döngüsü başlar
CBDR       body high/low toplanır
İlk geçerli sweep
           sweep yönü günlük BIAS olur
Aynı gün   BIAS kilitli kalır, yeni sweep BIAS'ı değiştiremez
Aynı gün   yeni sweep beklenmez
Aynı gün   yalnızca kilitli BIAS yönündeki FVG aranır
FVG        wick rejection + tüm filtreler geçerse entry
22:00      yeni CBDR döngüsü, eski BIAS/SWEEP latch temizlenir
Bu strateji bug değildir. Backtest ve paper trade aynı state sözleşmesini kullanmalıdır. D-02 adıyla önceki direktifte anlatılan sorun, stratejinin yanlış olduğu anlamına gelmez. Doğru sorun şudur: canlı bias_locked yalnızca memory'de tutuluyor, restart sonrasında yeniden oluşturulmuyor.

2. Doğrulanmış mevcut eksik
A. BIAS latch memory'de var, persistence'da yok
src/session.py içinde:

python
self.bias_locked: bool = False
self.daily_bias: DailyBias = DailyBias.NEUTRAL
self.sweep_confirmed: bool = False
self.sweep_direction: Literal["bullish", "bearish"] | None = None
self.sweep_level: float | None = None
İlk sweep sonrası latch memory'de set ediliyor:

python
self.daily_bias = DailyBias.BEARISH
self.bias_locked = True
Ancak src/state_manager.py'de kalıcı state yalnızca trade ve _used_sweeps/_consumed_sweeps bilgilerini tutuyor. SessionState yeniden oluşturulurken daily_bias, bias_locked, sweep_direction ve sweep_level restore edilmiyor.

src/bot.py başlangıçta yeni state nesneleri oluşturuyor:

python
self.states[sym] = SessionState(
    start_hour=get_session_hours(sym)["start"],
    end_hour=get_session_hours(sym)["end"],
)
Bu nedenle process restart'ında aynı CBDR döngüsü devam ediyor olsa bile BIAS tekrar NEUTRAL oluyor. Bot yeniden CBDR sweep bekleyen state'e dönüyor. Bu, kullanıcının yeni stratejisine aykırı ve P0/P1 sınırında canlı strateji state kaybıdır.

B. Önceki D-02 açıklamasının düzeltilmesi
confirm_entry_success() içindeki sweep persistence, sweep'in günlük BIAS kilidi değildir. O kayıt yalnızca aynı sweep ID'sinin restart sonrası tekrar tüketilmesini önleyen dedup metadata'sıdır. Persistence hatasında _pending_sweep_id'nin lock_bias() tarafından temizlenmesi kötü bir exactly-once ayrıntısıdır, fakat tek başına botu eski "tekrar sweep bekle" stratejisine döndüren ana neden değildir.

Asıl kritik fix: BIAS latch state'i CBDR day key ile birlikte persistence'a yazılmalı ve restart'ta restore edilmelidir.

3. Zorunlu kaynak değişiklikleri
3.1 state_manager.py: bias latch state API ekle
Yeni state formatı symbol-scoped olmalı:

json
{
  "BTCUSDT": {
    "date": "2026-08-14",
    "count": 1,
    "open": false,
    "bias_locked": true,
    "daily_bias": "BULLISH",
    "sweep_direction": "bullish",
    "sweep_level": 100.25,
    "bias_lock_day": "2026-08-14"
  }
}
Yeni fonksiyonlar:

python
def mark_bias_locked(
    symbol: str,
    day_key: str,
    daily_bias: str,
    sweep_direction: str,
    sweep_level: float,
) -> bool:
    """İlk geçerli sweep sonrası BIAS latch'i atomik olarak kaydet."""
python
def load_bias_lock(symbol: str, day_key: str) -> dict | None:
    """Aynı CBDR day key için persist edilmiş BIAS latch'i döndür."""
Kurallar:

Yazma FileLock altında mevcut _load() + _save() akışıyla atomik yapılmalı.

day_key eşleşmiyorsa eski latch restore edilmemeli.

daily_bias yalnızca BULLISH/BEARISH kabul etmeli.

sweep_level finite/pozitif kontrolünden geçmeli.

Persistence hatası exception yutularak "başarılı" dönmemeli.

3.2 session.py: latch'i tek sahip olarak yönet
CBDRState.check_sweep() mevcut bias_locked guard'ını koruyacak. İlk sweep başarılı olduğunda SessionState seviyesinde persist callback veya açık method kullanılmalı:

python
def lock_bias_from_sweep(
    self,
    symbol: str,
    direction: Literal["bullish", "bearish"],
    level: float,
) -> bool:
Bu method:

daily_bias, sweep_direction, sweep_level, bias_locked alanlarını set eder.

cbdr_day ile birlikte state_manager.mark_bias_locked(...) çağırır.

Persistence başarısızsa memory latch yine korunabilir, fakat False döner ve kritik log/event üretir.

Aynı CBDR day içinde ikinci çağrı mevcut latch'i değiştirmez.

Daha küçük patch istenirse mevcut check_sweep() içindeki set sonrasında state manager çağrısı yapılabilir; fakat önerilen yöntem state yazma sorumluluğunu SessionState methodunda toplamaktır.

3.3 bot.py: startup restore zorunlu
SessionState oluşturulduktan ve cbdr_day bilinir hale geldikten sonra aynı day key için restore yapılmalı. Restore sırası:

SessionState oluştur.

İlk gelen bar timestamp'i ile cbdr_day_key belirle veya mevcut session initialization akışında day key'i hesapla.

load_bias_lock(sym, expected_day_key) çağır.

Kayıt geçerliyse:

ss.daily_bias = DailyBias[stored["daily_bias"]]

ss._cbdr.sweep_direction = stored["sweep_direction"]

ss._cbdr.sweep_level = stored["sweep_level"]

ss._cbdr.bias_locked = True

ss._cbdr.sweep_confirmed = False

RSM restart sonrası BIAS_LOCKED olmalı, fakat eski trade yoksa _locked_from_bar için persisted bias_lock_bar_index tutulmalı. Bu alan yoksa mevcut bar index'ten önceki FVG'leri tekrar kullanmamak için conservative olarak last_processed_bar_index persist edilmelidir.

3.4 RSM restore ve sweep bekleme yasağı
Restart sonrası bias latch restore edilmişse RSM IDLE başlatılmamalı. Şu iki seçenekten biri uygulanmalı:

RetraceStateMachine.restore_bias_lock(direction, locked_from_bar) methodu eklenip BIAS_LOCKED state'e geçirilmeli.

Ya da SignalEngine ilk progress'te ss.bias_locked + ss.daily_bias eşleşmesini görüp RSM'yi BIAS_LOCKED state'e almalı.

Önerilen method:

python
def restore_bias_lock(
    self,
    direction: Literal["bullish", "bearish"],
    locked_from_bar: int,
) -> None:
    self.state = RetraceState.BIAS_LOCKED
    self.direction = direction
    self.sweep_level = None
    self.trigger_fvg = None
    self._pending_sweep_id = None
    self._locked_from_bar = locked_from_bar
Restore sonrası SignalEngine.progress_rsm() yeni sweep istememeli; on_bias_fvg() çağrısı ile yalnızca kilit yönünü aramalı.

4. L-08/L-09 sweep dedup kapsamı
confirm_entry_success() yalnızca sweep dedup persistence'ını yönetir. Bu fonksiyonun başarısız olması BIAS latch'i veya FVG aramasını durdurmamalı. Entry başarıldıysa:

bias_locked=True kalmalı.

RSM BIAS_LOCKED olmalı.

FVG araması devam etmeli.

Sweep persistence retry ayrı background/startup reconciliation işi olmalı.

lock_bias() şu anda _pending_sweep_id = None yapıyor. Bu, yeni stratejinin BIAS kilidini bozmaz; fakat persistence retry metadata'sını kaybettirir. Düzeltme:

_pending_sweep_id'yi lock state'ten bağımsız _pending_sweep_persistence_id alanına taşı.

lock_bias() bu alanı silmemeli.

Başarılı mark_sweep_used() sonrası yalnızca persistence pending alanı temizlenmeli.

Bu metadata yoksa bile persisted bias_locked kaydı FVG-only davranışını sürdürmeli.

5. D-01 nested lock düzeltmesi halen zorunlu
Bu strateji direktifinden bağımsız olarak UserDataHandler lock tutarken ExitLifecycleService.execute() aynı lock'u tekrar alıyor. L-01 registry identity fix'i bu deadlock'u görünür hale getirdi.

Önerilen zorunlu çözüm:

UserDataHandler exit branch'lerinden dış async with lock kaldırılmalı.

Pending mutation + execute ortak service API'sine taşınmalı.

Normalized ve legacy branch'leri aynı ExitLifecycleService metodunu kullanmalı.

Callback'ler asyncio.wait_for(..., timeout=1) ile deadlock testinden geçmeli.

6. Zorunlu cross-context test matrisi
Test A: İlk sweep BIAS'i kilitler
CBDR body high/low kur.

İlk bullish sweep gönder.

Beklenen: daily_bias=BULLISH, bias_locked=True, sweep_direction=bullish.

Test B: Aynı gün ters sweep BIAS'i değiştiremez
İlk bullish sweep sonrası bearish sweep candle gönder.

Beklenen: daily_bias hâlâ BULLISH, bias_locked=True, sweep_direction hâlâ bullish.

sweep_confirmed ikinci kez set edilmemeli.

Test C: BIAS kilitliyken sweep beklenmez
İlk sweep sonrası herhangi bir yeni bar.

BIAS yönlü yeni bullish FVG üret.

Beklenen: RSM BIAS_LOCKED → TRIGGER_READY; ikinci CBDR sweep yok.

Test D: Restart aynı CBDR döngüsünde
İlk sweep ile state dosyasına bias latch yaz.

Yeni PaperTrader/SessionState oluştur.

Aynı cbdr_day ile restore et.

Beklenen: state BIAS_LOCKED, FVG-only arama devam eder.

Test E: Restart yeni CBDR döngüsünde
Eski day key ile kayıt bırak.

Yeni day key ile başlat.

Beklenen: latch restore edilmez; state NEUTRAL; yeni CBDR sweep beklenir.

Test F: Persistence başarısız olsa bile FVG-only strateji korunur
mark_sweep_used veya mark_bias_locked I/O exception üretir.

Beklenen: memory'de BIAS latch korunur; RSM BIAS_LOCKED kalır; FVG araması devam eder; pending persistence retry metadata'sı kaybolmaz.

Test G: Doldurulmuş FVG tekrar kullanılmaz
Bias lock sonrası FVG oluştur.

Ara bar gap'e dokunsun.

Current bar tekrar wick yapsın.

Beklenen: BIAS_LOCKED, yeni trigger yok.

Test H: Exit callback deadlock yok
Shared exit registry kullan.

Normalized matched-fill ve legacy matched-fill callback'lerini gerçek async service stub ile çalıştır.

Beklenen: 1 saniye içinde tamamlanır; service commit veya retry sözleşmesine göre state bırakır.

7. Kabul kriterleri
Aynı CBDR day içinde ilk geçerli sweep sonrası BIAS hiçbir ikinci sweep ile değişmez.

BIAS locked iken sistem yeni sweep beklemez.

BIAS locked iken yalnızca aynı yöndeki FVG'ler taranır.

Restart aynı CBDR day içinde olsa bile BIAS locked state geri yüklenir.

Yeni CBDR day başladığında BIAS temizlenir ve yeni ilk sweep beklenir.

Sweep dedup persistence hatası, BIAS latch'i veya FVG-only aramayı durduramaz.

FVG touched/invalidated adaylar tekrar trigger olmaz.

Normalized/legacy exit callback'lerinde nested lock deadlock yoktur.

Mevcut testlerin pre-existing fail baseline'ı artmaz.

Son karar
D-02nin önceki anlatımı eksikti: pending sweep ID kaybı, yeni stratejinin ana kilidi değildir. Asıl eksik, günlük BIAS + sweep yönü latch'inin restart sonrası persist edilmemesidir. Bu direktif, sistemi tam olarak kullanıcının istediği modele taşır:

text
İlk CBDR sweep → BIAS kilitlenir → sweep bekleme yok → BIAS yönlü FVG → Entry
