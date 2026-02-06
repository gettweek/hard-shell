#!/usr/bin/env bash
set -euo pipefail

# Hard Shell Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/gettweek/hard-shell/master/install.sh | bash
#
# Clones the repo, builds the Docker image locally, and starts it.
# No Docker Hub account or pre-built image required.

REPO_URL="https://github.com/gettweek/hard-shell.git"
INSTALL_DIR="$HOME/.hard-shell"

# Colors
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

info()  { echo -e "${BLUE}[hard-shell]${NC} $*"; }
ok()    { echo -e "${GREEN}[hard-shell]${NC} $*"; }
warn()  { echo -e "${YELLOW}[hard-shell]${NC} $*"; }
fail()  { echo -e "${RED}[hard-shell]${NC} $*"; exit 1; }

# --- Banner ---
echo ""
echo -e "${BLUE}╔═══════════════════════════════════════════╗${NC}"
echo -e "${BLUE}║        Hard Shell Installer               ║${NC}"
echo -e "${BLUE}║   Hardened OpenClaw + Tweek Security      ║${NC}"
echo -e "${BLUE}╚═══════════════════════════════════════════╝${NC}"
echo ""

# --- Check prerequisites ---
if ! command -v docker &> /dev/null; then
    fail "Docker is not installed. Please install Docker first: https://docs.docker.com/get-docker/"
fi

if ! docker info &> /dev/null 2>&1; then
    fail "Docker is not running. Please start Docker and try again."
fi

if ! docker compose version &> /dev/null 2>&1; then
    fail "Docker Compose is not available. Please update Docker or install the compose plugin."
fi

if ! command -v git &> /dev/null; then
    fail "Git is not installed. Please install Git first."
fi

# --- Detect platform ---
ARCH=$(uname -m)
OS=$(uname -s)
info "Detected: $OS ($ARCH)"

case "$ARCH" in
    x86_64|amd64)  ;;
    arm64|aarch64)  ;;
    *)              fail "Unsupported architecture: $ARCH" ;;
esac

# --- Clone or update the repo ---
if [ -d "$INSTALL_DIR/.git" ]; then
    warn "Existing installation found at $INSTALL_DIR"
    info "Pulling latest changes..."
    git -C "$INSTALL_DIR" pull --ff-only || warn "Could not pull (offline or diverged). Using existing code."
else
    if [ -d "$INSTALL_DIR" ]; then
        warn "Non-git install directory found at $INSTALL_DIR — backing up to ${INSTALL_DIR}.bak"
        mv "$INSTALL_DIR" "${INSTALL_DIR}.bak.$(date +%s)"
    fi
    info "Cloning hard-shell..."
    git clone --depth 1 "$REPO_URL" "$INSTALL_DIR"
fi

# --- Generate gateway token if not set ---
ENV_FILE="$INSTALL_DIR/.env"
if [ ! -f "$ENV_FILE" ]; then
    GATEWAY_TOKEN=$(openssl rand -base64 32 | tr -d '/+=' | head -c 32)
    cat > "$ENV_FILE" <<EOF
# Hard Shell environment — do not commit this file
TWEEK_PRESET=cautious
OPENCLAW_GATEWAY_TOKEN=$GATEWAY_TOKEN
EOF
    chmod 600 "$ENV_FILE"
    ok "Generated gateway token."
fi

# --- Install the CLI ---
CLI_DIR="$HOME/.local/bin"
mkdir -p "$CLI_DIR"
ln -sf "$INSTALL_DIR/hard-shell" "$CLI_DIR/hard-shell"

# Add ~/.local/bin to PATH if not already there
if ! echo "$PATH" | grep -q "$CLI_DIR"; then
    SHELL_NAME=$(basename "$SHELL")
    case "$SHELL_NAME" in
        zsh)  RC_FILE="$HOME/.zshrc" ;;
        bash) RC_FILE="$HOME/.bashrc" ;;
        *)    RC_FILE="" ;;
    esac
    if [ -n "$RC_FILE" ]; then
        if ! grep -q '.local/bin' "$RC_FILE" 2>/dev/null; then
            echo 'export PATH="$HOME/.local/bin:$PATH"' >> "$RC_FILE"
            info "Added ~/.local/bin to PATH in $RC_FILE"
        fi
    fi
    export PATH="$CLI_DIR:$PATH"
fi

# --- Build the image locally ---
info "Building Docker image (this takes a few minutes on first run)..."
cd "$INSTALL_DIR"
docker compose build

# --- Start it up ---
info "Starting Hard Shell..."
docker compose up -d

# --- Wait for health ---
info "Waiting for services..."
HEALTHY=false
for i in $(seq 1 60); do
    STATUS=$(docker inspect --format='{{.State.Health.Status}}' hard-shell 2>/dev/null || echo "starting")
    if [ "$STATUS" = "healthy" ]; then
        HEALTHY=true
        break
    fi
    sleep 2
done

if [ "$HEALTHY" = false ]; then
    warn "Container did not become healthy within 120s. Check logs:"
    warn "  cd $INSTALL_DIR && docker compose logs"
fi

# --- Done ---
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        Hard Shell is running!             ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════╝${NC}"
echo ""
ok "Gateway:    http://127.0.0.1:18789"
ok "Security:   Tweek (cautious preset)"
ok "Install:    $INSTALL_DIR/"
echo ""
info "Next steps:"
info "  1. Open http://127.0.0.1:18789 in your browser"
info "  2. Complete the onboarding wizard (add your LLM API key)"
info "  3. Connect your messaging platforms"
echo ""
info "Commands:"
info "  hard-shell status     # Check health"
info "  hard-shell logs -f    # View logs"
info "  hard-shell restart    # Restart"
info "  hard-shell stop       # Stop"
info "  hard-shell update     # Pull latest + rebuild"
info "  hard-shell help       # All commands"
echo ""
