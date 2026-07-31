Denetim tamamlandı. Canlı kodu satır satır dolaştım; sonuçlar kritik:

Kural Parite Denetimi — canlı vs analyzer_v5
Giriş (Entry) tarafı
#	Kural	Canlı	Backtest	Durum
E1	RSM state machine	RetraceStateMachine	ortak sınıf	✅ aynı
E2	Sweep invalidation	RSM içinde	RSM içinde	✅ aynı
E3	FVG taraması + min size	scan_htf_fvgs(lookback=100, min=atr×FVG_SIZE_MAP[sym])	ortak RSM	✅ aynı
E4	wick_touched + body_broke_down	RSM	RSM	✅ aynı
E5	close-inside-FVG onayı	devre dışı (retrace_state.py:210)	trailing'de kullanıyor	⚠
E6	is_fvg_valid 45-bar expiry	VAR (bot.py:451)	YOK	⚠ senin kararın: canlıdan kaldır
E7	Bias filtre	evaluate_trigger	bias_reject	✅ aynı
E8	Session filtre	global: LONDON 2-13 + NEWYORK 13-22, CBDR 22-2 blok	coin-bazlı: REAL_CBDR 19-1, ASIA 1-5	⚠ FARKLI — REAL_CBDR coinlerinde 19-21 saatleri canlıda açık, backtestte kapalı
E9	should_trade (poison)	✅	✅	✅
E10	cbdr_mult==0	✅	✅	✅
E11	weekend_bonus	YOK (canlıda uygulanmıyor)	var (hepsi False → etkisiz)	⚠ ölü kod
E12	EL 1.5x (2-8)	RiskManager	2<=h<8	✅ aynı
E13	DD devre kesici (trip 15/reset 10)	VAR — entry tamamen engeller	YOK	⚠ büyük eksik
E14	Dinamik bakiye/equity	_available_balance + peak	sabit INITIAL_BALANCE	⚠ farklı
E15	qty cap (leverage, MAX_MARGIN_PCT, MIN_STOP_DIST_PCT)	calculate_qty	(bal×risk)/rd	⚠ farklı
E16	MIN_RISK_DIST_ATR_MULT	validate_risk → reject	quality_mult=0	✅ aynı
E17	MIN_SL_DISTANCE_PCT	VAR (apply_min_sl_distance)	YOK	⚠ eksik
E18	Entry fiyatı	trigger bar CLOSE	next bar OPEN	⚠ bilinçli fark
E19	SL/TP tick yuvarlama	round_sl_tp	yok	⚠ farklı
Trailing/Exit tarafı — asıl kopukluk burada
#	Kural	Canlı	Backtest	Durum
T1	Frekans	1m kapanış	15m bar	⚠ eklenmeli
T2	Seviye kaynağı	entry'nin trigger FVG'si (extractor, bot.py:321-358)	yeni FVG taraması	⚠ canlıdaki evaluate_trail artık ölü kod — backtest eski mantığı çalıştırıyor
T3	Buffer	tick_size × 2 (sl_buffer_ticks)	ATR × ATR_TRAIL_MULT	⚠ farklı
T4	Min move şartı	yok (sadece better-SL)	rpt2 × TMM	⚠ farklı
T5	close-inside-FVG şartı	yok	fvg_close_confirmed	⚠ farklı
T6	Tick normalizasyon	FLOOR/CEIL	yok	⚠ farklı
T7	Placeability (1m fiyat, epsilon)	var	yok	⚠ farklı
T8	Fingerprint dedup	var	yok	⚠ farklı
T9	TP delta-shift	var	var	✅
T10	Exit çözünürlüğü	1m high/low	15m high/low	⚠ farklı
En kritik bulgular:

T2 — Backtest trailing, canlıda ölü kod olan eski evaluate_trail mantığını (yeni FVG taraması + close-confirmed + ATR buffer + TMM) çalıştırıyor. Canlı aslında entry FVG'sine tek seferde tick-buffer'lı hareket ediyor (fingerprint ile bir kez). Bu, önceki oturumdaki "trailing canlıyla uyumlu" yorumumun yanlış olduğu nokta.
E8 — Session filtresi: canlı global (LONDON+NEWYORK), backtest coin-bazlı saatler. REAL_CBDR coinleri için 19-21 UTC'de farklı davranıyor.
E13/E14/E15 — DD devre kesici, dinamik equity ve qty cap'leri backtest'te hiç yok.
Sıradaki adımı sen seç: is_fvg_valid için canlıdan kaldırma işlemini yapayım mı, yoksa önce bu tablodaki eksikleri tek tek kapatacak mıyız?
