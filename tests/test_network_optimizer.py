"""Tests for Cloudflare Radar network optimiser."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from network_optimizer import NetworkOptimizer, NetworkPath


class TestNetworkOptimizer:

    def setup_method(self):
        self.optimizer = NetworkOptimizer()

    # ------------------------------------------------------------------
    # score_path
    # ------------------------------------------------------------------

    def test_perfect_path_score_near_one(self):
        score = self.optimizer.score_path(latency_ms=0, packet_loss_pct=0)
        assert score == pytest.approx(1.0, abs=0.01)

    def test_terrible_path_score_near_zero(self):
        score = self.optimizer.score_path(latency_ms=600, packet_loss_pct=100)
        assert score < 0.1

    def test_score_lower_for_higher_latency(self):
        score_low = self.optimizer.score_path(latency_ms=20, packet_loss_pct=0)
        score_high = self.optimizer.score_path(latency_ms=200, packet_loss_pct=0)
        assert score_low > score_high

    def test_score_lower_for_higher_loss(self):
        score_low = self.optimizer.score_path(latency_ms=50, packet_loss_pct=0)
        score_high = self.optimizer.score_path(latency_ms=50, packet_loss_pct=50)
        assert score_low > score_high

    def test_score_is_between_zero_and_one(self):
        for latency in [0, 50, 100, 300, 600, 1000]:
            for loss in [0, 10, 50, 100]:
                score = self.optimizer.score_path(latency, loss)
                assert 0.0 <= score <= 1.0

    # ------------------------------------------------------------------
    # get_optimal_path — no API token configured (fallback)
    # ------------------------------------------------------------------

    def test_get_optimal_path_returns_path(self):
        path = self.optimizer.get_optimal_path("WNAM")
        assert isinstance(path, NetworkPath)
        assert path.region == "WNAM"

    def test_fallback_path_has_reasonable_values(self):
        path = self.optimizer.get_optimal_path("EU")
        assert path.latency_ms > 0
        assert 0.0 <= path.score <= 1.0

    def test_optimal_path_pop_code_is_string(self):
        path = self.optimizer.get_optimal_path("APAC")
        assert isinstance(path.pop_code, str)

    # ------------------------------------------------------------------
    # NetworkPath.to_dict
    # ------------------------------------------------------------------

    def test_network_path_to_dict(self):
        path = NetworkPath(
            pop_code="LAX",
            region="WNAM",
            latency_ms=12.5,
            packet_loss_pct=0.1,
            score=0.98,
        )
        d = path.to_dict()
        assert d["pop_code"] == "LAX"
        assert d["region"] == "WNAM"
        assert d["score"] == 0.98

    # ------------------------------------------------------------------
    # API error graceful degradation
    # ------------------------------------------------------------------

    def test_api_error_falls_back_to_default(self, monkeypatch):
        def bad_get(url):
            raise RuntimeError("network failure")

        monkeypatch.setattr(self.optimizer, "_api_get", bad_get)
        monkeypatch.setattr(
            self.optimizer, "_api_token", "fake-token"
        )
        path = self.optimizer.get_optimal_path("WNAM")
        assert isinstance(path, NetworkPath)
        assert path.score > 0
