"""Tests for WebRTC session handler."""

import time
import pytest
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from webrtc_handler import WebRTCHandler, SessionState


class TestWebRTCHandler:

    def setup_method(self):
        self.handler = WebRTCHandler()

    # ------------------------------------------------------------------
    # ICE configuration
    # ------------------------------------------------------------------

    def test_get_ice_servers_includes_stun(self):
        servers = self.handler.get_ice_servers()
        assert any("stun" in url for srv in servers for url in srv.urls)

    def test_get_ice_configuration_structure(self):
        cfg = self.handler.get_ice_configuration()
        assert "iceServers" in cfg
        assert isinstance(cfg["iceServers"], list)
        assert cfg["iceCandidatePoolSize"] > 0

    # ------------------------------------------------------------------
    # Session lifecycle
    # ------------------------------------------------------------------

    def test_create_session(self):
        session = self.handler.create_session("room-abc")
        assert session.room_id == "room-abc"
        assert session.state == SessionState.PENDING
        assert len(session.session_id) > 0

    def test_get_session_returns_session(self):
        session = self.handler.create_session("room-xyz")
        retrieved = self.handler.get_session(session.session_id)
        assert retrieved is not None
        assert retrieved.session_id == session.session_id

    def test_get_session_unknown_id_returns_none(self):
        assert self.handler.get_session("does-not-exist") is None

    def test_set_local_sdp_valid(self):
        session = self.handler.create_session("room-1")
        sdp = {"type": "offer", "sdp": "v=0\r\n..."}
        assert self.handler.set_local_sdp(session.session_id, sdp) is True
        assert self.handler.get_session(session.session_id).state == SessionState.CONNECTING

    def test_set_local_sdp_invalid_type_raises(self):
        session = self.handler.create_session("room-2")
        with pytest.raises(ValueError):
            self.handler.set_local_sdp(session.session_id, {"type": "bad", "sdp": "v=0"})

    def test_set_local_sdp_missing_keys_raises(self):
        session = self.handler.create_session("room-3")
        with pytest.raises(ValueError):
            self.handler.set_local_sdp(session.session_id, {"type": "offer"})

    def test_set_remote_sdp_valid(self):
        session = self.handler.create_session("room-4")
        sdp = {"type": "answer", "sdp": "v=0\r\n..."}
        assert self.handler.set_remote_sdp(session.session_id, sdp) is True

    def test_add_ice_candidate(self):
        session = self.handler.create_session("room-5")
        candidate = {"candidate": "candidate:...", "sdpMid": "0", "sdpMLineIndex": 0}
        assert self.handler.add_ice_candidate(session.session_id, candidate) is True
        s = self.handler.get_session(session.session_id)
        assert len(s.ice_candidates) == 1

    def test_mark_connected(self):
        session = self.handler.create_session("room-6")
        assert self.handler.mark_connected(session.session_id) is True
        assert self.handler.get_session(session.session_id).state == SessionState.CONNECTED

    def test_terminate_session(self):
        session = self.handler.create_session("room-7")
        assert self.handler.terminate_session(session.session_id) is True
        assert self.handler.get_session(session.session_id) is None

    def test_terminate_nonexistent_returns_false(self):
        assert self.handler.terminate_session("ghost-session") is False

    def test_list_active_sessions(self):
        self.handler.create_session("room-a")
        self.handler.create_session("room-b")
        sessions = self.handler.list_active_sessions()
        assert len(sessions) >= 2

    # ------------------------------------------------------------------
    # SDP constraints
    # ------------------------------------------------------------------

    def test_build_sdp_constraints_has_audio_and_video(self):
        constraints = self.handler.build_sdp_constraints()
        assert "audio" in constraints
        assert "video" in constraints
        assert constraints["audio"]["codec"] == "opus"
        assert "maxBitrate" in constraints["video"]

    # ------------------------------------------------------------------
    # Session expiry
    # ------------------------------------------------------------------

    def test_expired_session_is_removed(self, monkeypatch):
        session = self.handler.create_session("room-exp")
        # Backdate creation time beyond TTL
        session.created_at = time.time() - 9999
        retrieved = self.handler.get_session(session.session_id)
        assert retrieved is None

    # ------------------------------------------------------------------
    # to_dict
    # ------------------------------------------------------------------

    def test_session_to_dict(self):
        session = self.handler.create_session("room-dict")
        d = session.to_dict()
        assert d["room_id"] == "room-dict"
        assert "state" in d
        assert "session_id" in d
