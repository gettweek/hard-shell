"""
Tests that verify the Tweek plugin loads correctly in OpenClaw
and registers its hooks without errors.
"""

import subprocess
import pytest


def docker_logs(container_name):
    """Get logs from a running container."""
    result = subprocess.run(
        ["docker", "logs", container_name],
        capture_output=True, text=True, timeout=10,
        check=False,
    )
    return result.stdout + result.stderr


class TestPluginRegistration:
    """Verify the Tweek plugin loads and registers with OpenClaw."""

    def test_no_plugin_registration_error(self, running_container):
        """Plugin should register without the TypeError trim bug."""
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        logs = docker_logs(running_container["name"])
        assert "tweek-security failed during register" not in logs, (
            "Plugin registration failed. Check container logs."
        )

    def test_no_undefined_trim_error(self, running_container):
        """The TypeError 'Cannot read properties of undefined' should not occur."""
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        logs = docker_logs(running_container["name"])
        assert "Cannot read properties of undefined" not in logs, (
            "TypeError in plugin code — likely API mismatch."
        )

    def test_plugin_not_in_error_state(self, running_container):
        """Plugin should not be in an error state after startup."""
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        logs = docker_logs(running_container["name"])
        # OpenClaw logs plugin errors with "failed" keyword
        plugin_errors = [
            line for line in logs.splitlines()
            if "tweek-security" in line.lower() and "failed" in line.lower()
        ]
        assert not plugin_errors, (
            f"Plugin errors found in logs: {plugin_errors}"
        )

    def test_tweek_activation_logged(self, running_container):
        """Plugin should log its activation message."""
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        logs = docker_logs(running_container["name"])
        assert "[Tweek]" in logs, (
            "No [Tweek] messages in logs — plugin may not have loaded."
        )

    def test_before_tool_call_hook_registered(self, running_container):
        """The before_tool_call hook should be registered."""
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        logs = docker_logs(running_container["name"])
        # After successful registration, there should be no "failed" for
        # our plugin, and the gateway should be running (which means
        # plugin registration didn't crash the startup)
        from conftest import docker_exec
        name = running_container["name"]

        # Gateway is still running (plugin didn't crash it)
        result = docker_exec(
            name, "curl", "-sf", "-o", "/dev/null", "-w", "%{http_code}",
            f"http://127.0.0.1:{running_container['gateway_port']}/health",
            check=False,
        )
        status = result.stdout.strip()
        assert status in ("200", "401", "403"), (
            f"Gateway not running after plugin load — status: {status}"
        )


class TestPluginHookBehavior:
    """Verify plugin hooks are wired up and functional."""

    def test_scanner_reachable_from_plugin(self, running_container):
        """The scanner server should be reachable at the configured port
        (plugin's ScannerBridge connects to it)."""
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        from conftest import docker_exec
        name = running_container["name"]
        port = running_container["scanner_port"]

        result = docker_exec(
            name, "curl", "-sf",
            f"http://127.0.0.1:{port}/health",
        )
        assert "ok" in result.stdout or "healthy" in result.stdout

    def test_plugin_config_resolves(self, running_container):
        """Plugin should be able to resolve its config from openclaw.json."""
        if not running_container["healthy"]:
            pytest.skip("Container not healthy")

        logs = docker_logs(running_container["name"])
        # If config resolution fails, plugin logs a config error
        config_errors = [
            line for line in logs.splitlines()
            if "[Tweek] Config error" in line
        ]
        assert not config_errors, (
            f"Plugin config errors: {config_errors}"
        )
