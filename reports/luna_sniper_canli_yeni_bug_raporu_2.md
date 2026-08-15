Sniper canlı bug envanteri ve yerel ajan fix planı
Tarih: 2026-08-13 15:42 Europe/Istanbul
Kaynak branch: ahmetonurof-lab/sniper main
Doğrulanan HEAD: 3cdde85e3bc22b2f13af87f23b65916a8b296ba1

1. Kapsam kararı
Önceki canlı raporda bulunan P0/P1 maddeler bu plana dahildir. Ancak kullanıcı tarafından ayrıca verilen 2.3 Ghost Trade / Double-Pop, 2.5 _exit_committed reset eksikleri ve 2.2 Cross-Symbol Sweep Contamination maddeleri önceki raporda yoktu. Güncel main üzerinde doğrulanıp envantere eklendiler.

Backtest bulguları bu planın kapsamına alınmadı. Öncelik canlı pozisyon takibi, CBDR/Bias/Sweep/FVG akışı ve gerçek Binance exit state senkronudur.

2. Doğrulanmış bug envanteri
ID	Öncelik	Durum	Etki alanı
L-01	P0	Doğrulandı	Paylaşılan exit lock registry boşken kopyalanıyor
L-02	P0	Doğrulandı	User-data handler execute() sonucunu yok sayıp koşulsuz pop() yapıyor
L-03	P1	Doğrulandı	_exit_committed duplicate-result ve invalid-fill dönüşlerinde resetlenmiyor
L-04	P1	Doğrulandı	Sweep ID sembol içermiyor, coinler arası dedup çakışması mümkün
L-05	P1	Doğrulandı	Aktif RSM setup'ı sonraki CBDR sweep'iyle daily bias üzerinden bozuluyor
L-06	P1	Doğrulandı	Son 10 FVG cap'i yön filtresinden önce uygulanıyor
L-07	P1	Doğrulandı	BIAS_LOCKED FVG taraması doldurulmuş/invalidated FVG'yi tekrar kullanıyor
L-08	P1	Doğrulandı	Sweep, başarılı entry/protection confirmation'dan önce kalıcı tüketiliyor
L-09	P1	Doğrulandı	Sweep persistence hatası exception yutularak in-memory state'i ilerletiyor
P0 maddeleri deploy öncesi zorunlu. P1 maddeleri aynı fix penceresinde kapatılmalı; L-05/L-06/L-08 düşük işlem sayısını doğrudan etkileyebilecek sinyal kaybı yaratıyor.

L-01 [P0] Boş exit lock registry kopyalanıyor
Dosyalar: src/trading/exit_lifecycle.py, src/trading/user_data_handler.py, src/bot.py

Kanıt
python
self._exit_locks: dict[str, asyncio.Lock] = exit_locks or {}
python
self._exit_locks = exit_locks or {}
python
self._exit_locks: dict[str, asyncio.Lock] = {}
bot.py boş sözlüğü dependency olarak geçiriyor. or {} falsey boş dict için yeni object üretiyor.

Fix
Her iki constructor'da:

python
self._exit_locks = exit_locks if exit_locks is not None else {}
exit_log için de aynı alias-preserving düzeltme yapılmalı. bot.py, ExitLifecycleService, RecoveryManager ve UserDataHandler aynı object identity'yi paylaşmalı.

Test
tests/test_exit_registry_identity.py:

python
locks = {}
service = ExitLifecycleService(..., exit_locks=locks)
handler = UserDataHandler(..., exit_locks=locks)
assert service._exit_locks is locks
assert handler._exit_locks is locks
Ayrıca aynı trade için WS callback ve 1m exit coroutine'lerini asyncio.gather() ile yarıştırıp ikinci lifecycle'ın aynı lock üzerinde beklediği test edilmeli.

L-02 [P0] Ghost Trade / Double-Pop
Dosya: src/trading/user_data_handler.py

Kanıt deseni
Güncel normalized handler'da üç ayrı branch'te aynı yapı bulunuyor:

python
await _exit_trade(
    sym, trade, evt.ts_ms or int(time.time() * 1000)
)
_active_trades.pop(sym, None)
Bu desen hem normal matched fill, REST cross-validated SL/TP fill ve WS fallback akışlarında bulunuyor. Legacy handler'da da aynı çağrı/popup deseni var.

ExitLifecycleService.execute() False dönebiliyor. False, pozisyonun hâlâ açık bırakıldığı stale event, -2021 bekleme, stale cooldown, repair veya duplicate guard yollarını temsil ediyor.

Fix
Her callback noktasında:

python
committed = await _exit_trade(
    sym, trade, evt.ts_ms or int(time.time() * 1000)
)
if committed:
    _active_trades.pop(sym, None)
Ancak daha doğru tasarım: _commit_confirmed_exit() zaten kendi içinde active_trades.pop() yaptığı için callback tarafındaki pop tamamen kaldırılmalı. execute() False döndüğünde trade kesinlikle registry'de kalmalı.

Test
Dört branch için test matrisi:

execute() False, trade ACTIVE, active_trades[sym] korunur.

execute() True ve service kendi pop'unu yapar, callback ikinci pop yapmaz.

stale event sonrası sonraki 1m bar trade'i tekrar görebilir.

-2021 reject sonrası WS FILLED beklenirken recovery trade'i ghost sanmaz.

L-03 [P1] _exit_committed reset eksikleri
Dosya: src/trading/exit_lifecycle.py, execute() ve _commit_confirmed_exit()

Doğrulanan yollar
execute() başında guard set ediliyor:

python
trade["_exit_committed"] = True
Duplicate-result dalı False dönüyor fakat flag'i resetlemiyor:

python
if prev_result == _exit_reason:
    log.warning(...)
    return False
Bu durumda sonraki legitimate retry çağrısı ilk guard'da takılır:

python
if trade.get("_exit_committed"):
    ...
    return False
İkinci doğrulanan yol _commit_confirmed_exit() içindeki invalid fill branch'idir. Entry/exit fiyatı veya qty geçersiz olduğunda trade STATUS_BROKEN_MANUAL_INTERVENTION_REQUIRED durumuna alınır ve False dönerken _exit_committed temizlenmiyor.

Fix
Duplicate-result dalında:

python
trade["_exit_committed"] = False
return False
Invalid-fill/manual-intervention branch'inde de aynı reset yapılmalı. Daha güvenli yaklaşım: execute() içinde try/finally kullanmak, yalnızca confirmed accounting commit sonrası flag'i True bırakmak; tüm non-commit çıkışlarda merkezi _release_exit_claim(trade) çağırmak.

Test
tests/test_exit_lifecycle.py:

Aynı trade/result duplicate çağrısı False döner ve _exit_committed is False kalır.

Invalid fill branch'i False döner ve flag False kalır.

Sonraki recovery/manual retry execute()'a girebilir.

Confirmed commit sonrası flag True veya trade registry'den silinmiş olur.

Her return False path'i parametrik olarak taranmalı.

L-04 [P1] Cross-symbol sweep dedup collision
Dosyalar: src/state_manager.py, src/retrace_state.py, src/trading/signal_engine.py

Kanıt
python
def is_sweep_used(sweep_id: str) -> bool:
python
sweep_id = f"{direction}_{bar_index}"
if is_sweep_used(sweep_id):
python
self._pending_sweep_id = (
    f"{direction}_{bar_index}" if bar_index is not None else None
)
signal_engine.py progress_rsm(..., symbol=...) alıyor fakat self.rsm.on_sweep(...) çağrısına symbol geçmiyor. bar_index her symbol için lokal olduğundan aynı direction/index başka coinlerde aynı persistence key'e düşebilir.

Fix
RetraceStateMachine.on_sweep() imzasına symbol eklenmeli:

python
def on_sweep(
    self,
    direction: Literal["bullish", "bearish"],
    level: float,
    bar_index: int | None = None,
    symbol: str = "",
):
ID tek helper'dan üretilmeli:

python
def _sweep_id(symbol, direction, bar_index):
    return f"{symbol}_{direction}_{bar_index}"
signal_engine.py çağrısı symbol=symbol geçirmeli. _pending_sweep_id, is_sweep_used, mark_sweep_used ve test fixture'ları aynı formatı kullanmalı. Eski formatlı state kayıtları migration veya ignore policy ile ele alınmalı.

Test
İki farklı symbol aynı direction ve bar index ile sweep alır. İlk coin mark edilir, ikinci coin is_sweep_used() ile reddedilmemelidir. Restart sonrası her coin kendi sweep'ini bir kez tüketebilmelidir.

L-05 [P1] Aktif RSM setup'ı sonraki CBDR sweep'iyle bozuluyor
Dosyalar: src/session.py, src/trading/signal_engine.py, src/retrace_state.py

Kanıt
python
if cbdr.locked and not cbdr.sweep_confirmed:
    cbdr.check_sweep(high, low, close, atr)
python
self.sweep_direction = "bearish"
self.daily_bias = DailyBias.BEARISH
python
if not htf_fvgs:
    ...
    return # sweep hala gecerli, bir sonraki bar'i bekle — RESET YOK
RSM SWEEP_DETECTED olarak eski yönde beklerken SessionState yeni sweep ile daily_bias ve yön alanlarını değiştirebiliyor. Sonraki trigger eski RSM yönü ile yeni bias arasında çelişiyor ve evaluate_trigger() resetliyor.

Fix
Bias/sweep sahipliği ayrıştırılmalı:

SessionState ilk geçerli CBDR bias'ını günlük latch olarak korumalı.

RSM SWEEP_DETECTED veya BIAS_LOCKED iken yeni sweep mevcut setup'ı ezmemeli.

Yeni yön ancak yeni CBDR cycle resetinde veya açık bir bias_conflict_reset kuralında kabul edilmeli.

daily_bias, sweep_direction ve RSM direction için tek kaynak veya açık snapshot kullanılmalı.

Test
Bullish setup + no FVG.

Sonraki ters sweep.

daily_bias ve RSM direction korunuyor.

Bullish FVG geldiğinde trigger mümkün oluyor.

Yeni CBDR cycle'da bias resetleniyor ve ters yön kabul ediliyor.

L-06 [P1] Son 10 FVG cap'i yön filtresinden önce
Dosya: src/retrace_state.py:42-58

Kanıt
python
levels = [HTFFVG(f.top, f.bottom, f.direction, f.real_index) for f in fvgs]
levels.sort(key=lambda x: x.bar_index)
return levels[-10:] if len(levels) > 10 else levels
Kullanımda yön filtresi daha sonra çalışıyor:

python
for fvg in reversed(htf_fvgs):
    if fvg.direction != self.direction:
        continue
Fix
Önce direction filter, sonra cap:

python
levels = [
    HTFFVG(...)
    for f in fvgs
    if f.direction == direction
]
return levels[-10:]
Daha iyi çözüm: scan_htf_fvgs(..., direction=None) parametresiyle yön filtresini fonksiyon içine almak ve çağıranların yön uyumlu aday sayısını loglamak.

Test
11 aday: 1 eski bullish, 10 yeni bearish. Bullish bias lock altında eski bullish FVG görünür ve trigger olur. Aynı fixture sweep confirmation için de çalıştırılmalı.

L-07 [P1] BIAS_LOCKED stale/filled FVG reuse
Dosya: src/retrace_state.py:118-158

Kanıt
python
if fvg.bar_index <= self._locked_from_bar:
    continue
if fvg.bar_index >= current.index:
    continue

if self.direction == "bullish":
    wick_touched = current.low <= fvg.top
    body_broke_down = current.close < fvg.bottom
else:
    wick_touched = current.high >= fvg.bottom
    body_broke_down = current.close > fvg.top
Kod yalnızca lock sonrası oluşma, current öncesi oluşma ve current wick/body kontrolü yapıyor. Formation ile current arasındaki barlarda FVG'nin doldurulup doldurulmadığını kontrol etmiyor.

Fix
Tek ortak helper kullanılmalı:

python
fvg_is_alive(direction, top, bottom, formation_index, bars)
Formation sonrası her kapalı bar taranmalı. Gap touch/fill veya far-side invalidation varsa aday reddedilmeli. İlk başarılı retest'te FVG consumed olarak işaretlenmeli.

Test
FVG oluşur, ara bar gap içine girer, sonra current bar tekrar wick atar. Beklenen state BIAS_LOCKED, TRIGGER_READY değil. Ayrıca far-side close ve bullish/bearish simetrisi test edilmeli.

L-08 [P1] Sweep, entry/protection success'tan önce tüketiliyor
Dosya: src/retrace_state.py:72-85, 220-241

Kanıt
python
self.state = RetraceState.TRIGGER_READY
self.trigger_fvg = fvg
self._mark_sweep_used()
python
def _mark_sweep_used(self):
    if self._pending_sweep_id is not None:
        try:
            from state_manager import mark_sweep_used
            mark_sweep_used(self._pending_sweep_id)
        except Exception:
            pass
    self._pending_sweep_id = None
Sweep trigger-ready aşamasında disk'e tüketilmiş yazılıyor. Entry daha sonra risk/router/qty/API/SLTP validation'da reddedilirse sweep geri alınmıyor.

Fix
Sweep lifecycle üç faza ayrılmalı:

detected/pending

triggered but uncommitted

entry_committed

mark_sweep_used() yalnızca başarılı entry ve gerekli protection confirmation sonrasında çalışmalı. Entry başarısızsa pending sweep ID korunmalı veya açık bir mark_sweep_retryable() yolu çalışmalı.

Test
Trigger-ready → execute_live_entry False → aynı sweep/FVG retry edilebilir ve persistence key tüketilmiş görünmez. Successful entry → exactly-once consume.

L-09 [P1] Sweep persistence hatası sessiz yutuluyor
Dosya: src/retrace_state.py:72-85

Kanıt
python
try:
    from state_manager import mark_sweep_used
    mark_sweep_used(self._pending_sweep_id)
except Exception:
    pass
self._pending_sweep_id = None
Fix
mark_sweep_used() bool veya typed result döndürmeli. False/exception durumunda pending ID temizlenmemeli; warning/critical event yazılmalı. Restart reconciliation, in-memory pending state ve disk state'i karşılaştırmalı.

Test
mark_sweep_used exception verir: pending ID korunur, event log oluşur, sonraki retry'da tekrar denenir. Disk write failure testinde process state ile persistence state ayrışmamalı.

3. Uygulama sırası
Faz 0: Güvenlik kilidi
Yeni canlı deploy yok.

WS_EVENT_NORMALIZATION_ENABLED ile legacy/normalized iki handler yolu ayrı ayrı test edilmeden flag değiştirilmez.

Mevcut açık pozisyonlar için deploy öncesi Binance position + openAlgoOrders snapshot alınır.

Faz 1: Exit state güvenliği
L-01: alias-preserving constructor fix.

L-02: dört callback pop noktasını düzelt veya tamamen service-owned pop'a indir.

L-03: _exit_committed merkezi claim/release helper'ı.

Exit lifecycle testlerini önce çalıştır, sonra user-data tests.

Faz 2: Sweep kimliği ve persistence
L-04: symbol-aware sweep ID.

Legacy state migration.

L-08: consume-on-entry-commit.

L-09: persistence result/error handling.

Faz 3: Bias ve FVG sinyal akışı
L-05: bias latch/RSM ownership.

L-06: direction-before-cap.

L-07: common FVG lifecycle helper.

L-05 → L-07 integration tests.

Faz 4: Canlı doğrulama
Paper mode'da en az bir tam CBDR cycle.

Her symbol için bias, rsm_direction, sweep_id, fvg_candidates_total, fvg_same_direction, fvg_rejected_stale, entry_rejected_after_trigger, sweep_consumed_after_entry eventleri.

Açık pozisyon varken WS FILLED, stale, -2021, recovery tick ve restart senaryoları.

4. Kabul kriterleri
Hiçbir execute() == False yolu active_trades kaydını silmez.

Aynı exit_locks object'i bot, service, recovery ve user handler arasında paylaşılır.

Aynı direction/bar index farklı symbol'lerde birbirini engellemez.

Trigger-ready olup entry reddedilen sweep retry edilebilir.

BIAS_LOCKED modunda ters CBDR sweep mevcut bias/RSM snapshot'ını ezmez.

Son 10 ters FVG, eski uyumlu FVG'yi gizlemez.

Doldurulmuş veya invalidated FVG tekrar trigger üretemez.

Legacy ve normalized WS handler'ları aynı state transition contract'ını sağlar.

Yeni testler ve mevcut exit/recovery/signal/retrace suite'leri yeşil olmadan deploy yapılmaz.

Kullanıcı stratejisi hakkında kesin karar
Bu rapor şu stratejiyi bug olarak işaretlemiyor: CBDR range sonrası oluşan ilk geçerli sweep ile günlük BIAS kilitlenir; BIAS kilitlendikten sonra gün sonuna kadar yalnızca BIAS yönündeki FVG aranır ve yeni CBDR sweep gerekmez. Bu davranış bilinçli strateji kuralıdır ve backtest ile paper trade arasında korunmalıdır.

Ancak mevcut canlı kod bu kuralı tam uygulamıyor. SessionState.update() içinde sweep_confirmed temizlendikten sonra cbdr.check_sweep() yeniden çağrılabiliyor; CBDRState.check_sweep() de daily_bias, sweep_direction ve sweep_level alanlarını yeni sweep ile değiştirebiliyor. Bu nedenle L-05 bir strateji tercihi değil, mevcut kodun kullanıcının istediği günlük bias latch davranışına aykırı olmasıdır. Fix, ilk geçerli sweep sonrası BIAS'i aynı CBDR döngüsü boyunca immutable/latch yapmak ve sonraki sweep'leri sinyal üretmeden loglayarak yok saymaktır. Yalnızca yeni CBDR döngüsünün resetinde BIAS NEUTRAL yapılmalı.

L-05 için doğru fix sözleşmesi
İlk geçerli CBDR sweep: daily_bias, sweep_direction, sweep_level, bias_locked=True.

Aynı CBDR döngüsünde sonraki sweep: mevcut bias alanlarına yazma yok, sweep_confirmed yeniden set etme yok; yalnızca gözlem/event log.

SignalEngine.progress_rsm() BIAS_LOCKED durumunda yeni sweep istememeli; RetraceStateMachine.on_bias_fvg() yalnızca kilit yönünde FVG aramalı.

Yeni döngü başlangıcında reset_for_new_cycle() bias latch'i temizlemeli.

Test: ilk bullish sweep, sonra bearish sweep, sonra bullish FVG. Beklenen: bias bullish kalır ve FVG trigger olabilir.

Bu nedenle L-05 rapordan çıkarılmadı; başlığı ve fix yönü düzeltilerek "yanlış strateji" değil, "istenen BIAS latch sözleşmesinin canlı kodda eksik uygulanması" olarak tutuldu.
