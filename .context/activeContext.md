# Active Context

## Current State
- Playwright snapshot module active: `src/snapshot/`
- Chart renders 15m OHLC with CBDR, FVG rays, markers (entry/exit/SL/TP/trail)
- Python trims candles to balanced window (PAD=8 each side)

## Latest Change
- Removed unused `from __future__ import annotations` import from `snapshot.py`
