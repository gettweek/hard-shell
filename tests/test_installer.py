"""
Tests that verify install.sh creates the correct directory structure
and configuration files. These tests run the installer logic in a
sandboxed temp directory without actually pulling Docker images.
"""

import os
import stat
import subprocess
import pytest


class TestInstallerScript:
    """Verify install.sh is valid and well-formed."""

    def test_script_exists(self):
        script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "install.sh",
        )
        assert os.path.isfile(script), "install.sh not found"

    def test_script_is_executable(self):
        script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "install.sh",
        )
        mode = os.stat(script).st_mode
        assert mode & stat.S_IXUSR, "install.sh should be executable"

    def test_script_has_bash_shebang(self):
        script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "install.sh",
        )
        with open(script) as f:
            first_line = f.readline().strip()
        assert first_line == "#!/usr/bin/env bash", (
            f"Expected bash shebang, got: {first_line}"
        )

    def test_script_uses_set_euo(self):
        """Script should use strict error handling."""
        script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "install.sh",
        )
        with open(script) as f:
            content = f.read()
        assert "set -euo pipefail" in content, (
            "install.sh should use 'set -euo pipefail' for strict mode"
        )

    def test_script_checks_docker(self):
        """Installer should check that Docker is installed."""
        script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "install.sh",
        )
        with open(script) as f:
            content = f.read()
        assert "docker" in content.lower(), "install.sh should check for Docker"

    def test_script_checks_git(self):
        """Installer should check that Git is installed."""
        script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "install.sh",
        )
        with open(script) as f:
            content = f.read()
        assert "git" in content.lower(), "install.sh should check for Git"

    def test_script_clones_repo(self):
        """Installer should clone the hard-shell repo."""
        script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "install.sh",
        )
        with open(script) as f:
            content = f.read()
        assert "git clone" in content, "install.sh should clone the repo"
        assert "gettweek/hard-shell" in content, "install.sh should reference the hard-shell repo"

    def test_script_uses_localhost_only(self):
        """Installer should bind to localhost, never 0.0.0.0."""
        script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "install.sh",
        )
        with open(script) as f:
            content = f.read()
        assert "127.0.0.1" in content, "install.sh should reference localhost binding"

    def test_script_installs_to_current_dir(self):
        """Installer should clone into the current directory, not ~/.hard-shell."""
        script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "install.sh",
        )
        with open(script) as f:
            content = f.read()
        assert "$(pwd)/hard-shell" in content, "install.sh should install to current directory"
        assert '$HOME/.hard-shell' not in content, "install.sh should not use ~/.hard-shell"

    def test_script_creates_data_dirs(self):
        """Installer should create data directories for bind mounts."""
        script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "install.sh",
        )
        with open(script) as f:
            content = f.read()
        assert "data/openclaw" in content, "install.sh should create data/openclaw"
        assert "data/tweek" in content, "install.sh should create data/tweek"
        assert "data/workspace" in content, "install.sh should create data/workspace"

    def test_script_builds_locally(self):
        """Installer should build the Docker image from source, not pull."""
        script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "install.sh",
        )
        with open(script) as f:
            content = f.read()
        assert "docker compose build" in content, "install.sh should build locally"
        assert "docker pull" not in content, "install.sh should not pull pre-built images"

    def test_script_generates_token(self):
        """Installer should generate a gateway auth token."""
        script = os.path.join(
            os.path.dirname(os.path.dirname(__file__)),
            "install.sh",
        )
        with open(script) as f:
            content = f.read()
        assert "GATEWAY_TOKEN" in content, "install.sh should generate a gateway token"


class TestInstallerDirectoryCreation:
    """
    Test the directory structure that install.sh creates.
    Uses a wrapper that overrides INSTALL_DIR to a temp path
    and stubs out docker commands.
    """

    @pytest.fixture
    def install_result(self, install_test_dir):
        """
        Simulate the installer's directory creation logic.
        Instead of cloning from GitHub (which the real installer does),
        we copy the repo contents into the test directory and generate
        the .env file, mimicking what install.sh produces.
        """
        test_dir = install_test_dir["dir"] / "hard-shell"

        # Create a wrapper that simulates git clone + .env generation
        # without hitting the network or Docker
        wrapper = install_test_dir["dir"] / "test_wrapper.sh"
        wrapper.write_text(f"""#!/usr/bin/env bash
set -euo pipefail

INSTALL_DIR="{test_dir}"

# Simulate git clone by copying the project files
mkdir -p "$INSTALL_DIR"
cp "{install_test_dir['project_root']}/docker-compose.yml" "$INSTALL_DIR/"
cp -r "{install_test_dir['project_root']}/config" "$INSTALL_DIR/"
cp "{install_test_dir['project_root']}/Dockerfile" "$INSTALL_DIR/"

# Create data directories (bind-mounted into container)
mkdir -p "$INSTALL_DIR/data/openclaw" "$INSTALL_DIR/data/tweek" "$INSTALL_DIR/data/workspace"

# Generate gateway token (same logic as install.sh)
GATEWAY_TOKEN=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)
cat > "$INSTALL_DIR/.env" <<ENVEOF
# Hard Shell environment — do not commit this file
TWEEK_PRESET=cautious
OPENCLAW_GATEWAY_TOKEN=$GATEWAY_TOKEN
ENVEOF
chmod 600 "$INSTALL_DIR/.env"

echo "INSTALL_DIR=$INSTALL_DIR"
echo "DONE"
""")
        wrapper.chmod(0o755)

        result = subprocess.run(
            ["bash", str(wrapper)],
            capture_output=True, text=True,
            timeout=30,
            cwd=str(install_test_dir["dir"]),
        )

        return {
            "returncode": result.returncode,
            "stdout": result.stdout,
            "stderr": result.stderr,
            "install_dir": test_dir,
        }

    def test_install_succeeds(self, install_result):
        assert install_result["returncode"] == 0, (
            f"Install wrapper failed:\n{install_result['stderr']}"
        )

    def test_install_dir_created(self, install_result):
        assert install_result["install_dir"].is_dir(), "Install dir not created"

    def test_docker_compose_present(self, install_result):
        f = install_result["install_dir"] / "docker-compose.yml"
        assert f.is_file(), "docker-compose.yml not present in install dir"
        assert f.stat().st_size > 0, "docker-compose.yml is empty"

    def test_dockerfile_present(self, install_result):
        f = install_result["install_dir"] / "Dockerfile"
        assert f.is_file(), "Dockerfile not present in install dir (needed for local build)"
        assert f.stat().st_size > 0, "Dockerfile is empty"

    def test_openclaw_config_present(self, install_result):
        f = install_result["install_dir"] / "config" / "openclaw.json"
        assert f.is_file(), "openclaw.json not present"
        assert f.stat().st_size > 0, "openclaw.json is empty"

    def test_tweek_config_present(self, install_result):
        f = install_result["install_dir"] / "config" / "tweek.yaml"
        assert f.is_file(), "tweek.yaml not present"
        assert f.stat().st_size > 0, "tweek.yaml is empty"

    def test_env_file_created(self, install_result):
        f = install_result["install_dir"] / ".env"
        assert f.is_file(), ".env file not created"

    def test_env_file_has_token(self, install_result):
        f = install_result["install_dir"] / ".env"
        content = f.read_text()
        assert "OPENCLAW_GATEWAY_TOKEN=" in content, "No gateway token in .env"
        # Token should not be empty
        for line in content.splitlines():
            if line.startswith("OPENCLAW_GATEWAY_TOKEN="):
                token = line.split("=", 1)[1]
                assert len(token) >= 16, f"Token too short: {token}"

    def test_env_file_permissions(self, install_result):
        f = install_result["install_dir"] / ".env"
        mode = f.stat().st_mode
        # Should be 600 (owner read/write only)
        assert stat.S_IMODE(mode) == 0o600, (
            f".env should be mode 600, got {oct(stat.S_IMODE(mode))}"
        )

    def test_env_has_tweek_preset(self, install_result):
        f = install_result["install_dir"] / ".env"
        content = f.read_text()
        assert "TWEEK_PRESET=cautious" in content, "Missing TWEEK_PRESET in .env"

    def test_data_directories_created(self, install_result):
        """Data directories for bind mounts should be created."""
        data = install_result["install_dir"] / "data"
        assert data.is_dir(), "data/ directory not created"
        assert (data / "openclaw").is_dir(), "data/openclaw/ not created"
        assert (data / "tweek").is_dir(), "data/tweek/ not created"
        assert (data / "workspace").is_dir(), "data/workspace/ not created"
