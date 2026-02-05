"""
Tests that the Docker image builds correctly and contains
the expected software, versions, and file structure.
"""

import json
import pytest
from conftest import docker_run, IMAGE_NAME


class TestImageContents:
    """Verify the image has the right software installed."""

    def test_node_installed(self, docker_image):
        result = docker_run(docker_image, "node", "--version")
        version = result.stdout.strip()
        # Must be Node 22+
        major = int(version.lstrip("v").split(".")[0])
        assert major >= 22, f"Expected Node >= 22, got {version}"

    def test_python_installed(self, docker_image):
        result = docker_run(docker_image, "python3", "--version")
        assert "Python 3" in result.stdout

    def test_openclaw_installed(self, docker_image):
        # openclaw binary should exist and be callable
        result = docker_run(docker_image, "which", "openclaw", check=False)
        assert result.returncode == 0, "openclaw binary not found in PATH"

    def test_tweek_installed(self, docker_image):
        result = docker_run(docker_image, "which", "tweek", check=False)
        assert result.returncode == 0, "tweek binary not found in PATH"

    def test_tweek_importable(self, docker_image):
        result = docker_run(
            docker_image,
            "python3", "-c", "import tweek; print('ok')",
            check=False,
        )
        assert result.returncode == 0, f"Failed to import tweek: {result.stderr}"

    def test_curl_installed(self, docker_image):
        """curl is needed for healthchecks."""
        result = docker_run(docker_image, "which", "curl", check=False)
        assert result.returncode == 0, "curl not found — needed for healthchecks"

    def test_tini_installed(self, docker_image):
        """tini is the init process for proper signal handling."""
        result = docker_run(docker_image, "which", "tini", check=False)
        assert result.returncode == 0, "tini not found — needed as PID 1"


class TestImageConfig:
    """Verify image configuration and metadata."""

    def test_entrypoint_exists(self, docker_image):
        result = docker_run(docker_image, "test", "-x", "/opt/hard-shell/entrypoint.sh")
        assert result.returncode == 0

    def test_healthcheck_exists(self, docker_image):
        result = docker_run(docker_image, "test", "-x", "/opt/hard-shell/healthcheck.sh")
        assert result.returncode == 0

    def test_openclaw_config_exists(self, docker_image):
        result = docker_run(docker_image, "test", "-f", "/opt/hard-shell/config/openclaw.json")
        assert result.returncode == 0

    def test_tweek_config_exists(self, docker_image):
        result = docker_run(docker_image, "test", "-f", "/opt/hard-shell/config/tweek.yaml")
        assert result.returncode == 0

    def test_openclaw_config_valid_json(self, docker_image):
        result = docker_run(docker_image, "cat", "/opt/hard-shell/config/openclaw.json")
        config = json.loads(result.stdout)
        assert "plugins" in config
        assert "tweek-security" in config["plugins"]["entries"]

    def test_tweek_plugin_enabled(self, docker_image):
        result = docker_run(docker_image, "cat", "/opt/hard-shell/config/openclaw.json")
        config = json.loads(result.stdout)
        plugin = config["plugins"]["entries"]["tweek-security"]
        assert plugin["enabled"] is True

    def test_exposed_port(self, image_inspect):
        exposed = image_inspect.get("Config", {}).get("ExposedPorts", {})
        assert "18789/tcp" in exposed, f"Port 18789 not exposed. Got: {exposed}"

    def test_healthcheck_configured(self, image_inspect):
        hc = image_inspect.get("Config", {}).get("Healthcheck", {})
        assert hc, "No HEALTHCHECK configured in image"
        test_cmd = " ".join(hc.get("Test", []))
        assert "healthcheck.sh" in test_cmd

    def test_env_vars(self, image_inspect):
        env = image_inspect.get("Config", {}).get("Env", [])
        env_dict = dict(e.split("=", 1) for e in env if "=" in e)
        assert env_dict.get("NODE_ENV") == "production"
        assert env_dict.get("HARD_SHELL") == "1"
        assert env_dict.get("TWEEK_PRESET") == "cautious"


class TestImageSecurity:
    """Verify the image is built securely."""

    def test_runs_as_non_root(self, docker_image):
        result = docker_run(docker_image, "whoami")
        user = result.stdout.strip()
        assert user != "root", "Container should NOT run as root"
        assert user == "node", f"Expected user 'node', got '{user}'"

    def test_user_id_is_1000(self, docker_image):
        result = docker_run(docker_image, "id", "-u")
        uid = result.stdout.strip()
        assert uid == "1000", f"Expected UID 1000, got {uid}"

    def test_home_dir_permissions(self, docker_image):
        result = docker_run(docker_image, "stat", "-c", "%a", "/home/node/.openclaw")
        perms = result.stdout.strip()
        assert perms == "700", f"Expected 700 on .openclaw, got {perms}"

    def test_tweek_dir_permissions(self, docker_image):
        result = docker_run(docker_image, "stat", "-c", "%a", "/home/node/.tweek")
        perms = result.stdout.strip()
        assert perms == "700", f"Expected 700 on .tweek, got {perms}"

    def test_no_root_write(self, docker_image):
        """Non-root user shouldn't be able to write to system dirs."""
        result = docker_run(
            docker_image,
            "sh", "-c", "touch /usr/local/test_file 2>&1",
            check=False,
        )
        assert result.returncode != 0, "Non-root user could write to /usr/local"

    def test_node_env_is_production(self, image_inspect):
        env = image_inspect.get("Config", {}).get("Env", [])
        env_dict = dict(e.split("=", 1) for e in env if "=" in e)
        assert env_dict.get("NODE_ENV") == "production"

    def test_no_build_tools(self, docker_image):
        """Build tools should be removed in the runtime stage."""
        for tool in ["gcc", "make", "g++"]:
            result = docker_run(docker_image, "which", tool, check=False)
            assert result.returncode != 0, f"{tool} should not be in the runtime image"
