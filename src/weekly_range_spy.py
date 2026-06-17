"""
weekly_range_spy.py
───────────────────
Weekly Range Spy (Haftalık Casus) — Nexus V3 log-only mekanizma.

Mevcut trading ve analiz mantığına DOKUNMAZ. Sadece 5m kapanışlarında
haftalık HH/LL likidite süpürmelerini ve CISD onaylarını log'lar.

İşleyiş:
  1. D1 barlarından geçen haftanın HH (en yüksek) ve LL (en düşük) seviyelerini hesapla.
  2. 5m kapanışta fiyat HH veya LL'yi süpürdü mü? → [WEEKLY-SPY] logu.
  3. Sweep sonrası ters yönlü CISD (Close Inside Swept Direction) mum kapanışı →
     [WEEKLY-SPY] CISD ONAYLANDI logu (SL: fitil ucu, TP: range ortası).
  4. Asla trade açmaz — sadece log basar.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Literal

from models import Bar

logger = logging.getLogger("nexus.weekly_spy")

# Her sembol için spy state'i (sadece loglama, state machine'den bağımsız)
_spy_state: dict[str, dict] = {}

# ─────────────────────────────────────────────────────────
# PUBLIC API
# ─────────────────────────────────────────────────────────


def check_5m(symbol: str, bars_d1: list[Bar], current_5m_bar: Bar) -> None:
    """
    5m kapanışta çağrılır. D1 barlarını kullanarak haftalık range sweep ve CISD kontrolü yapar.
    SADECE log basar, asla trade açmaz.

    Args:
        symbol: Sembol adı (örn. "BTCUSDT")
        bars_d1: D1 bar listesi (en az 10 bar olmalı)
        current_5m_bar: Yeni kapanmış 5m barı
    """
    if not bars_d1 or len(bars_d1) < 7:
        return

    # ── Geçen haftanın HH / LL hesapla ──
    weekly = _compute_weekly_range(bars_d1)
    if weekly is None:
        return

    hh, ll = weekly
    if hh is None or ll is None or hh <= ll:
        return

    mid = (hh + ll) / 2.0
    close = current_5m_bar.close
    high = current_5m_bar.high
    low = current_5m_bar.low

    # ── Per-symbol state init ──
    if symbol not in _spy_state:
        _spy_state[symbol] = {
            "swept": None,  # "HH" | "LL" | None
            "sweep_bar_index": -1,
            "hh": hh,
            "ll": ll,
            "mid": mid,
            "last_week_key": _week_key(),
        }

    st = _spy_state[symbol]
    current_week_key = _week_key()

    # ── Hafta değişti mi? → state sıfırla ──
    if st["last_week_key"] != current_week_key:
        st["swept"] = None
        st["sweep_bar_index"] = -1
        st["hh"] = hh
        st["ll"] = ll
        st["mid"] = mid
        st["last_week_key"] = current_week_key
        logger.debug("[WEEKLY-SPY] %s yeni hafta — state sıfırlandı | HH=%.5f LL=%.5f", symbol, hh, ll)
    else:
        # HH/LL güncelle (yeni D1 bar'ları geldiyse)
        st["hh"] = hh
        st["ll"] = ll
        st["mid"] = mid

    # ── SWEEP tespiti ──
    swept = st["swept"]

    if swept is None:
        if high > hh:  # İğne ucu çizgiyi geçti mi?
            st["swept"] = "HH"
            st["sweep_bar_index"] = current_5m_bar.index
            log_sweep(symbol, "HH", hh, high)
            return
        elif low < ll:  # İğne ucu çizgiyi deldi mi?
            st["swept"] = "LL"
            st["sweep_bar_index"] = current_5m_bar.index
            log_sweep(symbol, "LL", ll, low)
            return

    # ── CISD tespiti (sweep sonrası) ──
    if swept is not None:
        if _detect_cisd(swept, current_5m_bar):
            fitil_ucu = high if swept == "HH" else low
            log_cisd(symbol, swept, fitil_ucu, st["mid"], close)
            # Reset: bir sonraki sweep'i bekle
            st["swept"] = None
            st["sweep_bar_index"] = -1


def log_sweep(symbol: str, side: Literal["HH", "LL"], level: float, close: float) -> None:
    """Sweep logu — ekrana ve dosyaya."""
    msg = (
        f"[WEEKLY-SPY] {symbol.ljust(12)} Geçen haftanın likiditesi süpürüldü! "
        f"({side}=%.5f sweep → close=%.5f)"
        % (
            level,
            close,
        )
    )
    logger.warning(msg)


def log_cisd(
    symbol: str,
    swept_side: Literal["HH", "LL"],
    fitil_ucu: float,
    range_ortasi: float,
    close: float,
) -> None:
    """CISD onay logu — SL ve TP seviyeleriyle birlikte."""
    direction = "SHORT" if swept_side == "HH" else "LONG"
    msg = (
        f"[WEEKLY-SPY] {symbol.ljust(12)} CISD ONAYLANDI! Sanal Giriş Sinyali. "
        f"Yön={direction} SL=%.5f TP=%.5f | close=%.5f" % (fitil_ucu, range_ortasi, close)
    )
    logger.warning(msg)


# ─────────────────────────────────────────────────────────
# INTERNALS
# ─────────────────────────────────────────────────────────


def _week_key() -> int:
    """Şu anki UTC haftanın ISO yıl+hafta anahtarını döner (örn: 202532)."""
    now = datetime.now(UTC)
    iso = now.isocalendar()
    return iso[0] * 100 + iso[1]


def _compute_weekly_range(bars_d1: list[Bar]) -> tuple[float, float] | None:
    """
    Geçen takvim haftasının (Pazartesi 00:00 → Pazar 23:59 UTC)
    en yüksek (HH) ve en düşük (LL) değerlerini döner.

    Returns:
        (hh, ll) veya None (yetersiz bar).
    """
    now = datetime.now(UTC)
    today = now.date()

    # Bu haftanın Pazartesi'si
    this_monday = today - timedelta(days=today.weekday())

    # Geçen haftanın Pazartesi'si ve Pazar'ı
    last_monday = this_monday - timedelta(days=7)
    last_sunday = this_monday - timedelta(days=1)

    # UTC 00:00 → 23:59.999 aralığı
    start_dt = datetime(last_monday.year, last_monday.month, last_monday.day, tzinfo=UTC)
    end_dt = datetime(last_sunday.year, last_sunday.month, last_sunday.day, 23, 59, 59, 999999, tzinfo=UTC)

    start_ms = int(start_dt.timestamp() * 1000)
    end_ms = int(end_dt.timestamp() * 1000)

    weekly_bars = [b for b in bars_d1 if start_ms <= b.timestamp <= end_ms]

    if len(weekly_bars) < 2:
        # Geçen hafta yeterli bar yoksa son 7 D1 barını kullan (fallback)
        fallback = bars_d1[-7:]
        if len(fallback) < 2:
            return None
        hh = max(b.high for b in fallback)
        ll = min(b.low for b in fallback)
        logger.debug(
            "[WEEKLY-SPY] Fallback: son %d D1 barı kullanıldı (geçen hafta bulunamadı)",
            len(fallback),
        )
        return hh, ll

    hh = max(b.high for b in weekly_bars)
    ll = min(b.low for b in weekly_bars)

    logger.debug(
        "[WEEKLY-SPY] Haftalık range hesaplandı: %d bar | HH=%.5f LL=%.5f",
        len(weekly_bars),
        hh,
        ll,
    )
    return hh, ll


def _detect_cisd(swept_side: Literal["HH", "LL"], bar: Bar) -> bool:
    """
    CISD (Close Inside Swept Direction) tespiti.

    HH sweep → fiyat yukarı süpürdü → ters yön = aşağı → CISD: bearish mum kapanışı (close < open)
    LL sweep → fiyat aşağı süpürdü → ters yön = yukarı → CISD: bullish mum kapanışı (close > open)

    Returns:
        True: CISD gerçekleşti.
    """
    body = bar.close - bar.open

    if swept_side == "HH":
        # Ters yön = bearish: gövde aşağı kapandı (close < open)
        return body < 0
    else:
        # LL sweep → ters yön = bullish: gövde yukarı kapandı (close > open)
        return body > 0
