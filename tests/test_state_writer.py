import json
import os
import tempfile
from unittest.mock import patch
from state_writer import write_state
from session import SessionState
from models import STATUS_REPAIR_REQUIRED, ActiveTrade


def test_write_state_includes_new_fields():
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file_path = os.path.join(tmpdir, "live_state.json")

        with patch("state_writer._STATE_FILE", state_file_path), patch(
            "state_writer._OUTPUT_DIR", tmpdir
        ):
            ss = SessionState()
            states = {"BTCUSDT": ss}

            active_trades = {
                "BTCUSDT": ActiveTrade(
                    symbol="BTCUSDT",
                    side="long",
                    entry_price=50000.0,
                    sl=49000.0,
                    tp=52000.0,
                    qty=0.1,
                    fvg_top=51000.0,
                    fvg_bottom=50500.0,
                    trailing_count=1,
                    status=STATUS_REPAIR_REQUIRED,
                    sl_order_id="",
                    tp_order_id="12345",
                )
            }
            active_trades["BTCUSDT"].upnl = 100.0

            write_state(
                states=states,
                active_trades=active_trades,
                available_balance=1000.0,
                wallet_balance=1000.0,
                symbols=["BTCUSDT"],
            )

            assert os.path.exists(state_file_path)
            with open(state_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            active_trade = data["symbols"]["BTCUSDT"]["active_trade"]
            assert active_trade["status"] == STATUS_REPAIR_REQUIRED
            assert active_trade["sl_order_id_present"] is False
            assert active_trade["tp_order_id_present"] is True
            assert active_trade["exit_unconfirmed"] is False
            assert active_trade["repair_required"] is True
