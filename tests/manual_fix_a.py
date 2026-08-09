import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

import asyncio
from unittest.mock import MagicMock, AsyncMock
from trading.order_manager import OrderManager
from models import ActiveTrade, STATUS_ACTIVE


async def test():
    mock_rest = MagicMock()
    mock_rest.apply_price_precision = AsyncMock(side_effect=lambda sym, p: p)
    mock_rest.estimate_market_price = AsyncMock(return_value=100.0)
    mock_rest.get_max_qty = AsyncMock(return_value=1000.0)
    mock_rest.place_stop_order = AsyncMock(return_value={"algoId": "sl_new"})
    mock_rest.place_tp_order = AsyncMock(return_value={"algoId": "tp_new"})
    mock_rest.cancel_order = AsyncMock(return_value={})

    _mgr = OrderManager(rest_client=mock_rest, is_live=True)
    trade = ActiveTrade(
        symbol="BTCUSDT",
        side="long",
        entry_price=100.0,
        sl=100.0,
        tp=110.0,
        qty=0.5,
        sl_order_id="sl_old",
        tp_order_id="tp_old",
    )
    mock_protection = MagicMock()
    mock_protection.begin_replace_sl = MagicMock(side_effect=RuntimeError("boom"))
    _mgr._protection = mock_protection

    try:
        result = await _mgr.update_trail_orders("BTCUSDT", trade, 105.0, 115.0, 1)
        print("RESULT:", result)
        print("STATUS:", trade["status"])
        print("ACTIVE_OK:", trade["status"] == STATUS_ACTIVE)
    except Exception as e:
        print("EXCEPTION_LEAKED:", type(e).__name__, e)


asyncio.run(test())
