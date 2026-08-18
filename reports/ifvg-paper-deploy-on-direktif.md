# IFVG Paper-Deploy Öncesi Doğrulama — Direktif

## Amaç ve bağlam

IFVG (Inversion FVG) modülü backtest'te doğrulandı (guard-fix sonrası 28-coin
koşusu: 53,018 trade / +1,889,348, baseline'a göre +287,285/+%17.9, 28/28
coin pozitif). Kod `IFVG_ENABLED` flag'i ile kontrol ediliyor (default
`False`), henüz ne canlıda ne paper'da aktif değil.

Şimdi hedef: **canlıya değil, sadece paper'a** açmak. Ama açmadan önce
backtest'in hiç test edemediği iki riski kapatmak istiyoruz — backtest
restart/recovery yaşamıyor ve trade akışını izole ölçmüyor, bu ikisi sadece
gerçek (paper dahil) bir ortamda ortaya çıkar.

## GÖREV 1 — Restart/Recovery Persistence Testi (öncelik: yüksek)

### Sorun
`RetraceStateMachine._inverted_candidates` listesi, trade'ler arası
(hatta bar'lar arası) hafızada tutulan bir state. Bu projede daha önce
tam olarak bu türden bir bug ailesi görüldü: `recovery_manager.py`, bot
restart olduğunda `ActiveTrade`'i yeniden inşa ederken bazı alanları
(örn. `tick_size`) sessizce atlıyordu — hata fırlatmıyor, sadece eksik
state ile devam ediyordu. `_inverted_candidates` de aynı risk kategorisinde:
eğer recovery/restart akışı bu listeyi hiç görmüyorsa, sessizce sıfırlanır.

### Yapılacaklar
1. **Kod incelemesi**: `recovery_manager.py`'nin `RetraceStateMachine`'i
   (veya onu içeren state objesini) restart sırasında nasıl yeniden
   kurduğunu bulun. `_inverted_candidates` bu yeniden kurulumun neresinde?
   - Eğer RSM tamamen sıfırdan (`reset()` çağrısıyla) yaratılıyorsa,
     `_inverted_candidates` zaten boş başlıyor demektir — bu GÜVENLİ ama
     **veri kaybı** anlamına gelir (restart anında izlenen IFVG adayları
     kaybolur). Kritik değil ama davranış olarak belgelenmeli.
   - Eğer RSM'in bazı alanları JSON/state dosyasından restore ediliyor ama
     `_inverted_candidates` bu restore listesinde YOKSA, bu tam olarak
     geçmişteki `tick_size` bug'ının aynısı — sessiz, fark edilmesi zor bir
     eksiklik. BULUNURSA DÜZELTİLMELİ.
2. **Canlı/paper state persistence dosyasını** (varsa, örn.
   `runtime.protection` benzeri bir JSON) kontrol edin: `_inverted_candidates`
   buraya yazılıyor mu, yoksa yalnızca bellekte mi tutuluyor? Bellekteyse ve
   restart'ta kaybolacaksa, bu bilinçli bir tasarım kararı olarak kabul
   edilebilir (kritik değil) — ama **yazılı olarak belgelenmesi** şart, aksi
   halde ileride "neden IFVG adayı restart sonrası kayboldu" diye tekrar
   araştırma başlar.
3. **Manuel/entegrasyon testi**: Paper ortamında (veya lokal bir simülasyonda)
   şu senaryoyu çalıştırın: IFVG adayı kaydedilsin (`_register_inverted`
   tetiklensin) → bot'u yeniden başlatın (gerçek restart veya
   `recovery_manager`'ı tetikleyen bir yeniden başlatma simülasyonu) →
   `_inverted_candidates` listesinin restart sonrası durumunu loglayın/assert
   edin. Sonucu (korundu / temizlendi, hangisi bekleniyordu) raporlayın.
4. Bulgu ne olursa olsun (bug ya da bilinçli davranış), sonucu
   `sniper/memory-bank`'e ve bana kısa bir not olarak getirin.

## GÖREV 2 — 119K NORMAL Suppression Kök Nedeni (öncelik: orta-yüksek)

### Sorun
Guard-fix sonrası 28-coin koşusunda NORMAL trade sayısı hâlâ baseline'ın
altında: 43,146 vs baseline 48,943 (−5,797 trade, yaklaşık −119,307 PnL
maliyeti, IFVG'nin kendi +406,592 katkısıyla telafi ediliyor ama kök neden
hiç bulunmadı). Hipotez: `BIAS_LOCKED` state'i, IFVG adayı izlenirken RSM'i
belirli pencerelerde meşgul tutup yeni geçerli NORMAL FVG sinyallerinin
değerlendirilmesini geciktiriyor/engelliyor.

### Yapılacaklar
1. `sweep_sync.py` / `signal_engine.py`'de, bir NORMAL FVG adayının
   `reject=` ile loglandığı her noktaya (mevcut `stale`/`wick_not_touched`/
   `body_broke_fvg` sebeplerinin yanına) IFVG aktifken ek bir ret sebebi
   ekleyin veya mevcut loglara `rsm.state`/`rsm._inverted_candidates` sayısını
   iliştirin — amaç: reddedilen NORMAL adayların hangi RSM state'inde
   reddedildiğini görmek.
2. `--ifvg` açık 28-coin koşusunu, bu ek loglamayla tekrar çalıştırın (tam
   koşu gerekmez, birkaç coin/gün örneklem yeterli olabilir — amaç kök neden
   teşhisi, PnL doğrulaması değil).
3. Baseline'a göre kaybolan ~5,797 NORMAL trade'in **hangi RSM state'inde**
   ve **hangi koşulda** kaybolduğunu bulun. Beklenen adaylar:
   - RSM `BIAS_LOCKED` durumundayken (bir IFVG adayı izlenirken) yeni NORMAL
     FVG taraması hiç başlamıyor olabilir (state machine'in tek-thread/
     tek-state mantığı yüzünden)
   - Ya da `_inverted_candidates` doluyken bazı NORMAL adaylar yanlışlıkla
     "zaten kullanılmış" sayılıyor olabilir
4. Kök neden netleşince: bu **kabul edilebilir bir trade-off mu** (IFVG'nin
   kendi katkısı fazlasıyla telafi ediyor, dokunmaya değmez) yoksa **basit bir
   düzeltmeyle** (örn. RSM'in IFVG-tracking ile NORMAL-tracking'i paralel/
   bağımsız yürütmesi) kazanç daha da artırılabilir mi, kısa bir öneriyle
   raporlayın. Bu görevde KOD DEĞİŞİKLİĞİ YAPMAYIN — sadece teşhis edip
   raporlayın, düzeltme kararı ayrıca verilecek.

## GÖREV 3 — Paper'a açma (Görev 1 ve 2 raporları onaylandıktan SONRA)

1. `IFVG_ENABLED=True` **yalnızca paper config'inde** ayarlanacak — canlı
   config'e (varsa ayrı bir dosya/ortam değişkeni) kesinlikle dokunulmayacak.
2. Açılışta bir günlük/oturumluk gözlem hedefi: kaç IFVG entry oluştu, kaç
   tanesi kapandı, herhangi bir restart yaşandıysa `_inverted_candidates`
   davranışı Görev 1'deki beklentiyle uyumlu muydu.
3. Herhangi bir anomali (beklenmeyen hata, sessiz state kaybı, aşırı yüksek
   trade sıklığı) olursa hemen flag `False`'a çekilip rapor edilecek —
   paper'da da olsa "bırak devam etsin, sonra bakarız" yaklaşımı YOK; bu
   projenin daha önce (continuation-confirm olayı) tam olarak bu yüzden
   -50K+ bedel ödediği bir hata deseni.

## Sıra

Görev 1 ve Görev 2 paralel yürütülebilir (birbirinden bağımsız). İkisinin
raporu da baş mühendise gelmeden Görev 3'e geçilmeyecek.
