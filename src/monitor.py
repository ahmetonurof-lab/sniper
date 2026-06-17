"""
debug_monitor.py
----------------
Minimal runtime observability module for live trading systems.

Rules:
- No trading logic
- No decision making
- No strategy code
- No external dependencies
- Only state tracking + health reporting

Prometheus / Grafana Entegrasyonu
──────────────────────────────────
1. Prometheus HTTP hedefi (ör. aiohttp ile serve edilir):
   GET /metrics → Prometheus text format

2. Grafana dashboard için önerilen metrikler:
   - nexus_tick_seconds{status="LIVE|STALE|DEAD"} — son tick yaşı
   - nexus_signal_count_total{symbol="..."}
   - nexus_order_count_total{symbol="..."}
   - nexus_fill_count_total{symbol="..."}
   - nexus_rejected_count_total{symbol="..."}
   - nexus_health_status{symbol="..."}  — 0=DEAD, 1=STALE, 2=LIVE

3. Kullanım:
   from monitor import get_prometheus_metrics
   # aiohttp endpoint:
   async def metrics(request):
       return web.Response(text=get_prometheus_metrics(), content_type="text/plain; charset=utf-8")

4. Grafana datasource: http://<host>:<port>/metrics  (Prometheus type)
   Önerilen panel: Stat (health), Time series (counts), Table (reason logs)
"""

import contextlib
import logging
import time

logger = logging.getLogger("nexus.monitor")

# ---------------------------------------------------------------------------
# Internal state store
# ---------------------------------------------------------------------------

_state: dict = {
    # per-symbol tick times  { symbol: float (epoch) }
    "last_tick_time": {},
    # global event times
    "last_signal_time": None,
    "last_order_time": None,
    "last_fill_time": None,
    # per-symbol counters  { symbol: int }
    "signal_count": {},
    "rejected_count": {},
    "order_count": {},
    # ADDED: per-symbol fill tracking
    "fill_count": {},
    # ADDED: per-symbol last event timestamps
    "last_signal_time_per_symbol": {},
    "last_order_time_per_symbol": {},
    "last_fill_time_per_symbol": {},
    # optional reason logs (last N entries)
    "_signal_reasons": [],
    "_reject_reasons": [],
}

_REASON_LOG_LIMIT = 50  # keep last 50 reason strings in memory

# STALE threshold: no tick for this many seconds → STALE
STALE_SECONDS: float = 360.0

# DEAD threshold: no tick for this many seconds → DEAD
DEAD_SECONDS: float = 600.0


# ---------------------------------------------------------------------------
# Public update hooks  (call-and-forget; never raise)
# ---------------------------------------------------------------------------


def update_tick(symbol: str) -> None:
    """Call immediately after a market data tick is received for `symbol`."""
    with contextlib.suppress(Exception):
        _state["last_tick_time"][symbol] = time.time()


def update_signal(symbol: str, reason: str | None = None) -> None:
    """Call immediately after a trading signal is generated for `symbol`."""
    try:
        now = time.time()
        _state["last_signal_time"] = now
        _state["signal_count"][symbol] = _state["signal_count"].get(symbol, 0) + 1
        # ADDED: per-symbol last signal time
        _state["last_signal_time_per_symbol"][symbol] = now
        if reason:
            _append_reason(_state["_signal_reasons"], symbol, reason, now)
    except Exception as e:
        logger.debug("update_signal hatası: %s", e)


def update_order(symbol: str) -> None:
    """Call immediately after an order is submitted for `symbol`."""
    try:
        now = time.time()
        _state["last_order_time"] = now
        _state["order_count"][symbol] = _state["order_count"].get(symbol, 0) + 1
        # ADDED: per-symbol last order time
        _state["last_order_time_per_symbol"][symbol] = now
    except Exception as e:
        logger.debug("update_order hatası: %s", e)


def update_fill(symbol: str) -> None:
    """Call immediately after an execution fill is confirmed for `symbol`."""
    try:
        now = time.time()
        _state["last_fill_time"] = now
        # ADDED: per-symbol fill count and last fill time
        _state["fill_count"][symbol] = _state["fill_count"].get(symbol, 0) + 1
        _state["last_fill_time_per_symbol"][symbol] = now
    except Exception as e:
        logger.debug("update_fill hatası: %s", e)


def update_reject(symbol: str, reason: str | None = None) -> None:
    """Call immediately after a signal/order is rejected (risk filter, etc.)."""
    try:
        now = time.time()
        _state["rejected_count"][symbol] = _state["rejected_count"].get(symbol, 0) + 1
        if reason:
            _append_reason(_state["_reject_reasons"], symbol, reason, now)
    except Exception as e:
        logger.debug("update_reject hatası: %s", e)


# ---------------------------------------------------------------------------
# Public reason log queries (ADDED)
# ---------------------------------------------------------------------------


def get_signal_reasons(limit: int | None = None) -> list:
    """Return recent signal reasons (newest first)."""
    reasons = list(reversed(_state["_signal_reasons"]))
    return reasons[:limit] if limit else reasons


def get_reject_reasons(limit: int | None = None) -> list:
    """Return recent reject reasons (newest first)."""
    reasons = list(reversed(_state["_reject_reasons"]))
    return reasons[:limit] if limit else reasons


# ---------------------------------------------------------------------------
# Public health query
# ---------------------------------------------------------------------------


def get_health(symbol: str | None = None) -> dict:
    """
    Return a health snapshot.

    If `symbol` is provided → per-symbol view.
    If `symbol` is None     → aggregate view across all tracked symbols.

    Health status:
        LIVE  — last tick within STALE_SECONDS
        STALE — last tick between STALE_SECONDS and DEAD_SECONDS ago
        DEAD  — no tick ever, or last tick older than DEAD_SECONDS
    """
    now = time.time()

    if symbol:
        symbols = [symbol]
    else:
        # union of all symbols seen across all counters
        symbols = list(
            set(_state["last_tick_time"].keys())
            | set(_state["signal_count"].keys())
            | set(_state["rejected_count"].keys())
            | set(_state["order_count"].keys())
            | set(_state["fill_count"].keys())  # ADDED
        )

    # --- per-symbol health blocks ---
    symbol_health = {}

    def _age(ts):
        return round(now - ts, 2) if ts else None

    for sym in symbols:
        last_tick = _state["last_tick_time"].get(sym)
        if last_tick is None:
            age = None
            status = "DEAD"
        else:
            age = round(now - last_tick, 2)
            if age <= STALE_SECONDS:
                status = "LIVE"
            elif age <= DEAD_SECONDS:
                status = "STALE"
            else:
                status = "DEAD"

        symbol_health[sym] = {
            "status": status,
            "seconds_since_last_tick": age,
            "signal_count": _state["signal_count"].get(sym, 0),
            "rejected_count": _state["rejected_count"].get(sym, 0),
            "order_count": _state["order_count"].get(sym, 0),
            # added fields
            "fill_count": _state["fill_count"].get(sym, 0),
            "last_signal_seconds": _age(_state["last_signal_time_per_symbol"].get(sym)),
            "last_order_seconds": _age(_state["last_order_time_per_symbol"].get(sym)),
            "last_fill_seconds": _age(_state["last_fill_time_per_symbol"].get(sym)),
        }

    # --- aggregate timing ---
    aggregate = {
        "symbols": symbol_health,
        "last_signal_seconds": _age(_state["last_signal_time"]),
        "last_order_seconds": _age(_state["last_order_time"]),
        "last_fill_seconds": _age(_state["last_fill_time"]),
    }

    # If a single symbol was requested, flatten for convenience
    if symbol and symbol in symbol_health:
        result = symbol_health[symbol].copy()
        result.update(
            {
                "symbol": symbol,
                "last_signal_seconds": aggregate["last_signal_seconds"],
                "last_order_seconds": aggregate["last_order_seconds"],
                "last_fill_seconds": aggregate["last_fill_seconds"],
            }
        )
        return result

    return aggregate


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _append_reason(log: list, symbol: str, reason: str, ts: float) -> None:
    """Append a reason entry; trim to _REASON_LOG_LIMIT."""
    log.append({"symbol": symbol, "reason": reason, "ts": ts})
    if len(log) > _REASON_LOG_LIMIT:
        del log[0]


# ---------------------------------------------------------------------------
# Prometheus Metrics Exposition  (zero external dependency — manual text format)
# ---------------------------------------------------------------------------

_GAUGE = "gauge"
_COUNTER = "counter"


def _prometheus_metric(
    name: str,
    help_text: str,
    mtype: str,
    value: float,
    labels: dict[str, str] | None = None,
) -> str:
    """Build a single Prometheus metric line in exposition format."""
    lines = [
        f"# HELP {name} {help_text}",
        f"# TYPE {name} {mtype}",
    ]
    if labels:
        label_str = "{" + ", ".join(f'{k}="{v}"' for k, v in sorted(labels.items())) + "}"
        lines.append(f"{name}{label_str} {value}")
    else:
        lines.append(f"{name} {value}")
    return "\n".join(lines) + "\n"


def get_prometheus_metrics() -> str:
    """
    Return all metrics in Prometheus text exposition format.

    This can be served at a /metrics HTTP endpoint for Prometheus scraping.
    Compatible with Grafana dashboards using a Prometheus datasource.
    """
    now = time.time()
    lines: list[str] = []

    # ── Nexus process info ──
    lines.append(_prometheus_metric("nexus_up", "Nexus bot is running", _GAUGE, 1.0))

    # ── Per-symbol health gauges ──
    symbols = list(
        set(_state["last_tick_time"].keys())
        | set(_state["signal_count"].keys())
        | set(_state["order_count"].keys())
        | set(_state["fill_count"].keys())
    )

    for sym in sorted(symbols):
        last_tick = _state["last_tick_time"].get(sym)
        if last_tick is None:
            age = None
            status_val = 0  # DEAD
        else:
            age = now - last_tick
            if age <= STALE_SECONDS:
                status_val = 2  # LIVE
            elif age <= DEAD_SECONDS:
                status_val = 1  # STALE
            else:
                status_val = 0  # DEAD

        # Tick age in seconds
        if age is not None:
            lines.append(
                _prometheus_metric(
                    "nexus_tick_seconds",
                    "Seconds since last market data tick",
                    _GAUGE,
                    round(age, 2),
                    {"symbol": sym},
                )
            )

        # Health status (enum: 0=DEAD, 1=STALE, 2=LIVE)
        lines.append(
            _prometheus_metric(
                "nexus_health_status",
                "Health status: 0=DEAD, 1=STALE, 2=LIVE",
                _GAUGE,
                status_val,
                {"symbol": sym},
            )
        )

        # Counters
        lines.append(
            _prometheus_metric(
                "nexus_signal_count_total",
                "Total trading signals generated",
                _COUNTER,
                _state["signal_count"].get(sym, 0),
                {"symbol": sym},
            )
        )
        lines.append(
            _prometheus_metric(
                "nexus_order_count_total",
                "Total orders submitted",
                _COUNTER,
                _state["order_count"].get(sym, 0),
                {"symbol": sym},
            )
        )
        lines.append(
            _prometheus_metric(
                "nexus_fill_count_total",
                "Total fills executed",
                _COUNTER,
                _state["fill_count"].get(sym, 0),
                {"symbol": sym},
            )
        )
        lines.append(
            _prometheus_metric(
                "nexus_rejected_count_total",
                "Total rejected signals/orders",
                _COUNTER,
                _state["rejected_count"].get(sym, 0),
                {"symbol": sym},
            )
        )

    # ── Global aggregate counters ──
    total_signals = sum(_state["signal_count"].values())
    total_orders = sum(_state["order_count"].values())
    total_fills = sum(_state["fill_count"].values())
    total_rejects = sum(_state["rejected_count"].values())

    lines.append(_prometheus_metric("nexus_total_signals", "Total signals across all symbols", _COUNTER, total_signals))
    lines.append(_prometheus_metric("nexus_total_orders", "Total orders across all symbols", _COUNTER, total_orders))
    lines.append(_prometheus_metric("nexus_total_fills", "Total fills across all symbols", _COUNTER, total_fills))
    lines.append(_prometheus_metric("nexus_total_rejects", "Total rejects across all symbols", _COUNTER, total_rejects))

    return "".join(lines)


def get_grafana_dashboard_json() -> dict:
    """
    Return a minimal Grafana dashboard JSON snippet for nexus monitoring.

    This provides a programmatic starting point for Grafana dashboard creation.
    Can be imported directly into Grafana via the Import UI.
    """
    return {
        "title": "Nexus Trading Bot",
        "panels": [
            {
                "title": "Health Status",
                "type": "stat",
                "targets": [
                    {
                        "expr": 'nexus_health_status{symbol=~"$symbol"}',
                        "legendFormat": "{{symbol}}",
                    }
                ],
            },
            {
                "title": "Signal / Order / Fill Rates",
                "type": "timeseries",
                "targets": [
                    {"expr": 'rate(nexus_signal_count_total{symbol=~"$symbol"}[5m])', "legendFormat": "signals"},
                    {"expr": 'rate(nexus_order_count_total{symbol=~"$symbol"}[5m])', "legendFormat": "orders"},
                    {"expr": 'rate(nexus_fill_count_total{symbol=~"$symbol"}[5m])', "legendFormat": "fills"},
                ],
            },
            {
                "title": "Tick Age",
                "type": "timeseries",
                "targets": [
                    {"expr": 'nexus_tick_seconds{symbol=~"$symbol"}', "legendFormat": "{{symbol}}"},
                ],
            },
            {
                "title": "Rejection Rate",
                "type": "timeseries",
                "targets": [
                    {
                        "expr": 'rate(nexus_rejected_count_total{symbol=~"$symbol"}[5m])',
                        "legendFormat": "{{symbol}}",
                    }
                ],
            },
        ],
    }
