"""
session_router.py testleri — BUG-23: cbdr_width_pct=None fail-closed.
"""

from unittest.mock import patch

from session_router import should_trade


class TestShouldTrade:
    @patch("session_router.cfg")
    def test_none_cbdr_fail_closed(self, mock_cfg):
        mock_cfg.CBDR_RISK_MATRIX = {"BTCUSDT": {"buckets": []}}
        allowed, reason = should_trade("BTCUSDT", cbdr_width_pct=None)
        assert allowed is False
        assert "fail-closed" in reason

    @patch("session_router.cfg")
    def test_unknown_symbol_rejected(self, mock_cfg):
        mock_cfg.CBDR_RISK_MATRIX = {}
        allowed, reason = should_trade("XRPUSDT", cbdr_width_pct=1.0)
        assert allowed is False
        assert "tanimli degil" in reason

    @patch("session_router.cfg")
    def test_poison_zone_mult_zero(self, mock_cfg):
        mock_cfg.CBDR_RISK_MATRIX = {
            "BTCUSDT": {"buckets": [(0.0, 0.5, 0.0), (0.5, 10.0, 1.0)]}
        }
        allowed, reason = should_trade("BTCUSDT", cbdr_width_pct=0.3)
        assert allowed is False
        assert "Zehirli Bolge" in reason

    @patch("session_router.cfg")
    def test_valid_cbdr_allowed(self, mock_cfg):
        mock_cfg.CBDR_RISK_MATRIX = {
            "BTCUSDT": {"buckets": [(0.0, 0.5, 0.0), (0.5, 10.0, 1.0)]}
        }
        allowed, reason = should_trade("BTCUSDT", cbdr_width_pct=2.0)
        assert allowed is True
        assert reason == ""

    @patch("session_router.cfg")
    def test_no_profile_but_cbdr_none_still_closed(self, mock_cfg):
        mock_cfg.CBDR_RISK_MATRIX = {"BTCUSDT": {"buckets": []}}
        allowed, reason = should_trade("BTCUSDT", cbdr_width_pct=None)
        assert allowed is False
