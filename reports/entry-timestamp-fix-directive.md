DİREKTİF — ActiveTrade'de entry_timestamp alanı hiç yok, snapshot yanlış bar seçiyor

Repo: sniper
Öncelik: ORTA — trade kararlarını etkilemiyor (borsa kayıtları zaten doğru,
bunu yerel ajan testnet emir geçmişiyle doğruladı), ama snapshot/chart analizi
36+ saat yanlış zaman gösterebiliyor — post-mortem/inceleme güvenilirliğini
bozuyor.

KÖK NEDEN (doğrulandı)
-----------------------
src/models.py, ActiveTrade dataclass (~satır 475-525):
  exit_timestamp: int = 0   ← VAR
  entry_timestamp           ← YOK, alan hiç tanımlı değil

src/bot.py, ActiveTrade(...) oluşturma bloğu (~satır 986-1017):
  entry_price, entry_bar_index, entry_order_id, entry_actual_price hepsi
  yazılıyor — zaman damgası hiçbir yerde set edilmiyor.

src/snapshot/snapshot.py:capture_snapshot() (~satır 256):
  entry_ts_ms = trade.get("timestamp") or trade.get("entry_timestamp", 0)
  Alan hiç olmadığı için bu her zaman 0 → _find_bar() ts_ms=None ile
  çağrılıyor → sadece FİYAT bazlı arama (ilk eşleşen low<=price<=high barı)
  yapıyor → o fiyat seviyesi grafikte daha önce başka bir yerde de varsa
  YANLIŞ (çok daha erken) bir bar seçilebiliyor.

Kanıt: ONDOUSDT trade'i — gerçek giriş 08-08 22:15:01 (testnet emir kaydı,
orderId 347397051), ama snapshot entryBar'ı 08-07 11:00'a (36 saat erken)
denk düşen bir bar'ı seçmiş, çünkü fiyat (0.352) o barda da varmış.

recovery_manager.py'deki 3 ActiveTrade(...) oluşturma noktası (~satır 159,
577, 605) da aynı eksikliği taşıyor — restore edilen trade'ler için de
entry_timestamp hiç set edilmiyor.

İSTENEN FIX (3 parça)
----------------------
A) models.py — ActiveTrade dataclass'ına yeni alan ekle:
     entry_timestamp: int = 0
   exit_timestamp'in hemen yanına, aynı stilde.

B) bot.py — ActiveTrade(...) oluşturma bloğuna (~satır 986) ekle:
     entry_timestamp=int(time.time() * 1000),
   (time modülü zaten import edilmiş olmalı, kontrol et; değilse ekle).
   Bu, fill onaylandıktan hemen sonraki an olduğu için yeterince doğru —
   entry_bar_index zaten current bar'a göre ayarlanıyor, tutarlı olur.
   Eğer entry fill event'inde (entry_manager.py, MARKET fill logunda
   görülen "gecikmeli fill tespit" civarı) daha kesin bir borsa zaman
   damgası mevcutsa onu tercih et, yoksa yukarıdaki yeterli.

C) recovery_manager.py — 3 ActiveTrade(...) noktasının (159, 577, 605)
   her birine entry_timestamp ekle. Restore sırasında gerçek fill zamanı
   borsadan (get_all_orders / recovered order response) çekilebiliyorsa
   onu kullan; yoksa restore anının zamanını yaz (0'dan iyi, ama
   yaklaşık olduğunu unutma — bir yorum satırıyla belirt).

   Ayrıca kontrol et: ActiveTrade → dict dönüşümü (dataclasses.asdict veya
   ActiveTrade'in kendi dict-uyumluluk mekanizması) entry_timestamp'i
   olduğu gibi taşıyor mu — normalize_trade() (snapshot.py:148) sadece
   `dict(trade)` yapıp geçiyor, yeni alan otomatik gelecek, ekstra alias
   eklemene gerek yok, ama trades_history.jsonl'a yazan serileştirme
   noktasını da (varsa ayrı bir writer) kontrol et, orada da alan adı
   "entry_timestamp" olarak kalsın.

TEST
----
- ActiveTrade oluşturulduğunda entry_timestamp'in 0'dan farklı ve makul
  (şu anki zamana yakın) olduğunu doğrulayan test.
- snapshot.py: entry_timestamp dolu bir trade verildiğinde _find_bar'ın
  fiyat fallback'ine DÜŞMEDİĞİNİ, doğrudan zaman eşleşmesiyle doğru barı
  bulduğunu doğrulayan test — ONDOUSDT senaryosunu (aynı fiyatın iki farklı
  zamanda göründüğü durum) simüle et.
- Mevcut testleri çalıştır, regresyon kontrolü yap.

DEPLOY
------
Risk düşük — yeni alan eklemek geriye dönük uyumlu (default 0), mevcut
davranışı bozmaz. Test edip push etmen yeterli. Not: bu fix SADECE
YENİ açılacak trade'leri düzeltir — geçmiş/açık trade kayıtlarında hâlâ
entry_timestamp olmayacak, o yüzden ONDOUSDT gibi geçmiş trade'lerin
snapshot'ları hâlâ yanlış bar gösterecek (retroaktif düzeltme istenmedi).
