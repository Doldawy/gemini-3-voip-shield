"""
Centralised configuration for the Gemini-3 VoIP Shield bot.

All tuneable values live here so that environment-specific deployments can
override them via environment variables without touching application code.
"""

import os

# ---------------------------------------------------------------------------
# Google Chat / OAuth
# ---------------------------------------------------------------------------
GOOGLE_CHAT_PROJECT_ID: str = os.environ.get("GOOGLE_CHAT_PROJECT_ID", "")
GOOGLE_APPLICATION_CREDENTIALS: str = os.environ.get(
    "GOOGLE_APPLICATION_CREDENTIALS", ""
)

# ---------------------------------------------------------------------------
# WebRTC / STUN / TURN
# ---------------------------------------------------------------------------
STUN_SERVERS: list[str] = [
    "stun:stun.l.google.com:19302",
    "stun:stun1.l.google.com:19302",
]

TURN_SERVER_URL: str = os.environ.get("TURN_SERVER_URL", "")
TURN_USERNAME: str = os.environ.get("TURN_USERNAME", "")
TURN_CREDENTIAL: str = os.environ.get("TURN_CREDENTIAL", "")

# Maximum bitrate in kbps for video streams.
VIDEO_MAX_BITRATE_KBPS: int = int(os.environ.get("VIDEO_MAX_BITRATE_KBPS", "2500"))
# Maximum bitrate in kbps for audio streams.
AUDIO_MAX_BITRATE_KBPS: int = int(os.environ.get("AUDIO_MAX_BITRATE_KBPS", "128"))

# How long (seconds) a generated call link remains valid.
CALL_LINK_TTL_SECONDS: int = int(os.environ.get("CALL_LINK_TTL_SECONDS", "3600"))

# ---------------------------------------------------------------------------
# Cloudflare Radar
# ---------------------------------------------------------------------------
CLOUDFLARE_API_TOKEN: str = os.environ.get("CLOUDFLARE_API_TOKEN", "")
CLOUDFLARE_RADAR_BASE_URL: str = "https://api.cloudflare.com/client/v4/radar"

# ---------------------------------------------------------------------------
# CertiK Skynet
# ---------------------------------------------------------------------------
CERTIK_API_KEY: str = os.environ.get("CERTIK_API_KEY", "")
CERTIK_SKYNET_BASE_URL: str = "https://api.certik.com/skynet/v1"
CERTIK_AUDIT_ENABLED: bool = os.environ.get("CERTIK_AUDIT_ENABLED", "true").lower() == "true"

# ---------------------------------------------------------------------------
# Starlink
# ---------------------------------------------------------------------------
STARLINK_GRPC_HOST: str = os.environ.get("STARLINK_GRPC_HOST", "192.168.100.1")
STARLINK_GRPC_PORT: int = int(os.environ.get("STARLINK_GRPC_PORT", "9200"))
STARLINK_LINK_QUALITY_THRESHOLD: float = float(
    os.environ.get("STARLINK_LINK_QUALITY_THRESHOLD", "0.7")
)
