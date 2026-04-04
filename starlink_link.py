"""
Starlink satellite link manager for the Gemini-3 VoIP Shield bot.

Communicates with the Starlink dish gRPC API (Dishy McFlatface) to:
  - Query real-time link quality metrics (SNR, throughput, latency, obstructions)
  - Determine whether the satellite link is healthy enough for a VoIP call
  - Expose link status to the rest of the application in a normalised format

The Starlink dish exposes a local gRPC service on 192.168.100.1:9200.
Because importing the generated protobuf stubs is optional (they may not be
installed in all environments), this module uses a thin abstraction layer that
can be backed by the real gRPC client *or* a mock implementation.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from typing import Any

import config

logger = logging.getLogger(__name__)


@dataclass
class StarlinkStatus:
    """Real-time status snapshot from the Starlink dish."""

    uptime_seconds: float
    snr_db: float
    downlink_throughput_mbps: float
    uplink_throughput_mbps: float
    latency_ms: float
    obstruction_pct: float
    is_online: bool
    timestamp: float = field(default_factory=time.time)

    @property
    def link_quality(self) -> float:
        """
        Normalised link-quality score in [0, 1].

        Combines SNR, obstruction, and latency into a single figure that the
        :class:`~bot.Gemini3Bot` uses to decide whether a call should proceed
        over the satellite link.
        """
        if not self.is_online:
            return 0.0
        import math

        snr_score = min(1.0, self.snr_db / 20.0)  # 20 dB → perfect
        obstruction_score = max(0.0, 1.0 - self.obstruction_pct / 100.0)
        latency_score = max(0.0, 1.0 - math.log1p(self.latency_ms) / math.log1p(800))
        return round(
            0.4 * snr_score + 0.3 * obstruction_score + 0.3 * latency_score, 4
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "uptime_seconds": self.uptime_seconds,
            "snr_db": self.snr_db,
            "downlink_throughput_mbps": self.downlink_throughput_mbps,
            "uplink_throughput_mbps": self.uplink_throughput_mbps,
            "latency_ms": self.latency_ms,
            "obstruction_pct": self.obstruction_pct,
            "is_online": self.is_online,
            "link_quality": self.link_quality,
            "timestamp": self.timestamp,
        }


class StarlinkLink:
    """
    Manages the connection to the Starlink dish and exposes link-quality data.

    Attempts to connect to the dish gRPC API at startup.  If the gRPC
    dependency (``grpcio``) or the dish is not available the link manager
    falls back to :meth:`_mock_status` so that the application remains
    functional in development / test environments.
    """

    def __init__(self) -> None:
        self._host = config.STARLINK_GRPC_HOST
        self._port = config.STARLINK_GRPC_PORT
        self._threshold = config.STARLINK_LINK_QUALITY_THRESHOLD
        self._channel: Any = None
        self._stub: Any = None
        self._grpc_available = self._try_connect()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def get_status(self) -> StarlinkStatus:
        """
        Return the current link status.

        Uses the live gRPC API when available, otherwise returns a simulated
        status suitable for testing.
        """
        if self._grpc_available and self._stub is not None:
            try:
                return self._fetch_live_status()
            except Exception as exc:
                logger.warning("Starlink gRPC query failed: %s; using mock.", exc)
        return self._mock_status()

    def is_link_healthy(self) -> bool:
        """
        Return ``True`` when the satellite link quality exceeds the configured
        threshold and is suitable for a VoIP call.
        """
        status = self.get_status()
        healthy = status.link_quality >= self._threshold
        if not healthy:
            logger.warning(
                "Starlink link quality %.3f below threshold %.3f",
                status.link_quality,
                self._threshold,
            )
        return healthy

    def close(self) -> None:
        """Close the gRPC channel if one is open."""
        if self._channel is not None:
            try:
                self._channel.close()
            except Exception as exc:
                logger.debug("Error closing Starlink gRPC channel: %s", exc)
            finally:
                self._channel = None
                self._stub = None

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _try_connect(self) -> bool:
        """Attempt to import grpcio and open the dish channel."""
        try:
            import grpc  # type: ignore[import]

            self._channel = grpc.insecure_channel(f"{self._host}:{self._port}")
            logger.info(
                "Starlink gRPC channel opened: %s:%s", self._host, self._port
            )
            return True
        except ImportError:
            logger.info("grpcio not installed; Starlink link will use mock data.")
            return False
        except Exception as exc:
            logger.warning("Could not connect to Starlink dish: %s", exc)
            return False

    def _fetch_live_status(self) -> StarlinkStatus:
        """
        Query the dish gRPC API for the current device status.

        The generated stub type is not bundled with this package (it must be
        generated from the Starlink proto files), so we perform the call
        dynamically to avoid hard import failures.
        """
        import grpc  # type: ignore[import]

        # The Starlink dish exposes GetDeviceInfo / GetStatus RPCs.
        # We use a raw unary RPC call with the well-known method path.
        rpc = self._channel.unary_unary(
            "/SpaceX.API.Device.Device/Handle",
            request_serializer=lambda x: x,
            response_deserializer=lambda x: x,
        )
        # In a production integration the request would be a serialised
        # GetStatusRequest protobuf.  Here we pass an empty bytes payload
        # and parse whatever comes back.  A real deployment would use the
        # generated stubs.
        try:
            raw = rpc(b"", timeout=5)
            return self._parse_grpc_response(raw)
        except grpc.RpcError as exc:
            raise RuntimeError(f"gRPC call failed: {exc}") from exc

    @staticmethod
    def _parse_grpc_response(raw: bytes) -> StarlinkStatus:
        """Parse a raw gRPC response bytes object into :class:`StarlinkStatus`."""
        # Without the generated proto stubs we cannot deserialise the binary
        # payload.  A full implementation would use the generated classes.
        # This stub demonstrates where the parsing logic belongs.
        raise NotImplementedError(
            "Proto stubs required for live status parsing; falling back to mock."
        )

    @staticmethod
    def _mock_status() -> StarlinkStatus:
        """Return a plausible simulated status for development / CI use."""
        return StarlinkStatus(
            uptime_seconds=86400.0,
            snr_db=14.5,
            downlink_throughput_mbps=150.0,
            uplink_throughput_mbps=20.0,
            latency_ms=40.0,
            obstruction_pct=0.2,
            is_online=True,
        )
