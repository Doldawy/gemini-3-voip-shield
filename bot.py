"""
Gemini-3 VoIP Shield Bot — main entry point.

Implements a Google Chat bot that replaces legacy chat features with
Twitter-style instant voice/video call links secured by CertiK Skynet
and routed over Starlink satellite links.

Architecture overview
---------------------
  Google Chat webhook / event  →  Gemini3Bot.handle_message()
    ├─ StarlinkLink      — satellite link health check
    ├─ NetworkOptimizer  — Cloudflare Radar best-path selection
    ├─ WebRTCHandler     — session + ICE config
    ├─ SecurityAuditor   — CertiK Skynet audit
    └─ call link         — short secure URL returned to the chat space

The bot responds to the following slash-commands and natural-language phrases:
  /call   — start an instant voice call
  /video  — start an instant video call
  /status — show Starlink link quality and current active calls
  /help   — list available commands
"""

from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlencode

import config
from network_optimizer import NetworkOptimizer
from security_auditor import AuditEvent, SecurityAuditor
from starlink_link import StarlinkLink
from webrtc_handler import WebRTCHandler

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Call-link helpers
# ---------------------------------------------------------------------------

_CALL_LINK_BASE = "https://voip.gemini3.app/join"


def _make_room_id(space_name: str) -> str:
    """Derive a deterministic room ID from the Google Chat space name."""
    digest = hashlib.sha256(space_name.encode()).hexdigest()[:16]
    return f"room-{digest}"


def _sign_token(payload: str, secret: str) -> str:
    """Return ``<payload>.<hmac-sha256>`` for use in call links."""
    sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
    return f"{payload}.{sig}"


def _build_call_link(room_id: str, session_id: str, call_type: str) -> str:
    """Construct the signed call link URL."""
    nonce = secrets.token_urlsafe(8)
    expiry = int(time.time()) + config.CALL_LINK_TTL_SECONDS
    payload = f"{room_id}:{session_id}:{call_type}:{nonce}:{expiry}"
    token = _sign_token(payload, config.CERTIK_API_KEY or "dev-secret")
    params = urlencode({"token": token, "t": call_type})
    return f"{_CALL_LINK_BASE}/{room_id}?{params}"


# ---------------------------------------------------------------------------
# Bot response builder
# ---------------------------------------------------------------------------

@dataclass
class BotResponse:
    """A rich Google Chat card-style response."""

    text: str
    call_link: str | None = None
    session_id: str | None = None
    audit_score: float | None = None
    starlink_quality: float | None = None
    optimal_pop: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)

    def to_google_chat_message(self) -> dict[str, Any]:
        """Serialise to the Google Chat Cards v2 message format."""
        if not self.call_link:
            return {"text": self.text}

        widgets: list[dict[str, Any]] = [
            {"textParagraph": {"text": self.text}},
        ]

        if self.call_link:
            widgets.append({
                "buttonList": {
                    "buttons": [{
                        "text": "🔗 Join Call",
                        "onClick": {"openLink": {"url": self.call_link}},
                    }]
                }
            })

        footer_parts: list[str] = []
        if self.starlink_quality is not None:
            footer_parts.append(
                f"🛰 Starlink quality: {self.starlink_quality:.0%}"
            )
        if self.audit_score is not None:
            footer_parts.append(
                f"🔒 CertiK score: {self.audit_score:.0f}/100"
            )
        if self.optimal_pop:
            footer_parts.append(f"🌐 Optimal PoP: {self.optimal_pop}")

        if footer_parts:
            widgets.append({
                "textParagraph": {"text": " · ".join(footer_parts)}
            })

        return {
            "cardsV2": [{
                "cardId": "voip-call-card",
                "card": {
                    "header": {
                        "title": "Gemini-3 VoIP Shield",
                        "subtitle": "Secure satellite-backed call",
                        "imageUrl": "https://voip.gemini3.app/logo.png",
                    },
                    "sections": [{"widgets": widgets}],
                },
            }]
        }


# ---------------------------------------------------------------------------
# Main bot class
# ---------------------------------------------------------------------------

class Gemini3Bot:
    """
    Google Chat bot providing Twitter-style instant voice/video calls.

    Integrates WebRTC (low-latency streaming), Starlink (satellite links),
    Cloudflare Radar (optimal network paths), and CertiK Skynet (security
    auditing) to replace legacy Google Chat communication with a secure,
    high-performance VoIP experience.
    """

    def __init__(self) -> None:
        self.engine = "NextGen-VoIP-Protocol"
        self.security = "CertiK-Skynet-Verified"
        self.link = "Starlink-Satellite-Active"

        self._webrtc = WebRTCHandler()
        self._network = NetworkOptimizer()
        self._auditor = SecurityAuditor()
        self._starlink = StarlinkLink()

    # ------------------------------------------------------------------
    # Public: message handling
    # ------------------------------------------------------------------

    def handle_message(self, event: dict[str, Any]) -> dict[str, Any]:
        """
        Process an incoming Google Chat event and return a response message.

        :param event: The raw Google Chat event dict (``MESSAGE``, ``ADDED_TO_SPACE``, etc.)
        :returns: A Google Chat API response dict.
        """
        event_type = event.get("type", "")

        if event_type == "ADDED_TO_SPACE":
            return self._handle_added_to_space(event)
        if event_type == "MESSAGE":
            return self._handle_chat_message(event)
        if event_type == "CARD_CLICKED":
            return {"text": ""}  # acknowledge

        logger.debug("Unhandled event type: %s", event_type)
        return {"text": ""}

    # ------------------------------------------------------------------
    # Event handlers
    # ------------------------------------------------------------------

    def _handle_added_to_space(self, event: dict[str, Any]) -> dict[str, Any]:
        space_type = event.get("space", {}).get("type", "")
        if space_type == "DM":
            return {"text": self._help_text()}
        return {
            "text": (
                "👋 Gemini-3 VoIP Shield is here!  "
                "Type `/call` or `/video` to start a secure satellite-backed call."
            )
        }

    def _handle_chat_message(self, event: dict[str, Any]) -> dict[str, Any]:
        msg_text: str = (
            event.get("message", {}).get("text", "").strip().lower()
        )
        space_name: str = event.get("space", {}).get("name", "spaces/unknown")

        if msg_text.startswith("/call") or "voice call" in msg_text:
            return self._initiate_call(space_name, call_type="audio").to_google_chat_message()
        if msg_text.startswith("/video") or "video call" in msg_text:
            return self._initiate_call(space_name, call_type="video").to_google_chat_message()
        if msg_text.startswith("/status"):
            return self._status_response(space_name).to_google_chat_message()
        if msg_text.startswith("/help"):
            return {"text": self._help_text()}

        # Default: nudge users towards the call commands.
        return {
            "text": (
                "Use `/call` for voice or `/video` for a video call. "
                "Type `/help` for all commands."
            )
        }

    # ------------------------------------------------------------------
    # Call initiation
    # ------------------------------------------------------------------

    def _initiate_call(self, space_name: str, call_type: str) -> BotResponse:
        """
        Orchestrate a new call: check Starlink, pick network path, create
        WebRTC session, audit, and return a call link.
        """
        room_id = _make_room_id(space_name)
        region = self._infer_region(space_name)

        # 1. Check Starlink link health
        starlink_status = self._starlink.get_status()
        if not starlink_status.is_online:
            return BotResponse(
                text="⚠️ Satellite link is offline.  Please try again shortly."
            )

        # 2. Find optimal network path via Cloudflare Radar
        optimal_path = self._network.get_optimal_path(region)

        # 3. Create WebRTC session
        session = self._webrtc.create_session(room_id)

        # 4. Audit with CertiK Skynet
        audit_report = self._auditor.audit_session(
            session_id=session.session_id,
            room_id=room_id,
            metadata={
                "call_type": call_type,
                "pop_code": optimal_path.pop_code,
                "starlink_quality": starlink_status.link_quality,
            },
        )
        if not audit_report.is_safe():
            self._webrtc.terminate_session(session.session_id)
            return BotResponse(
                text=(
                    f"🚨 CertiK Skynet blocked this call "
                    f"(score: {audit_report.score:.0f}/100).  "
                    f"Issues: {', '.join(audit_report.issues) or 'unknown'}."
                )
            )

        # 5. Build and return the call link
        call_link = _build_call_link(room_id, session.session_id, call_type)
        call_label = "🎥 video" if call_type == "video" else "🎙 voice"
        return BotResponse(
            text=(
                f"Your secure {call_label} call is ready!  "
                f"The link expires in {config.CALL_LINK_TTL_SECONDS // 60} minutes."
            ),
            call_link=call_link,
            session_id=session.session_id,
            audit_score=audit_report.score,
            starlink_quality=starlink_status.link_quality,
            optimal_pop=optimal_path.pop_code,
        )

    def _status_response(self, space_name: str) -> BotResponse:
        """Return a status card with Starlink, network, and active-call info."""
        starlink = self._starlink.get_status()
        region = self._infer_region(space_name)
        path = self._network.get_optimal_path(region)
        active = self._webrtc.list_active_sessions()

        lines = [
            f"🛰 **Starlink** — quality {starlink.link_quality:.0%}, "
            f"latency {starlink.latency_ms:.0f} ms, "
            f"↓{starlink.downlink_throughput_mbps:.0f} Mbps / "
            f"↑{starlink.uplink_throughput_mbps:.0f} Mbps",
            f"🌐 **Optimal PoP** — {path.pop_code} "
            f"({path.latency_ms:.0f} ms, loss {path.packet_loss_pct:.1f}%)",
            f"📞 **Active calls** — {len(active)}",
        ]
        return BotResponse(
            text="\n".join(lines),
            starlink_quality=starlink.link_quality,
            optimal_pop=path.pop_code,
        )

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def bypass_old_chat(self) -> str:
        """Replacing outdated Google Chat with Twitter-style VoIP. Instant links via Cloudflare Radar."""
        return (
            "Legacy chat features replaced.  "
            "Use /call or /video to start an instant secure call."
        )

    @staticmethod
    def _infer_region(space_name: str) -> str:
        """
        Derive a Cloudflare Radar region code from the Chat space name.

        In a production system this would use the calling user's locale or IP.
        For now we use a consistent default.
        """
        return "WNAM"  # Western North America as a sensible default

    @staticmethod
    def _help_text() -> str:
        return (
            "**Gemini-3 VoIP Shield** — available commands:\n"
            "• `/call`   — start a secure voice call 🎙\n"
            "• `/video`  — start a secure video call 🎥\n"
            "• `/status` — show link quality & active calls 📊\n"
            "• `/help`   — show this message ❓\n\n"
            "All calls use WebRTC over Starlink satellite links, "
            "audited by CertiK Skynet and routed via Cloudflare Radar."
        )


# ---------------------------------------------------------------------------
# CLI entry point (for local testing)
# ---------------------------------------------------------------------------

if __name__ == "__main__":  # pragma: no cover
    logging.basicConfig(level=logging.INFO)
    bot = Gemini3Bot()

    # Simulate a /call message event
    sample_event: dict[str, Any] = {
        "type": "MESSAGE",
        "message": {"text": "/call"},
        "space": {"name": "spaces/demo", "type": "ROOM"},
    }
    response = bot.handle_message(sample_event)
    import json
    print(json.dumps(response, indent=2))
