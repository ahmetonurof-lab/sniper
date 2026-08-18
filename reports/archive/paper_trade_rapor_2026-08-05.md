# Paper Trade Olumsuzluk Raporu — 2026-08-05

Kaynaklar: `sniper/output/paper_trade.log` (5730+ satır), `sniper/output/trades_history.jsonl`,
sniper repo commit geçmişi (HEAD `c3d4c6e`).

## 1. Özet

Log'da üç olumsuzluk deseni tespit edildi; yalnızca biri gerçek bir operasyonel hata
(SUIUSDT -2021), o da P1-15 bilinen yarışının guard'lı bir örneği. Diğer ikisi gürültü/kalıntı.

## 2. Ana Bulgu: SUIUSDT -2021 STOP_MARKET reject

### Log (satır 4202–4227) zaman çizelgesi

| Zaman (04:40) | Kaynak | Olay |
|---|---|---|
| 00,818 | sniper.paper | `[P1-15_DEBUG] SUIUSDT check_exit oncesi: current.high=0.6928 trade_sl=0.6931 current.ts=1785893940000` |
| 03,211–212 | sniper.user_data | `[WS-ORDER] SUIUSDT NEW → PARTIALLY_FILLED → FILLED` id=`5XPDpG6zLMRzzC6tE4oh3M` |
| 04,448 | sniper.exit_lifecycle | `[EXIT] SUIUSDT SL stale event #1 — pozisyon hala acik, exit iptal` |
| 04,916 | sniper.exit_lifecycle | `[EXIT] SUIUSDT koruma eksik (sl=False tp=True) — onariliyor` |
| 05,883 | nexus.live | `[SL] SUIUSDT STOP_MARKET hatasi: HTTP 400: {"code":-2021,"msg":"Order would immediately trigger."}` |
| 05,883 | sniper.order_manager | `[ORDER] SUIUSDT -2021 immediately trigger reject kaydedildi — pozisyon zaten dolmus, WS FILLED bekleniyor` |
| 05,883 | sniper.order_manager | `[REPAIR] SUIUSDT SL -2021 immediately trigger — pozisyon zaten dolmus, repair atlaniyor` |

### trades_history.jsonl teyidi (satır 416)

- SUIUSDT **short**, entry 0.69, SL 0.6931, TP 0.6845, qty 2327, `result: "SL"`, **pnl: -8.82**
- `sl_order_id: 1000000156769271` → log'daki `[POST_ENTRY_DEBUG] raw_ids=['1000000156769271']` ile eşleşiyor
- `exit_order_id: ""` + `exit_actual_qty: 0.0` → kapanış **WS FILLED event'iyle** yapıldı (REST emir sorgusuna gerek kalmadı)
- `trade_sl=0.6931` (P1-15_DEBUG) = kayıttaki `sl` → **trade birebir eşleşiyor**

### Değerlendirme

P1-15 bilinen yarışının **guard'lı** bir örneği:

1. SL STOP_MARKET borsada fiziksel olarak doldu (04:40:03 WS FILLED — kapanış fiyatı 0.6931 = SL).
2. WS FILLED gelmeden önceki `check_exit`/stale kontrolleri pozisyonu hâlâ açık gördü → "stale event #1" ve "koruma eksik" onarımı tetiklendi.
3. Onarımın yerleştirmeye çalıştığı STOP_MARKET -2021 aldı (pozisyon zaten kapalı).
4. **Guard çalıştı:** reject kaydedildi (`had_immediately_trigger`), repair atlandı, trade WS FILLED ile kapandı. Zarar (-8.82) meşru SL zararı; çift zarar/yanlış pozisyon oluşmadı.

Bu, `ed024c3` / `d5331fa` commit'lerindeki mitigasyonun koşuda doğru çalıştığının canlı kanıtıdır.

## 3. İkincil Bulgu: ARBUSDT stale tekrarı + recovery kalıntıları

- `[EXIT] ARBUSDT TP stale event #1/#2/...` tekrarlı deseni (WS gecikmesiyle aynı P1-15 ailesi).
- `[ORPHAN] ARBUSDT emir iptal edildi`, `[RECOVER] GMXUSDT icin Binance uzerinde SL/TP emirleri olusturuldu` → recovery/orphan akışı çalışıyor, operasyonel hasar yok.

## 4. Gürültü: WARNING seviyesindeki debug logları

- `[P1-15_DEBUG] <sym> check_exit oncesi: ...` (sniper.paper) — ENA/ADA/ARB/ONDO/SUI/SOL/TIA/GMX vb. her çevrimde tekrarlıyor.
- `[POST_ENTRY_DEBUG] ...` (sniper.order_manager) — giriş sonrası emir sorgusu.
- İkisi de `WARNING` seviyesinde ama **debug amaçlı**; gerçek hata değil. `progress.md` madde 5'te `DEBUG`'a çekilmesi zaten kayıtlı, henüz yapılmadı.

## 5. Commit Geçmişi Bağlantısı (sniper repo)

- `ed024c3` — fix: P1-15 stale event mitigation (-2021 sinyal, cooldown, GMXUSDT SL genişletme)
- `d5331fa` — fix: -2021 reject gürültüsüne hedefli guard'lar (WS-REPAIR + 60s recover loop; `user_data_handler.py`, `recovery_manager.py`)
- `f1ef618` — docs: P1-15 mitigation memory-bank güncellemesi
- `b6f2473` — docs: trailing kilitlenme (`identical_invalid_candidate_suppressed`), STRKUSDT -4005, GMX çift koruma
- `c3d4c6e` (HEAD) — feat: WS handler primary pop after FILLED + tests
- Kök neden analizi: commit `61ab07d` (sonnet/memory-bank) — WS FILLED 87–353 sn gecikmeli; -2021 en erken "pozisyon doldu" kanıtı.

## 6. Sonuç

- **Gerçek operasyonel hata:** yok (SUIUSDT -2021 yarışı guard'lı ve zararsız kapandı).
- **Guard etkinliği:** doğrulandı — `-2021` reject → repair atlama → WS FILLED kapanış zinciri beklendiği gibi çalıştı.
- **Yapılacaklar (öneri):** `P1-15_DEBUG` ve `POST_ENTRY_DEBUG` loglarını `DEBUG` seviyesine indirmek (progress.md madde 5).
