"""Tests for the main Gemini3Bot."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from bot import Gemini3Bot, BotResponse, _make_room_id, _sign_token, _build_call_link
from security_auditor import AuditReport, AuditResult


class TestBotHelpers:

    def test_make_room_id_is_deterministic(self):
        assert _make_room_id("spaces/ABC") == _make_room_id("spaces/ABC")

    def test_make_room_id_different_spaces(self):
        assert _make_room_id("spaces/A") != _make_room_id("spaces/B")

    def test_make_room_id_prefix(self):
        assert _make_room_id("spaces/X").startswith("room-")

    def test_sign_token_structure(self):
        token = _sign_token("payload", "secret")
        assert "." in token
        parts = token.split(".")
        assert parts[0] == "payload"
        assert len(parts[1]) == 64  # SHA-256 hex

    def test_build_call_link_contains_room(self):
        link = _build_call_link("room-abc", "sess-1", "audio")
        assert "room-abc" in link

    def test_build_call_link_contains_type_param(self):
        link = _build_call_link("room-abc", "sess-1", "video")
        assert "t=video" in link


class TestBotResponse:

    def test_text_only_response(self):
        resp = BotResponse(text="Hello!")
        msg = resp.to_google_chat_message()
        assert msg == {"text": "Hello!"}

    def test_call_link_response_has_cards(self):
        resp = BotResponse(
            text="Your call is ready!",
            call_link="https://example.com/join/room-1?token=abc",
            session_id="sess-1",
            audit_score=98.0,
            starlink_quality=0.85,
            optimal_pop="LAX",
        )
        msg = resp.to_google_chat_message()
        assert "cardsV2" in msg

    def test_call_link_response_contains_join_button(self):
        resp = BotResponse(
            text="Ready!",
            call_link="https://example.com/join",
        )
        msg = resp.to_google_chat_message()
        card_str = str(msg)
        assert "Join Call" in card_str


class TestGemini3Bot:

    def setup_method(self):
        self.bot = Gemini3Bot()

    # ------------------------------------------------------------------
    # ADDED_TO_SPACE
    # ------------------------------------------------------------------

    def test_added_to_space_dm_returns_help(self):
        event = {"type": "ADDED_TO_SPACE", "space": {"type": "DM", "name": "spaces/dm-1"}}
        response = self.bot.handle_message(event)
        assert "text" in response

    def test_added_to_space_room_returns_greeting(self):
        event = {"type": "ADDED_TO_SPACE", "space": {"type": "ROOM", "name": "spaces/room-1"}}
        response = self.bot.handle_message(event)
        assert "text" in response

    # ------------------------------------------------------------------
    # /help
    # ------------------------------------------------------------------

    def test_help_command(self):
        event = {
            "type": "MESSAGE",
            "message": {"text": "/help"},
            "space": {"name": "spaces/test", "type": "ROOM"},
        }
        response = self.bot.handle_message(event)
        assert "/call" in response["text"]
        assert "/video" in response["text"]

    # ------------------------------------------------------------------
    # /call
    # ------------------------------------------------------------------

    def test_call_command_returns_response(self):
        event = {
            "type": "MESSAGE",
            "message": {"text": "/call"},
            "space": {"name": "spaces/test-call", "type": "ROOM"},
        }
        response = self.bot.handle_message(event)
        # Should produce either a card or a text message
        assert "cardsV2" in response or "text" in response

    def test_call_creates_webrtc_session(self):
        event = {
            "type": "MESSAGE",
            "message": {"text": "/call"},
            "space": {"name": "spaces/test-sess", "type": "ROOM"},
        }
        self.bot.handle_message(event)
        sessions = self.bot._webrtc.list_active_sessions()
        assert len(sessions) >= 1

    # ------------------------------------------------------------------
    # /video
    # ------------------------------------------------------------------

    def test_video_command_returns_response(self):
        event = {
            "type": "MESSAGE",
            "message": {"text": "/video"},
            "space": {"name": "spaces/test-video", "type": "ROOM"},
        }
        response = self.bot.handle_message(event)
        assert "cardsV2" in response or "text" in response

    # ------------------------------------------------------------------
    # /status
    # ------------------------------------------------------------------

    def test_status_command_returns_starlink_info(self):
        event = {
            "type": "MESSAGE",
            "message": {"text": "/status"},
            "space": {"name": "spaces/test-status", "type": "ROOM"},
        }
        response = self.bot.handle_message(event)
        text = response.get("text", "") or str(response)
        assert "Starlink" in text or "cardsV2" in response

    # ------------------------------------------------------------------
    # Unknown command
    # ------------------------------------------------------------------

    def test_unknown_message_returns_nudge(self):
        event = {
            "type": "MESSAGE",
            "message": {"text": "hello there"},
            "space": {"name": "spaces/nudge-test", "type": "ROOM"},
        }
        response = self.bot.handle_message(event)
        assert "text" in response

    # ------------------------------------------------------------------
    # CARD_CLICKED
    # ------------------------------------------------------------------

    def test_card_clicked_returns_empty(self):
        event = {"type": "CARD_CLICKED"}
        response = self.bot.handle_message(event)
        assert response == {"text": ""}

    # ------------------------------------------------------------------
    # Audit block
    # ------------------------------------------------------------------

    def test_call_blocked_when_audit_fails(self, monkeypatch):
        from security_auditor import AuditReport, AuditResult

        fail_report = AuditReport(
            result=AuditResult.FAIL,
            score=5.0,
            issues=["malicious session detected"],
        )
        monkeypatch.setattr(
            self.bot._auditor,
            "audit_session",
            lambda *a, **kw: fail_report,
        )
        response = self.bot._initiate_call("spaces/blocked", "audio")
        assert "blocked" in response.text.lower() or "CertiK" in response.text

    # ------------------------------------------------------------------
    # Starlink offline
    # ------------------------------------------------------------------

    def test_call_blocked_when_starlink_offline(self, monkeypatch):
        from starlink_link import StarlinkStatus
        import time

        offline = StarlinkStatus(
            uptime_seconds=0,
            snr_db=0,
            downlink_throughput_mbps=0,
            uplink_throughput_mbps=0,
            latency_ms=0,
            obstruction_pct=0,
            is_online=False,
        )
        monkeypatch.setattr(self.bot._starlink, "get_status", lambda: offline)
        response = self.bot._initiate_call("spaces/offline", "audio")
        assert "offline" in response.text.lower() or "satellite" in response.text.lower()

    # ------------------------------------------------------------------
    # bypass_old_chat
    # ------------------------------------------------------------------

    def test_bypass_old_chat_returns_string(self):
        result = self.bot.bypass_old_chat()
        assert isinstance(result, str)
        assert len(result) > 0

    # ------------------------------------------------------------------
    # Bot attributes
    # ------------------------------------------------------------------

    def test_bot_has_engine_attribute(self):
        assert self.bot.engine == "NextGen-VoIP-Protocol"

    def test_bot_has_security_attribute(self):
        assert self.bot.security == "CertiK-Skynet-Verified"

    def test_bot_has_link_attribute(self):
        assert self.bot.link == "Starlink-Satellite-Active"
