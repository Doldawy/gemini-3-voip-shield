"""Tests for CertiK Skynet security auditor."""

import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import pytest
from security_auditor import AuditEvent, AuditReport, AuditResult, SecurityAuditor


class TestSecurityAuditor:

    def setup_method(self):
        # Auditor without API key → dry-run mode
        self.auditor = SecurityAuditor()

    # ------------------------------------------------------------------
    # Dry-run / skipped behaviour
    # ------------------------------------------------------------------

    def test_audit_session_dry_run_returns_skipped(self):
        report = self.auditor.audit_session("sess-1", "room-1")
        assert report.result == AuditResult.SKIPPED

    def test_skipped_report_is_safe(self):
        report = self.auditor.audit_session("sess-2", "room-2")
        assert report.is_safe() is True

    def test_skipped_report_score_100(self):
        report = self.auditor.audit_session("sess-3", "room-3")
        assert report.score == 100.0

    # ------------------------------------------------------------------
    # Caching
    # ------------------------------------------------------------------

    def test_audit_session_cached(self):
        report1 = self.auditor.audit_session("sess-cache", "room-c")
        report2 = self.auditor.audit_session("sess-cache", "room-c")
        assert report1.result == report2.result

    # ------------------------------------------------------------------
    # validate_call_token
    # ------------------------------------------------------------------

    def test_validate_call_token_valid(self):
        import hmac
        import hashlib
        secret = "test-secret"
        payload = "room-xyz:session-abc:audio:nonce:9999"
        sig = hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()
        token = f"{payload}.{sig}"
        assert self.auditor.validate_call_token(token, secret) is True

    def test_validate_call_token_tampered(self):
        secret = "test-secret"
        token = "room-xyz:session-abc:audio:nonce:9999.invalidsig"
        assert self.auditor.validate_call_token(token, secret) is False

    def test_validate_call_token_malformed(self):
        assert self.auditor.validate_call_token("no-dot-here", "secret") is False

    # ------------------------------------------------------------------
    # AuditReport helpers
    # ------------------------------------------------------------------

    def test_audit_report_is_safe_for_pass(self):
        r = AuditReport(result=AuditResult.PASS, score=95.0)
        assert r.is_safe() is True

    def test_audit_report_is_safe_for_warn(self):
        r = AuditReport(result=AuditResult.WARN, score=70.0)
        assert r.is_safe() is True

    def test_audit_report_not_safe_for_fail(self):
        r = AuditReport(result=AuditResult.FAIL, score=10.0)
        assert r.is_safe() is False

    def test_audit_report_to_dict(self):
        r = AuditReport(
            result=AuditResult.PASS,
            score=99.0,
            issues=[],
            recommendations=["Keep it up"],
        )
        d = r.to_dict()
        assert d["result"] == "pass"
        assert d["score"] == 99.0
        assert d["recommendations"] == ["Keep it up"]

    # ------------------------------------------------------------------
    # AuditEvent
    # ------------------------------------------------------------------

    def test_audit_event_to_dict(self):
        event = AuditEvent(
            event_type="call_session_start",
            session_id="s-1",
            room_id="r-1",
            metadata={"call_type": "audio"},
        )
        d = event.to_dict()
        assert d["event_type"] == "call_session_start"
        assert d["metadata"]["call_type"] == "audio"

    # ------------------------------------------------------------------
    # submit_event (no-op in dry-run)
    # ------------------------------------------------------------------

    def test_submit_event_does_not_raise_in_dry_run(self):
        event = AuditEvent("test_event", "sess-evt", "room-evt")
        # Should not raise even though API is disabled
        self.auditor.submit_event(event)

    # ------------------------------------------------------------------
    # API response parsing
    # ------------------------------------------------------------------

    def test_parse_response_pass(self):
        data = {"result": "pass", "score": 95.0, "issues": [], "recommendations": []}
        report = SecurityAuditor._parse_response(data)
        assert report.result == AuditResult.PASS
        assert report.score == 95.0

    def test_parse_response_unknown_result_defaults_to_warn(self):
        data = {"result": "unknown_value", "score": 50.0}
        report = SecurityAuditor._parse_response(data)
        assert report.result == AuditResult.WARN
