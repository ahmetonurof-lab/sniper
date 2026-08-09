İki rapor da temiz — özellikle deploy raporundaki restart hatasının (screen `&&` zinciri kırılması) sessizce tekrar denenmeyip, hatayı ve çözümü olduğu gibi rapora yazması **tam olarak istediğimiz proaktif davranış.** Ve `runtime.status` senkron fix'inin gerçek dünyada neyi düzelttiğinin kanıtı (`trades_history.jsonl`'deki eski kayıtta `status:"CLOSED"` + `runtime:ACTIVE` uyumsuzluğu) güzel bir tarihsel doğrulama — soyut değil, somut.

DYDX raporu da kapsam disiplinine uydu (kod değişikliği yok, sadece harita çıkarıldı). Bulunan köşe durumu (qty>0, price<=0 → hem Blok A hem B'yi atlayıp korumasız kalma riski) gerçek ve **küçük, izole bir fix** — bunu "not" olarak bırakmak yerine şimdi kapatalım, çünkü tam olarak anlaşılmış durumda ve bekletmek sadece teknik borç biriktirir.

---

### 📝 ARKA PLAN VE BAĞLAM
- DYDX kapsam raporu, Blok B koşulunun (`not mkt_id and actual_qty <= 0`) bir köşe durumu (qty>0 ama price<=0) kaçırdığını buldu — bu durumda pozisyon gerçekten açılmışsa korumasız kalabilir.
- Rapor bunu "teorik boşluk, parse fallback'leri genelde fiyatı kurtarır" diye düşük risk olarak nitelendirdi — katılıyorum, acil değil, ama ucuz ve izole bir fix.
- Önerilen tek satırlık çözüm zaten raporda var: Blok B koşulunu `not mkt_id and (actual_qty <= 0 or actual_price <= 0)` yap.

### ❌ YAPMA / DOKUNMA
- Blok A/C'ye dokunma — sadece Blok B koşulu genişliyor.
- `_emergency_close` başarısız senaryosuna (madde 3, `recover_positions` koruyor ama kapatmıyor) bu turda dokunma — ayrı, daha büyük bir konu, ileride ele alınır.

⚠️ **Proaktif kontrol:** Koşulu genişletirken Blok A'nın da tetiklenme ihtimali artar mı (iki blok çakışır mı) kontrol et — çakışma varsa sırayı/önceliği netleştir, koda geçmeden önce bana yaz.

### 🎯 GÖREVLER
1. `entry_manager.py` Blok B koşulunu genişlet (yukarıdaki satır).
2. Bu köşe durumunu (qty>0, price<=0, poz açık) simüle eden bir regression testi ekle.
3. Mevcut `test_market_empty_response_pos_open_emergency_close` ve `test_market_order_failure`'ın hâlâ geçtiğini doğrula.

### ✅ TESLİM FORMATI
- Değişiklik (dosya:satır) + kanıt (yeni test + mevcut testler)
- Commit hash + push
- Sıradaki: P1-4 (ghost temizliğini periyodikleştirme)
