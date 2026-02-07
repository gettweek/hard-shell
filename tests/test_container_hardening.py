"""
Tests that verify Docker container security hardening works
when the container is run with the production docker-compose settings.
"""

import subprocess
import pytest
from conftest import _docker, IMAGE_NAME


HARDENED_CONTAINER = "hard-shell-hardening-test"


@pytest.fixture(scope="module")
def hardened_container(docker_image):
    """
    Run a container with the same security opts as docker-compose.yml.
    This tests that the image works under hardened constraints.
    """
    _docker("rm", "-f", HARDENED_CONTAINER, check=False)

    # Match the exact security settings from docker-compose.yml
    # Volumes are required because root FS is read-only
    _docker(
        "run", "-d",
        "--name", HARDENED_CONTAINER,
        "--security-opt", "no-new-privileges:true",
        "--cap-drop", "ALL",
        "--read-only",
        "--tmpfs", "/tmp:size=100M",
        "--tmpfs", "/home/node/.cache:size=200M,uid=1000,gid=1000",
        "-v", f"{HARDENED_CONTAINER}-config:/home/node/.openclaw",
        "-v", f"{HARDENED_CONTAINER}-tweek:/home/node/.tweek",
        "-v", f"{HARDENED_CONTAINER}-workspace:/home/node/workspace",
        "--memory", "2g",
        "--cpus", "2.0",
        "--pids-limit", "256",
        docker_image,
    )

    # Wait for services to start under constrained resources
    import time
    for _ in range(30):
        result = _docker(
            "inspect", "-f", "{{.State.Running}}", HARDENED_CONTAINER,
            check=False,
        )
        if result.stdout.strip() == "true":
            break
        time.sleep(1)

    yield HARDENED_CONTAINER

    _docker("stop", HARDENED_CONTAINER, check=False)
    _docker("rm", HARDENED_CONTAINER, check=False)
    # Clean up named volumes
    for suffix in ("config", "tweek", "workspace"):
        _docker("volume", "rm", f"{HARDENED_CONTAINER}-{suffix}", check=False)


class TestHardenedContainer:
    """Verify the container runs correctly under hardened settings."""

    def test_container_is_running(self, hardened_container):
        result = _docker(
            "inspect", "-f", "{{.State.Running}}", hardened_container,
            check=False,
        )
        assert result.stdout.strip() == "true", "Container should be running"

    def test_read_only_root_fs(self, hardened_container):
        """Writes to the root filesystem should fail."""
        result = _docker(
            "exec", hardened_container,
            "sh", "-c", "touch /test_readonly 2>&1",
            check=False,
        )
        assert result.returncode != 0, "Write to read-only root FS should fail"

    def test_tmp_is_writable(self, hardened_container):
        """tmpfs /tmp should still be writable."""
        result = _docker(
            "exec", hardened_container,
            "sh", "-c", "touch /tmp/test_tmpfs && echo ok",
            check=False,
        )
        assert "ok" in result.stdout, "/tmp should be writable (tmpfs)"

    def test_cache_is_writable(self, hardened_container):
        """tmpfs .cache should still be writable."""
        result = _docker(
            "exec", hardened_container,
            "sh", "-c", "touch /home/node/.cache/test && echo ok",
            check=False,
        )
        assert "ok" in result.stdout, ".cache should be writable (tmpfs)"

    def test_not_running_as_root(self, hardened_container):
        result = _docker("exec", hardened_container, "whoami")
        assert result.stdout.strip() != "root"

    def test_cannot_escalate_privileges(self, hardened_container):
        """su/sudo should not work with no-new-privileges."""
        result = _docker(
            "exec", hardened_container,
            "sh", "-c", "su -c 'whoami' root 2>&1",
            check=False,
        )
        assert result.returncode != 0, "Privilege escalation should fail"

    def test_capabilities_dropped(self, hardened_container):
        """Verify no capabilities are available."""
        result = _docker(
            "exec", hardened_container,
            "sh", "-c", "cat /proc/1/status | grep CapEff",
            check=False,
        )
        if result.returncode == 0:
            cap_eff = result.stdout.strip().split()[-1]
            assert cap_eff == "0000000000000000", (
                f"Expected no capabilities, got {cap_eff}"
            )

    def test_pid_limit_enforced(self, hardened_container):
        """
        The container has a 256 PID limit. We can't easily verify the limit
        itself from inside, but we can confirm the container started fine
        under the constraint.
        """
        result = _docker(
            "inspect", "-f", "{{.HostConfig.PidsLimit}}",
            hardened_container,
        )
        limit = result.stdout.strip()
        assert limit == "256", f"Expected PID limit 256, got {limit}"

    def test_memory_limit_enforced(self, hardened_container):
        result = _docker(
            "inspect", "-f", "{{.HostConfig.Memory}}",
            hardened_container,
        )
        # 2g = 2147483648 bytes
        mem = int(result.stdout.strip())
        assert mem == 2147483648, f"Expected 2GB memory limit, got {mem}"


class TestSecurityHardening:
    """Verify gateway binding and credential security after startup."""

    def test_openclaw_dir_permissions(self, hardened_container):
        """~/.openclaw should be 700 (owner only)."""
        result = _docker(
            "exec", hardened_container,
            "stat", "-c", "%a", "/home/node/.openclaw",
            check=False,
        )
        if result.returncode == 0:
            perms = result.stdout.strip()
            assert perms == "700", f"Expected .openclaw to be 700, got {perms}"

    def test_tweek_dir_permissions(self, hardened_container):
        """~/.tweek should be 700 (owner only)."""
        result = _docker(
            "exec", hardened_container,
            "stat", "-c", "%a", "/home/node/.tweek",
            check=False,
        )
        if result.returncode == 0:
            perms = result.stdout.strip()
            assert perms == "700", f"Expected .tweek to be 700, got {perms}"

    def test_credentials_dir_permissions(self, hardened_container):
        """~/.openclaw/credentials should be 700 if it exists."""
        import time
        time.sleep(5)  # Give entrypoint time to harden
        result = _docker(
            "exec", hardened_container,
            "sh", "-c", "test -d /home/node/.openclaw/credentials && stat -c '%a' /home/node/.openclaw/credentials || echo 'nodir'",
            check=False,
        )
        output = result.stdout.strip()
        if output != "nodir":
            assert output == "700", f"Expected credentials dir to be 700, got {output}"

    def test_default_bind_is_loopback_outside_docker(self):
        """entrypoint.sh should default to loopback when not in Docker."""
        import os
        entrypoint = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts", "entrypoint.sh",
        )
        with open(entrypoint) as f:
            content = f.read()
        # The non-Docker fallback should resolve to loopback
        assert 'BIND_MODE="loopback"' in content, "Default bind mode should be loopback"

    def test_docker_detection_forces_lan(self):
        """entrypoint.sh should detect Docker and force --bind lan."""
        import os
        entrypoint = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts", "entrypoint.sh",
        )
        with open(entrypoint) as f:
            content = f.read()
        assert '/.dockerenv' in content, "Should detect /.dockerenv for Docker"
        assert 'IN_DOCKER=true' in content, "Should set IN_DOCKER flag"
        # Inside Docker, default should be lan (so Docker port forwarding works)
        assert 'BIND_MODE="lan"' in content, "Docker default should be lan"

    def test_bind_mode_lan_in_running_container(self, hardened_container):
        """Inside Docker, the gateway should bind lan for port forwarding to work."""
        import time
        time.sleep(10)  # Give entrypoint time to complete
        result = _docker(
            "exec", hardened_container,
            "cat", "/home/node/logs/hard-shell.log",
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            # The bind_mode log line should show "lan" inside Docker
            assert '"bind":"lan"' in result.stdout or '"bind": "lan"' in result.stdout, (
                "Inside Docker, bind mode should resolve to lan"
            )

    def test_default_config_secure(self):
        """config/openclaw.json should default to loopback and insecure auth disabled."""
        import os, json
        config_path = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "config", "openclaw.json",
        )
        with open(config_path) as f:
            config = json.load(f)
        assert config["gateway"]["bind"] == "loopback", "Default bind should be loopback"
        assert config["gateway"]["controlUi"]["allowInsecureAuth"] is False, (
            "Default allowInsecureAuth should be false"
        )

    def test_bind_mode_env_override_in_entrypoint(self):
        """entrypoint.sh should read OPENCLAW_BIND_MODE env var."""
        import os
        entrypoint = os.path.join(
            os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
            "scripts", "entrypoint.sh",
        )
        with open(entrypoint) as f:
            content = f.read()
        assert 'OPENCLAW_BIND_MODE' in content, "Entrypoint should support OPENCLAW_BIND_MODE"
        assert '--bind "$BIND_MODE"' in content, "Gateway start should use $BIND_MODE variable"


class TestDockerComposeConfig:
    """Verify docker-compose.yml has the expected security settings."""

    def test_compose_config_valid(self):
        """docker-compose.yml should parse without errors."""
        result = subprocess.run(
            ["docker", "compose", "config"],
            capture_output=True, text=True,
            cwd=subprocess.os.path.dirname(subprocess.os.path.dirname(__file__)),
            check=False,
        )
        assert result.returncode == 0, f"docker-compose.yml invalid:\n{result.stderr}"

    def test_compose_has_security_opts(self):
        result = subprocess.run(
            ["docker", "compose", "config"],
            capture_output=True, text=True,
            cwd=subprocess.os.path.dirname(subprocess.os.path.dirname(__file__)),
        )
        config = result.stdout
        assert "no-new-privileges:true" in config
        assert "read_only: true" in config

    def test_compose_localhost_only(self):
        result = subprocess.run(
            ["docker", "compose", "config"],
            capture_output=True, text=True,
            cwd=subprocess.os.path.dirname(subprocess.os.path.dirname(__file__)),
        )
        config = result.stdout
        assert "127.0.0.1" in config, "Gateway port should be bound to localhost only"
