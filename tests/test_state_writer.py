import json
import os
import tempfile
from unittest.mock import patch
from state_writer import write_state
from session import SessionState
from models import STATUS_REPAIR_REQUIRED, STATUS_ACTIVE, ActiveTrade


def test_write_state_includes_new_fields():
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file_path = os.path.join(tmpdir, "live_state.json")

        with patch("state_writer._STATE_FILE", state_file_path), patch(
            "state_writer._OUTPUT_DIR", tmpdir
        ), patch("state_writer.cfg") as mock_cfg:
            mock_cfg.EXIT_LIFECYCLE_SERVICE_ENABLED = False
            mock_cfg.PROTECTION_LIFECYCLE_SERVICE_ENABLED = False
            mock_cfg.WS_EVENT_NORMALIZATION_ENABLED = True

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
            assert active_trade["frozen"] is True
            assert active_trade["sl_order_id_present"] is False
            assert active_trade["tp_order_id_present"] is True
            assert active_trade["exit_unconfirmed"] is False
            assert active_trade["repair_required"] is True

            flags = data["feature_flags"]
            assert flags["exit_lifecycle_service"] is False
            assert flags["protection_lifecycle_service"] is False
            assert flags["ws_event_normalization"] is True


def test_write_state_active_trade_not_frozen():
    with tempfile.TemporaryDirectory() as tmpdir:
        state_file_path = os.path.join(tmpdir, "live_state.json")

        with patch("state_writer._STATE_FILE", state_file_path), patch(
            "state_writer._OUTPUT_DIR", tmpdir
        ), patch("state_writer.cfg") as mock_cfg:
            mock_cfg.EXIT_LIFECYCLE_SERVICE_ENABLED = True
            mock_cfg.PROTECTION_LIFECYCLE_SERVICE_ENABLED = True
            mock_cfg.WS_EVENT_NORMALIZATION_ENABLED = False

            ss = SessionState()
            states = {"BTCUSDT": ss}

            active_trades = {
                "BTCUSDT": ActiveTrade(
                    symbol="BTCUSDT",
                    side="short",
                    entry_price=50000.0,
                    sl=51000.0,
                    tp=48000.0,
                    qty=0.1,
                    status=STATUS_ACTIVE,
                    sl_order_id="SL_ACTIVE",
                    tp_order_id="",
                )
            }

            write_state(
                states=states,
                active_trades=active_trades,
                available_balance=2000.0,
                wallet_balance=2000.0,
                symbols=["BTCUSDT"],
            )

            with open(state_file_path, "r", encoding="utf-8") as f:
                data = json.load(f)

            active_trade = data["symbols"]["BTCUSDT"]["active_trade"]
            assert active_trade["status"] == STATUS_ACTIVE
            assert active_trade["frozen"] is False
            assert active_trade["sl_order_id_present"] is True
            assert active_trade["tp_order_id_present"] is False
            assert active_trade["repair_required"] is False

            flags = data["feature_flags"]
            assert flags["exit_lifecycle_service"] is True
            assert flags["protection_lifecycle_service"] is True
            assert flags["ws_event_normalization"] is False
