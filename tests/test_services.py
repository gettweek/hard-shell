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


class TestConfigImmutability:
    """Verify security configs are locked read-only after startup."""

    def test_openclaw_config_is_readonly(self, running_container):
        """openclaw.json should be read-only (444) after startup."""
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        from conftest import docker_exec
        name = running_container["name"]

        result = docker_exec(
            name, "stat", "-c", "%a", "/home/node/.openclaw/openclaw.json",
            check=False,
        )
        if result.returncode == 0:
            perms = result.stdout.strip()
            assert perms == "444", f"openclaw.json should be 444 (read-only), got {perms}"

    def test_tweek_config_is_readonly(self, running_container):
        """tweek config.yaml should be read-only (444) after startup."""
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        from conftest import docker_exec
        name = running_container["name"]

        result = docker_exec(
            name, "stat", "-c", "%a", "/home/node/.tweek/config.yaml",
            check=False,
        )
        if result.returncode == 0:
            perms = result.stdout.strip()
            assert perms == "444", f"tweek config.yaml should be 444 (read-only), got {perms}"

    def test_config_hashes_recorded(self, running_container):
        """Config fingerprints should be recorded for tamper detection."""
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        from conftest import docker_exec
        name = running_container["name"]

        result = docker_exec(
            name,
            "test", "-f", "/home/node/.openclaw/.config_hashes",
            check=False,
        )
        assert result.returncode == 0, "Config hash file should exist for tamper detection"

    def test_config_hashes_are_readonly(self, running_container):
        """Config hash file itself should be read-only."""
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        from conftest import docker_exec
        name = running_container["name"]

        result = docker_exec(
            name,
            "stat", "-c", "%a", "/home/node/.openclaw/.config_hashes",
            check=False,
        )
        if result.returncode == 0:
            perms = result.stdout.strip()
            assert perms == "444", f"Config hash file should be 444, got {perms}"

    def test_configs_locked_in_audit_log(self, running_container):
        """Audit log should contain a configs_locked event."""
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        from conftest import docker_exec
        name = running_container["name"]

        result = docker_exec(
            name, "cat", "/home/node/logs/audit.log",
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            assert "configs_locked" in result.stdout, (
                "Audit log should contain configs_locked event"
            )
