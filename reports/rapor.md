Değişiklik
src/trading/entry_manager.py:520 — Blok B koşulu: not mkt_id and actual_qty <= 0 → not mkt_id and (actual_qty <= 0 or actual_price <= 0). Köşe durumu (qty>0 ama price<=0, mkt_id yok) artık reconcile'a düşüyor → poz açıksa _emergency_close.
Proaktif çakışma kontrolü (direktif gereği, koda geçmeden): Blok A (qty>0 AND price>0) ile yeni Blok B (qty<=0 OR price<=0) karşılıklı dışlayıcı — çakışma yok, sıra değişmedi.
tests/test_entry_manager.py:1051 — yeni test_market_qty_no_price_pos_open_emergency_close: entry resp {"executedQty":"0.5"} → parse_market_fill=(0.5, 0.0, 0.0), poz açık → SELL emergency close doğrulanır.
Kanıt
3 reconcile testi (yeni + empty_response + market_order_failure): 3 passed / 92 deselected
test_entry_manager tamamı: 95 passed / 0 failed
integration_lifecycle + models + recovery: 69 passed; test_bot: 13 failed / 32 passed (13 pre-existing, 0 yeni)
Commit + Push
8d4f954 → origin/main (3a7cafc..8d4f954)

Sıradaki
P1-4 (ghost temizliğini periyodikleştirme) + SEIUSDT kapanışında runtime.status canlı teyidi + P2-8 + PRE-ENTRY iz.
