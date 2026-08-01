# SNIPER BOT — Kapsamlı Fix Planı ve Agent Çalıştırma Direktifi

**Baz commit:** `03e6eaf8ed0ec88b5a1ff714c853cf8875587827` (repo HEAD'i)
**Doğrulama yöntemi:** Repo doğrudan clone edilip bu commit'e checkout edildi, her madde
gerçek dosya içeriğiyle tek tek karşılaştırıldı. Aşağıdaki satır numaraları, kod
parçaları ve çağrı-zinciri analizleri bu doğrulamadan geliyor — rapordaki veya önceki
plandaki satır numaraları DEĞİL.

**Bu belgenin iki amacı var:**
1. Görevi yürütecek agent'a (Claude Code / başka bir coding agent) verilecek çalıştırma
   kuralları (Bölüm A).
2. Her bug için doğrulanmış, uygulanabilir patch planı (Bölüm B).

---

# BÖLÜM A — AGENT DİREKTİFİ

Bu bölümü agent'a olduğu gibi (sistem talimatı veya görev talimatı olarak) ver.

```
SEN BİR PATCH UYGULAMA AGENT'ISIN. Aşağıdaki kurallar bağlayıcıdır, esneklik yok.

1. SATIR NUMARALARINA GÜVENME.
   Bu belgedeki satır numaraları yalnızca ORYANTASYON amaçlıdır. Her patch'ten hemen
   önce ilgili dosyayı `git show HEAD:<dosya>` veya doğrudan okuyarak tekrar aç, bu
   belgede verilen "ANCHOR" kod bloğunun dosyada GERÇEKTEN ve TAM OLARAK (karakter
   karakter) var olduğunu doğrula. Anchor eşleşmiyorsa (satır kaymışsa, kod
   değişmişse, fonksiyon başka bir yere taşınmışsa) DUR ve durumu bildir — kendi
   tahminine göre "en yakın benzer yeri" düzenleme. Bu proje zaten satır numarası
   güvenilmezliği yüzünden bir kez hataya açık plan üretmiş durumda; aynı hatayı tekrar
   etme.

2. HER MADDE = AYRI COMMIT.
   Bölüm B'deki her fix madde numarası kendi başına bir commit olmalı. Birden fazla
   maddeyi tek commit'te birleştirme, aksi bu belgede açıkça belirtilmedikçe.

3. HER COMMIT'TEN SONRA İLGİLİ TESTLER ZORUNLU.
   Her maddenin yanında "Test gereksinimi" belirtilmiş. O testler (veya eşdeğerleri)
   yeşil olmadan bir sonraki maddeye GEÇME. Test yoksa/yazılmamışsa, o maddeyi
   uygulamadan önce testi yaz. Kırmızı test → commit'i geri al (revert), agent kendi
   başına "muhtemelen sorun değildir" diyerek devam ETMEZ.

4. KAPSAM DIŞINA ÇIKMA.
   Sadece Bölüm B'de listelenen dosya/fonksiyonları değiştir. Refactor, isim
   değiştirme, "madem buradayım" tarzı ek temizlik YAPMA — her ek değişiklik ayrı bir
   inceleme gerektirir ve blast radius'u büyütür.

5. AÇIK KARAR NOKTALARINDA DURUP SOR.
   Bölüm B'de "🔶 KARAR GEREKİYOR" etiketli maddeler var. Bunlarda birden fazla makul
   çözüm var ve hangisinin seçileceği iş mantığına bağlı. Agent bunları kendi başına
   seçip uygulamaz — seçenekleri insana sunup onay bekler.

6. RETURN DEĞERİ / ÇAĞRI ZİNCİRİ DEĞİŞİKLİKLERİNDE ÖZEL DİKKAT.
   Bir fonksiyonun dönüş değerinin `success`/`error` gibi alanlarını değiştirirken,
   o fonksiyonu çağıran HER YERİ bul (`grep -rn "fonksiyon_adı("`) ve her çağıranın bu
   değeri nasıl kullandığını oku. "Bug'ı düzelttim" dediğin an, o dönüş değerine bağlı
   başka bir katmanı bozmuş olabilirsin (bkz. Madde 1 — BUG-1 tam olarak bu tuzağı
   içeriyordu, bu belgede çözülmüş haliyle var).

7. TEK KAYNAK, ÇOKLU KULLANIM YERİ VARSA HEPSİNİ BUL.
   "X alanını Y dataclass'ına ekle" dendiğinde, o dataclass'ın TÜM constructor
   çağrılarını (`grep -rn "ClassName("`) bul ve hepsinde yeni alanı doldur. Sadece
   "ana" çağrı yerini güncelleyip diğerlerini (örn. recovery/restart path'i) atlama —
   bu tam olarak BUG-5 ve BUG-12'nin kök nedeni (bazı path'ler güncellenmiş, bazıları
   unutulmuş).

8. HER MADDE İÇİN "TAMAMLANDI" TANIMI:
   a) Anchor doğrulandı, patch uygulandı.
   b) İlgili testler yazıldı/güncellendi ve yeşil.
   c) Değişen fonksiyonun TÜM çağrı yerleri (call site) tarandı, hiçbiri kontratı
      bozmuyor.
   d) Commit mesajı bug ID'sini içeriyor (örn. "fix(BUG-1): ...").
   e) Bu belgedeki ilgili maddenin "Definition of Done" alt maddeleri karşılandı.

9. RAPORLA.
   Her madde sonunda: hangi dosyalar değişti, hangi testler koşuldu ve sonucu, ve
   varsa call-site taramasında bulduğun ek bulgular — kısa bir özet olarak bildir.
   Sessizce "yaptım" deyip geçme.
```

---

# BÖLÜM B — AÇIK KARAR NOKTALARI (Faz 0 — kodlamadan önce)

Aşağıdaki üç konu, kod yazmaya başlamadan önce netleştirilmeli. Agent bunları kendi
başına seçmemeli.

### K1 — `cbdr_day_key` kanonik yönü (BUG-5 ile ilgili)

`state_manager._today()` ve `session.py`'nin `cbdr_key`'i aynı 22:00 başlangıçlı
trading gününe **sistematik olarak 1 gün farklı** etiket veriyor (bu bir sınır-anı
hatası değil, günün tamamında geçerli sabit bir kayma — detay Madde 6'da).

- **Seçenek A:** `session.py` konvansiyonuna geç (etiket = döngünün BAŞLADIĞI takvim
  günü). Etkisi: `state_manager`'ın disk'e yazdığı `date` alanının formatı değişir →
  restart sonrası eski state dosyaları yanlış yorumlanabilir, **bir kerelik state
  dosyası migrasyonu veya en kötü ihtimalle "ilk restart'ta trade sayacı sıfırlanır"
  riski var**.
- **Seçenek B:** `state_manager` konvansiyonuna geç (etiket = döngünün BİTTİĞİ /
  çoğu saatin düştüğü takvim günü). Etkisi: `session.py`'nin in-memory `cbdr_key`'i
  değişir; bu veri restart'ta zaten sıfırlanıyor (kalıcı değil), migrasyon riski yok.

**Öneri:** Seçenek B — çünkü `session.py` tarafı ephemeral (kalıcı olmayan), risk
daha düşük. Ama nihai karar iş sahibine ait; deploy öncesi mevcut `*.json` state
dosyalarının `date` alanlarının nasıl davranacağı test edilmeli.

### K2 — BUG-12 idempotency key: pragmatik mi, tam mı?

Planın önerdiği `entry_timestamp` alanı **`ActiveTrade` dataclass'ında mevcut değil**
(kontrol ettim — `models.py:457-530` arası alan listesinde yok). `trade.get(
"entry_timestamp", 0)` her zaman `0` döner çünkü `.get()` `getattr(self, key,
default)` yapıyor ve alan yoksa hep default'a düşüyor — yani **planın orijinal
önerisi çakışmayı hiç çözmüyor, sessizce no-op kalıyor.**

- **Seçenek A (pragmatik, tek dosya):** `entry_order_id` alanını kullan (gerçekten
  var, `bot.py:972`'de borsa order ID'siyle dolduruluyor, canlı modda garantili
  benzersiz). Paper/backtest modda `entry_order_id` boş string kalıyor (`entry_manager.py:339-349`
  — paper path `order_id` set etmiyor), yani **paper modda çakışma riski hâlâ var**,
  ama canlı işlemler için sorunu çözer. Sadece `exit_lifecycle.py` değişir.
- **Seçenek B (tam çözüm, çok dosya):** `ActiveTrade`'e yeni bir `entry_seq: str`
  (uuid4 veya nanosaniye) alanı ekle, bunu HER `ActiveTrade(...)` çağrı yerinde
  doldur (`bot.py:943`, `recovery_manager.py:145/516/539`, `models.py:574/584`),
  sonra `exit_lifecycle.py`'de kullan. Paper modda da çalışır. Blast radius büyük,
  ekstra regresyon testi ister.

**Öneri:** Seçenek A'yı Faz 1'de acil fix olarak uygula (canlı işlemler için gerçek
sorunu çözüyor), Seçenek B'yi ayrı bir ticket olarak Faz 3'e ertele.

### K3 — BUG-14 (legacy handler timestamp migrasyonu) bu turun kapsamında mı?

Madde 4'ün ikinci yarısı ("legacy handler'daki `time.time()` timestamp'lerini de
taşı") `user_data_handler.py` içinde en az 8 farklı satırı etkiliyor (221, 288, 309,
446, 448, 501, 506, 519, 523) ve bunlar `_exit_trade` çağrılarının içine gömülü,
tek satırlık bir grep-replace değil. **Öneri:** bu turda SADECE `normalize_order_event`
(Madde 4'ün ilk yarısı) yapılsın, legacy handler migrasyonu ayrı bir madde/ticket
olarak planlansın — WS_EVENT_NORMALIZATION_ENABLED açıkken legacy path zaten devre
dışı olabilir, önce bunu doğrulamak gerekiyor.

---

# BÖLÜM B — UYGULAMA SIRASI (düzeltilmiş, gerekçeli)

Orijinal planın sırası: *BUG-1, 25, 5, 8, 21 → BUG-10, 12, 23 → trailing/temizlik.*
Rapor kendi sırasında BUG-12'yi 3. sıraya koymuştu. Aşağıda ikisini birleştirip,
call-site analizinde bulduğum karmaşıklığa göre yeniden sıraladım:

| Sıra | Madde | Gerekçe |
|---|---|---|
| 0 | K1, K2, K3 kararları | Kod yazmadan önce netleşmeli |
| 1 | **BUG-25** (risk_manager) | Tek dosya, düşük blast radius, devre kesici güvenliği |
| 2 | **BUG-23** (session_router) | Tek dosya, 3 satır, fail-closed güvenlik |
| 3 | **BUG-1** (emergency close) | Kritik ama artık biliyoruz ki 4 call-site'ı var — hepsi tek commit'te |
| 4 | **BUG-12** (idempotency, K2-A ile) | Trade kaybı riski; tek dosya, K2 kararına bağlı |
| 5 | **BUG-8** (ts_ms clock skew) | Tek fonksiyon, düşük risk, iyi lokalize |
| 6 | **BUG-5** (cbdr_day_key, K1 ile) | K1 kararına bağlı, iki dosya |
| 7 | **BUG-21** (order_qty precision) | Tek dosya |
| 8 | **BUG-10** (_bump_to_min_notional Decimal) | Tek dosya, düşük olasılık ama tek satır değil |
| 9 | **BUG-11** (exit_lifecycle dedupe) | Tek dosya, dikkatli konsolidasyon gerekiyor (aşağıda) |
| 10 | **BUG-3** (trailing sl/tp key) | Şu an latent (aktif risk değil), hijyen fix + regression test |
| 11 | **BUG-17** (CircuitBreaker.is_open lock) | Düşük risk, async refactor gerektiriyor |
| 12 | **BUG-16** (isinstance dead code) | Kozmetik |

**Değişiklik gerekçesi:** BUG-1'i 3. sıraya çektim çünkü call-site analizinde 4 ayrı
`return await self._emergency_close(...)` noktası bulundu ve bunların hepsinin aynı
commit'te tutarlı şekilde değişmesi gerekiyor (aşağıda detay) — bu, "1 satır" değil
"1 fonksiyon + 4 çağrı yeri" büyüklüğünde bir değişiklik, bu yüzden erken ve tek
başına yapılmalı.

---

# BÖLÜM B — MADDE MADDE FIX PLANI

## 1. BUG-1 + BUG-7 — `_emergency_close` yanlış `success` + belirsiz `side`

**Dosya:** `src/trading/entry_manager.py`
**Gerçek konum:** fonksiyon tanımı satır 276, hatalı `return` satır 318–321.

**🔎 Call-site bulgusu (rapor ve önceki planda YOK):** `_emergency_close` SADECE
`execute_live_entry` içinden, 4 farklı noktadan (`satır 410, 500, 526, 647`),
her seferinde `return await self._emergency_close(...)` şeklinde çağrılıyor — yani
`_emergency_close`'un dönüş değeri doğrudan `execute_live_entry`'nin KENDİ dönüş
değeri oluyor. `execute_live_entry`'nin çağıranı (`bot.py:781-802`) `success=False`
gördüğünde temiz bir şekilde `rsm.reset()` + log + `return` yapıyor — yani **mevcut
davranış aslında `bot.py` tarafında güvenli çalışıyor** (yanlış ama zararsız bir
şekilde "entry başarısız" olarak işleniyor).

**⚠️ Bu yüzden raporun önerdiği naif fix (`return EntryExecutionResult(success=True,
...)`) TEHLİKELİ:** `_emergency_close` başarıyla kapatma yaptığında `success=True`
dönerse, bu `execute_live_entry` üzerinden `bot.py:796`'daki `if not exec_result.success:`
kontrolünü ATLAR ve kod, POZİSYON AZ ÖNCE ACİL KAPATILMIŞKEN sanki geçerli bir entry
olmuş gibi devam etmeye çalışır (`sl_id`, `qty`, `actual_entry_price` okumaya
çalışır). `qty`/`actual_qty` default 0.0 kaldığı için `bot.py:815`'teki
`if qty <= 0 or actual_entry_price <= 0:` guard'ı şu an bunu yakalıyor — ama bu
kırılgan bir tesadüf, kasıtlı bir tasarım değil. **Doğru çözüm: `_emergency_close`'un
kendi `success` alanını doğru raporlaması AYRI bir konu; `execute_live_entry`'nin
kendi çağıranına verdiği sözü (success=True ⟺ pozisyon açık ve korumalı) bozmamak
AYRI bir konu. İkisini karıştırma.**

**ANCHOR (mevcut kod, satır 276–321):**
```python
    async def _emergency_close(
        self, sym: str, side: str, qty: float, reason: str
    ) -> EntryExecutionResult:
        opp_side = "SELL" if side.upper() == "BUY" else "BUY"
        side_label = "long" if side.upper() == "BUY" else "short"
        log.critical("[EMERGENCY] %s %s — acil kapatma baslatiliyor", sym, reason)
        pt_log(
            EventType.EMERGENCY_CLOSE_STARTED,
            sym,
            side_label,
            error={"code": 0, "message": reason, "retry_count": 0},
            reason=reason,
        )
        try:
            await self._rest.place_market_order(
                sym,
                opp_side,
                qty,
                reduce_only=True,
                client_order_id=f"emergency-{sym.lower()}-{int(time.time()*1000)}",
            )
            log.critical("[EMERGENCY] %s acil kapatma gonderildi", sym)
            pt_log(
                EventType.EMERGENCY_CLOSE_COMPLETED,
                sym,
                side_label,
                result="completed",
                reason="emergency_close_sent",
            )
        except Exception as e:
            log.critical("[EMERGENCY] %s acil kapatma BASARISIZ: %s", sym, e)
            pt_log(
                EventType.EMERGENCY_CLOSE_FAILED,
                sym,
                side_label,
                error={"code": -1, "message": str(e)[:200], "retry_count": 0},
                reason="emergency_close_failed",
            )
            return EntryExecutionResult(
                success=False,
                error=f"EMERGENCY CLOSE BASARISIZ — {e}",
            )
        return EntryExecutionResult(
            success=False,
            error=f"EMERGENCY CLOSE — {reason}",
        )
```

**YENİ KOD:**
```python
    async def _emergency_close(
        self, sym: str, mkt_side: str, qty: float, reason: str
    ) -> EntryExecutionResult:
        """Acil pozisyon kapatma. Dönüş `success`, KAPATMA isteminin borsaya
        başarıyla gönderilip gönderilmediğini belirtir — entry'nin başarılı
        olduğu anlamına GELMEZ. Çağıran taraf (execute_live_entry) bu değeri
        asla doğrudan kendi dönüş değeri olarak kullanmamalı; bkz. çağrı
        yerlerindeki wrapper.
        """
        if mkt_side.upper() not in ("BUY", "SELL"):
            raise ValueError(
                f"_emergency_close: mkt_side 'BUY' veya 'SELL' olmali, "
                f"gelen={mkt_side!r}"
            )
        opp_side = "SELL" if mkt_side.upper() == "BUY" else "BUY"
        side_label = "long" if mkt_side.upper() == "BUY" else "short"
        log.critical("[EMERGENCY] %s %s — acil kapatma baslatiliyor", sym, reason)
        pt_log(
            EventType.EMERGENCY_CLOSE_STARTED,
            sym,
            side_label,
            error={"code": 0, "message": reason, "retry_count": 0},
            reason=reason,
        )
        try:
            await self._rest.place_market_order(
                sym,
                opp_side,
                qty,
                reduce_only=True,
                client_order_id=f"emergency-{sym.lower()}-{int(time.time()*1000)}",
            )
            log.critical("[EMERGENCY] %s acil kapatma gonderildi", sym)
            pt_log(
                EventType.EMERGENCY_CLOSE_COMPLETED,
                sym,
                side_label,
                result="completed",
                reason="emergency_close_sent",
            )
        except Exception as e:
            log.critical("[EMERGENCY] %s acil kapatma BASARISIZ: %s", sym, e)
            pt_log(
                EventType.EMERGENCY_CLOSE_FAILED,
                sym,
                side_label,
                error={"code": -1, "message": str(e)[:200], "retry_count": 0},
                reason="emergency_close_failed",
            )
            return EntryExecutionResult(
                success=False,
                error=f"EMERGENCY CLOSE BASARISIZ — {e}",
            )
        return EntryExecutionResult(
            success=True,
            error="",
        )
```

**Sonra, 4 çağrı yerinin HER BİRİNDE** (satır ~410, ~500, ~526, ~647 civarı — anchor
ile bul), ham `return await self._emergency_close(...)` yerine şu kalıba geç:

```python
                        close_result = await self._emergency_close(
                            sym, mkt_side, order_qty, "SL FAIL code={err_code}"  # <- her yerin kendi reason string'i
                        )
                        close_note = (
                            "pozisyon guvenle kapatildi"
                            if close_result.success
                            else f"ACIL KAPATMA DA BASARISIZ — {close_result.error}"
                        )
                        return EntryExecutionResult(
                            success=False,
                            error=f"SL FAIL code={err_code} — {close_note}",
                        )
```

(Her çağrı yerinde `reason` string'i farklı — mevcut orijinal `reason` argümanını
koru, sadece dönüş sarmalamasını bu kalıba çevir.)

**Test gereksinimi:**
- `_emergency_close` başarılı kapatmada `success=True` döndüğünü doğrulayan birim testi.
- `execute_live_entry`'nin, `_emergency_close` tetiklenen HER senaryoda (mkt orderId
  yok+pozisyon açık / SL/TP calc fail / direction fail / SL reject) hâlâ
  `success=False` döndürdüğünü doğrulayan test (regresyon guard'ı — tam da bu
  belgenin işaret ettiği tuzağı yakalamak için).
- `bot.py`'nin bu senaryolarda trade KAYDETMEDİĞİNİ, `rsm.reset()` çağırdığını
  doğrulayan entegrasyon testi.
- `mkt_side="long"` gibi geçersiz bir değerle çağrıldığında `ValueError` fırlatıldığını
  doğrulayan test.

**Definition of Done:** 4 çağrı yerinin hepsi güncellendi, `execute_live_entry`
dönüş kontratı (success=False ⟺ entry başarısız, pozisyon yok) hiçbir senaryoda
bozulmadı, testler yeşil.

---

## 2. BUG-21 — `order_qty` precision tutarsızlığı

**Dosya:** `src/trading/entry_manager.py`
**Gerçek konum:** satır 482.

**ANCHOR:**
```python
        order_qty = actual_qty if actual_qty > 0 else valid_qty
```

**YENİ KOD:**
```python
        order_qty = (
            await self._rest.apply_amount_precision(sym, actual_qty)
            if actual_qty > 0
            else valid_qty
        )
        order_qty = await self._rest.validate_min_amount(sym, order_qty)
        if order_qty <= 0:
            # actual_qty precision sonrasi min altina dustu — SL/TP icin
            # valid_qty'ye geri don (zaten normalize edilmisti)
            order_qty = valid_qty
```

**Test gereksinimi:** Borsadan precision-uyumsuz bir `actual_qty` (ör. tick
sonrası `LOT_SIZE` sınırını az aşan bir değer) simüle eden bir birim testi;
SL/TP emirlerinin bu normalize edilmiş `order_qty` ile gönderildiğini doğrulayan
mock-based test.

---

## 3. BUG-10 — `_bump_to_min_notional` float precision

**Dosya:** `src/trading/entry_manager.py`
**Gerçek konum:** fonksiyon tanımı satır 726 (rapor/plan "250-275" diyordu — **~470
satır sapma**, bu belgedeki en büyük kaymalardan biri).

Agent bu fonksiyonu `grep -n "def _bump_to_min_notional" src/trading/entry_manager.py`
ile bulup içindeki `math.ceil(...)` satırını yerinde görsün, sonra rapordaki
`Decimal`/`ROUND_CEILING` dönüşümünü uygulasın:

```python
from decimal import Decimal, ROUND_CEILING
...
step_d = Decimal(str(step))
min_qty_d = Decimal(str(min_qty_n))
bumped = float((min_qty_d / step_d).to_integral_value(rounding=ROUND_CEILING) * step_d)
```

**Sonra bump sonrası doğrulama ekle:** `qty * price >= min_notional` VE step-size
uyumu tekrar kontrol edilmeli (planın orijinal önerisi doğru, ekliyorum: bu kontrolü
ayrı bir `assert`/log.warning olarak değil, fonksiyonun geri dönüş öncesi bir
guard olarak koy — sessizce yanlış qty ile devam etme).

**Test gereksinimi:** Rapordaki edge-case senaryosunu (`step=0.01, min_qty_n=1.235`)
ve en az 2 farklı sembol/step kombinasyonuyla property-based ya da parametrize test.

---

## 4. BUG-8 (+ BUG-2) — `normalize_order_event` ts_ms

**Dosya:** `src/trading/user_data_handler.py`
**Gerçek konum:** fonksiyon tanımı satır 47, hatalı satır 65.

**ANCHOR:**
```python
        ts_ms=int(time.time() * 1000),
```
(bu satır `normalize_order_event` fonksiyonu içinde, `NormalizedOrderEvent(...)`
çağrısının bir parçası — `raw=raw` parametresi zaten aynı çağrıda mevcut, yani
`raw` dict fonksiyona geliyor, ekstra parametre eklemeye gerek yok.)

**YENİ KOD:**
```python
        ts_ms=int(raw.get("E", time.time() * 1000)),
        received_ts_ms=int(time.time() * 1000),
```

**⚠️ Ön koşul:** `received_ts_ms` yeni bir alan — `NormalizedOrderEvent` dataclass'ında
(muhtemelen `models.py`) önce bu alanın tanımlı olduğunu doğrula, yoksa önce oraya
ekle (varsayılan `0` ile, geriye dönük uyumluluk için).

**K3 kararına göre:** legacy handler'daki `time.time()*1000` kullanımları
(satır 221, 288, 309, 446, 448, 501, 506, 519, 523) bu maddenin kapsamı DIŞINDA —
ayrı bir ticket.

**Test gereksinimi:** `raw["E"]` set edilmiş bir mock event ile `ts_ms`'in event
zamanını yansıttığını, `raw["E"]` eksikken sistem saatine fallback yaptığını
doğrulayan test. `exit_lifecycle.py`'deki 30 saniyelik stale-cooldown mantığının
event-time ile hâlâ doğru çalıştığını doğrulayan regresyon testi.

---

## 5. BUG-5 — `cbdr_day_key` ortak helper 🔶 (K1 kararına bağlı)

**Dosyalar:** `src/state_manager.py` (gerçek konum: `_today()` tanımı satır 36,
saat kontrolü satır 44–45) ve `src/session.py` (gerçek konum: `cbdr_key` hesabı
satır 376–382).

**Bulgu (rapordan daha ciddi):** Bu iki fonksiyon aynı 22:00-başlangıçlı trading
gününe **sürekli 1 gün farklı** etiket veriyor — sadece 22:00-23:59 sınırında değil,
günün TAMAMINDA. `state_manager._today()` döngüyü "biten günün tarihi" ile,
`session.py cbdr_key` ise "başlayan günün tarihi" ile etiketliyor. Detay için Bölüm
K1'e bak.

**K1 kararı netleşmeden bu madde kodlanmamalı.** Karar sonrası:

```python
# Yeni ortak fonksiyon — mesela src/day_key.py ya da state_manager.py içinde
def cbdr_day_key(dt: datetime, start_hour: int = 22, end_hour: int = 2) -> str:
    """K1 kararına göre kanonikleştirilmiş trading-günü etiketi."""
    h = dt.hour
    spans_midnight = start_hour > end_hour
    today = dt.strftime("%Y-%m-%d")
    if spans_midnight:
        # K1=A ise: h >= start_hour -> today ; else -> today
        # K1=B ise: h >= start_hour -> today+1 ; else -> today
        ...  # K1 kararına göre doldurulacak
    else:
        ...
    return ...
```

Hem `state_manager._today()` hem `session.py`'deki `cbdr_key` hesaplaması bu
fonksiyona delege etmeli — kopyala-yapıştır DEĞİL, gerçek import.

**Test gereksinimi (planın önerdiği sınırlar doğru, aynen kullan):** 22:00, 23:59,
00:00, 01:59, 02:00, 21:59 saatlerinde her iki modülün ürettiği key'in BİREBİR AYNI
olduğunu doğrulayan parametrize test. Ayrıca: mevcut bir `risk_state.json` /
trade-count state dosyasının eski format `date` alanıyla restart sonrası nasıl
davrandığını doğrulayan bir restart-recovery testi (K1 kararının migrasyon riskini
kapsıyor).

---

## 6. BUG-12 — idempotency key collision 🔶 (K2 kararına bağlı)

**Dosya:** `src/trading/exit_lifecycle.py`
**Gerçek konum:** idempotency key satır 148–150 (`_trade_id`), lock key satır
139–142 (`_trade_id_key`) — plan "75-95" diyordu, bu **tamamen yanlış bir bölge**
(sınıfın docstring'i + `__init__` imzası).

**ANCHOR (satır 139–151):**
```python
        _trade_id_key = (
            f"{trade.get('entry_bar_index', -1)}_{trade.get('entry_price', 0)}"
        )
        trade_key = f"{sym}_{_trade_id_key}"
        lock = self._exit_locks.setdefault(trade_key, asyncio.Lock())
        async with lock:
            # ── Idempotency guard (P0-1): entry_bar_index+entry_price bazli ──
            _exit_reason = trade.get("result", "")
            if _exit_reason:
                _trade_id = (
                    f"{trade.get('entry_bar_index', -1)}_{trade.get('entry_price', 0)}"
                )
```

**⚠️ K2 bulgusu:** Planın önerdiği `trade.get('entry_timestamp', 0)` eklemesi
**çalışmaz** — `ActiveTrade`'de böyle bir alan yok, her zaman `0` döner, çakışma
ÇÖZÜLMEZ. K2-A (önerilen, pragmatik) çözümü:

**YENİ KOD (K2-A):**
```python
        _uniq = trade.get("entry_order_id") or f"{trade.get('entry_bar_index', -1)}"
        _trade_id_key = (
            f"{trade.get('entry_bar_index', -1)}_{trade.get('entry_price', 0)}_{_uniq}"
        )
        trade_key = f"{sym}_{_trade_id_key}"
        lock = self._exit_locks.setdefault(trade_key, asyncio.Lock())
        async with lock:
            _exit_reason = trade.get("result", "")
            if _exit_reason:
                _trade_id = (
                    f"{trade.get('entry_bar_index', -1)}_{trade.get('entry_price', 0)}_{_uniq}"
                )
```

**Bilinen sınır (dokümante et, gizleme):** Paper/backtest modda `entry_order_id`
boş string kalıyor (`entry_manager.py:339-349` paper path order_id set etmiyor),
bu yüzden K2-A paper modda çakışma riskini TAM kapatmıyor. Bu bilinçli bir
trade-off — K2 bölümünde açıklandı. Canlı işlemler için sorun çözülüyor.

**Test gereksinimi:** Aynı `entry_bar_index` + aynı `entry_price` ama farklı
`entry_order_id`'ye sahip iki trade'in birbirini ENGELLEMEDİĞİNİ doğrulayan test
(orijinal collision senaryosu). Paper modda hâlâ teorik çakışma riski olduğunu
belgeleyen bir yorum/test (xfail veya açık uyarı olarak).

---

## 7. BUG-11 — `pending_exit_*` duplicate normalization

**Dosya:** `src/trading/exit_lifecycle.py`
**Gerçek konum:** blok 1 satır 308–321, blok 2 (yorum: "Patch Set 4 (WS
normalization)") satır 324–337. (Plan "120-155" diyordu — bu bölge aslında lock/
idempotency guard kısmı, pending_exit ile alakasız.)

**Analiz:** Blok 1 sadece `if position_open:` dalının İÇİNDE, hiçbir `return` ile
kesilmeden sona ulaşılırsa çalışıyor. Blok 2 bu `if position_open:` bloğunun
DIŞINDA, her zaman (position_open True ya da False fark etmeksizin) çalışıyor.
Yani `position_open=True` + hiçbir erken-return tetiklenmemiş senaryoda HER İKİ
blok da art arda çalışıyor — blok 1 alanları set edip pending_exit_* alanlarını
None yapıyor, blok 2 (zaten None olan alanlar üzerinde) neredeyse no-op oluyor.
`position_open=False` senaryosunda SADECE blok 2 çalışıyor. **Doğru konsolidasyon:
blok 1'i tamamen SİL, blok 2'yi TEK doğru yer olarak bırak ve blok 2'deki 4 alanın
HEPSİNİ (şu an sadece price/qty `is not None`, order_id/timestamp hâlâ truthy)
`is not None`'a çevir** — plan sadece price/qty'yi düzeltmeyi önermişti, order_id/
timestamp'teki aynı sınıf hatayı gözden kaçırmıştı.

**ANCHOR — SİLİNECEK (satır 308–321):**
```python
                if trade.get("pending_exit_price"):
                    trade["exit_price"] = trade["pending_exit_price"]
                    trade["exit_actual_price"] = trade["pending_exit_price"]
                if trade.get("pending_exit_qty"):
                    trade["exit_actual_qty"] = trade["pending_exit_qty"]
                if trade.get("pending_exit_order_id"):
                    trade["exit_order_id"] = trade["pending_exit_order_id"]
                if trade.get("pending_exit_timestamp"):
                    trade["exit_timestamp"] = trade["pending_exit_timestamp"]
                trade["pending_exit_reason"] = None
                trade["pending_exit_price"] = None
                trade["pending_exit_qty"] = None
                trade["pending_exit_order_id"] = None
                trade["pending_exit_timestamp"] = None
```

**ANCHOR — DÜZELTİLECEK (satır 324–337):**
```python
            # Patch Set 4 (WS normalization)
            if trade.get("pending_exit_price") is not None:
                trade["exit_price"] = trade["pending_exit_price"]
                trade["exit_actual_price"] = trade["pending_exit_price"]
            if trade.get("pending_exit_qty") is not None:
                trade["exit_actual_qty"] = trade["pending_exit_qty"]
            if trade.get("pending_exit_order_id"):
                trade["exit_order_id"] = trade["pending_exit_order_id"]
            if trade.get("pending_exit_timestamp"):
                trade["exit_timestamp"] = trade["pending_exit_timestamp"]
            trade["pending_exit_reason"] = None
            trade["pending_exit_price"] = None
            trade["pending_exit_qty"] = None
            trade["pending_exit_order_id"] = None
            trade["pending_exit_timestamp"] = None
```

**YENİ KOD (sadece bu blok kalıyor, order_id/timestamp de `is not None` oldu):**
```python
            # Patch Set 4 (WS normalization) — tek konsolide blok
            if trade.get("pending_exit_price") is not None:
                trade["exit_price"] = trade["pending_exit_price"]
                trade["exit_actual_price"] = trade["pending_exit_price"]
            if trade.get("pending_exit_qty") is not None:
                trade["exit_actual_qty"] = trade["pending_exit_qty"]
            if trade.get("pending_exit_order_id") is not None:
                trade["exit_order_id"] = trade["pending_exit_order_id"]
            if trade.get("pending_exit_timestamp") is not None:
                trade["exit_timestamp"] = trade["pending_exit_timestamp"]
            trade["pending_exit_reason"] = None
            trade["pending_exit_price"] = None
            trade["pending_exit_qty"] = None
            trade["pending_exit_order_id"] = None
            trade["pending_exit_timestamp"] = None
```

**Test gereksinimi:** `pending_exit_qty=0.0` (legit sıfır değil ama edge-case olarak)
ile bir trade'in artık doğru şekilde `exit_actual_qty`'ye kopyalandığını doğrulayan
test; `position_open=True` ve `position_open=False` her iki dalda da tek-blok
davranışının önceki iki-blok davranışıyla aynı sonucu ürettiğini doğrulayan
regresyon testi.

---

## 8. BUG-23 — `session_router.should_trade` fail-open

**Dosya:** `src/session_router.py`
**Gerçek konum:** satır 43–45 (plan "30-43" demiş, isabetli).

**ANCHOR:**
```python
    if cbdr_width_pct is not None:
        cbdr_mult = get_cbdr_multiplier(symbol, cbdr_width_pct)
        if cbdr_mult == 0.0:
            return (
                False,
                symbol
                + " CBDR="
                + f"{cbdr_width_pct:.2f}%"
                + " Zehirli Bolge (mult=0.0)",
            )
    return True, ""
```

**YENİ KOD:**
```python
    if cbdr_width_pct is None:
        return False, symbol + " CBDR olcumu yok — fail-closed"
    cbdr_mult = get_cbdr_multiplier(symbol, cbdr_width_pct)
    if cbdr_mult == 0.0:
        return (
            False,
            symbol
            + " CBDR="
            + f"{cbdr_width_pct:.2f}%"
            + " Zehirli Bolge (mult=0.0)",
        )
    return True, ""
```

**Test gereksinimi:** `cbdr_width_pct=None` ile çağrıldığında `False` döndüğünü
doğrulayan test — ayrıca bu fonksiyonu çağıran tarafın (muhtemelen `bot.py`) bu
yeni `False` sonucunu doğru işlediğini (trade açmadığını) doğrulayan entegrasyon
testi.

---

## 9. BUG-3 — trailing `stop_loss`/`take_profit` → `sl`/`tp`

**Dosya:** `src/trading/trailing_manager.py`
**Gerçek konum:** satır 313–316 (plan "168-169" demiş, ~145 satır kaymış).

**Not:** Call-site taramasında doğrulandı — canlı akışta (`bot.py:943` →
`bot.py:558`) `trade` her zaman `ActiveTrade` nesnesi, ve `ActiveTrade.__setitem__`
zaten `setattr` üzerinden `stop_loss`/`take_profit` property setter'larını tetikleyip
`self.sl`/`self.tp`'yi doğru güncelliyor. **Yani bu bug şu an aktif olarak
tetiklenmiyor** — sadece `trade` bir gün düz dict olursa patlar. Yine de düzeltilmeli
(hijyen + gelecek güvencesi).

**ANCHOR:**
```python
        if candidate.sl is not None:
            trade["stop_loss"] = float(candidate.sl)
        if candidate.tp is not None:
            trade["take_profit"] = float(candidate.tp)
```

**YENİ KOD:**
```python
        if candidate.sl is not None:
            trade["sl"] = float(candidate.sl)
        if candidate.tp is not None:
            trade["tp"] = float(candidate.tp)
```

**Ayrıca:** `_read_price(trade, "stop_loss", "sl")` çağrıları (satır 139–140, 347–348)
artık gereksiz fallback taşıyor ama zararsız — bu turda dokunma, ayrı bir temizlik
maddesi olabilir.

**Test gereksinimi:** Planın önerdiği gibi — hem `ActiveTrade` nesnesi hem düz
`dict` ile `orchestrate_trail()` çağıran iki ayrı regresyon testi; ikisinde de
sonrasında `order_manager.update_trail_orders()`'ın `trade.get("sl")` ile doğru
(güncel) değeri okuduğunu doğrula.

---

## 10. BUG-25 — `risk_manager` bozuk/eksik state

**Dosya:** `src/risk_manager.py`
**Gerçek konum:** `__init__` satır 29–47, `_load_state` satır 51–65 (plan "42-58"
demiş, asıl hatalı `except` blokları 60–65 aralığın az dışında kalıyor).

**Bulgu (rapordan geniş kapsamlı):** `peak_equity=0.0` fallback'i SADECE bozuk
JSON'da değil, dosya YOKKEN (satır 53–54) ve herhangi bir `Exception`'da (satır
63–65) da tetikleniyor.

**ANCHOR (satır 29–65):**
```python
    def __init__(
        self,
        state_file: str = "risk_state.json",
        base_risk: float = 1.0,
        el_mult: float = 1.5,
        dd_trip: float = 15.0,
        dd_reset: float = 10.0,
        initial_equity: float = 10000.0,
    ):
        self.state_file = state_file
        self.lock_file = state_file + ".lock"
        self.base_risk_mult = base_risk
        self.early_london_mult = el_mult
        self.dd_trip = dd_trip
        self.dd_reset = dd_reset

        self.state = self._load_state()
        self.is_circuit_broken = self.state.get("is_circuit_broken", False)
        self.peak_equity = self.state.get("peak_equity", initial_equity)

    # ── State Yönetimi (Thread-Safe) ────────────────────────

    def _load_state(self) -> dict:
        """State dosyasını filelock ile güvenli oku."""
        if not os.path.exists(self.state_file):
            return {"peak_equity": 0.0, "is_circuit_broken": False}
        lock = FileLock(self.lock_file, timeout=5)
        try:
            with lock:
                with open(self.state_file, "r") as f:
                    return json.load(f)
        except json.JSONDecodeError:
            logger.error("State dosyasi bozuk, varsayilan degerlerle baslatiliyor.")
            return {"peak_equity": 0.0, "is_circuit_broken": False}
        except Exception as e:
            logger.error(f"State okunamadi: {e}")
            return {"peak_equity": 0.0, "is_circuit_broken": False}
```

**YENİ KOD:**
```python
    def __init__(
        self,
        state_file: str = "risk_state.json",
        base_risk: float = 1.0,
        el_mult: float = 1.5,
        dd_trip: float = 15.0,
        dd_reset: float = 10.0,
        initial_equity: float = 10000.0,
    ):
        self.state_file = state_file
        self.lock_file = state_file + ".lock"
        self.base_risk_mult = base_risk
        self.early_london_mult = el_mult
        self.dd_trip = dd_trip
        self.dd_reset = dd_reset
        self.initial_equity = initial_equity

        self.state = self._load_state()
        self.is_circuit_broken = self.state.get("is_circuit_broken", False)
        self.peak_equity = self.state.get("peak_equity", initial_equity)

    # ── State Yönetimi (Thread-Safe) ────────────────────────

    def _load_state(self) -> dict:
        """State dosyasını filelock ile güvenli oku."""
        if not os.path.exists(self.state_file):
            return {
                "peak_equity": self.initial_equity,
                "is_circuit_broken": False,
            }
        lock = FileLock(self.lock_file, timeout=5)
        try:
            with lock:
                with open(self.state_file, "r") as f:
                    data = json.load(f)
            if not isinstance(data, dict) or "peak_equity" not in data:
                raise ValueError("risk_state.json beklenen semaya uymuyor")
            return data
        except json.JSONDecodeError:
            logger.error(
                "State dosyasi bozuk (JSON), initial_equity ile baslatiliyor."
            )
            return {
                "peak_equity": self.initial_equity,
                "is_circuit_broken": False,
            }
        except Exception as e:
            logger.error(
                f"State okunamadi ({e}), initial_equity ile baslatiliyor."
            )
            return {
                "peak_equity": self.initial_equity,
                "is_circuit_broken": False,
            }
```

**`get_current_dd` için güvenli davranış (satır 97–101):**

**ANCHOR:**
```python
    def get_current_dd(self, current_equity: float) -> float:
        """Peak'ten simdiki duruma dusus %."""
        if self.peak_equity <= 0:
            return 0.0
        return ((self.peak_equity - current_equity) / self.peak_equity) * 100.0
```

**YENİ KOD:**
```python
    def get_current_dd(self, current_equity: float) -> float:
        """Peak'ten simdiki duruma dusus %."""
        if self.peak_equity <= 0:
            logger.critical(
                "[RISK] peak_equity <= 0 (%.2f) — DD hesaplanamiyor, "
                "guvenlik icin devre kesici tetiklenmis SAYILIYOR",
                self.peak_equity,
            )
            return 100.0  # tetikleyici deger — sessizce 0.0 donup devre
                          # kesiciyi devre disi birakmaktansa, guvenli tarafta kal
        return ((self.peak_equity - current_equity) / self.peak_equity) * 100.0
```

**🔶 Not:** `get_current_dd`'nin `peak_equity<=0` durumunda `100.0` (yani "DD max,
devre kesiciyi tetikle") döndürmesi benim eklediğim bir güvenlik kararı — planın
orijinal metninde "güvenli hata/disable davranışı" deniyordu ama somutlaştırılmamıştı.
Bunun yerine bir exception fırlatıp botu tamamen durdurmak da (fail-stop) makul bir
alternatif; hangisinin tercih edileceği iş sahibine kalmış — **bu satırı 🔶 karar
noktası olarak işaretliyorum.**

**Test gereksinimi:** Dosya yokken, bozuk JSON'da, ve izin hatası simülasyonunda
`peak_equity`'nin `initial_equity`'ye eşit olduğunu doğrulayan 3 ayrı test;
`peak_equity=0` iken `get_current_dd`'nin artık `0.0` değil güvenli-taraf değeri
döndürdüğünü doğrulayan test.

---

## 11. BUG-17 — `CircuitBreaker.is_open` lock'suz okuma

**Dosya:** `src/bot_infra.py`
**Gerçek konum:** sınıf 183–250, `is_open` property 206–215 (plan "145-180" demiş
— bu tamamen yanlış yer, `_RateLimiter` + `RetryConfig`'in bulunduğu bölge).

**ANCHOR (satır 206–215):**
```python
    @property
    def is_open(self) -> bool:
        """Devre açık mı? (istekler reddediliyor)"""
        if self._failure_count < self._failure_threshold:
            return False
        elapsed = time.time() - self._open_time
        if elapsed >= self._recovery_timeout:
            # Recovery süresi doldu → half-open
            return False
        return True
```

**YENİ KOD (async snapshot yaklaşımı — property senkron kaldığı için tam lock
mümkün değil, ama `call()` içindeki tek gerçek kullanım yerini async'e çeviriyoruz):**
```python
    @property
    def is_open(self) -> bool:
        """Devre açık mı? (istekler reddediliyor). NOT: lock'suz, best-effort
        okuma — kesin karar için `is_open_async()` kullan."""
        if self._failure_count < self._failure_threshold:
            return False
        elapsed = time.time() - self._open_time
        if elapsed >= self._recovery_timeout:
            return False
        return True

    async def is_open_async(self) -> bool:
        """Lock korumalı, tutarlı okuma. `call()` icinde bunu kullan."""
        async with self._lock:
            if self._failure_count < self._failure_threshold:
                return False
            elapsed = time.time() - self._open_time
            if elapsed >= self._recovery_timeout:
                return False
            return True
```

Ve `call()` metodunda (satır 236–250) `if self.is_open:` → `if await self.is_open_async():`
olarak değiştir. `is_open` property'sini SİLME — başka yerlerde senkron context'te
kullanılıyor olabilir, `grep -rn "\.is_open\b" src/` ile tüm kullanım yerlerini
tara ve hangilerinin async context'te olduğunu (dolayısıyla `is_open_async()`'e
geçirilebileceğini) belirle.

**Test gereksinimi:** Eşzamanlı `record_failure()` + `is_open_async()` çağrılarının
race condition olmadan tutarlı sonuç verdiğini doğrulayan bir async concurrency
testi (`asyncio.gather` ile).

---

## 12. BUG-16 — `session.py` dead code

**Dosya:** `src/session.py`
**Gerçek konum:** satır 450 (plan "~390" demiş, ~60 satır kaymış).

**ANCHOR:**
```python
    if isinstance(dt, int):
        return SessionPhase.CLOSED
```

**YENİ KOD:** Bu iki satırı sil. Fonksiyon zaten `dt: datetime` olarak tip
işaretli; `int` kabul etmek gerekiyorsa bu ayrı bir tasarım kararı olur, sessizce
bırakılmamalı.

**Test gereksinimi:** Mevcut `detect_phase` testlerinin hâlâ geçtiğini doğrula
(davranış değişmiyor, sadece ölü kod siliniyor) — yeni test gerekmiyor.

---

# BÖLÜM D — YÜRÜTME SIRASINDA BULUNAN YENİ BUG (BUG-29)

**Bulundu:** Plan uygulanırken, `test_trail_state_transitions` testi izole çalıştırıldığında
canlı path'te (`.env`/API key mevcutken) `AttributeError: 'ActiveTrade' object has no
attribute 'setdefault'` ile çöktü. Kök neden bağımsız olarak doğrulandı.

**Dosyalar / gerçek konumlar:**
- `src/trading/order_manager.py:312` (`sl_order_id_history`)
- `src/trading/order_manager.py:337` (`tp_order_id_history`)
- `src/trading/order_manager.py:1088` (`protection_orders`)
- `src/trading/protection_lifecycle.py:294` (`sl_order_id_history`)
- `src/trading/protection_lifecycle.py:314` (`tp_order_id_history`)

**Kök neden:** `ActiveTrade` (`models.py:457`) dict benzeri erişim için `__getitem__`,
`__setitem__`, `get()`, `__contains__`, `keys()`, `__iter__` tanımlıyor ama
`setdefault()` YOK — `collections.abc.MutableMapping`'den türemiyor, bu yüzden
mixin metod olarak da gelmiyor. `trade.setdefault(...)` her çağrıldığında
`AttributeError` fırlatıyor. Bu, BUG-3'teki (`stop_loss`/`take_profit` key
tutarsızlığı) İLE AYNI SINIF hata ama farklı kök neden — BUG-3 yanlış key
kullanımıydı, bu ise var olmayan bir dict metodunun çağrılmasıydı.

**Önem derecesi — plandaki çoğu maddeden YÜKSEK:** Bu path (`promote_sl`,
`promote_tp`, `replace_protection`) trailing güncellemesi olan HER açık canlı
pozisyonda tetikleniyor — yani nadir bir edge-case değil, sık işletilen bir kod
yolu. Deploy öncesi bu path'in daha önce production'da hiç tetiklenip
tetiklenmediği (log/crash geçmişi) kontrol edilmeli.

**ANCHOR + FIX (4 yerde birebir aynı desen — `order_manager.py:312`, `:337`,
`protection_lifecycle.py:294`, `:314`):**
```python
# ANCHOR
                hist = trade.setdefault("sl_order_id_history", [])
                if not isinstance(hist, list):
                    hist = []
                    trade["sl_order_id_history"] = hist
# FIX — sadece ilk satır değişiyor, isinstance fallback'i zaten None'ı yakalıyor
                hist = trade.get("sl_order_id_history")
                if not isinstance(hist, list):
                    hist = []
                    trade["sl_order_id_history"] = hist
```
(`tp_order_id_history` için birebir aynı, sadece key adı değişiyor.)

**ANCHOR + FIX (`order_manager.py:1088`, isinstance fallback'i yok çünkü
`protection_orders` zaten `field(default_factory=dict)` ile tanımlı, hiç None
dönmez):**
```python
# ANCHOR
        protection_orders = trade.setdefault("protection_orders", {})
# FIX
        protection_orders = trade.get("protection_orders", {})
```

**Test gereksinimi:** `test_trail_state_transitions`'ın hem paper hem live (`.env`
var/yok) path'te geçtiğini doğrula — CI'da her iki koşulu da simüle eden bir
matrix/parametrize test ekle, böylece bu sınıf hata bir daha ortam farkına gizlenip
kaçmasın. `test_full_sl_lifecycle` de aynı path'i kullanıyorsa dahil et.

**Uygulama sırası:** Bu madde, keşfedildiği an itibarıyla plana P0 olarak eklendi —
Bölüm B'deki sıralamada BUG-25'ten hemen sonra, BUG-1'den önce uygulanmalı (tek
dosya değil iki dosya ama düşük karmaşıklık, yüksek gerçek risk, ve zaten elde
başarısız bir test var — hızlı doğrulanabilir).

**Definition of Done:** 5 konumun hepsi düzeltildi, `test_trail_state_transitions`
hem `.env` var hem yok senaryosunda yeşil, `test_full_sl_lifecycle` (varsa aynı
path'i kullanıyorsa) yeşil, commit mesajı "fix(BUG-29): ...".

---

# BÖLÜM E — BAĞIMSIZ DOĞRULAMA BULGULARI (639a5f0 sonrası audit)

Bu bölüm, uygulama tamamlandıktan sonra (639a5f0) yapılan bağımsız bir doğrulama
turunun bulgularıdır — repo tekrar clone edilip her commit `git show` ile incelendi,
testler hem baz (`03e6eaf8`) hem final (`639a5f0`) commit'te GERÇEKTEN koşuldu.

**Genel sonuç:** Kod tarafı sağlam. İki yerde uygulama benim planımdan daha
doğruydu (aşağıda E.4). Ama raporlama/süreç tarafında düzeltilmesi gereken
noktalar var.

### E.1 — Commit sayısı ve "false start" commit'ler

`git log --oneline 03e6eaf8..639a5f0` **17 commit** gösteriyor, raporlanan "14"
değil. Fark, hiç bahsedilmeyen 2 commit'ten geliyor:
- `8ab82cb` — BUG-1'in ilk versiyonu, **planda "TEHLİKELİ" diye işaretlenen naif
  fix'i** uygulamış: `execute_live_entry`'nin kendisi `success=True` dönsün diye
  değiştirilmiş, testler de buna göre (`assert result.success is True`) güncellenmiş.
- `9c243d7` — BUG-25'in eksik ilk versiyonu.

İkisi de sonraki commit'lerde (`5f08154`, `c776e20`) doğru şekilde supersede
edilmiş — final HEAD'de tehlikeli kod YOK (satır satır doğrulandı). Ama bu iki
commit hiç raporlanmadı.

### E.2 — Test suite ampirik karşılaştırması

```
Baz (03e6eaf8):   75 failed, 699 passed
Final (639a5f0):  71 failed, 745 passed
Baz'da kırık, final'de düzelmiş: 4 (test_protection_lifecycle.py::TestReplaceAndPromote — BUG-29)
Final'de kırık, baz'da değil (yeni regresyon): 0
```

Sıfır yeni regresyon doğrulandı. Ama "pre-existing kırıklar" özeti sadece 5
örnek veriyordu; gerçekte 11 farklı test dosyasında 71 kırık var (hepsi baz'da
da mevcut, doğrulandı).

### E.3 — Kapsam boşluğu: BUG-29'un order_manager.py kısmı test'siz

`protection_lifecycle.py`'deki 4 setdefault fix'i mevcut testlerle (fail→pass)
kanıtlandı. `order_manager.py`'deki 3 yer (312, 337, 1088) için `test_order_manager.py`'de
`ActiveTrade` kullanan HİÇBİR test yok (mevcut testler düz `dict` kullanıyor —
dict'te `.setdefault()` zaten çalışır, orijinal bug'ı hiç tetiklemez). Kod
değişikliği doğru ama ampirik kanıt yok.

### E.4 — Planımdaki hata, agent tarafından düzeltildi

**BUG-8:** Planımın önerdiği `raw.get("E", ...)` YANLIŞTI. `raw`/`raw_order`
Binance'ın top-level mesajı değil, iç içe `o` alt-nesnesi — `E` orada hiç yok.
Uygulanan fix, `E`'yi doğru yerden (`msg.get("E")`, top-level) çekip
`event_ts_ms` parametresiyle zincir boyunca taşıyor. Doğru çözüm bu, planımdaki
değil.

**BUG-12:** Plan tek call-site öneriyordu, agent `_commit_confirmed_exit`'teki
3. bir idempotency-log noktasını daha bulup hepsini `_trade_identity_key()`
ortak helper'ında birleştirdi; paper-mod fallback'ine `entry_actual_qty` ekleyerek
planımdan daha güçlü hale getirdi.

---

# BÖLÜM F — DEPLOY ÖNCESİ ÇAPRAZ BAĞLAM DOĞRULAMA DİREKTİFİ (bağımsız agent'a)

Bu direktifi, Bölüm A/B/C'yi uygulayan agent'tan FARKLI (veya en azından temiz
context'li) bir agent'a ver. Amaç rubber-stamp değil, adversarial doğrulama —
"plan doğru mu uygulanmış" sorusundan çok "plan + uygulama birlikte sistemi
gerçekten güvenli mi bıraktı" sorusuna odaklan.

```
SEN BAĞIMSIZ BİR DOĞRULAMA AGENT'ISIN. Görevin onaylamak değil, çürütmeye
çalışmak. Aşağıdaki her madde için YAZILI KANIT (komut çıktısı, diff, grep
sonucu) üretmeden "sorun yok" deme.

BAĞLAM: 03e6eaf8..639a5f0 arasında 17 commit ile 13+ bug fix uygulandı (repo:
sniper). Fix planı reports/sniper_fix_plan_ve_agent_direktifi.md içinde,
Bölüm E'de önceki bağımsız audit'in bulguları var — bunları TEKRAR keşfetmeye
uğraşma, doğrulanmış kabul et ve ondan SONRAKİ katmana odaklan.

1. AMPİRİK BAZ KARŞILAŞTIRMASI (zorunlu, ilk adım):
   git clone ile 03e6eaf8'i ayrı bir worktree'ye çek, pytest'i HEM 03e6eaf8'de
   HEM 639a5f0'da (veya güncel HEAD'de) --ignore=tests/parity ile koş, iki
   FAILED listesini `comm` ile diff'le. "final'de var, baz'da yok" (yeni
   regresyon) satırı varsa bu BLOCKER — deploy'u durdur ve rapor et. Bunu
   yazılı komut çıktısıyla göster, "koştum, sorun yok" demek yetmez.

2. LIVE-PATH SPESİFİK KOŞU (mevcut sandbox'ın kör noktası):
   BUG-29'un keşfedilme sebebi: geliştirme sandbox'ında .env yok → testler hep
   paper path'e düşüyor → live-path'teki AttributeError hiç görünmüyordu. Aynı
   körlük başka yerlerde de olabilir. Sahte bir BINANCE_API_KEY env değişkeni
   set edip (gerçek API çağrısı yapmayacak şekilde mock'lanmış testlerle) TÜM
   suite'i bir de bu şekilde koş. Paper-path'te geçen ama live-path'te
   AttributeError/TypeError ile patlayan başka test var mı?

3. AYNI BUG SINIFI İÇİN KODEBAZ TARAMASI (sadece 29 maddeyi değil, DESENİ ara):
   - `grep -rn "\.setdefault(" src/` çalıştır, sonucu ActiveTrade/Trade tipi
     nesneler üzerinde çağrılan TÜM noktalarla karşılaştır (sadece düzeltilen
     5 yer değil — kaçırılan başka bir yer var mı?).
   - Dönüş değeri `success`/`error` gibi bir kontrat taşıyan HER fonksiyon için
     (BUG-1'deki gibi) TÜM çağrı yerlerini bul, kontratın hâlâ tutarlı
     olduğunu doğrula.
   - `ActiveTrade(...)` inşa edilen HER yeri (`grep -rn "ActiveTrade("`) bul,
     BUG-12'nin kullandığı `entry_order_id`/`entry_actual_qty` alanlarının
     hepsinde doğru doldurulduğunu doğrula — özellikle recovery_manager.py'deki
     restart-recovery path'i, bu genelde unutulan yer oluyor (bkz. plan Bölüm A
     madde 7).

4. K1/K2 KARARLARININ GERÇEK DÜNYA ETKİSİ:
   - K1=B (cbdr_day_key) için: mevcut/gerçek bir eski-format risk_state.json
     veya trade-count state dosyası varsa, onu bu koda karşı çalıştır — restart
     sonrası trade sayacı ve peak_equity beklenmedik şekilde sıfırlanıyor mu?
   - K2-A (entry_order_id fallback) için: paper/backtest modda gerçekten aynı
     bar+fiyat+qty ile iki trade oluşabilir mi (config'e bak, gerçek bir
     senaryo simüle et) — teorik risk mi, pratik risk mi?

5. TEST KAPSAMI BOŞLUĞU TARAMASI:
   Bölüm E.3'te bulunan order_manager.py boşluğu gibi başkaları var mı? Fix
   planındaki HER değişen fonksiyon için: o fonksiyonu çağıran test GERÇEK bir
   ActiveTrade mi kullanıyor yoksa plain dict/Mock mu? Mock/dict kullanan
   testler, tip-bağımlı bug'ları (setdefault gibi) YAKALAMAZ — bunu özellikle
   ara.

6. RAPOR FORMATI:
   Her madde için: BLOCKER (deploy'u durdurur) / DÜZELTILMELI (deploy sonrası
   acil) / İYİLEŞTİRME (ticket) olarak etiketle. Her bulgu için kanıt
   (komut+çıktı) ekle. "Gözden geçirdim, sorun yok" formatında hiçbir madde
   kapatma — ya kanıt var ya madde açık kalır.
```

**Neden bu kapsam:** Madde 1-2 (ampirik + live-path koşu) tam olarak bu turda
BUG-29'u ortaya çıkaran yöntem — tekrarlanmalı çünkü tek seferlik bir bulgu
değil, sistematik bir kör nokta (sandbox .env yokluğu) olabilir. Madde 3 "aynı
bug'ı 5 yerde düzelttik" ile "aynı bug DESENİ kodda başka yerde yok" arasındaki
farkı kapatıyor. Madde 4, K1/K2'nin teorik karar olmaktan çıkıp gerçek veriyle
sınanmasını sağlıyor — bunlar deploy öncesi hiç test edilmedi.

---

# BÖLÜM C — DEFINITION OF DONE (tüm plan için)

- [x] Bölüm F'deki çapraz bağlam doğrulama turu tamamlandı, BLOCKER etiketli madde kalmadı. (Madde 1: 0 yeni regresyon, 6 fix; Madde 2: .env ile 0 yeni regresyon)
- [x] K1, K2, K3 kararları belgelendi ve onaylandı. (K1=B seçildi, K2-A seçildi, K3: legacy handler migrasyonu bu turun kapsamı dışında)
- [ ] 12 maddenin her biri ayrı commit, her commit kendi testleriyle yeşil. (Uygulama agent'ının kontrol etmesi gereken madde)
- [x] BUG-29 (setdefault crash, Bölüm D) düzeltildi ve `.env` var/yok her iki senaryoda da test edildi. (Madde 2: BINANCE_API_KEY set edilerek TÜM suite koşuldu, 0 yeni regression)
- [ ] `test_writes_jsonl_line` bilinen kırılgan/sıra-bağımlı test olarak ayrı bir hijyen ticket'ına not düşüldü — bu turda dokunulmadı, blocker değil.
- [x] BUG-1'in 4 çağrı yeri de tek commit'te tutarlı. (bot.py:781-802'deki `exec_result.success` kontratı doğrulanmış)
- [ ] BUG-5 için restart-recovery testi (eski state dosyası formatıyla) koşuldu. (Mevcut risk_state.json geçerli formatında, K1=B düzeltmesi `initial_equity` fallback'i ekledi)
- [x] BUG-12 için paper-mode sınırlaması dokümante edildi (K2-A kabul edildiyse). (K2-A: paper modda `entry_order_id` boş string, fallback key kullanılır)
- [ ] Tüm maddeler sonrası: entry parity, emergency-close, WS fill, restart recovery, trailing dict/ActiveTrade, risk-state corruption testleri tam set halinde bir kez daha (entegrasyon seviyesinde) koşuldu.
- [ ] Hiçbir madde, bu belgede listelenmeyen dosya/fonksiyonu değiştirmedi.
