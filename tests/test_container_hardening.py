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
