# RISK MATRIX VALIDATOR — SPRINT A / B UYGULAMA PLANI
# Amaç: Canlı execution çekirdeğini güvenlik açısından stabilize etmek
# Not: Sprint A = güvenlik sabitleme, Sprint B+ = yapısal refactor

---
## SPRINT A — CANLI RİSKİ HEMEN AZALTACAK DEĞİŞİKLİKLER

### MADDE A1 — _exit_trade içinde erken pop + erken muhasebe commit kaldırılacak
- [X] Bot
- **Dosya:** `src/bot.py`
- **Fonksiyon:** `_exit_trade()`
- **Satır:** `706-915` (özellikle `752`, `773-780`, `801-915`)
- **Şu an:**
  - `trade = self.active_trades.pop(sym, None)` çok erken çalışıyor (`752`)
  - `pnl`, `available_balance`, `peak_equity` close doğrulanmadan önce hesaplanıp yazılıyor (`773-783`)
  - gerçek market fill gelirse `821-823` satırlarında exit fill güncelleniyor ama `pnl` aynı akışta yeniden hesaplanmıyor
  - invalid fill durumunda (`761-763`) fonksiyon `return` ediyor, trade active set’e geri konmuyor
- **İstenen:**
  - `active_trades.pop()` close doğrulaması (`pos_closed=True`) sonrasına alınsın
  - `pnl`, `available_balance`, `peak_equity`, `log_event("exit")`, `cleanup_on_exit`, `trades.append`, `mark_trade_closed` yalnızca close doğrulandıktan sonra çalışsın
  - `exit_actual_price/qty` son haline göre realized pnl **tek kez** hesaplanıp commit edilsin
  - invalid fill path’inde trade state’ten düşmesin; `EXIT_UNCONFIRMED` veya `BROKEN_MANUAL_INTERVENTION_REQUIRED` durumuna alınsın
- **Test:**
  - `market close başarısız → positionAmt != 0 → trade active_trades'te kalmalı`
  - `market fill farklı fiyattan geldi → committed pnl fill fiyatına göre olmalı`
  - `invalid fill → trade kaybolmamalı`

---
### MADDE A2 — _exit_trade için minimal status tabanlı ara durumlar eklenecek
- [X] Bot
- **Dosya:** `src/models.py` + `src/bot.py`
- **Model Alanı:** `ActiveTrade`
- **Satır:** `models.py 349-359` (mevcut `status` alanı kullanılabilir, gerekirse yanına ek alanlar eklenir)
- **Şu an:**
  - `status` alanı var ama exit/protection lifecycle için fiilen kullanılmıyor
- **İstenen:**
  - Sprint A seviyesinde tam state machine değil, ama en azından şu durumlar kullanılmalı:
    - `ACTIVE`
    - `TRAIL_REPLACING`
    - `EXIT_VERIFYING`
    - `REPAIR_REQUIRED`
    - `BROKEN_MANUAL_INTERVENTION_REQUIRED`
  - `_exit_trade`, `update_trail_orders`, `reconcile_orphan_orders`, `on_order_update` bu alanı okuyup davranışını kısıtlasın
- **Test:**
  - `market close başarısız → status=EXIT_VERIFYING veya REPAIR_REQUIRED`
  - `broken durumda trailing/orphan agresif çalışmamalı`

---
### MADDE A3 — WS_FALLBACK path’i trade core alanlarını doğrudan kirletmeyecek
- [X] Bot
- **Dosya:** `src/trading/user_data_handler.py` + `src/models.py`
- **Fonksiyon:** `on_order_update()`
- **Satır:** `62-119` (özellikle `106-119`)
- **Şu an:**
  - unmatched reduceOnly fill geldiğinde şu alanlar doğrudan trade’e yazılıyor:
    - `exit_price`
    - `exit_actual_price`
    - `exit_actual_qty`
    - `exit_quote_qty`
    - `exit_order_id`
    - `exit_timestamp`
    - `result = "WS_FALLBACK"`
- **İstenen:**
  - bu alanlar doğrudan confirmed trade state’e yazılmasın
  - `ActiveTrade` içine Sprint A için minimal geçici alanlar eklenebilir:
    - `pending_exit_reason`
    - `pending_exit_price`
    - `pending_exit_qty`
    - `pending_exit_order_id`
    - `pending_exit_timestamp`
  - `WS_FALLBACK` event önce bu geçici alana yazılsın
  - `_exit_trade()` verify sonrası commit ederse confirmed exit alanlarına taşısın
  - stale event ise pending alanlar temizlensin, confirmed trade değişmesin
- **Test:**
  - `WS_FALLBACK tetiklendi + position açık → confirmed exit alanları boş kalmalı`
  - `WS_FALLBACK doğrulandı → commit sonrası exit alanları dolmalı`

---
### MADDE A4 — verify_protection() presence mantığından çıkarılacak
- [X] Bot
- **Dosya:** `src/trading/order_manager.py`
- **Fonksiyon:** `verify_protection()`
- **Satır:** `181-202` (özellikle `188-195`)
- **Şu an:**
  - `sl_present = (not s_id) or (s_id in open_ids)`
  - `tp_present = (not t_id) or (t_id in open_ids)`
  - yani boş ID bazı durumlarda “present” sayılıyor
- **İstenen:**
  - mantık şu hale gelsin:
    - `expects_sl = bool(trade.get("sl"))`
    - `expects_tp = bool(trade.get("tp"))`
    - `expects_sl` varsa ama `sl_order_id` boşsa → `sl_present = False`
    - `expects_tp` varsa ama `tp_order_id` boşsa → `tp_present = False`
  - REST failure hâlinde fail-safe yine korunabilir; ama `unknown` ile `healthy` aynı kabul edilmesin
  - mümkünse dönüş tipi ileride genişletilmeye uygun olsun (`present/unknown/missing` mantığına yakın)
- **Test:**
  - `trade['sl']=100, sl_order_id='' → sl_present=False`
  - `trade['sl']=0 → sl_present=True veya not_required mantığı`

---
### MADDE A5 — orphan cleanup current ID ile sınırlı kalmayacak
- [X] Bot
- **Dosya:** `src/trading/recovery_manager.py`
- **Fonksiyon:** `reconcile_orphan_orders()`
- **Satır:** `478-515`
- **Şu an:**
  - `known_ids` yalnızca `sl_order_id` ve `tp_order_id` ile kuruluyor (`484-489`)
  - prev/history/pending ID’ler dikkate alınmıyor
  - function başında tek snapshot alınıyor, geçiş sırasında bayatlıyor
- **İstenen:**
  - `_known_protection_ids()` helper’ı eklensin
  - helper şu ID kaynaklarını toplasın:
    - `sl_order_id`, `tp_order_id`
    - `sl_order_id_prev`, `tp_order_id_prev`
    - `sl_order_id_history`, `tp_order_id_history`
    - varsa `pending_sl_order_id`, `pending_tp_order_id`
  - her sembol taraması öncesinde known set tazelensin
  - `status in (TRAIL_REPLACING, EXIT_VERIFYING, REPAIR_REQUIRED)` ise o sembolde orphan cleanup skip veya muhafazakâr çalışsın
- **Test:**
  - `trailing replacement sırasında orphan sweep → yeni emir iptal edilmemeli`
  - `prev/history id iptalleri yanlış orphan sayılmamalı`

---
### MADDE A6 — update_trail_orders() pending replacement farkındalığı kazanacak
- [X] Bot
- **Dosya:** `src/trading/order_manager.py` + `src/models.py`
- **Fonksiyon:** `update_trail_orders()`
- **Satır:** `38-177`
- **Şu an:**
  - yeni emir place ediliyor, sonra current ID direkt değişiyor
  - transition bilgisi dış dünyaya açık şekilde modellenmiyor
  - `trade.setdefault("sl_order_id_history", [])` → `ActiveTrade` dataclass'ında `setdefault` METODU YOK (AttributeError)
  - Aynı sorun `tp_order_id_history` için de geçerli (satır 145)
  - Bu yüzden `sl_order_id_prev`/`history` alanları pratikte HİÇ DOLMUYOR — trailing replacement'larda WS fill eşleşmesi için kritik olan prev/history ID'ler boş kalıyor
- **İstenen:**
  - Sprint A seviyesinde minimal pending alanlar eklenebilir:
    - `pending_sl_order_id`
    - `pending_tp_order_id`
  - yeni emir alındığında önce pending'e yazılsın
  - eski emir cancel + yeni emir visible olduktan sonra current alana promote edilsin
  - `status = TRAIL_REPLACING` kısa süreli set edilsin
  - iki taraf da fail olursa status eski haline dönsün, `trail_fail_streak` artsın
  - `setdefault` hatası düzeltilsin: `history` listesini manuel başlat (`if not trade.get("sl_order_id_history"): trade["sl_order_id_history"] = []`)
- **Test:**
  - `SL yeni id alındı, TP gecikti → orphan bunu orphan sanmamalı`
  - `trailing replacement sonrası prev/history ID'ler dolu olmalı (setdefault hatası giderilmeli)`

---
### MADDE A7 — cancel_all_open_orders erken broad-cancel olmaktan çıkarılacak
- [X] Bot
- **Dosya:** `src/bot.py` + `src/trading/order_manager.py`
- **Fonksiyon:** `_exit_trade()` + `cleanup_on_exit()`
- **Satır:**
  - `bot.py 801-809`
  - `order_manager.py 283-336`
- **Şu an:**
  - `_exit_trade()` içinde close attempt’ten önce `cancel_all_open_orders()` çağrılıyor
  - bu broad cancel close başarısız olursa protection’ı da uçurabiliyor
- **İstenen:**
  - `_exit_trade()` içinden broad `cancel_all_open_orders` kaldırılmalı
  - protection cancel mantığı `cleanup_on_exit()` içine taşınmalı
  - cleanup yalnızca exit sonucu gerçekten commit edildikten sonra veya açıkça gerekli olduğunda çalışmalı
- **Test:**
  - `market close başarısız → protection mümkünse yerinde kalmalı veya repairable olmalı`

---
### MADDE A8 — cleanup_on_exit() result türüne göre doğru davranacak
- [ ] Bot
- **Dosya:** `src/trading/order_manager.py`
- **Fonksiyon:** `cleanup_on_exit()`
- **Satır:** `283-336`
- **Şu an:**
  - `result == "SL"` değilse otomatik `tp_order_id` trigger olmuş gibi varsayıyor (`296-320`)
  - `TRAIL_CLOSE`, `WS_FALLBACK`, `TIMEOUT` gibi path’lerde bu varsayım yanlış
- **İstenen:**
  - davranış 3 sınıfa ayrılmalı:
    1. `SL` → kalan TP iptal et
    2. `TP` → kalan SL iptal et
    3. `TRAIL_CLOSE`, `WS_FALLBACK`, `TIMEOUT`, `MANUAL_CLOSE` benzeri synthetic/market path’ler → her iki protection tarafını güvenli biçimde iptal etmeye çalış
  - acil market close fallback yalnızca `result in ("SL", "TP")` ve tetiklenen tarafın Binance ID’si yoksa düşünülmeli
- **Test:**
  - `TRAIL_CLOSE → hem SL hem TP iptal denensin`
  - `TRAIL_CLOSE path’inde yanlış trigger varsayımı yapılmasın`

---
### MADDE A9 — market close başarısızlığında trade doğrudan ACTIVE’e dönmeyecek
- [ ] Bot
- **Dosya:** `src/bot.py`
- **Fonksiyon:** `_exit_trade()`
- **Satır:** `890-915`
- **Şu an:**
  - başarısız close sonrası `sl_order_id=''`, `tp_order_id=''`, `result=None`, `active_trades[sym]=trade`
  - trade fiilen sıradan active trade gibi dolaşıma dönüyor
- **İstenen:**
  - başarısız close sonrası:
    - `result` eski confirmed state’i bozmasın
    - `status = EXIT_VERIFYING` veya `REPAIR_REQUIRED`
    - `verify_protection()` çağrılsın
    - eksik protection varsa `repair_protection()` çalışsın
  - trade normal ACTIVE durumuna otomatik dönmesin
  - bu durumda `_on_1m_close` bazı akışları (trailing/new exit/orphan aggression) yavaşlatsın veya atlasın
- **Test:**
  - `market close başarısız → trade ACTIVE değil, EXIT_VERIFYING/REPAIR_REQUIRED kalmalı`

---
### MADDE A10 — adapter belirsizliği explicit ambiguous state yaratacak
- [ ] Bot
- **Dosya:** `src/bot_binance.py` + `src/bot.py`
- **Fonksiyon:** `place_market_order()` + `_exit_trade()`
- **Satır:**
  - `bot_binance.py 551-616`
  - `bot.py 811-863`
- **Şu an:**
  - adapter bazen `{}` veya eksik kimlikli response döndürebiliyor
  - üst katman bunu yeterince transaction mantığıyla ele almıyor
- **İstenen:**
  - şu ayrım zorunlu olsun:
    - `REQUEST_SENT`
    - `ORDER_ACKNOWLEDGED`
    - `EXECUTION_CONFIRMED`
  - boş/kimliksiz response → `EXIT_VERIFYING` veya `MARKET_CLOSE_AMBIGUOUS`
  - bu durumda commit yapılmasın
- **Test:**
  - `{}` response → no commit`
  - `response var ama position açık → no commit`

---
### MADDE A11 — on_order_update cancel/repair logic’i status farkındalığı kazanacak
- [ ] Bot
- **Dosya:** `src/trading/user_data_handler.py`
- **Fonksiyon:** `on_order_update()`
- **Satır:** `131-164`
- **Şu an:**
  - CANCELED/EXPIRED geldiğinde current ID ise doğrudan `repair_protection()` deneniyor
  - trade’in o sırada exit/replacement halinde olup olmadığına dair güçlü bir status kontrolü yok
- **İstenen:**
  - `status` tabanlı guard eklensin:
    - `EXIT_VERIFYING` sırasında gelen bazı cancel event’leri repair tetiklemesin
    - `TRAIL_REPLACING` sırasında old/new geçişleri daha muhafazakâr yorumlansın
  - repair yalnızca gerçekten `ACTIVE` veya `REPAIR_REQUIRED` protection eksikliği halinde denensin
- **Test:**
  - `exit sırasında cancel event → gereksiz protection repair tetiklenmemeli`

---
### MADDE A12 — state_writer operatöre minimal güvenlik görünürlüğü verecek
- [ ] Bot
- **Dosya:** `src/state_writer.py`
- **Fonksiyon:** `write_state()`
- **Satır:** `16-66` (özellikle symbol payload bölümü `33-63`)
- **Şu an:**
  - active trade özeti var ama transition/broken/protection health görünürlüğü yok
- **İstenen:**
  - active_trade payload’ına en azından şu alanlar eklensin:
    - `status`
    - `sl_order_id_present`
    - `tp_order_id_present`
    - `exit_unconfirmed` (bool)
    - `repair_required` (bool)
  - pending fiyat/qty gibi fazla detay yazılmak zorunda değil; amaç operator visibility
- **Test:**
  - `repair_required durumda state file bunu göstermeli`

---
### MADDE A13 — incident sabitleri ve log dili standardize edilecek
- [ ] Bot
- **Dosya:** yeni yardımcı dosya veya `src/bot.py` üst kısmı + ilgili modüller
- **İstenen:**
  - Incident tipleri tanımlansın:
    - `POSITION_OPEN_BUT_STATE_MISSING`
    - `EXIT_UNCONFIRMED`
    - `PROTECTION_BROKEN`
    - `WS_UNMATCHED_REDUCE_ONLY`
    - `ORPHAN_CANCEL_DURING_TRANSITION`
  - Log akışı mümkün olduğunca şu sırayı izlesin:
    - intent
    - execution attempt
    - exchange confirmation
    - state commit
    - rollback / repair
- **Test:**
  - her incident için en az bir sentetik senaryoda log satırı üret

---
### MADDE A14 — symbol freeze / safety brake eklenecek
- [ ] Bot
- **Dosya:** `src/bot.py` + gerekirse `src/models.py`
- **Fonksiyon:** `_on_1m_close()`
- **Satır:** `403-445`
- **Şu an:**
  - sembol exit verifying / repair required haldeyken aynı loop trailing/exit akışları normal gibi devam edebiliyor
- **İstenen:**
  - `status in (EXIT_VERIFYING, REPAIR_REQUIRED, BROKEN_MANUAL_INTERVENTION_REQUIRED)` ise:
    - trailing update atlanabilir
    - yeni sentetik exit tetiklenmeyebilir
    - orphan cleanup bu sembolde skip olabilir
    - gerekiyorsa yeni entry bloklanır
- **Test:**
  - `REPAIR_REQUIRED durumda trailing tekrar tekrar koşturulmamalı`

---
## SPRINT A — SON TEST MATRİSİ
- [ ] `TRAIL_CLOSE -> market close başarısız -> protection korunuyor / onarılıyor mu?`
- [ ] `WS_FALLBACK -> position hâlâ açık -> confirmed exit alanları kirleniyor mu?`
- [ ] `invalid fill -> trade state’ten düşüyor mu?`
- [ ] `trailing replacement sırasında orphan sweep -> yeni emir iptal oluyor mu?`
- [ ] `adapter {} response -> commit yapılıyor mu?`
- [ ] `repair_required durumda state_writer bunu gösteriyor mu?`

---
## SPRINT B — MİNİMUM YAPISAL GÜÇLENDİRME

### MADDE B1 — ActiveTrade confirmed vs runtime ayrımı
- [ ] Mimari
- **Dosya:** `src/models.py`
- **Satır:** `314-382`
- **İstenen:**
  - `ActiveTrade` tek gövde olmaktan çıkarılsın
  - confirmed state ve runtime/pending state ayrımı kurulsun

### MADDE B2 — pending exit context / pending protection refs veri yapıları
- [ ] Mimari
- **Dosya:** `src/models.py` + ilgili modüller
- **İstenen:**
  - pending exit context
  - pending replacement refs
  - buffered WS event alanları

### MADDE B3 — verify_protection dönüşünü lifecycle’a yaklaştır
- [ ] Mimari
- **Dosya:** `src/trading/order_manager.py`
- **İstenen:**
  - bool yerine ileride stateful sonuç yapısına evrilebilecek tasarım

---
## SPRINT C — LIFECYCLE ENGINE

### MADDE C1 — explicit lifecycle state machine
- [ ] Mimari
- **Dosya:** yeni servis veya `src/bot.py` + `src/models.py`
- **İstenen state’ler:**
  - `ACTIVE`
  - `TRAIL_REPLACING_PROTECTION`
  - `EXIT_REQUESTED`
  - `EXIT_SUBMITTED`
  - `EXIT_VERIFYING`
  - `REPAIR_REQUIRED`
  - `CLOSED`
  - `BROKEN_MANUAL_INTERVENTION_REQUIRED`

### MADDE C2 — exit flow transaction ayrımı
- [ ] Mimari
- **Akış:**
  - Prepare
  - Execute
  - Verify
  - Commit
- **Kural:** commit öncesi muhasebe kalıcılaşmaz

### MADDE C3 — WS event normalization
- [ ] Mimari
- **Dosya:** `src/trading/user_data_handler.py`
- **İstenen:**
  - WS handler event-source olacak, state mutator olmayacak

---
## SPRINT D — PROTECTION / ACCOUNTING AYRIŞTIRMA

### MADDE D1 — protection manager lifecycle
- [ ] Mimari
- **Dosya:** `src/trading/order_manager.py` veya yeni servis
- **Durumlar:**
  - `EXPECTED`
  - `PENDING_CREATE`
  - `ACTIVE_CONFIRMED`
  - `PENDING_REPLACE`
  - `BROKEN`
  - `NOT_REQUIRED`

### MADDE D2 — accounting / persistence separation
- [ ] Mimari
- **Dosya:** `src/bot.py` + `src/state_writer.py`
- **İstenen:**
  - accounting yalnızca confirmed transition sonrası
  - persistence runtime speculation’dan ayrılacak

### MADDE D3 — orphan cleanup stable-state servisi
- [ ] Mimari
- **Dosya:** `src/trading/recovery_manager.py`
- **İstenen:**
  - orphan cleanup transition-aware, stable-state temelli çalışacak

---
## SPRINT E — TEST / OPERASYON / ROLLOUT

### MADDE E1 — Unit test seti
- [ ] Test
- invalid fill rollback
- unmatched reduceOnly fallback
- pending replace sırasında orphan suppression
- previous/history/current ID semantiği

### MADDE E2 — Integration test seti
- [ ] Test
- trailing replace sırasında eski SL fill
- market close accepted ama delayed confirmation
- adapter boş/ambiguous response

### MADDE E3 — Simulation / chaos test
- [ ] Test
- REST timeout
- WS delay
- order create success ama ID geç görünmesi
- open order snapshot stale gelmesi

### MADDE E4 — rollout
- [ ] Operasyon
- önce instrumentation
- sonra Sprint A hotfix
- sonra feature-flag ile yeni lifecycle
- bir süre dual logging

---
## BAŞ MÜHENDİSE TEK CÜMLELİK KARAR
Bu sorun birkaç lokal bug fix ile kapanacak sınıfta değildir. Sprint A’da canlı riski hızla azaltacak güvenlik sabitlemeleri yapılmalı; ardından confirmed/pending state ayrımı ve explicit lifecycle state machine içeren mimari refactor uygulanmalıdır. Aksi halde aynı risk sınıfı yeni varyasyonlarla canlıda geri gelir.


Bu versiyon, benim gerçek teknik görüşümü çok daha doğru temsil ediyor.
