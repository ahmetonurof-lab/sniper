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
import os
from datetime import datetime, timedelta, UTC

from filelock import FileLock

log = logging.getLogger("sniper.state")

# ── Dosya konumları ────────────────────────────────────────────────
_SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
_OUTPUT_DIR = os.path.join(_SCRIPT_DIR, "..", "output")
os.makedirs(_OUTPUT_DIR, exist_ok=True)

STATE_FILE = os.path.join(_OUTPUT_DIR, "trade_state.json")
LOCK_FILE = STATE_FILE + ".lock"


# ── Yardımcılar ───────────────────────────────────────────────────


def _today() -> str:
    """CBDR döngüsüne uyumlu gün tanımı.

    SessionState 22:00 UTC'de yeni CBDR döngüsü başlatır ve trades_today=0 yapar.
    state_manager da aynı sınırı kullanmalı, aksi halde 22:00-00:00 UTC arasında
    can_open_trade() eski günün count'unu görüp yeni döngünün ilk trade'ini engeller.
    """
    now = datetime.now(UTC)
    if now.hour >= 22:
        return (now + timedelta(days=1)).strftime("%Y-%m-%d")
    return now.strftime("%Y-%m-%d")


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
        state[symbol] = {
            "date": _today(),
            "count": 1,
            "entry_price": entry_price,
            "open": True,
        }
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
    sweep_id formatı: "{direction}_{bar_index}" → örn: "bullish_12345"
    """
    with FileLock(LOCK_FILE):
        state = _load()
        used = state.get("_used_sweeps", {})
        entry = used.get(sweep_id)
        if not entry:
            return False
        return entry.get("date") == _today()


def unmark_sweep_used(sweep_id: str):
    """Sweep ID'yi kullanılmadı olarak işaretle (reset/başarısız deneme sonrası)."""
    with FileLock(LOCK_FILE):
        state = _load()
        used = state.get("_used_sweeps", {})
        used.pop(sweep_id, None)
        state["_used_sweeps"] = used
        _save(state)


def mark_sweep_used(sweep_id: str):
    """
    Sweep ID'yi bugün kullanıldı olarak işaretle.
    Eski günlerin sweep kayıtlarını otomatik temizler.
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
            state[sym] = {
                "date": today,
                "count": 1,
                "entry_price": trade.get("entry_price", 0.0),
                "open": True,
                "source": "startup_reconcile",
            }
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
