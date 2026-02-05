"""
Hard Shell Integration Test Fixtures

Session-scoped fixtures that build the Docker image once and reuse it
across all tests. Tests verify the Docker build, container security,
service startup, and installer behavior.
"""

import json
import os
import shutil
import subprocess
import time

import pytest

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IMAGE_NAME = "hard-shell:test"
CONTAINER_NAME = "hard-shell-test"
GATEWAY_PORT = 18789
SCANNER_PORT = 9878


def _docker(*args, check=True, capture=True, timeout=120):
    """Run a docker command and return the result."""
    cmd = ["docker"] + list(args)
    return subprocess.run(
        cmd,
        capture_output=capture,
        text=True,
        check=check,
        timeout=timeout,
        cwd=PROJECT_ROOT,
    )


@pytest.fixture(scope="session")
def docker_image():
    """Build the Docker image once for the entire test session."""
    print(f"\n=== Building {IMAGE_NAME} ===")
    result = _docker("build", "-t", IMAGE_NAME, ".", timeout=600)
    assert result.returncode == 0, f"Docker build failed:\n{result.stderr}"
    yield IMAGE_NAME
    # Cleanup: remove the test image
    _docker("rmi", IMAGE_NAME, check=False)


@pytest.fixture(scope="session")
def image_inspect(docker_image):
    """Inspect the built image and return metadata."""
    result = _docker("inspect", docker_image)
    return json.loads(result.stdout)[0]


@pytest.fixture(scope="module")
def running_container(docker_image):
    """
    Start a hard-shell container for tests that need running services.
    Module-scoped so each test file gets a fresh container.
    """
    # Stop any leftover test container
    _docker("rm", "-f", CONTAINER_NAME, check=False)

    # Start the container with dev-friendly settings
    _docker(
        "run", "-d",
        "--name", CONTAINER_NAME,
        "-p", f"127.0.0.1:{GATEWAY_PORT}:{GATEWAY_PORT}",
        "-p", f"127.0.0.1:{SCANNER_PORT}:{SCANNER_PORT}",
        "--tmpfs", "/tmp:size=100M",
        "--tmpfs", "/home/node/.cache:size=200M",
        docker_image,
    )

    # Wait for scanner to become healthy (up to 60s)
    # Use docker exec to check health from inside the container, since the
    # scanner binds to 127.0.0.1 (container loopback, not reachable via port mapping)
    scanner_healthy = False
    for _ in range(60):
        try:
            r = _docker(
                "exec", CONTAINER_NAME,
                "curl", "-sf", f"http://127.0.0.1:{SCANNER_PORT}/health",
                check=False, timeout=10,
            )
            if r.returncode == 0:
                scanner_healthy = True
                break
        except (subprocess.TimeoutExpired, Exception):
            pass
        time.sleep(1)

    # Wait for gateway to become healthy (up to 30s more)
    gateway_healthy = False
    if scanner_healthy:
        for _ in range(30):
            try:
                r = _docker(
                    "exec", CONTAINER_NAME,
                    "curl", "-sf", "-o", "/dev/null", "-w", "%{http_code}",
                    f"http://127.0.0.1:{GATEWAY_PORT}/health",
                    check=False, timeout=10,
                )
                status = r.stdout.strip()
                if status in ("200", "401", "403"):
                    gateway_healthy = True
                    break
            except (subprocess.TimeoutExpired, Exception):
                pass
            time.sleep(1)

    healthy = scanner_healthy and gateway_healthy

    if not healthy:
        logs = _docker("logs", CONTAINER_NAME, check=False)
        print(f"Container logs:\n{logs.stdout}\n{logs.stderr}")

    # Read the scanner auth token for screening tests
    scanner_token = None
    if scanner_healthy:
        try:
            r = _docker(
                "exec", CONTAINER_NAME,
                "cat", "/home/node/.tweek/.scanner_token",
                check=False, timeout=5,
            )
            if r.returncode == 0:
                scanner_token = r.stdout.strip()
        except Exception:
            pass

    yield {
        "name": CONTAINER_NAME,
        "image": docker_image,
        "gateway_port": GATEWAY_PORT,
        "scanner_port": SCANNER_PORT,
        "scanner_token": scanner_token,
        "healthy": healthy,
    }

    # Cleanup
    _docker("stop", CONTAINER_NAME, check=False)
    _docker("rm", CONTAINER_NAME, check=False)


@pytest.fixture
def install_test_dir(tmp_path):
    """
    Create a temporary directory simulating a fresh install target.
    Provides the install.sh script and a modified version that installs
    into the test directory instead of ~/.hard-shell.
    """
    test_home = tmp_path / "home"
    test_home.mkdir()

    # Copy install.sh into the test dir
    src = os.path.join(PROJECT_ROOT, "install.sh")
    dst = tmp_path / "install.sh"
    shutil.copy2(src, dst)

    yield {
        "dir": tmp_path,
        "home": test_home,
        "install_script": dst,
        "project_root": PROJECT_ROOT,
    }


def docker_exec(container_name, *cmd, check=True):
    """Execute a command inside a running container."""
    return _docker("exec", container_name, *cmd, check=check)


def docker_run(image, *cmd, check=True, user=None):
    """Run a one-off command in a new container from the image."""
    args = ["run", "--rm"]
    if user:
        args += ["--user", user]
    args.append(image)
    args.extend(cmd)
    return _docker(*args, check=check)
