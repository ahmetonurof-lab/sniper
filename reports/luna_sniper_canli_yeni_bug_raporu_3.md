Sniper 3a88319 sonrası eksik fix direktifi
Kontrol edilen commit: 3a8831937e464b7393e7ce05182c411ce6c17441
Sonuç: L-01…L-09'un ana fixleri uygulanmış; fakat commit sonrası canlı akışta 2 yeni/doğrulanmış eksik kaldı. Deploy öncesi kapatılmalı.

D-01 [P0] UserDataHandler aynı exit lock'u tutarken execute() aynı lock'u tekrar alıyor
Dosya: src/trading/user_data_handler.py
Akış: normalized matched-fill, normalized REST cross-check, normalized WS_FALLBACK ve legacy karşılıkları
İlişkili: src/trading/exit_lifecycle.py:execute

Kanıt
UserDataHandler içinde her exit branch'i şu deseni kullanıyor:

python
lock = _exit_locks.setdefault(trade_key, asyncio.Lock())
async with lock:
    trade["pending_exit_price"] = price
    trade["pending_exit_qty"] = cum_qty
    trade["pending_exit_order_id"] = oid_c or oid_i
    trade["pending_exit_timestamp"] = evt.ts_ms
    trade["result"] = result
    await _exit_trade(
        sym, trade, evt.ts_ms or int(time.time() * 1000)
    )
_exit_trade bot tarafından ExitLifecycleService.execute'a bağlıdır. execute() aynı key ile tekrar lock alıyor:

python
_trade_id_key = _trade_identity_key(trade)
trade_key = f"{sym}_{_trade_id_key}"
lock = self._exit_locks.setdefault(trade_key, asyncio.Lock())
async with lock:
Mekanizma
asyncio.Lock reentrant değildir. UserDataHandler lock'u tuttuğu sırada await _exit_trade(...) çağırıyor. ExitLifecycleService.execute() aynı registry ve aynı key üzerinden aynı lock'u tekrar almaya çalışıyor. Coroutine kendi tuttuğu lock'un serbest bırakılmasını beklediği için ilerleyemiyor.

L-01 fix'i registry identity'sini düzelttiği için bu artık teorik yarış değil, aynı lock paylaşımı aktif olduğunda deterministik deadlock yoludur. Önceden registry'lerin yanlışlıkla ayrılması bu deadlock'u gizleyip yarış üretirken, L-01 doğru registry paylaşımını sağlayınca nested acquire görünür hale geldi.

Tetikleyici
WS_EVENT_NORMALIZATION_ENABLED=True iken SL/TP FILLED event'i gelir.

UserDataHandler trade key lock'unu alır.

Pending exit alanlarını yazar.

await _exit_trade() çağrılır.

ExitLifecycleService.execute() aynı trade key lock'unu almaya çalışır.

Lock sahibi olan aynı callback kendi lock'unu bırakmadan bekler.

Aynı durum normalized REST cross-check, normalized WS_FALLBACK ve legacy matched/cross-check/FALLBACK branch'lerinde de vardır.

Etki
WS exit callback'i kilitlenir; trade kapanışı, muhasebesi, cleanup'ı ve registry'den çıkarılması tamamlanamaz. Pozisyon borsada kapanmış olsa bile bot state'i EXIT/ACTIVE geçişinde takılabilir; protection cleanup ve recovery davranışı bozulur.

Zorunlu fix yönü
Tek bir owner seçilmeli. Önerilen yaklaşım: UserDataHandler dış lock'u kaldırmalı, yalnızca pending alanlarını yazmayı ExitLifecycleService.execute() içindeki ortak lock'a bırakmalı. Bunun için pending mutation + execute çağrısı service API'si içinde atomik yapılabilir veya handler lock almadan execute() çağrılabilir.

Alternatif olarak execute()'a lock_held=True iç API parametresi eklenebilir; fakat yanlış caller'da kullanılırsa güvenlik açığı yaratır. Tercih edilen çözüm nested lock'u tamamen ortadan kaldırmak.

Regresyon testleri
tests/test_user_data_handler.py içine:

python
async def test_normalized_matched_fill_does_not_nested_acquire_same_lock():
    # shared exit_locks + real ExitLifecycleService stub
    # callback tamamlanmalı, timeout olmamalı
    await asyncio.wait_for(callback(msg), timeout=1.0)
Aynı test dört normalized ve dört legacy exit branch'i için parametrik çalışmalı. asyncio.wait_for(..., timeout=1) zorunlu; yalnızca mock call assertion deadlock'u yakalamaz.

D-02 [P1] L-09 pending sweep koruması, lock_bias() tarafından tekrar siliniyor
Dosya: src/retrace_state.py
Dosya: src/bot.py:_try_entry

Kanıt
L-09 persistence hatasında pending ID'nin korunacağı söyleniyor:

python
except Exception:
    logger.warning(
        f"[RST] sweep persistence hatasi (pending ID korunuyor): "
        f"{self._pending_sweep_id}",
        exc_info=True,
    )
    return False
self._pending_sweep_id = None
return True
Entry başarı hattı bunu çağırıyor, fakat dönüş değerine bakmadan bias lock'a devam ediyor:

python
if not rsm.confirm_entry_success():
    log.warning(
        f"[BOT] sweep persistence tuketim hatasi (sym={sym}) — "
        f"sweep kaydi diskte kalabilir"
    )
# Bias Kilit Modu
rsm.lock_bias(bar_index=current.index)
lock_bias() pending sweep ID'yi temizliyor:

python
self._pending_sweep_id = None
Mekanizma
_consume_sweep() persistence exception aldığında _pending_sweep_id bellekte bırakıyor ve False dönüyor. Ancak aynı _try_entry akışında hemen sonra lock_bias() çağrılıyor; lock_bias() bu ID'yi koşulsuz siliyor. Yani L-09'un "pending ID korunuyor, sonraki retry'da tekrar denenir" sözleşmesi aynı başarılı entry akışında bozuluyor.

Tetikleyici
Sweep + FVG trigger-ready olur.

Entry ve protection başarılı olur.

confirm_entry_success() çağrılır.

mark_sweep_used() disk/filelock/JSON hatası verir.

_consume_sweep() False döner, pending ID hâlâ vardır.

lock_bias() çağrılır ve pending ID'yi siler.

Diskte sweep consumed kaydı yoktur, bellekte de retry bilgisi kalmaz.

Etki
Restart sonrası aynı sweep'in tekrar işlenmesi veya günlük dedup state'inin eksik kalması mümkün olur. Başarılı trade açıldığı için mevcut pozisyon doğrudan kaybolmaz; fakat persistence exactly-once garantisi bozulur ve sonraki CBDR/restart akışı yanlış state ile başlayabilir.

Zorunlu fix yönü
lock_bias() sweep persistence durumunu yok etmemeli. Önerilen seçenekler:

confirm_entry_success() başarısızsa pending ID'yi ayrı bir _unconsumed_sweep_id alanına taşıyıp lock bias sonrasında da korumak ve periyodik retry yapmak.

Başarılı entry sonrası _consume_sweep() için sınırlı retry/backoff uygulamak; başarı olmadan lock_bias() çağrısını tamamlamamak.

En sağlam çözüm: sweep tüketimini SessionState/state manager tarafında idempotent bir consume_after_entry_commit(symbol, sweep_id) transaction'ına taşımak.

Sadece lock_bias() içindeki satırı silmek yeterli değil; başarılı persistence sonrası pending ID'nin temizlenmesi, hata sonrası retry ve restart reconciliation birlikte test edilmeli.

Regresyon testleri
mark_sweep_used exception verir: confirm_entry_success() is False, lock_bias() sonrası retry ID hâlâ korunur.

Sonraki retry başarılı olur: ID temizlenir ve state dosyasında symbol-scoped sweep key bulunur.

Entry başarılı + persistence başarısız + process restart: aynı sweep yeniden işlem açamaz veya açıkça retryable state olarak restore edilir.

lock_bias() eski unit testleri: successful consume sonrası pending None, failed consume sonrası pending preserved.

Kabul kriterleri
UserDataHandler callback'lerinin hiçbiri aynı asyncio.Lock üzerinde nested acquire yapmaz.

Normalized ve legacy WS callback'leri 1 saniye timeout altında tamamlanır.

execute() == False trade'i silmez; execute() == True service-owned commit ile tamamlanır.

Sweep persistence exception sonrası pending identity lock_bias() tarafından kaybedilmez.

Symbol-scoped sweep ID, entry commit ve restart reconciliation aynı key formatını kullanır.

Bu iki düzeltmeden sonra exit, user-data, retrace, signal, session ve recovery suite'leri yeniden çalıştırılır; pre-existing fail sayısı HEAD ile karşılaştırılır.

Son karar
L-01…L-09'un ana yönü doğru ve büyük bölümü gerçekten uygulanmış. Ancak D-01 P0 nested-lock deadlock ve D-02 P1 L-09 pending-ID kaybı kapatılmadan 3a88319 canlıya güvenli kabul edilmemeli.
