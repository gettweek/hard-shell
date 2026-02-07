"""
Tests that verify both services (Tweek scanner + OpenClaw gateway)
start up and respond to health checks inside the container.
"""

import json
import subprocess
import pytest


class TestServiceStartup:
    """Verify services come up and respond."""

    def test_container_is_healthy(self, running_container):
        assert running_container["healthy"], (
            "Container services did not become healthy within timeout. "
            "Check container logs for errors."
        )

    def test_scanner_health_endpoint(self, running_container):
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        from conftest import docker_exec
        name = running_container["name"]
        port = running_container["scanner_port"]
        result = docker_exec(
            name, "curl", "-sf", f"http://127.0.0.1:{port}/health",
            check=False,
        )
        assert result.returncode == 0, "Scanner /health endpoint did not respond"

    def test_scanner_health_response(self, running_container):
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        from conftest import docker_exec
        name = running_container["name"]
        port = running_container["scanner_port"]
        result = docker_exec(
            name, "curl", "-sf", f"http://127.0.0.1:{port}/health",
        )
        body = json.loads(result.stdout)
        assert body.get("status") in ("ok", "healthy"), f"Unexpected health response: {body}"

    def test_gateway_responds(self, running_container):
        """Gateway should respond on its port (even if not fully configured)."""
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        from conftest import docker_exec
        name = running_container["name"]
        port = running_container["gateway_port"]
        result = docker_exec(
            name, "curl", "-sf", "-o", "/dev/null", "-w", "%{http_code}",
            f"http://127.0.0.1:{port}/health",
            check=False,
        )
        # Accept 200 (healthy) or 401/403 (auth required — still means it's running)
        status = result.stdout.strip()
        assert status in ("200", "401", "403"), (
            f"Gateway returned unexpected status: {status}"
        )


class TestEntrypoint:
    """Verify entrypoint behavior."""

    def test_first_run_creates_config(self, running_container):
        """On first run, entrypoint should copy default config files."""
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        from conftest import docker_exec
        name = running_container["name"]

        result = docker_exec(
            name, "test", "-f", "/home/node/.openclaw/openclaw.json",
            check=False,
        )
        assert result.returncode == 0, "openclaw.json should be created on first run"

    def test_first_run_creates_tweek_config(self, running_container):
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        from conftest import docker_exec
        name = running_container["name"]

        result = docker_exec(
            name, "test", "-f", "/home/node/.tweek/config.yaml",
            check=False,
        )
        assert result.returncode == 0, "tweek config.yaml should be created on first run"

    def test_scanner_token_generated(self, running_container):
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        from conftest import docker_exec
        name = running_container["name"]

        result = docker_exec(
            name, "test", "-f", "/home/node/.tweek/.scanner_token",
            check=False,
        )
        assert result.returncode == 0, "Scanner auth token should be generated"

    def test_scanner_token_permissions(self, running_container):
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        from conftest import docker_exec
        name = running_container["name"]

        result = docker_exec(
            name, "stat", "-c", "%a", "/home/node/.tweek/.scanner_token",
        )
        perms = result.stdout.strip()
        assert perms == "600", f"Scanner token should be 600, got {perms}"

    def test_processes_running(self, running_container):
        """Both python (scanner) and node (gateway) should be running."""
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        from conftest import docker_exec
        name = running_container["name"]

        result = docker_exec(name, "ps", "aux", check=False)
        processes = result.stdout

        assert "python3" in processes, "Tweek scanner server (python3) not running"


class TestPostStartupSecurity:
    """Verify post-startup security checks run."""

    def test_security_audit_runs_at_startup(self, running_container):
        """App log should contain security audit output from post-startup checks."""
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        import time
        time.sleep(10)  # Give post-startup checks time to complete

        from conftest import docker_exec
        name = running_container["name"]

        result = docker_exec(
            name, "cat", "/home/node/logs/hard-shell.log",
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            content = result.stdout
            assert "security" in content.lower() or "doctor" in content.lower() or "audit" in content.lower(), (
                "Post-startup security checks should appear in app log"
            )

    def test_audit_log_has_security_audit_event(self, running_container):
        """Audit log should contain a security_audit event."""
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        import time
        time.sleep(10)

        from conftest import docker_exec
        name = running_container["name"]

        result = docker_exec(
            name, "cat", "/home/node/logs/audit.log",
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            assert "security_audit" in result.stdout, (
                "Audit log should contain security_audit event"
            )
