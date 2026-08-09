📝 ARKA PLAN VE BAĞLAM
TestExitStateTransitions'ın 3 fail'i araştırıldı; kök neden runtime.status (nested) ile flat ActiveTrade.status'un iki ayrı kopya olup senkron olmaması — tanıdık "state iki yerde tutuluyor" ailesinin yeni örneği.
Ajan kapsam raporunda 15 yazım noktasının tamamının __setitem__'ten geçtiğini kanıtladı — bu yüzden tek nokta düzeltmesi (yalnızca __setitem__'e senkron eklemek) yeterli, dağınık patch gerekmiyor.
Yan etki analizi net: state_writer.py etkilenmiyor (zaten flat'ten türetiyor), "" durumu güvenli, TradeStatus çevrimi uyumlu — reddedilecek bir alternatif yok, plan olduğu gibi onaylandı.
❌ YAPMA / DOKUNMA
except ValueError: pass'i sessiz bırakma — en azından log.debug ile iz bırak (bugünkü bot.py:983 deneyiminden ders: sessiz except'ler ileride debug'ı zorlaştırıyor).
state_writer.py'ye veya order_manager._sync_runtime_protection'a dokunma — kapsam dışı, rapor zaten etkilenmediklerini doğruladı.

⚠️ Proaktif kontrol: Bu yaklaşımda gözden kaçan bir test hatası, repo kısıtlaması veya daha önce kapattığımız bir bug'ın nüksü görüyorsan, koda dokunmadan önce beni uyar.

🎯 GÖREVLER
src/models.py — __setitem__'e status→runtime senkronu ekle (raporda önerilen 6 satır, pass yerine log.debug).
tests/test_integration_lifecycle.py — _trade() fixture'ına tick_size=0.001 ekle (CRITICAL log kirliliği temizliği).
Doğrula: TestExitStateTransitions → 3 passed; tüm dosya → 12 passed/0 failed; diğer suite'ler baseline ile birebir.
✅ TESLİM FORMATI
Kök neden / değişiklik (dosya:satır)
Kanıt (test sonucu, pre-existing fail sayısı değişmedi mi)
Commit hash + push durumu
Açık kalan/sıradaki iz
