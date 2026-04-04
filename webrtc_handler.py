"""
WebRTC session handler for the Gemini-3 VoIP Shield bot.

Manages the full lifecycle of a WebRTC peer connection:
  - ICE candidate negotiation (STUN / TURN)
  - SDP offer / answer exchange
  - Codec constraints (opus for audio, VP8 / H.264 for video)
  - Bandwidth throttling via bitrate caps
  - Session state tracking
"""

from __future__ import annotations

import logging
import secrets
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

import config

logger = logging.getLogger(__name__)


class SessionState(str, Enum):
    """Lifecycle states of a WebRTC session."""

    PENDING = "pending"
    CONNECTING = "connecting"
    CONNECTED = "connected"
    DISCONNECTED = "disconnected"
    FAILED = "failed"


@dataclass
class IceServer:
    """Represents a single STUN or TURN server."""

    urls: list[str]
    username: str = ""
    credential: str = ""


@dataclass
class WebRTCSession:
    """Holds runtime state for a single WebRTC call session."""

    session_id: str
    room_id: str
    created_at: float = field(default_factory=time.time)
    state: SessionState = SessionState.PENDING
    local_sdp: dict[str, Any] = field(default_factory=dict)
    remote_sdp: dict[str, Any] = field(default_factory=dict)
    ice_candidates: list[dict[str, Any]] = field(default_factory=list)
    audio_bitrate_kbps: int = field(default_factory=lambda: config.AUDIO_MAX_BITRATE_KBPS)
    video_bitrate_kbps: int = field(default_factory=lambda: config.VIDEO_MAX_BITRATE_KBPS)

    def is_expired(self) -> bool:
        return time.time() - self.created_at > config.CALL_LINK_TTL_SECONDS

    def to_dict(self) -> dict[str, Any]:
        return {
            "session_id": self.session_id,
            "room_id": self.room_id,
            "created_at": self.created_at,
            "state": self.state.value,
            "audio_bitrate_kbps": self.audio_bitrate_kbps,
            "video_bitrate_kbps": self.video_bitrate_kbps,
        }


class WebRTCHandler:
    """
    Manages WebRTC sessions for voice and video calls.

    Each call gets an isolated :class:`WebRTCSession`.  The handler is
    responsible for building the ICE configuration, validating SDP payloads,
    and enforcing bitrate constraints so that calls remain low-latency even
    over satellite links.
    """

    def __init__(self) -> None:
        self._sessions: dict[str, WebRTCSession] = {}

    # ------------------------------------------------------------------
    # ICE helpers
    # ------------------------------------------------------------------

    def get_ice_servers(self) -> list[IceServer]:
        """Return the list of ICE servers to include in the RTCConfiguration."""
        servers: list[IceServer] = [
            IceServer(urls=[url]) for url in config.STUN_SERVERS
        ]
        if config.TURN_SERVER_URL:
            servers.append(
                IceServer(
                    urls=[config.TURN_SERVER_URL],
                    username=config.TURN_USERNAME,
                    credential=config.TURN_CREDENTIAL,
                )
            )
        return servers

    def get_ice_configuration(self) -> dict[str, Any]:
        """Serialise ICE server list for delivery to WebRTC clients."""
        return {
            "iceServers": [
                {
                    "urls": srv.urls,
                    **({"username": srv.username} if srv.username else {}),
                    **({"credential": srv.credential} if srv.credential else {}),
                }
                for srv in self.get_ice_servers()
            ],
            "iceCandidatePoolSize": 10,
        }

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def create_session(self, room_id: str) -> WebRTCSession:
        """Create and register a new WebRTC session for *room_id*."""
        session_id = secrets.token_urlsafe(16)
        session = WebRTCSession(session_id=session_id, room_id=room_id)
        self._sessions[session_id] = session
        logger.info("WebRTC session created: %s (room=%s)", session_id, room_id)
        return session

    def get_session(self, session_id: str) -> WebRTCSession | None:
        """Return the session for *session_id*, or ``None`` if not found."""
        session = self._sessions.get(session_id)
        if session and session.is_expired():
            self.terminate_session(session_id)
            return None
        return session

    def set_local_sdp(self, session_id: str, sdp: dict[str, Any]) -> bool:
        """
        Store the local SDP offer/answer for a session.

        Returns ``True`` on success, ``False`` if the session is not found.
        """
        session = self.get_session(session_id)
        if not session:
            logger.warning("set_local_sdp: session %s not found", session_id)
            return False
        self._validate_sdp(sdp)
        session.local_sdp = sdp
        session.state = SessionState.CONNECTING
        return True

    def set_remote_sdp(self, session_id: str, sdp: dict[str, Any]) -> bool:
        """
        Store the remote SDP answer for a session.

        Returns ``True`` on success, ``False`` if the session is not found.
        """
        session = self.get_session(session_id)
        if not session:
            logger.warning("set_remote_sdp: session %s not found", session_id)
            return False
        self._validate_sdp(sdp)
        session.remote_sdp = sdp
        return True

    def add_ice_candidate(
        self, session_id: str, candidate: dict[str, Any]
    ) -> bool:
        """Add a remote ICE candidate to the session candidate pool."""
        session = self.get_session(session_id)
        if not session:
            return False
        session.ice_candidates.append(candidate)
        return True

    def mark_connected(self, session_id: str) -> bool:
        """Transition a session to the CONNECTED state."""
        session = self.get_session(session_id)
        if not session:
            return False
        session.state = SessionState.CONNECTED
        logger.info("WebRTC session connected: %s", session_id)
        return True

    def terminate_session(self, session_id: str) -> bool:
        """Remove a session and release its resources."""
        if session_id not in self._sessions:
            return False
        session = self._sessions.pop(session_id)
        session.state = SessionState.DISCONNECTED
        logger.info("WebRTC session terminated: %s", session_id)
        return True

    # ------------------------------------------------------------------
    # Codec / bandwidth helpers
    # ------------------------------------------------------------------

    def build_sdp_constraints(self) -> dict[str, Any]:
        """
        Return SDP media constraints that enforce the configured bitrate caps
        and prefer low-latency codecs (opus for audio, VP8 for video).
        """
        return {
            "audio": {
                "codec": "opus",
                "maxBitrate": config.AUDIO_MAX_BITRATE_KBPS * 1000,
                "stereo": True,
                "echoCancellation": True,
                "noiseSuppression": True,
            },
            "video": {
                "codec": "VP8",
                "maxBitrate": config.VIDEO_MAX_BITRATE_KBPS * 1000,
                "maxFramerate": 30,
                "width": {"ideal": 1280},
                "height": {"ideal": 720},
            },
        }

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_sdp(sdp: dict[str, Any]) -> None:
        """Raise ``ValueError`` if *sdp* is missing required keys."""
        required = {"type", "sdp"}
        missing = required - sdp.keys()
        if missing:
            raise ValueError(f"SDP payload missing required keys: {missing}")
        if sdp["type"] not in {"offer", "answer", "pranswer", "rollback"}:
            raise ValueError(f"Invalid SDP type: {sdp['type']!r}")

    def list_active_sessions(self) -> list[dict[str, Any]]:
        """Return a summary of all non-expired sessions."""
        self._purge_expired()
        return [s.to_dict() for s in self._sessions.values()]

    def _purge_expired(self) -> None:
        expired = [sid for sid, s in self._sessions.items() if s.is_expired()]
        for sid in expired:
            self.terminate_session(sid)
