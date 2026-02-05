#!/usr/bin/env bash
set -euo pipefail

# Hard Shell Installer
# Usage: curl -fsSL https://raw.githubusercontent.com/gettweek/hard-shell/master/install.sh | bash
#
# Installs OpenClaw + Tweek security in one command.

REPO="gettweek/hard-shell"
IMAGE="tweek/hard-shell:latest"
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

# --- Check Docker ---
if ! command -v docker &> /dev/null; then
    fail "Docker is not installed. Please install Docker first: https://docs.docker.com/get-docker/"
fi

if ! docker info &> /dev/null 2>&1; then
    fail "Docker is not running. Please start Docker and try again."
fi

# --- Check Docker Compose ---
if ! docker compose version &> /dev/null 2>&1; then
    fail "Docker Compose is not available. Please update Docker or install the compose plugin."
fi

# --- Detect platform ---
ARCH=$(uname -m)
OS=$(uname -s)
info "Detected: $OS ($ARCH)"

case "$ARCH" in
    x86_64|amd64)  PLATFORM="linux/amd64" ;;
    arm64|aarch64)  PLATFORM="linux/arm64" ;;
    *)              fail "Unsupported architecture: $ARCH" ;;
esac

# --- Create install directory ---
if [ -d "$INSTALL_DIR" ]; then
    warn "Existing installation found at $INSTALL_DIR"
    read -p "Overwrite configuration? (y/N) " -n 1 -r
    echo
    if [[ ! $REPLY =~ ^[Yy]$ ]]; then
        info "Keeping existing config. Pulling latest image..."
    fi
else
    mkdir -p "$INSTALL_DIR"
    info "Created $INSTALL_DIR"
fi

# --- Download docker-compose.yml ---
info "Downloading docker-compose.yml..."
curl -fsSL "https://raw.githubusercontent.com/$REPO/master/docker-compose.yml" \
    -o "$INSTALL_DIR/docker-compose.yml"

# --- Download default configs ---
mkdir -p "$INSTALL_DIR/config"

if [ ! -f "$INSTALL_DIR/config/openclaw.json" ]; then
    curl -fsSL "https://raw.githubusercontent.com/$REPO/master/config/openclaw.json" \
        -o "$INSTALL_DIR/config/openclaw.json"
fi

if [ ! -f "$INSTALL_DIR/config/tweek.yaml" ]; then
    curl -fsSL "https://raw.githubusercontent.com/$REPO/master/config/tweek.yaml" \
        -o "$INSTALL_DIR/config/tweek.yaml"
fi

# --- Pull the image ---
info "Pulling $IMAGE..."
docker pull --platform "$PLATFORM" "$IMAGE"

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

# --- Start it up ---
info "Starting Hard Shell..."
cd "$INSTALL_DIR"
docker compose up -d

# --- Wait for health ---
info "Waiting for services..."
for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:18789/health" > /dev/null 2>&1; then
        break
    fi
    sleep 1
done

# --- Done ---
echo ""
echo -e "${GREEN}╔═══════════════════════════════════════════╗${NC}"
echo -e "${GREEN}║        Hard Shell is running!             ║${NC}"
echo -e "${GREEN}╚═══════════════════════════════════════════╝${NC}"
echo ""
ok "Gateway:    http://127.0.0.1:18789"
ok "Security:   Tweek (cautious preset)"
ok "Config:     $INSTALL_DIR/"
echo ""
info "Next steps:"
info "  1. Open http://127.0.0.1:18789 in your browser"
info "  2. Complete the onboarding wizard (add your LLM API key)"
info "  3. Connect your messaging platforms"
echo ""
info "Commands:"
info "  cd $INSTALL_DIR && docker compose logs -f    # View logs"
info "  cd $INSTALL_DIR && docker compose restart     # Restart"
info "  cd $INSTALL_DIR && docker compose down        # Stop"
echo ""
