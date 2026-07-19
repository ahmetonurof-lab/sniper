İşte baş mühendise gönderilecek rapor:

---

## 🔴 TRAILING SİSTEMİ KRİTİK ANALİZ RAPORU

**Konu:** Canlı trading'de trailing emir kaybı ve pozisyon korumasız kalması
**Rapor Tarihi:** 19 Temmuz 2026
**Durum:** Kritik — Üretimde aktif

---

### 1. SORUNUN ÖZETİ

Canlı trading'de aşağıdaki belirtiler gözlemlenmektedir:

```
[12:29:00] [APTUSDT] 🟥 EXIT: TRAIL_CLOSE | TRAIL: 0
[12:29:04] [APTUSDT] 🚨 CRITICAL: APTUSDT kapanmadi!
[12:29:30] [LDOUSDT] 🟥 EXIT: WS_FALLBACK | TRAIL: 0
[12:30:14] [APTUSDT] 🟥 EXIT: TRAIL_CLOSE | TRAIL: 0
```

**Temel belirtiler:**
- Trailing count hep 0 — trailing mekanizması hiç ilerleyememiş
- TRAIL_CLOSE emri verilmesine rağmen pozisyon kapanmıyor ("kapanmadı")
- WS_FALLBACK tetikleniyor — emir ID eşleşmesi kaybolmuş
- Aynı pozisyon tekrar tekrar exit almaya devam ediyor (zombie trade)

---

### 2. TESPİT EDİLEN KRİTİK HATALAR

#### HATA-1: Trailing Count Hiç Güncellenmemesi
| | |
|---|---|
| **Dosya** | `src/trading/order_manager.py` |
| **Fonksiyon** | `update_trail_orders()` |
| **Satır** | 156-167 |
| **Durum** | Hem SL hem TP placement başarısız olduğunda `trailing_count` güncellenmiyor. `trade["trailing_count"] = new_trail_count` satırı sadece `sl_ok or tp_ok` koşulunda çalışıyor. Her ikisi de başarısızsa `return False` ile çıkılıyor ve sayaç hep 0 kalıyor. |

#### HATA-2: "Kapanmadı" Yolu Pozisyonu Korumasız Bırakıyor
| | |
|---|---|
| **Dosya** | `src/bot.py` |
| **Fonksiyon** | `_exit_trade()` |
| **Satır** | 890-914 |
| **Durum** | Market close 5 denemede başarısız olursa trade `sl_order_id=""`, `tp_order_id=""` ile `active_trades`'e geri konuyor. Ama `cancel_all_open_orders` (satır 804) zaten tüm koruyucu emirleri iptal etmiş. Pozisyon açık, koruma yok, bot devam ediyor. |

#### HATA-3: WS_FALLBACK ID Eşleşmesi Kaybı
| | |
|---|---|
| **Dosya** | `src/trading/user_data_handler.py` |
| **Fonksiyon** | `_handle_order_update()` |
| **Satır** | 78-84, 106-119 |
| **Durum** | WS'den gelen fill ID'si hiçbir bilinen ID ile eşleşmiyor (current, prev, history). Kısmi trailing başarısızlığı sonrası ID listesi kirleniyor. Eşleşmeyen reduceOnly fill → WS_FALLBACK → bazen pozisyon açık kalıyor. |

> **Düzeltme (19 Temmuz 2026 — Canlı Doğrulama):**
> Raporun tarif ettiği ID eşleşmesi aslında sanıldığından daha güçlüdür: `s_id`/`t_id`/`s_id_prev`/`t_id_prev` + her iki taraf için 5'lik history listesi kontrol ediliyor. Ayrıca `bot.py::_exit_trade` başında raporun bilmediği bir **WS_FALLBACK guard** mekanizması mevcuttur: `position_still_open()` REST sorgusu yapılarak pozisyon hâlâ açıksa `verify_protection` + `repair_protection` çağrılıyor, trade pop edilmiyor. Yani "bazen pozisyon açık kalıyor" senaryosu **kısmen zaten ele alınmış**.
>
> **Ancak** `user_data_handler.py`'de gerçek bir sızıntı tespit edilmiştir: eşleşmeyen reduceOnly fill geldiğinde, `_exit_trade` çağrılmadan önce `trade["exit_price"]`, `exit_actual_price`, `exit_actual_qty`, `exit_order_id`, `exit_timestamp` doğrudan trade objesi üzerinde **mutasyona uğratılıyor**. Guard sonradan "pozisyon hâlâ açık, exit iptal" deyip `result=None` yapsa bile, bu **5 alan geri alınmıyor** — trade aktif kalmaya devam ederken **kirli/hayalet fill verisiyle** dolaşıyor. Sonraki gerçek exit'te üzerine yazılacağı için genelde zararsız, ama arada state okunan her yer (dashboard, `write_state`) o pencerede **yanlış fiyat gösterebilir**.

#### HATA-4: Verify_protection Boş ID'leri "Mevcut" Sayıyor
| | |
|---|---|
| **Dosya** | `src/trading/order_manager.py` |
| **Fonksiyon** | `verify_protection()` |
| **Satır** | 193 |
| **Durum** | `sl_present = (not s_id) or (s_id in open_ids)` — boş string `sl_order_id` "mevcut" kabul ediliyor. Emir kaybolduğunda bile koruma varmış gibi görünüyor. |

#### HATA-5: Orphan Sweep Yarışı
| | |
|---|---|
| **Dosya** | `src/bot.py` + `src/trading/recovery_manager.py` |
| **Fonksiyon** | `_on_1m_close()` + `reconcile_orphan_orders()` |
| **Satır** | bot.py:403-405, recovery_manager.py:478-515 |
| **Durum** | Her 5. 1m bar'da çalışan orphan sweep, `active_trades`'deki ID'lere göre bilinmeyen emirleri iptal ediyor. Trailing update sırasında yeni emir yerleştirilip henüz state güncellenmeden orphan sweep çalışırsa, yeni emir iptal edilebilir. |

---

### 3. TESPİT EDİLEN BELİRSİZLİKLER

| # | Belirsizlik | Etki |
|---|---|---|
| 1 | `_exit_trade` 5 deneme sonrası pozisyonu geri koyuyor ama **yeni koruyucu emir yerleştirmiyor**. Bunu yapan `repair_protection()` var ama "kapanmadı" yolunda çağrılmıyor. | Pozisyon korumasız kalıyor |
| 2 | `cancel_all_open_orders` market close'dan **önce** çalışıyor (satır 804 vs 811). Emirler iptal edildikten sonra market close başarısız olursa, SL/TP giden pozisyon kalıyor. | Sıralama hatası |
| 3 | `update_trail_orders` içinde `place_tp_order` await'i sırasında event loop WS mesajlarını işleyebiliyor. Eski SL bu pencerede dolarsa, trade reference'ı üzerinden tutarsız yazma yaşanabilir. | Race condition |
| 4 | `extract_order_id({})` boş string dönüyor ama hiçbir yer bu durumu kontrol etmiyor. Boş ID ile devam ediliyor. | sessiz hata |
| 5 | Recovery (`recover_positions`) SL yerleştiremezse market close deniyor, o da başarısız olursa trade boş ID'lerle kaydediliyor. | Zombie trade |

---

### 4. TESPİT EDİLEN DAVRANIŞ ANOMALİLERİ

| # | Davranış | Açıklama |
|---|---|---|
| 1 | **Zombie trade döngüsü** | "Kapanmadı" sonrası trade boş ID'lerle geri konuyor, bot hala onu yönetmeye çalışıyor ama Binance'te emir yok. Fiyat ters giderse kayıp büyüyor. |
| 2 | **TRAIL_CLOSE çelişkisi** | Bot TRAIL_CLOSE veriyor ama pozisyon kapanmıyor. Piyasa fiyatıyla exit hesaplıyor ama gerçek fill farklı olabiliyor. |
| 3 | **Trail count asla artmaması** | API reddettiğinde sayaç 0 kalıyor, trailing mekanizması aynı döngüye sıkışıyor. |
| 4 | **WS_FALLBACK zincirleme** | Bir WS_FALLBACK sonrası result=None yapılıp çıkılıyor, ama bir sonraki bar'da aynı sorun tekrar tetikleniyor. |

---

### 5. cbdr_risk_matrix VE risk_pts İLİŞKİSİ

**cbdr_risk_matrix** mevcut emirleri **etkilemez**. Sadece yeni girişlerin pozisyon büyüklüğünü belirler:
- `cbdr_mult=0.0` → yeni giriş engellenir
- `cbdr_mult=0.5-1.5` → risk_pct çarpılır

**risk_pts** trailing'i dolaylı etkiler:
- `TRAIL_MIN_MOVE_MULT=0.2` ile minimum hareket eşiğini belirler
- Ama emir silemez veya iptal edemez

**Sonuç:** Bu iki mekanizma trailing sorunlarının kök nedeni değil, dolaylı faktörler.

---

### 6. DOSYA LİSTESİ

| Dosya | İlgili Fonksiyonlar |
|---|---|
| `src/bot.py` | `_exit_trade()`, `_on_1m_close()`, `_on_15m_close()` |
| `src/trading/order_manager.py` | `update_trail_orders()`, `verify_protection()`, `repair_protection()`, `cleanup_on_exit()`, `cancel_all_open_orders()` |
| `src/trading/user_data_handler.py` | `_handle_order_update()`, WS_FALLBACK mantığı |
| `src/trading/trailing_manager.py` | `evaluate_trail()` |
| `src/trading/recovery_manager.py` | `reconcile_orphan_orders()`, `recover_positions()` |
| `src/models.py` | `ActiveTrade` dataclass, `PendingLock` |
| `src/config.py` | `CBDR_RISK_MATRIX`, risk sabitleri |

---

### 7. İSTENEN EYLEM

Bu rapor **aspirin tedavisi** ile geçiştirilemeyecek kadar kritik sorunlar içermektedir. Şimdiye kadar yapılan düzeltmeler (emir iptal sıralaması, WS eşleştirme, bar arama yönü) semptomları hafifletmiş ama kök nedenleri çözmemiştir.

**Baş mühendisten beklenen:**

1. Yukarıdaki dosya ve fonksiyonları **bağımsız olarak** incelemesi
2. Kendi metodolojisiyle **derin kök neden analizi** yapması
3. **Yeni tespitler** varsa raporlaması
4. Özellikle aşağıdaki sorulara cevap bulması:
   - "Kapanmadı" sonrası neden `repair_protection()` çağrılmıyor?
   - `cancel_all_open_orders` neden market close'dan önce çalışıyor?
   - Boş `sl_order_id` neden "mevcut" sayılıyor?
   - Zombie trade döngüsü nasıl kırılabilir?

5. Diğer baş mühendislerden de **bağımsız araştırma** istenecektir
6. Çıkan tabloya göre **ortak eylem planı** oluşturulacaktır

---

**Not:** Bu bulgular canlı production verisine dayanmaktadır. Backtest ile test edilemez — bu sorunlar sadece canlı trading'in dinamik sonuçlarıdır. Bot ile Binance arasındaki state senkronizasyon kopuşu kök sebeptir.
