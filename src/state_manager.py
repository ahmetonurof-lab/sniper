"""
state_manager.py — Günlük işlem state'i + sweep tekilleştirme
Nexus v4 / Sniper Bot — disk tabanlı, restart-proof state yönetimi.

Kullanım:
    from state_manager import can_open_trade, mark_trade_opened, mark_trade_closed
    from state_manager import is_sweep_used, mark_sweep_used, reconcile_from_active
    from state_manager import get_trade_count_today
"""

from __future__ import annotations

import json
import logging
import math
import os
from datetime import datetime, timedelta, UTC

from filelock import FileLock

log = logging.getLogger("sniper.state")

# ── Dosya konumları ────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_OUTPUT_DIR = os.environ.get("SNIPER_OUTPUT_DIR") or os.path.join(
    _SCRIPT_DIR, "..", "output"
)
os.makedirs(_OUTPUT_DIR, exist_ok=True)

STATE_FILE = os.path.join(_OUTPUT_DIR, "trade_state.json")
LOCK_FILE = STATE_FILE + ".lock"


# ── Yardımcılar ───────────────────────────────────────────────────


def cbdr_day_key(dt: datetime, start_hour: int = 22, end_hour: int = 2) -> str:
    """CBDR döngüsünün BİTTİĞİ takvim gününü döner (K1=Seçenek B).

    SessionState 22:00 UTC'de yeni CBDR döngüsü başlatır ve trades_today=0 yapar.
    Etiket, döngünün bitişine denk gelen takvim günüdür: saat 22:00 ve sonrası
    yeni döngünün "bitiş günü" (yarın) olarak etiketlenir; öncesi bugün kalır.

    Hem state_manager._today() hem session.py cbdr_key bu fonksiyona delege
    eder — iki modül aynı key'i üretir (BUG-5).
    """
    today = dt.strftime("%Y-%m-%d")
    if dt.hour >= start_hour:
        return (dt + timedelta(days=1)).strftime("%Y-%m-%d")
    return today


def _today() -> str:
    """CBDR döngüsüne uyumlu gün tanımı (K1=B ortak helper'a delege eder).

    SessionState 22:00 UTC'de yeni CBDR döngüsü başlatır ve trades_today=0 yapar.
    state_manager da aynı sınırı kullanmalı, aksi halde 22:00-00:00 UTC arasında
    can_open_trade() eski günün count'unu görüp yeni döngünün ilk trade'ini engeller.
    """
    return cbdr_day_key(datetime.now(UTC))


def _load() -> dict:
    """State dosyasını oku. Hata/eksikse boş dict döner."""
    try:
        with open(STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def _save(state: dict):
    """Atomic yazım: önce .tmp, sonra rename (yarım yazım riski yok)."""
    tmp = STATE_FILE + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, ensure_ascii=False)
    os.replace(tmp, STATE_FILE)


# ── Günlük işlem kotası ───────────────────────────────────────────


def can_open_trade(symbol: str) -> bool:
    """
    Bu sembol için bugün işlem açılabilir mi?
    - Yeni gün ise → True
    - Bugün zaten count>=1 ise → False
    """
    with FileLock(LOCK_FILE):
        state = _load()
        s = state.get(symbol, {})
        if s.get("date") != _today():
            return True  # Yeni gün, kota sıfırlandı
        if s.get("count", 0) >= 1:
            log.info("[STATE] %s — bugün kotası doldu (date=%s)", symbol, _today())
            return False
        return True


def mark_trade_opened(symbol: str, entry_price: float = 0.0):
    """
    Trade açıldıktan hemen sonra çağır.
    Günlük count=1 olarak diske yazar.
    """
    with FileLock(LOCK_FILE):
        state = _load()
        s = state.setdefault(symbol, {})
        s["date"] = _today()
        s["count"] = 1
        s["entry_price"] = entry_price
        s["open"] = True
        _save(state)
    log.info("[STATE] %s — trade açıldı kaydedildi @ %.4f", symbol, entry_price)


def mark_trade_closed(symbol: str):
    """
    Trade kapandıktan sonra çağır (opsiyonel — debug/log için).
    Count sıfırlanmaz, bugün yeni işlem açılmaz.
    """
    with FileLock(LOCK_FILE):
        state = _load()
        if symbol in state:
            state[symbol]["open"] = False
            _save(state)
    log.info("[STATE] %s — trade kapandı olarak işaretlendi", symbol)


# ── Sweep tekilleştirme ───────────────────────────────────────────


def is_sweep_used(sweep_id: str) -> bool:
    """
    Bu sweep ID bugün zaten kullanıldı mı?
    sweep_id formatı (L-04): "{symbol}_{direction}_{bar_index}" → örn:
    "BTCUSDT_bullish_12345". Symbol bilinmiyorsa legacy fallback
    "{direction}_{bar_index}" geçerli (örn. "bullish_12345"). Opaque ID —
    bu fonksiyon ID'nin ic yapisini bilmez; disaridan _sweep_id() üretilir.
    """
    with FileLock(LOCK_FILE):
        state = _load()
        used = state.get("_used_sweeps", {})
        entry = used.get(sweep_id)
        if not entry:
            return False
        return entry.get("date") == _today()


def mark_sweep_used(sweep_id: str):
    """
    Sweep ID'yi bugün kullanıldı olarak işaretle.
    Eski günlerin sweep kayıtlarını otomatik temizler.
    sweep_id opaque'dir (formatlama _sweep_id() ile caller tarafında yapilir).
    """
    with FileLock(LOCK_FILE):
        state = _load()
        used = state.get("_used_sweeps", {})

        # Bugünün sweep'ini kaydet
        used[sweep_id] = {"date": _today()}

        # Eski günlerin kayıtlarını temizle (dosya şişmesin)
        used = {k: v for k, v in used.items() if v.get("date") == _today()}
        state["_used_sweeps"] = used
        _save(state)
    log.info("[STATE] sweep kaydedildi: %s", sweep_id)


def is_sweep_consumed(direction: str, level: float) -> bool:
    """Level-based sweep consumption check. Uses level ID not bar index."""
    sweep_id = f"{direction}_{level:.4f}"
    with FileLock(LOCK_FILE):
        state = _load()
        consumed = state.get("_consumed_sweeps", {})
        entry = consumed.get(sweep_id)
        if not entry:
            return False
        return entry.get("date") == _today()


def mark_sweep_consumed(direction: str, level: float):
    """Mark a level-based sweep as consumed (permanent for the session)."""
    sweep_id = f"{direction}_{level:.4f}"
    with FileLock(LOCK_FILE):
        state = _load()
        consumed = state.get("_consumed_sweeps", {})
        consumed[sweep_id] = {"date": _today()}
        consumed = {k: v for k, v in consumed.items() if v.get("date") == _today()}
        state["_consumed_sweeps"] = consumed
        _save(state)
    log.info("[STATE] sweep consumed: %s", sweep_id)


# ── Günlük BIAS kilidi (latch) persistence ────────────────────────
# Rapor 4: ilk geçerli sweep, o CBDR günü için günlük BIAS'ı belirler ve kilitler.
# Bu latch disk'e yazilir; restart sonrasi SessionState/RSM yeniden yuklenir.


def mark_bias_locked(
    symbol: str,
    day_key: str,
    daily_bias: str,
    sweep_direction: str,
    sweep_level: float,
    bias_lock_bar_index: int | None = None,
) -> bool:
    """Günlük BIAS latch'ini atomik olarak kaydet.

    Symbol-scoped: latch, trade state'iyle aynı symbol dict'inde tutulur
    (mark_trade_opened merge yapar, latch alanlarını silmez). day_key uyumsuzsa
    yazılmaz. Aynı günün latch'i zaten varsa True döner (idempotent) — ikinci
    sweep latch'i DEĞİŞTİREMEZ (bias günde bir kez kilitlenir).

    Hata asla yutulmaz: StateManager/disk hatası exception olarak firlar;
    caller (bot) critical log basar, bellek latch'i korunur.
    """
    if daily_bias not in ("BULLISH", "BEARISH"):
        raise ValueError(f"daily_bias BULLISH/BEARISH olmali: {daily_bias!r}")
    if sweep_direction not in ("bullish", "bearish"):
        raise ValueError(f"sweep_direction bullish/bearish olmali: {sweep_direction!r}")
    if not isinstance(sweep_level, (int, float)) or not math.isfinite(sweep_level):
        raise ValueError(f"sweep_level finite olmali: {sweep_level!r}")
    if sweep_level <= 0:
        raise ValueError(f"sweep_level pozitif olmali: {sweep_level!r}")
    if not day_key:
        raise ValueError("day_key bos olamaz")

    with FileLock(LOCK_FILE):
        state = _load()
        s = state.setdefault(symbol, {})
        if s.get("bias_locked") and s.get("bias_lock_day") == day_key:
            return True  # idempotent: aynı gün latch'i değiştirilmez
        s["bias_locked"] = True
        s["daily_bias"] = daily_bias
        s["sweep_direction"] = sweep_direction
        s["sweep_level"] = sweep_level
        s["bias_lock_day"] = day_key
        if bias_lock_bar_index is not None:
            s["bias_lock_bar_index"] = bias_lock_bar_index
        _save(state)
    log.info(
        "[STATE] %s BIAS latch kaydedildi: %s (day=%s, sweep_level=%.4f)",
        symbol,
        daily_bias,
        day_key,
        sweep_level,
    )
    return True


def load_bias_lock(symbol: str, day_key: str) -> dict | None:
    """Aynı CBDR day key için persist edilmiş BIAS latch'ini döndür.

    Latch yoksa, day_key uyumsuzsa (yeni gün) veya alanlar geçersizse None.
    Dönüş dict: daily_bias, sweep_direction, sweep_level, bias_lock_day,
    bias_lock_bar_index (varsa).
    """
    with FileLock(LOCK_FILE):
        state = _load()
        s = state.get(symbol, {})
        if not s.get("bias_locked") or s.get("bias_lock_day") != day_key:
            return None
        daily_bias = s.get("daily_bias")
        sweep_direction = s.get("sweep_direction")
        sweep_level = s.get("sweep_level")
        if daily_bias not in ("BULLISH", "BEARISH"):
            return None
        if sweep_direction not in ("bullish", "bearish"):
            return None
        if not isinstance(sweep_level, (int, float)) or not math.isfinite(sweep_level):
            return None
        return {
            "daily_bias": daily_bias,
            "sweep_direction": sweep_direction,
            "sweep_level": sweep_level,
            "bias_lock_day": s["bias_lock_day"],
            "bias_lock_bar_index": s.get("bias_lock_bar_index"),
        }


# ── Startup reconciliation ────────────────────────────────────────


def reconcile_from_active(active_trades: dict):
    """
    Bot restart sonrası: active_trades'deki sembolleri state'e işle.
    _recover_positions() çağrısından SONRA çağrılmalı.

    Örnek:
        await self._recover_positions()
        reconcile_from_active(self.active_trades)
    """
    if not active_trades:
        log.info("[STATE] reconcile: açık pozisyon yok, state dokunulmadı")
        return

    with FileLock(LOCK_FILE):
        state = _load()
        today = _today()
        changed = []

        for sym, trade in active_trades.items():
            # Bugünkü kayıt zaten varsa dokunma
            if state.get(sym, {}).get("date") == today:
                continue
            s = state.setdefault(sym, {})
            s["date"] = today
            s["count"] = 1
            s["entry_price"] = trade.get("entry_price", 0.0)
            s["open"] = True
            s["source"] = "startup_reconcile"
            changed.append(sym)

        if changed:
            _save(state)
            log.info(
                "[STATE] reconcile: %s → bugün işlem açıldı olarak işaretlendi", changed
            )
        else:
            log.info("[STATE] reconcile: tüm semboller zaten güncel")


# ── Restart senkronizasyonu ───────────────────────────────────────


def get_trade_count_today(symbol: str) -> int:
    """
    Bot restart sonrası trades_today'i disk'ten okumak için.
    Bugüne ait kayıt varsa count döner, yoksa 0 döner.

    bot.py run() içinde reconcile_from_active'den sonra çağrılır:
        count = get_trade_count_today(sym)
        if count > 0:
            self.states[sym].trades_today = count
    """
    with FileLock(LOCK_FILE):
        state = _load()
        s = state.get(symbol, {})
        if s.get("date") != _today():
            return 0
        return s.get("count", 0)


# ── Debug yardımcısı ──────────────────────────────────────────────


def dump_state() -> dict:
    """Tüm state'i döner (log/debug için)."""
    with FileLock(LOCK_FILE):
        return _load()
