Aşağıya baş mühendisler için vereceğim detaylı eylem planını bırakıyorum. Bu planın amacı “mevcut bug’ları kapatmak” değil; canlı execution çekirdeğini güvenilir hale getirmek.



Ana strateji: İki paralel hedef var: (1) kısa vadede canlı riski azaltmak, (2) orta vadede state-machine merkezli mimari refactor yapmak. Sadece hotfix yapılırsa risk azalır ama kök sınıf sorun yaşamaya devam eder.

1) Yönetici özeti
Temel teşhis

Sorun tek bir fonksiyonda değil; state sahipliği, protection yaşam döngüsü, exit commit sırası ve WS event işleme sınırları yanlış ayrılmış.
Bu yüzden hata sınıfı şu şekilde tarif edilmeli:

execution-state consistency failure
protection lifecycle ambiguity
premature accounting commit


Bu planın hedefi

Bot ile borsa state’inin ayrışmasını azaltmak
Korumasız açık pozisyon riskini minimize etmek
Zombie trade sınıfını bitirmek
Muhasebe / equity / PnL commit’lerini güvenilir hale getirmek
Background repair/cleanup akışlarını deterministic hale getirmek


2) Uygulama stratejisi: 3 katmanlı plan
Faz 0 — Değişiklik öncesi güvenlik çerçevesi
Bu faz refactor değil; ama refactor öncesi zorunlu emniyet katmanı.



Amaç

Yeni değişiklikler yapılırken canlı riskin kontrolsüz artmasını önlemek
Incident olduğunda sistemin ne yaptığı daha net görülsün


Yapılacaklar

Yeni incident sınıfları tanımlansın:

POSITION_OPEN_BUT_STATE_MISSING
EXIT_UNCONFIRMED
PROTECTION_BROKEN
WS_UNMATCHED_REDUCE_ONLY
ORPHAN_CANCEL_DURING_TRANSITION
Log/event dili standardize edilsin:

intent
execution attempt
exchange confirmation
state commit
rollback / repair
Canlıda “manual intervention required” durumu açık bir terminal state olarak raporlansın; sessiz fallback olmasın.


Beklenen sonuç

Ekip aynı incident’ı farklı isimlerle tartışmaz
Sorunlar artık “semptom bazlı” değil “state bazlı” izlenir


3) Faz 1 — Acil güvenlik hotfix’leri
Bu fazın amacı mimariyi çözmek değil; canlıda en tehlikeli davranışları hemen kırmak.

1.1 _exit_trade() commit sırası düzeltilecek
Şu anki risk

trade çok erken pop() ediliyor
PnL/balance close confirm’den önce yazılıyor
gerçek market fill gelirse PnL aynı akışta yeniden hesaplanmıyor


Yapılacaklar

active_trades.pop() close confirm sonrasına alınsın
available_balance, peak_equity, trades_history, mark_trade_closed yalnızca confirmed close sonrası çalışsın
exit_actual_price/qty yoksa realized PnL commit edilmesin
invalid fill path’lerinde trade state’ten düşmesin


Beklenen sonuç

açık pozisyonun bot state’inden kaybolması engellenir
muhasebe drift’i ciddi azalır
1.2 WS_FALLBACK pre-commit mutation kaldırılacak
Şu anki risk

unmatched reduceOnly fill geldiğinde trade kalıcı alanları kirleniyor


Yapılacaklar

WS handler trade core alanlarını doğrudan yazmasın
fallback event geçici bir pending/event objesine alınsın
stale event ise pending temizlensin; confirmed state değişmesin


Beklenen sonuç

phantom exit penceresi daralır
debug kalitesi yükselir
1.3 verify_protection() semantiği düzeltilecek
Şu anki risk

boş ID mevcut sayılabiliyor


Yapılacaklar

boş string / null state “confirmed protection exists” anlamına gelmesin
protection doğrulaması “presence” değil “lifecycle state” mantığına yaklaşsın
REST başarısızlığında fail-safe kalabilir; ama unknown ile healthy aynı kabul edilmesin


Beklenen sonuç

broken protection yanlışlıkla sağlıklı görünmez
1.4 orphan cleanup’e geçiş farkındalığı eklenecek
Şu anki risk

trailing replacement sırasında meşru yeni emir orphan sanılabiliyor


Yapılacaklar

replacement in-flight iken orphan cleanup aynı sembolde agresif çalışmasın
en azından grace window / pending ID listesi / symbol-level transition flag eklensin


Beklenen sonuç

yanlış orphan cancel düşer
1.5 invalid fill / adapter belirsizliği için güvenli davranış tanımlanacak
Şu anki risk

adapter {} ya da eksik ID döndürebiliyor
üst katman bunu kesin gerçeklik gibi ele alabiliyor


Yapılacaklar

“response accepted” ile “execution confirmed” ayrıştırılsın
eksik ID / eksik fill / ambiguous response ayrı incident tipi olsun
belirsizlikte commit değil pending verification state’ine girilsin


Beklenen sonuç

adapter kaynaklı gri alanlar daha güvenli yönetilir


4) Faz 2 — Mimari refactor blueprint’i
Burası esas çözüm.

4.1 Trade domain model ikiye bölünecek
A) Confirmed Trade State
Bu katman sadece doğrulanmış gerçekleri taşımalı:

entry actual price / qty
current confirmed protection refs
trade open/closed durumu
realized pnl (yalnızca close sonrası)
stable trail level / confirmed trailing count
B) Runtime / Pending Execution State
Bu katman geçici ve çalışma alanı olmalı:

exit requested mi?
market close submitted mi?
new SL/TP pending mi?
unmatched WS event buffered mı?
repair bekleniyor mu?
verification in progress mi?


Neden gerekli?

Şu an aynı ActiveTrade her role zorlanıyor
confirmed ve speculative veriler aynı gövdede karışıyor


Olası sonucu

rollback ve retry daha güvenli olur
incident analizi kolaylaşır
4.2 Explicit lifecycle state machine kurulacak
Önerdiğim kavramsal durumlar:

ACTIVE
TRAIL_REPLACING_PROTECTION
EXIT_REQUESTED
EXIT_SUBMITTED
EXIT_VERIFYING
REPAIR_REQUIRED
CLOSED
BROKEN_MANUAL_INTERVENTION_REQUIRED


Kural

Her state için:

hangi event’ler kabul edilir
hangi event’ler ignore edilir
hangi side-effect’ler serbesttir
hangi background job’lar yasaktır
açık tanımlanmalı.


Neden gerekli?

Şu an trade yarı çıkılmışken yeniden normal aktif trade gibi dolaşabiliyor


Olası sonucu

zombie trade sınıfı ciddi ölçüde biter
cleanup/repair/trailing çakışmaları azalır
4.3 Exit flow transaction haline getirilecek
Önerilen akış:

Prepare
exit reason belirlenir
trade snapshot alınır
pending exit context yaratılır
trade hâlâ confirmed open state’te kalır
Execute
gerekiyorsa protection cancel/replacement stratejisi uygulanır
market close gönderilir
adapter response alınır
Verify
fill verisi / positionAmt / order state doğrulanır
belirsizlik varsa EXIT_VERIFYING veya REPAIR_REQUIRED
Commit
realized pnl hesaplanır
available balance güncellenir
trade closed kaydı yazılır
active set’ten çıkarılır


Kritik not

commit öncesi hiçbir muhasebe kalıcılaşmamalı.


Olası sonucu

close başarısız ama pnl yazıldı sınıfı hata biter
adapter/doğrulama belirsizliği daha güvenli ele alınır
4.4 Protection manager bağımsız servis olarak ayrılacak
Protection şu an order ID alanları etrafında şekilleniyor. Bunun yerine:

her taraf için ayrı lifecycle olsun:

SL status
TP status
durumlar:

EXPECTED
PENDING_CREATE
ACTIVE_CONFIRMED
PENDING_REPLACE
BROKEN
NOT_REQUIRED


Servisin sorumlulukları:

create
replace
verify
repair
invalidate
register previous/current/pending refs


Olası sonucu

verify/repair/orphan akışları daha tutarlı olur
“ID var mı?” yerine “koruma güvenli mi?” sorusu cevaplanır
4.5 WS handler event-source olacak, state mutator olmayacak
Bugünkü sorun

WS event doğrudan domain state’e yazıyor


Yeni model

WS handler normalize edilmiş event üretir
state machine bu event’i tüketir
trade commit’i yalnızca lifecycle yöneticisi yapar


Örnek event tipleri:

PROTECTION_FILLED_MATCHED
PROTECTION_FILLED_UNMATCHED_REDUCE_ONLY
PROTECTION_CANCELED_CURRENT
PROTECTION_CANCELED_PREVIOUS
POSITION_GHOST_FILL


Olası sonucu

WS_FALLBACK daha kontrollü olur
geçici event ile kalıcı gerçeklik ayrılır
4.6 Accounting ve persistence ayrılacak
Bugünkü sorun

muhasebe execution ile iç içe


Yeni model

accounting yalnızca confirmed state transition sonrası çalışır
persistence katmanı lifecycle sonucu yazar; event handler doğrudan state dosyası yazmaz
write_state yalnızca confirmed open state’i ve gerekli runtime-safe özetleri görür


Olası sonucu

dashboard/state file ile gerçeklik daha uyumlu olur
equity/drawdown mantığı daha güvenilir hale gelir


5) Faz 3 — Operasyonel koruma katmanı
Mimari düzelse bile canlıda şu politikalar olmalı:

5.1 Broken state politikası
Aşağıdaki durumda trade normal yönetimden çıkmalı:

close doğrulanamıyor
protection restore edilemiyor
adapter response belirsiz ve state tutarsız
active trade var ama exchange karşılığı net değil


Bu durumda:

otomatik strateji devam etmemeli
trade BROKEN_MANUAL_INTERVENTION_REQUIRED olsun
sembol bazlı yeni entry geçici dursun
5.2 Symbol-level isolation
Bir sembolde transition/broken state varsa o sembolün background repair/cleanup davranışı izole olmalı
Diğer semboller gereksiz etkilenmemeli
5.3 Incident replay / forensic logging
Baş mühendisler için çok değerli olur:

son lifecycle state
son accepted event
pending refs
verified refs
last known positionAmt
last known open orders snapshot


Bunlar olmadan canlı incident’lar tekrar “ne olmuş olabilir?” tartışmasına döner.



6) Test planı
Bu refactor testsiz bırakılırsa aynı sınıf hata başka biçimde geri gelir.

Unit test seviyesinde
exit prepare/execute/verify/commit ayrımı
invalid fill rollback
unmatched reduceOnly fallback
pending replace sırasında orphan suppression
empty ID / unknown ID / old ID semantiği
Integration test seviyesinde
trailing replace sırasında eski SL fill
market close accepted ama delayed confirmation
adapter {} response + later position open
current vs previous vs history ID fill event’leri
Simulation / chaos style test
REST timeout
WS geç gelmesi
cancel success ama event delay
order create success ama ID geç düşmesi
open orders snapshot stale gelmesi


Başarı kriteri

Sistem belirsizlikte yanlış commit yapmamalı
Belirsizlikte conservative state’e geçmeli


7) Uygulama sıralaması
Ben olsam şöyle ilerlerdim:

Sprint A — Güvenlik sabitleme
hotfix 1.1–1.5
yeni incident/log dili
broken/manual intervention state
Sprint B — State model ayrımı
ActiveTrade core/runtime ayrımı
pending refs / pending exit context
Sprint C — Lifecycle engine
explicit state machine
exit transaction akışı
WS event normalization
Sprint D — Protection/orphan refactor
protection manager lifecycle
transition-aware orphan cleanup
repair policy cleanup
Sprint E — Accounting/persistence hardening
commit-only accounting
persistence cleanup
forensic logging


8) Rollout stratejisi
Bu değişiklik tek seferde “big bang” gitmemeli.



Önerim:

önce passive instrumentation
sonra hotfix’ler
sonra state machine feature flag ile
bir süre dual logging:

eski flow ne yapardı
yeni flow ne yaptı
güven oluşunca tam geçiş


Neden?

execution çekirdeği değişiyor
hatayı düzeltirken yeni state boşlukları yaratmamak gerekir


9) Baş mühendislere tek cümlelik karar
Nihai tavsiyem: Bu sistemi “birkaç kritik bug fix edip geçelim” diye ele almak yanlış olur. Doğru yaklaşım, hemen güvenlik hotfix’leri + ardından state-machine merkezli mimari refactor yapmaktır. Aksi halde aynı risk sınıfı yeni varyasyonlarla canlıda geri gelir.
