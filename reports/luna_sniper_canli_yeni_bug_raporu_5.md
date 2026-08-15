Sniper 49f67e6 sonrası eksik fix direktifi
Kontrol edilen HEAD: 49f67e6a07458619845dd72fe75bdfe7c2edd6dc
Kontrol sonucu: D-01 tek-lock refactor'u doğru uygulanmış; UserDataHandler artık production'da execute_with_pending() kullanıyor ve iç içe lock deadlock yolu kapanmış. Ancak execute() idempotency claim'inde yeni, doğrulanmış bir P0/P1 açığı kaldı.

D-03 [P0] execute() pending olmadan _exit_committed claim set etmiyor
Dosya: src/trading/exit_lifecycle.py
Fonksiyon: _execute_locked()

Kanıt kodu
python
if trade.get("_exit_committed"):
    log.warning(
        "[EXIT] %s idempotency guard: _exit_committed=True — tekrar engellendi",
        sym,
    )
    return False
if pending:
    for _k, _v in pending.items():
        trade[_k] = _v
    trade["_exit_committed"] = True
trade["_exit_committed"] = True yalnızca pending truthy olduğunda çalışıyor.

Mekanizma
execute() şu şekilde _execute_locked() çağırıyor:

python
async def execute(self, sym: str, trade: Any, exit_timestamp: int) -> bool:
    _trade_id_key = _trade_identity_key(trade)
    trade_key = f"{sym}_{_trade_id_key}"
    lock = self._exit_locks.setdefault(trade_key, asyncio.Lock())
    async with lock:
        return await self._execute_locked(sym, trade, exit_timestamp)
Bu çağrıda pending=None olur. Dolayısıyla _execute_locked() ilk guard'ı kontrol eder ama claim'i set etmeden REST sorgularına, exit submission'a veya commit'e devam eder.

Tetikleyici senaryo
Aynı trade için normal 1m exit yolu execute(sym, trade, ts) çağırır, yani pending=None.

_execute_locked() _exit_committed değerini True yapmadan position_still_open() ve exit akışına girer.

Aynı trade için ikinci bir çağrı farklı bir callback/coroutine üzerinden gelir.

Per-trade lock çağrıları serialize etse bile ilk çağrı False dönerse veya execution sırasında claim yoksa ikinci çağrı "işlem devam ediyor" bilgisini göremez; aynı trade için tekrar REST close/verify/cleanup akışı çalışabilir.

execute_with_pending() pending dict ile çağrıldığı için yeni WS yollarında flag set ediliyor, fakat normal execute() API'si ve pending={} gibi falsey çağrılar korunmasız kalıyor.

Etki
Idempotency guard tüm exit API yüzeyinde çalışmıyor. Aynı pozisyon için duplicate reduceOnly market close, tekrar REST doğrulama, çift cleanup veya duplicate accounting riski oluşabilir. D-01 refactor'ünün hedefi olan tek ortak exit lifecycle sözleşmesi bozuluyor.

Zorunlu fix
Claim, pending'den bağımsız olarak guard kontrolünden hemen sonra set edilmeli:

python
if trade.get("_exit_committed"):
    log.warning(...)
    return False

trade["_exit_committed"] = True

if pending:
    for _k, _v in pending.items():
        trade[_k] = _v
Daha güvenli çözüm, _claim_exit(trade) helper'ı kullanmak ve _release_exit_claim(trade) çağrısını bütün non-commit dönüş yollarında merkezi hale getirmektir. Confirmed accounting commit sonrası trade zaten service-owned pop ile registry'den çıkarılmalı; False dönüşlerde claim mutlaka bırakılmalı.

pending={} ile pending=None aynı idempotency davranışını vermeli. Pending alanlarının uygulanması claim set edildikten sonra yapılmalı.

Zorunlu regresyon testleri
execute(..., pending=None) çağrısında service REST'e girmeden önce _exit_committed=True claim görülmeli.

execute_with_pending(..., pending={}) aynı claim davranışını vermeli.

İlk çağrı non-commit/False döndüğünde _exit_committed=False kalmalı ve ikinci çağrı tekrar denenebilmeli.

İlk çağrı confirmed commit yaptığında duplicate ikinci çağrı engellenmeli.

Normal bot trailing exit yolu, normalized WS yolu ve legacy WS yolu aynı claim contract'ını kullanmalı.

asyncio.gather() ile aynı trade'e iki exit çağrısı verilmeli; en fazla bir market close/commit gerçekleşmeli.

D-01 sonucu
D-01'in kendisi doğru düzeltilmiş: production wiring execute_with_pending() kullanıyor ve UserDataHandler dış lock tutmuyor. Bu nedenle önceki nested-lock bulgusu kapanmıştır.

Bias/FVG stratejisi sonucu
49f67e6'daki BIAS latch yönü, kullanıcının stratejisiyle uyumlu: ilk CBDR sweep sonrası bias kilitleniyor, sonraki sweep'ler yönü değiştiremiyor, BIAS yönlü FVG aranıyor ve yeni CBDR döngüsünde resetleniyor. Bu strateji bug olarak işaretlenmemeli.

Kabul kriteri
D-03 düzeltildikten sonra test_exit_lifecycle, test_user_data_handler, bot trailing exit integration testleri ve tam suite tekrar çalıştırılmalı. Pre-existing fail sayısı HEAD ile aynı kalmalı; yeni testlerde pending=None, pending={}, False release ve duplicate commit yolları açıkça kapsanmalı.
