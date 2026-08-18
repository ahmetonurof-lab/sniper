"""
ifvg_paper_monitor.py — Sunucuda IFVG paper izleme (paper-deploy direktifi G3).

Kullanım (sunucuda):
    python3 tools/ifvg_paper_monitor.py [--days N] [--live]

Kaynaklar:
  - output/trades_history.jsonl  → entry_source alanı (IFVG/NORMAL) + pnl
  - output/paper_trade.log       → [IFVG] entry logları + [RST] restore logları

Çıktı (gün bazlı):
  - Toplam entry, IFVG entry, NORMAL entry
  - IFVG PnL / NORMAL PnL, kapalı trade'ler
  - IFVG oranı (backtest ~%19 ile karşılaştırma)
  - Son restart davranışı ([RST] BIAS_LOCKED RESTORE sayısı)
"""

from __future__ import annotations

import argparse
import json
import os
from collections import defaultdict
from datetime import datetime, timedelta, UTC

OUTPUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "output")


def _day(ts_ms: int) -> str:
    try:
        return datetime.fromtimestamp(ts_ms / 1000, tz=UTC).strftime("%Y-%m-%d")
    except Exception:
        return "?"


def load_trades() -> list[dict]:
    path = os.path.join(OUTPUT, "trades_history.jsonl")
    if not os.path.exists(path):
        return []
    trades = []
    with open(path, encoding="utf-8") as f:
        for line in f:
            line = line.strip()
            if not line:
                continue
            try:
                trades.append(json.loads(line))
            except Exception:
                continue
    return trades


def scan_log() -> dict:
    path = os.path.join(OUTPUT, "paper_trade.log")
    stats = defaultdict(int)
    if not os.path.exists(path):
        return {}
    with open(path, encoding="utf-8", errors="replace") as f:
        for line in f:
            if "[IFVG] " in line and "IFVG entry" in line:
                stats["ifvg_entries_log"] += 1
            if "[RST] BIAS_LOCKED RESTORE" in line:
                stats["bias_restore"] += 1
    return stats


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=1)
    args = parser.parse_args()

    trades = load_trades()
    log_stats = scan_log()

    cutoff = datetime.now(UTC) - timedelta(days=args.days)
    by_day: dict[str, dict] = defaultdict(
        lambda: {"total": 0, "ifvg": 0, "normal": 0, "ifvg_pnl": 0.0, "normal_pnl": 0.0}
    )
    for t in trades:
        ts = t.get("entry_timestamp") or t.get("close_time") or 0
        d = _day(int(ts))
        if not d or d < cutoff.strftime("%Y-%m-%d"):
            continue
        src = t.get("entry_source", "NORMAL")
        pnl = float(t.get("pnl") or 0.0)
        by_day[d]["total"] += 1
        if src == "IFVG":
            by_day[d]["ifvg"] += 1
            by_day[d]["ifvg_pnl"] += pnl
        else:
            by_day[d]["normal"] += 1
            by_day[d]["normal_pnl"] += pnl

    print("=" * 78)
    print("  IFVG PAPER İZLEME (paper-deploy G3)")
    print(f"  trades_history kayıtları: {len(trades)} | log: {dict(log_stats)}")
    print("=" * 78)
    if not by_day:
        print("  Henüz kapalı trade yok (trades_history.jsonl boş veya gün dışı).")
    for d in sorted(by_day):
        s = by_day[d]
        ratio = (s["ifvg"] / s["total"] * 100) if s["total"] else 0.0
        print(f"\n  {d}:")
        print(f"    Toplam entry : {s['total']}")
        print(f"    IFVG entry   : {s['ifvg']}  ({ratio:.1f}% — backtest ~%19)")
        print(f"    NORMAL entry : {s['normal']}")
        print(f"    IFVG   PnL   : {s['ifvg_pnl']:+,.2f}")
        print(f"    NORMAL PnL   : {s['normal_pnl']:+,.2f}")
    if log_stats:
        print(
            f"\n  Log: [IFVG] entry logları={log_stats.get('ifvg_entries_log', 0)} "
            f"| [RST] BIAS_LOCKED RESTORE={log_stats.get('bias_restore', 0)}"
        )


if __name__ == "__main__":
    main()
