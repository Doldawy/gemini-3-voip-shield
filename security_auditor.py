"""
CertiK Skynet security auditor for the Gemini-3 VoIP Shield bot.

Responsibilities:
  - Submit call-session events to the CertiK Skynet audit trail
  - Validate incoming call-link tokens against the Skynet trust oracle
  - Report anomalous session behaviour (e.g. unexpected topology changes)
  - Cache audit results to reduce API round-trips

When ``CERTIK_AUDIT_ENABLED`` is ``False`` or ``CERTIK_API_KEY`` is absent
the auditor operates in *dry-run* mode: all checks pass but nothing is sent
to the external API.  This keeps the rest of the call flow testable without
live credentials.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

import config

logger = logging.getLogger(__name__)

_TIMEOUT_SECONDS = 8
_CACHE_TTL_SECONDS = 300  # 5 minutes


class AuditResult(str, Enum):
    PASS = "pass"
    WARN = "warn"
    FAIL = "fail"
    SKIPPED = "skipped"


@dataclass
class AuditEvent:
    """A single security-audit event submitted to CertiK Skynet."""

    event_type: str
    session_id: str
    room_id: str
    timestamp: float = field(default_factory=time.time)
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "event_type": self.event_type,
            "session_id": self.session_id,
            "room_id": self.room_id,
            "timestamp": self.timestamp,
            "metadata": self.metadata,
        }


@dataclass
class AuditReport:
    """Result returned by :meth:`SecurityAuditor.audit_session`."""

    result: AuditResult
    score: float  # 0–100
    issues: list[str] = field(default_factory=list)
    recommendations: list[str] = field(default_factory=list)
    raw_response: dict[str, Any] = field(default_factory=dict)

    def is_safe(self) -> bool:
        """Return ``True`` when the audit result allows the call to proceed."""
        return self.result in (AuditResult.PASS, AuditResult.WARN, AuditResult.SKIPPED)

    def to_dict(self) -> dict[str, Any]:
        return {
            "result": self.result.value,
            "score": self.score,
            "issues": self.issues,
            "recommendations": self.recommendations,
        }


class SecurityAuditor:
    """
    Integrates with CertiK Skynet to audit VoIP call sessions.

    All public methods are safe to call in dry-run mode (when the API key is
    absent or auditing is disabled via config).
    """

    def __init__(self) -> None:
        self._api_key = config.CERTIK_API_KEY
        self._enabled = config.CERTIK_AUDIT_ENABLED and bool(self._api_key)
        self._cache: dict[str, tuple[AuditReport, float]] = {}

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def audit_session(
        self, session_id: str, room_id: str, metadata: dict[str, Any] | None = None
    ) -> AuditReport:
        """
        Audit a call session against CertiK Skynet.

        Returns a cached result if an audit was performed recently for the same
        *session_id*, otherwise queries the API.
        """
        cache_key = session_id
        cached = self._cache.get(cache_key)
        if cached:
            report, ts = cached
            if time.time() - ts < _CACHE_TTL_SECONDS:
                logger.debug("Returning cached audit result for %s", session_id)
                return report

        if not self._enabled:
            return self._skipped_report()

        event = AuditEvent(
            event_type="call_session_start",
            session_id=session_id,
            room_id=room_id,
            metadata=metadata or {},
        )
        report = self._submit_audit(event)
        self._cache[cache_key] = (report, time.time())
        return report

    def validate_call_token(self, token: str, secret: str) -> bool:
        """
        Verify the HMAC-SHA256 integrity of a call-link token.

        :param token: ``<payload>.<signature>`` string from the call link.
        :param secret: Shared secret used to generate the token.
        :returns: ``True`` if the signature is valid.
        """
        parts = token.split(".")
        if len(parts) != 2:
            return False
        payload, provided_sig = parts[0], parts[1]
        expected_sig = hmac.new(
            secret.encode(), payload.encode(), hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(expected_sig, provided_sig)

    def submit_event(self, event: AuditEvent) -> None:
        """Fire-and-forget: submit an audit event without waiting for a report."""
        if not self._enabled:
            return
        try:
            self._submit_audit(event)
        except Exception as exc:
            logger.warning("Audit event submission failed: %s", exc)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _submit_audit(self, event: AuditEvent) -> AuditReport:
        url = f"{config.CERTIK_SKYNET_BASE_URL}/audit"
        payload = json.dumps(event.to_dict()).encode("utf-8")
        req = Request(
            url,
            data=payload,
            method="POST",
            headers={
                "X-API-Key": self._api_key,
                "Content-Type": "application/json",
            },
        )
        try:
            with urlopen(req, timeout=_TIMEOUT_SECONDS) as resp:
                body = resp.read().decode("utf-8")
                data = json.loads(body)
                return self._parse_response(data)
        except (HTTPError, URLError) as exc:
            logger.error("CertiK Skynet API error: %s", exc)
            # Degrade gracefully — pass with a warning so the call is not blocked.
            return AuditReport(
                result=AuditResult.WARN,
                score=50.0,
                issues=[f"Skynet API unreachable: {exc}"],
                recommendations=["Retry audit when API is available."],
            )

    @staticmethod
    def _parse_response(data: dict[str, Any]) -> AuditReport:
        result_str = data.get("result", "pass").lower()
        try:
            result = AuditResult(result_str)
        except ValueError:
            result = AuditResult.WARN
        return AuditReport(
            result=result,
            score=float(data.get("score", 100.0)),
            issues=data.get("issues", []),
            recommendations=data.get("recommendations", []),
            raw_response=data,
        )

    @staticmethod
    def _skipped_report() -> AuditReport:
        return AuditReport(
            result=AuditResult.SKIPPED,
            score=100.0,
            issues=[],
            recommendations=[],
        )
