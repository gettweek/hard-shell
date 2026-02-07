"""
Hard Shell Logging Tests

Tests structured JSONL logging, audit trail, and security (no secrets in logs).
Requires a running container via the `running_container` fixture.
"""

import json
import subprocess
import time

import pytest

from conftest import CONTAINER_NAME, docker_exec, docker_run


class TestLogDirectory:
    """Verify the log directory exists in the image with correct permissions."""

    def test_log_directory_exists(self, docker_image):
        """Log directory exists in the image."""
        result = docker_run(docker_image, "test", "-d", "/home/node/logs", check=False)
        assert result.returncode == 0, "/home/node/logs directory missing from image"

    def test_log_directory_owned_by_node(self, docker_image):
        """Log directory is owned by the node user."""
        result = docker_run(docker_image, "stat", "-c", "%U", "/home/node/logs", check=False)
        assert result.stdout.strip() == "node", f"Expected owner 'node', got '{result.stdout.strip()}'"

    def test_log_directory_permissions(self, docker_image):
        """Log directory has 755 permissions."""
        result = docker_run(docker_image, "stat", "-c", "%a", "/home/node/logs", check=False)
        assert result.stdout.strip() == "755", f"Expected 755, got '{result.stdout.strip()}'"


class TestStructuredLogging:
    """Verify structured JSONL log output from a running container."""

    def test_app_log_exists(self, running_container):
        """hard-shell.log is created after container startup."""
        assert running_container["healthy"], "Container not healthy"
        result = docker_exec(
            running_container["name"],
            "test", "-f", "/home/node/logs/hard-shell.log",
            check=False,
        )
        assert result.returncode == 0, "hard-shell.log not found in running container"

    def test_app_log_is_valid_jsonl(self, running_container):
        """Every line in hard-shell.log is valid JSON."""
        assert running_container["healthy"], "Container not healthy"
        result = docker_exec(
            running_container["name"],
            "cat", "/home/node/logs/hard-shell.log",
            check=False,
        )
        lines = [l for l in result.stdout.strip().split("\n") if l.strip()]
        assert len(lines) > 0, "App log is empty"

        for i, line in enumerate(lines):
            try:
                obj = json.loads(line)
                assert "ts" in obj, f"Line {i+1}: missing 'ts' field"
                assert "level" in obj, f"Line {i+1}: missing 'level' field"
                assert "component" in obj, f"Line {i+1}: missing 'component' field"
                assert "msg" in obj, f"Line {i+1}: missing 'msg' field"
            except json.JSONDecodeError as e:
                pytest.fail(f"Line {i+1} is not valid JSON: {e}\n  Content: {line[:200]}")

    def test_log_levels_are_valid(self, running_container):
        """All log levels are INFO, WARN, or ERROR."""
        assert running_container["healthy"], "Container not healthy"
        result = docker_exec(
            running_container["name"],
            "cat", "/home/node/logs/hard-shell.log",
            check=False,
        )
        valid_levels = {"INFO", "WARN", "ERROR"}
        for line in result.stdout.strip().split("\n"):
            if not line.strip():
                continue
            obj = json.loads(line)
            assert obj["level"] in valid_levels, f"Unexpected level: {obj['level']}"


class TestAuditLog:
    """Verify security audit trail."""

    def test_audit_log_exists(self, running_container):
        """audit.log is created after container startup."""
        assert running_container["healthy"], "Container not healthy"
        result = docker_exec(
            running_container["name"],
            "test", "-f", "/home/node/logs/audit.log",
            check=False,
        )
        assert result.returncode == 0, "audit.log not found"

    def test_audit_log_has_startup_event(self, running_container):
        """Audit log contains a startup event."""
        assert running_container["healthy"], "Container not healthy"
        result = docker_exec(
            running_container["name"],
            "cat", "/home/node/logs/audit.log",
            check=False,
        )
        lines = result.stdout.strip().split("\n")
        events = [json.loads(l)["event"] for l in lines if l.strip()]
        assert "startup" in events, f"No startup event in audit log. Events: {events}"

    def test_audit_log_has_ready_event(self, running_container):
        """Audit log contains a ready event with timing info."""
        assert running_container["healthy"], "Container not healthy"
        result = docker_exec(
            running_container["name"],
            "cat", "/home/node/logs/audit.log",
            check=False,
        )
        lines = result.stdout.strip().split("\n")
        ready_events = [json.loads(l) for l in lines if l.strip() and '"ready"' in l]
        assert len(ready_events) > 0, "No ready event in audit log"
        detail = ready_events[-1]["detail"]
        assert "total_ms" in detail, "Ready event missing total_ms"

    def test_audit_log_is_valid_jsonl(self, running_container):
        """Every line in audit.log is valid JSON."""
        assert running_container["healthy"], "Container not healthy"
        result = docker_exec(
            running_container["name"],
            "cat", "/home/node/logs/audit.log",
            check=False,
        )
        for i, line in enumerate(result.stdout.strip().split("\n")):
            if not line.strip():
                continue
            try:
                obj = json.loads(line)
                assert "ts" in obj, f"Line {i+1}: missing 'ts'"
                assert "event" in obj, f"Line {i+1}: missing 'event'"
                assert "detail" in obj, f"Line {i+1}: missing 'detail'"
            except json.JSONDecodeError as e:
                pytest.fail(f"Audit line {i+1} is not valid JSON: {e}")


class TestNoSecretsInLogs:
    """Verify no API keys or sensitive data leak into log files."""

    SECRET_PATTERNS = [
        r"sk-[a-zA-Z0-9]{20,}",       # Anthropic/OpenAI key prefix
        r"AKIA[A-Z0-9]{16}",           # AWS access key
        r"-----BEGIN.*PRIVATE KEY",     # PEM private key
    ]

    def test_no_secrets_in_app_log(self, running_container):
        """App log contains no secret patterns."""
        assert running_container["healthy"], "Container not healthy"
        result = docker_exec(
            running_container["name"],
            "cat", "/home/node/logs/hard-shell.log",
            check=False,
        )
        content = result.stdout
        for pattern in self.SECRET_PATTERNS:
            import re
            matches = re.findall(pattern, content)
            assert len(matches) == 0, f"Secret pattern '{pattern}' found in app log: {matches}"

    def test_no_secrets_in_audit_log(self, running_container):
        """Audit log contains no secret patterns."""
        assert running_container["healthy"], "Container not healthy"
        result = docker_exec(
            running_container["name"],
            "cat", "/home/node/logs/audit.log",
            check=False,
        )
        content = result.stdout
        for pattern in self.SECRET_PATTERNS:
            import re
            matches = re.findall(pattern, content)
            assert len(matches) == 0, f"Secret pattern '{pattern}' found in audit log: {matches}"


class TestStartupTiming:
    """Verify startup timing is logged."""

    def test_startup_timing_in_app_log(self, running_container):
        """App log contains a ready message with timing metrics."""
        assert running_container["healthy"], "Container not healthy"
        result = docker_exec(
            running_container["name"],
            "cat", "/home/node/logs/hard-shell.log",
            check=False,
        )
        lines = result.stdout.strip().split("\n")
        ready_lines = [
            json.loads(l) for l in lines
            if l.strip() and '"Hard Shell is running"' in l
        ]
        assert len(ready_lines) > 0, "No 'Hard Shell is running' entry in app log"
        extra = ready_lines[-1].get("extra", {})
        assert "scanner_ms" in extra, "Missing scanner_ms in startup timing"
        assert "gateway_ms" in extra, "Missing gateway_ms in startup timing"
        assert "total_ms" in extra, "Missing total_ms in startup timing"
        assert extra["total_ms"] > 0, "total_ms should be positive"
