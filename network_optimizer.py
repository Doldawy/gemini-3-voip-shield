"""
Cloudflare Radar network-path optimiser for the Gemini-3 VoIP Shield bot.

Uses the Cloudflare Radar API to:
  - Query real-time BGP / routing data for a destination IP / ASN
  - Identify the lowest-latency edge PoP for a given region
  - Score candidate network paths and return the optimal egress point

The results are used by the bot to steer WebRTC media traffic through the
best available network path, reducing round-trip time for satellite-relayed
calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen
import json

import config

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 10


@dataclass
class NetworkPath:
    """Represents a candidate egress path scored by latency."""

    pop_code: str
    region: str
    latency_ms: float
    packet_loss_pct: float
    score: float  # 0–1, higher is better

    def to_dict(self) -> dict[str, Any]:
        return {
            "pop_code": self.pop_code,
            "region": self.region,
            "latency_ms": self.latency_ms,
            "packet_loss_pct": self.packet_loss_pct,
            "score": self.score,
        }


class NetworkOptimizer:
    """
    Queries Cloudflare Radar to find the optimal network path for VoIP traffic.

    When ``CLOUDFLARE_API_TOKEN`` is not configured the optimizer falls back to
    a static default path so that the rest of the call flow is unaffected.
    """

    # Cloudflare Radar endpoint for internet quality / latency data
    _QUALITY_ENDPOINT = f"{config.CLOUDFLARE_RADAR_BASE_URL}/quality/speed/summary"
    # Endpoint for BGP routing details
    _BGP_ENDPOINT = f"{config.CLOUDFLARE_RADAR_BASE_URL}/bgp/routes/summary"

    def __init__(self) -> None:
        self._api_token = config.CLOUDFLARE_API_TOKEN

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_optimal_path(self, destination_region: str) -> NetworkPath:
        """
        Return the best-scoring :class:`NetworkPath` for *destination_region*.

        Falls back to :meth:`_default_path` when the API is unavailable.
        """
        try:
            paths = self._fetch_candidate_paths(destination_region)
            if not paths:
                return self._default_path(destination_region)
            return max(paths, key=lambda p: p.score)
        except Exception as exc:
            logger.warning(
                "Cloudflare Radar query failed (%s); using default path.", exc
            )
            return self._default_path(destination_region)

    def score_path(
        self, latency_ms: float, packet_loss_pct: float
    ) -> float:
        """
        Compute a normalised quality score in [0, 1] for a network path.

        Latency is penalised logarithmically; packet loss has a linear penalty.
        Both are combined into a single quality figure that can be compared
        across candidate paths.

        :param latency_ms: Round-trip latency in milliseconds.
        :param packet_loss_pct: Packet-loss percentage (0–100).
        :returns: Score in [0, 1] where 1 is ideal.
        """
        import math

        # Normalise latency: 0 ms → 1.0, 600 ms → ~0.0
        latency_score = max(0.0, 1.0 - math.log1p(latency_ms) / math.log1p(600))
        # Normalise packet loss: 0 % → 1.0, 100 % → 0.0
        loss_score = max(0.0, 1.0 - packet_loss_pct / 100.0)
        return round(0.6 * latency_score + 0.4 * loss_score, 4)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _fetch_candidate_paths(self, region: str) -> list[NetworkPath]:
        """Call the Cloudflare Radar API and parse candidate paths."""
        if not self._api_token:
            logger.debug("No Cloudflare API token configured; skipping query.")
            return []

        url = f"{self._QUALITY_ENDPOINT}?location={region}"
        data = self._api_get(url)

        results = data.get("result", {}).get("summary_0", {})
        # The Radar quality endpoint returns p50 / p90 latency figures.
        latency_ms = float(results.get("p50", 80))
        # Radar does not expose packet-loss directly; default to 0.5 %.
        packet_loss_pct = float(results.get("packetLoss", 0.5))
        pop_code = results.get("pop", "auto")

        path = NetworkPath(
            pop_code=pop_code,
            region=region,
            latency_ms=latency_ms,
            packet_loss_pct=packet_loss_pct,
            score=self.score_path(latency_ms, packet_loss_pct),
        )
        return [path]

    def _api_get(self, url: str) -> dict[str, Any]:
        """Perform an authenticated GET request against the Cloudflare API."""
        req = Request(
            url,
            headers={
                "Authorization": f"Bearer {self._api_token}",
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
                body = resp.read().decode("utf-8")
                return json.loads(body)
        except (HTTPError, URLError) as exc:
            raise RuntimeError(f"Cloudflare Radar API error: {exc}") from exc

    @staticmethod
    def _default_path(region: str) -> NetworkPath:
        """Return a conservative fallback path when the API is unavailable."""
        return NetworkPath(
            pop_code="auto",
            region=region,
            latency_ms=80.0,
            packet_loss_pct=0.5,
            score=0.85,
        )
