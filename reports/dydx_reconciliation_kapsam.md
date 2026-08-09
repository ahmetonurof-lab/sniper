# DYDX Reconciliation — KAPSAM RAPORU

> **Tarih:** 2026-08-09
> **Durum:** KAPSAM NETLEŞTİRİLDİ (kod değişikliği YOK)
> **Kaynak:** `reports/runtime_status_senkronizasyon_kapsam_3.md` — "Sıradaki: DYDX reconciliation kapsamını netleştir (önceki turdaki gibi bir kapsam raporu, kod değişikliği yok)"

---

## 1. Bağlam: DYDX reconciliation nedir

**Kök olay (2026-08-02 21:42:30):** DYDXUSDT entry'de Binance **HTTP 408 timeout** döndü — emir durumu belirsiz ("execution status unknown"). Bot emri başarısız sayıyor ama emir sunucuda dolmuş olabilir → pozisyon korumasız ve takipsiz kalma riski.

**Çözüm (commit `bc3f3ff`, 2026-08-03):** `entry_manager.py execute_live_entry()` içine **MARKET empty_response reconcile guard** — `mkt_id` yok + `actual_qty <= 0` (cevap boş) + pozisyon açık → `_emergency_close()`.

---

## 2. Yürütme sırası ve kapsama matrisi

`execute_live_entry()` — MARKET emri sonrası (480-581):

```python
mkt_resp = await self._rest.place_market_order(...)      # :480
actual_qty, actual_price, quote_qty = self.parse_market_fill(mkt_resp)  # :486
mkt_id = extract_order_id(mkt_resp)                      # :487
```

| Blok | Koşul | Aksiyon | Kanıt |
|---|---|---|---|
| **A** `:489` | `not mkt_id and actual_qty > 0 and actual_price > 0` — orderId yok ama fill bilgisi var | `get_positions()` → poz açıksa `_emergency_close()` | bc3f3ff'ten ÖNCE de vardı |
| **B** `:520` | `not mkt_id and actual_qty <= 0` — **cevap boş (408/-1007)** | `get_positions()` → poz açıksa `_emergency_close()` | **bc3f3ff'in eklediği blok** |
| **C** `:550` | `not mkt_id or actual_qty <= 0 or actual_price <= 0` — kalan belirsizlik | `mkt_id` varsa 1.5s bekleyip `get_positions()` ile geçikmeli fill dener; hâlâ yoksa `MARKET BASARISIZ` | mevcut |

**Destekleyici davranışlar (doğrulandı):**
- `parse_market_fill({})` → `(0.0, 0.0, 0.0)` — empty response güvenli döner, Blok B'ye ulaşır (238-240).
- `_emergency_close()` (345-402): karşı taraf reduce_only market emri (`emergency-{sym}-...`), EMERGENCY_CLOSE_STARTED/COMPLETED/FAILED event'leri loglar. Dönüş `success` = KAPATMA isteminin gönderildiği, entry'nin başarılı olduğu değil (docstring).
- Blok A/B başarılı reconcile sonrası `success=False` döner → caller trade'i state'e KAYDETMEZ (kapatılan pozisyon bot için hiç girmemiş gibi).

---

## 3. Kapsanan senaryolar

| Senaryo | Sonuç |
|---|---|
| HTTP 408 / `-1007` → cevap `{}` + pozisyon aslında açıldı | ✅ Blok B: pozisyon bulunur, `_emergency_close` ile kapatılır; trade state'e girmez |
| Cevap `{}` + pozisyon gerçekten açılmadı | ✅ Blok B boş geçer → Blok C → `MARKET BASARISIZ — empty_response` (success=False) |
| orderId yok + fill qty/fiyat var | ✅ Blok A: aynı reconcile |
| orderId var + qty 0 (timeout ama orderId geldi) | ✅ Blok C: 1.5s bekleyip `get_positions()` ile geçikmeli fill tespiti |

**Test kanıtı (bc3f3ff + mevcut):** `tests/test_entry_manager.py` — `test_market_empty_response_pos_open_emergency_close` (empty_response + poz açık → SELL emergency close, 2 market emri çağrısı) ve `test_market_order_failure` (pozisyon yok → `MARKET BASARISIZ`). Çalıştırıldı: **2 passed / 92 deselected**.

---

## 4. Kapsam DIŞI / boşluklar (kod değişikliği yok — not olarak)

1. **Canlı teyit YOK:** fix 08-03'te deploy edildi; sonrasında DYDXUSDT'de yeni 408/empty_response olayı gözlemlenmedi → Blok B'nin canlı çalıştığına dair event kanıtı henüz yok (pasif iz; tetikleyici olay gelirse `EMERGENCY_CLOSE_*` event'leri ve `[MARKET] ... cevap yok ama pozisyon acik` log satırı beklenir).

2. **Köşe durumu — fill qty var ama fiyat yok:** `not mkt_id and actual_qty > 0 and actual_price <= 0` (ör. response'ta `executedQty` var ama `avgPrice`/`cumQuote` hiçbiri gelmedi, parse_market_fill 241-254 fallback'leri boş) → **Blok A çalışmaz** (`actual_price > 0` şartı), **Blok B çalışmaz** (`actual_qty <= 0` şartı), Blok C'de `mkt_id` yok → geçikmeli fill denenmez → `MARKET BASARISIZ` + **pozisyon aslında açıksa korumasız kalır**. Teorik boşluk (parse fallback'leri genelde fiyatı kurtarır); tek nokta düzeltme adayı: Blok B koşulunu `not mkt_id and (actual_qty <= 0 or actual_price <= 0)` yapmak. **Bu turda DEĞİŞTİRİLMEDİ** (kod değişikliği yok direktifi).

3. **`_emergency_close` başarısız senaryosu:** pozisyon açık kalır, trade state'e girmez → korumasız pozisyon. Periyodik `RecoveryManager.recover_positions` (60 sn) borsadaki pozisyonu bulup **koruma (SL/TP) kurar ama KAPATMAZ** — pozisyon açık kalır. Kapsam dışı; iz olarak not.

4. **Hedge-mod çift pozisyon:** Blok A/B `pos_amt = abs(...)` — aynı sembolde long+short varsa ilki eşleşir; bot birleşik modda çalıştığı için pratik risk yok.

---

## 5. Kapsam dışı bırakılanlar (dokunulmadı)

- `reconcile_ghost_positions()` / `recover_positions()` (restart + periyodik katmanlar) — DYDX fix'i bunlardan bağımsız, entry pipeline'ına özgü.
- `reconcile_orphan_orders()` (orphan STOP/TP temizliği) — ayrı konu (P1-4).
- `order_manager` / `exit_lifecycle` — bu fix yalnızca `entry_manager.execute_live_entry`.

---

## 6. Açık kalan izler

| İz | Durum |
|---|---|
| Blok B canlı tetikleyici beklentisi | pasif — `EMERGENCY_CLOSE_*` event / `[MARKET] cevap yok ama pozisyon acik` log |
| Köşe durumu (qty>0, price<=0) | not — ileride tek nokta fix adayı (Blok B koşul genişletmesi) |
| `_emergency_close` fail → pozisyon açık | not — `recover_positions` korur (kapatmaz) |
| DYDXUSDT günlük canlı iz | pasif — 08-02 olayından sonra yeni 408 yok |
