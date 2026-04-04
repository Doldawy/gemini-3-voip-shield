"""Tests for Starlink satellite link manager."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from starlink_link import StarlinkLink, StarlinkStatus


class TestStarlinkStatus:

    def _make_status(self, **kwargs):
        defaults = dict(
            uptime_seconds=3600,
            snr_db=15.0,
            downlink_throughput_mbps=100,
            uplink_throughput_mbps=15,
            latency_ms=45,
            obstruction_pct=0.5,
            is_online=True,
        )
        defaults.update(kwargs)
        return StarlinkStatus(**defaults)

    def test_online_status_has_positive_quality(self):
        status = self._make_status()
        assert status.link_quality > 0

    def test_offline_status_quality_is_zero(self):
        status = self._make_status(is_online=False)
        assert status.link_quality == 0.0

    def test_high_snr_improves_quality(self):
        low = self._make_status(snr_db=5)
        high = self._make_status(snr_db=20)
        assert high.link_quality > low.link_quality

    def test_high_obstruction_reduces_quality(self):
        low = self._make_status(obstruction_pct=0)
        high = self._make_status(obstruction_pct=80)
        assert low.link_quality > high.link_quality

    def test_high_latency_reduces_quality(self):
        low_lat = self._make_status(latency_ms=20)
        high_lat = self._make_status(latency_ms=500)
        assert low_lat.link_quality > high_lat.link_quality

    def test_link_quality_in_range(self):
        for snr in [0, 5, 10, 20]:
            for obstruct in [0, 10, 50, 100]:
                status = self._make_status(snr_db=snr, obstruction_pct=obstruct)
                assert 0.0 <= status.link_quality <= 1.0

    def test_to_dict_keys(self):
        status = self._make_status()
        d = status.to_dict()
        for key in ("uptime_seconds", "snr_db", "latency_ms",
                    "is_online", "link_quality", "timestamp"):
            assert key in d


class TestStarlinkLink:

    def setup_method(self):
        self.link = StarlinkLink()

    def test_get_status_returns_status_object(self):
        status = self.link.get_status()
        assert isinstance(status, StarlinkStatus)

    def test_mock_status_is_online(self):
        status = StarlinkLink._mock_status()
        assert status.is_online is True

    def test_mock_status_has_positive_throughput(self):
        status = StarlinkLink._mock_status()
        assert status.downlink_throughput_mbps > 0
        assert status.uplink_throughput_mbps > 0

    def test_is_link_healthy_with_good_mock_data(self):
        # The mock returns quality ~0.85, well above the 0.7 threshold
        assert self.link.is_link_healthy() is True

    def test_is_link_healthy_false_when_offline(self, monkeypatch):
        offline = StarlinkStatus(
            uptime_seconds=0,
            snr_db=0,
            downlink_throughput_mbps=0,
            uplink_throughput_mbps=0,
            latency_ms=9999,
            obstruction_pct=100,
            is_online=False,
        )
        monkeypatch.setattr(self.link, "get_status", lambda: offline)
        assert self.link.is_link_healthy() is False

    def test_close_does_not_raise(self):
        self.link.close()  # Should not raise even with no open channel

    def test_close_is_idempotent(self):
        self.link.close()
        self.link.close()
