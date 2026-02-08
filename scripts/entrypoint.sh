#!/usr/bin/env bash
set -euo pipefail

# Hard Shell Entrypoint
# Starts Tweek scanner server, then OpenClaw gateway with plugin.
# All output is structured JSONL to /home/node/logs/ and human-readable to stdout.

SCANNER_PORT="${TWEEK_SCANNER_PORT:-9878}"
GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"
TWEEK_PRESET="${TWEEK_PRESET:-cautious}"
LOG_DIR="/home/node/logs"
APP_LOG="$LOG_DIR/hard-shell.log"
AUDIT_LOG="$LOG_DIR/audit.log"
MAX_LOG_SIZE=$((10 * 1024 * 1024))  # 10MB
MAX_ROTATED=3

SCANNER_PID=""
GATEWAY_PID=""
BOOT_START=$(date +%s%3N)

# =============================================================================
# Logging helpers
# =============================================================================

# log_json LEVEL COMPONENT MESSAGE [EXTRA_JSON]
# Writes structured JSONL to APP_LOG and human-readable to stdout.
log_json() {
    local level="$1" component="$2" msg="$3" extra="${4:-}"
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ" 2>/dev/null || date -u +"%Y-%m-%dT%H:%M:%SZ")

    # Build JSON line
    local json="{\"ts\":\"$ts\",\"level\":\"$level\",\"component\":\"$component\",\"msg\":\"$msg\""
    if [ -n "$extra" ]; then
        json="$json,\"extra\":$extra"
    fi
    json="$json}"

    # Append to log file (if directory exists)
    if [ -d "$LOG_DIR" ]; then
        echo "$json" >> "$APP_LOG"
    fi

    # Human-readable stdout
    echo "[hard-shell] [$level] $msg"
}

# audit_log EVENT [DETAIL_JSON]
# Append-only security audit trail. Never rotated.
audit_log() {
    local event="$1" detail="${2:-"{}"}"
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ" 2>/dev/null || date -u +"%Y-%m-%dT%H:%M:%SZ")

    local json="{\"ts\":\"$ts\",\"event\":\"$event\",\"detail\":$detail}"

    if [ -d "$LOG_DIR" ]; then
        echo "$json" >> "$AUDIT_LOG"
    fi

    # Also emit to structured app log
    log_json INFO audit "$event" "$detail"
}

# mask_sensitive VALUE
# Keeps first 4 chars, masks the rest. Returns masked string on stdout.
mask_sensitive() {
    local val="$1"
    if [ ${#val} -le 4 ]; then
        echo "***REDACTED***"
    else
        echo "${val:0:4}***REDACTED***"
    fi
}

# rotate_logs
# Rotate APP_LOG if it exceeds MAX_LOG_SIZE. Keep up to MAX_ROTATED old files.
# Audit log is never rotated (append-only security requirement).
rotate_logs() {
    if [ ! -f "$APP_LOG" ]; then
        return
    fi

    local size
    size=$(stat -c%s "$APP_LOG" 2>/dev/null || stat -f%z "$APP_LOG" 2>/dev/null || echo 0)

    if [ "$size" -gt "$MAX_LOG_SIZE" ]; then
        log_json INFO entrypoint "Rotating app log" "{\"size_bytes\":$size}"

        # Shift existing rotated files
        local i=$MAX_ROTATED
        while [ "$i" -gt 1 ]; do
            local prev=$((i - 1))
            if [ -f "$APP_LOG.$prev" ]; then
                mv "$APP_LOG.$prev" "$APP_LOG.$i"
            fi
            i=$prev
        done

        mv "$APP_LOG" "$APP_LOG.1"
        # Create fresh log file
        : > "$APP_LOG"
    fi

    # Warn if audit log is very large (> 50MB) but never rotate it
    if [ -f "$AUDIT_LOG" ]; then
        local audit_size
        audit_size=$(stat -c%s "$AUDIT_LOG" 2>/dev/null || stat -f%z "$AUDIT_LOG" 2>/dev/null || echo 0)
        if [ "$audit_size" -gt $((50 * 1024 * 1024)) ]; then
            log_json WARN entrypoint "Audit log is large — consider archiving" "{\"size_bytes\":$audit_size}"
        fi
    fi
}

# =============================================================================
# Startup
# =============================================================================

# Rotate logs before writing anything new
rotate_logs

audit_log startup "{\"preset\":\"$TWEEK_PRESET\",\"scanner_port\":$SCANNER_PORT,\"gateway_port\":$GATEWAY_PORT}"
log_json INFO entrypoint "Hard Shell starting" "{\"preset\":\"$TWEEK_PRESET\"}"

# --- Graceful shutdown ---
cleanup() {
    log_json INFO entrypoint "Shutting down..."
    if [ -n "$GATEWAY_PID" ] && kill -0 "$GATEWAY_PID" 2>/dev/null; then
        kill -TERM "$GATEWAY_PID"
        wait "$GATEWAY_PID" 2>/dev/null || true
    fi
    if [ -n "$SCANNER_PID" ] && kill -0 "$SCANNER_PID" 2>/dev/null; then
        kill -TERM "$SCANNER_PID"
        wait "$SCANNER_PID" 2>/dev/null || true
    fi
    audit_log shutdown "{}"
    log_json INFO entrypoint "Stopped."
    exit 0
}
trap cleanup SIGTERM SIGINT

# --- Apply default config if first run ---
if [ ! -f "$HOME/.openclaw/openclaw.json" ]; then
    log_json INFO entrypoint "First run — applying default OpenClaw + Tweek config"
    mkdir -p "$HOME/.openclaw"
    cp /opt/hard-shell/config/openclaw.json "$HOME/.openclaw/openclaw.json"
fi

if [ ! -f "$HOME/.tweek/config.yaml" ]; then
    log_json INFO entrypoint "Applying default Tweek config" "{\"preset\":\"$TWEEK_PRESET\"}"
    mkdir -p "$HOME/.tweek"
    cp /opt/hard-shell/config/tweek.yaml "$HOME/.tweek/config.yaml"
fi

# --- Harden directory permissions ---
chmod 700 "$HOME/.openclaw" "$HOME/.tweek" 2>/dev/null || true
if [ -d "$HOME/.openclaw/credentials" ]; then
    chmod 700 "$HOME/.openclaw/credentials"
    log_json INFO entrypoint "Hardened credentials directory permissions"
fi

# --- Determine gateway bind mode ---
# Priority: env var override > Docker detection > Tailscale detection > loopback (safest default)
#
# CRITICAL: Inside Docker, the gateway must bind 0.0.0.0 (lan) because Docker's
# bridge network delivers host traffic via 172.18.0.x, not 127.0.0.1.
# Binding loopback inside the container silently rejects all connections.
# This is safe because docker-compose.yml restricts the host-side port to
# 127.0.0.1 — the real security boundary is Docker's port mapping, not the
# gateway's bind address.
BIND_MODE="${OPENCLAW_BIND_MODE:-}"
TAILSCALE_AVAILABLE=false
IN_DOCKER=false

if [ -f /.dockerenv ] || grep -q 'docker\|containerd' /proc/1/cgroup 2>/dev/null; then
    IN_DOCKER=true
fi

if [ -z "$BIND_MODE" ]; then
    if [ "$IN_DOCKER" = true ]; then
        # Inside Docker: must bind lan (0.0.0.0) for Docker port forwarding to work.
        # Host-side security is enforced by docker-compose.yml port mapping (127.0.0.1:port:port).
        BIND_MODE="lan"
        log_json INFO entrypoint "Running inside Docker — binding lan (host restricts to 127.0.0.1)"
    elif command -v tailscale &> /dev/null && tailscale status &> /dev/null; then
        TAILSCALE_AVAILABLE=true
        BIND_MODE="loopback"
        TAILSCALE_IP=$(tailscale ip -4 2>/dev/null || echo "")
        log_json INFO entrypoint "Tailscale detected" "{\"ip\":\"$TAILSCALE_IP\"}"
        audit_log tailscale_detected "{\"ip\":\"$TAILSCALE_IP\"}"
    else
        BIND_MODE="loopback"
    fi
fi

# Determine allowInsecureAuth based on bind mode and environment
# loopback = safe (localhost only) → allow insecure auth
# lan inside Docker = safe (docker-compose restricts host to 127.0.0.1) → allow insecure auth
# lan outside Docker = exposed to network → require secure auth (HTTPS/device pairing)
if [ "$BIND_MODE" = "loopback" ]; then
    ALLOW_INSECURE_AUTH=true
elif [ "$BIND_MODE" = "lan" ] && [ "$IN_DOCKER" = true ]; then
    ALLOW_INSECURE_AUTH=true
else
    ALLOW_INSECURE_AUTH=false
fi

log_json INFO entrypoint "Gateway bind mode resolved" "{\"bind\":\"$BIND_MODE\",\"in_docker\":$IN_DOCKER,\"tailscale\":$TAILSCALE_AVAILABLE,\"insecure_auth\":$ALLOW_INSECURE_AUTH}"
audit_log bind_mode "{\"bind\":\"$BIND_MODE\",\"in_docker\":$IN_DOCKER,\"tailscale\":$TAILSCALE_AVAILABLE,\"insecure_auth\":$ALLOW_INSECURE_AUTH}"

# --- Detect API key and configure LLM provider/model ---
OPENCLAW_CONFIG="$HOME/.openclaw/openclaw.json"
MODEL=""
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    MODEL="anthropic/claude-sonnet-4-5-20250929"
    log_json INFO entrypoint "API key detected" "{\"provider\":\"anthropic\",\"model\":\"$MODEL\"}"
elif [ -n "${GOOGLE_API_KEY:-}" ]; then
    MODEL="google/gemini-2.0-flash"
    log_json INFO entrypoint "API key detected" "{\"provider\":\"google\",\"model\":\"$MODEL\"}"
elif [ -n "${OPENAI_API_KEY:-}" ]; then
    MODEL="openai/gpt-4o"
    log_json INFO entrypoint "API key detected" "{\"provider\":\"openai\",\"model\":\"$MODEL\"}"
elif [ -n "${XAI_API_KEY:-}" ]; then
    MODEL="xai/grok-3"
    log_json INFO entrypoint "API key detected" "{\"provider\":\"xai\",\"model\":\"$MODEL\"}"
else
    log_json WARN entrypoint "No LLM API key found — run 'hard-shell apikey' on the host"
fi

if [ -n "$MODEL" ] && [ -f "$OPENCLAW_CONFIG" ]; then
    PROVIDER="${MODEL%%/*}"
    AUTH_KEY=""
    case "$PROVIDER" in
        anthropic) AUTH_KEY="${ANTHROPIC_API_KEY:-}" ;;
        google)    AUTH_KEY="${GOOGLE_API_KEY:-}" ;;
        openai)    AUTH_KEY="${OPENAI_API_KEY:-}" ;;
        xai)       AUTH_KEY="${XAI_API_KEY:-}" ;;
    esac

    python3 -c "
import json, os

# --- Update model and gateway security in openclaw.json ---
config_path = '$OPENCLAW_CONFIG'
model = '$MODEL'
bind_mode = '$BIND_MODE'
allow_insecure = $ALLOW_INSECURE_AUTH

with open(config_path) as f:
    config = json.load(f)
config.setdefault('agents', {}).setdefault('defaults', {}).setdefault('model', {})['primary'] = model
config.setdefault('gateway', {})['bind'] = bind_mode
config['gateway'].setdefault('controlUi', {})['allowInsecureAuth'] = allow_insecure
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')

# --- Create auth profile so OpenClaw can find the API key ---
provider = '$PROVIDER'
auth_key = '$AUTH_KEY'
if auth_key:
    agent_dir = os.path.expanduser('~/.openclaw/agents/main/agent')
    os.makedirs(agent_dir, exist_ok=True)
    auth_file = os.path.join(agent_dir, 'auth-profiles.json')
    store = {
        'version': 1,
        'profiles': {
            provider + '-env': {
                'provider': provider,
                'type': 'api-key',
                'apiKey': auth_key
            }
        }
    }
    with open(auth_file, 'w') as f:
        json.dump(store, f, indent=2)
        f.write('\n')
    os.chmod(auth_file, 0o600)
" 2>/dev/null && log_json INFO entrypoint "Model and auth profile configured" "{\"provider\":\"$PROVIDER\"}" \
             || log_json WARN entrypoint "Could not update model/auth config"
    audit_log config_change "{\"provider\":\"$PROVIDER\",\"model\":\"$MODEL\",\"bind\":\"$BIND_MODE\"}"
else
    # No API key, but still update gateway bind/auth settings
    if [ -f "$OPENCLAW_CONFIG" ]; then
        python3 -c "
import json
config_path = '$OPENCLAW_CONFIG'
bind_mode = '$BIND_MODE'
allow_insecure = $ALLOW_INSECURE_AUTH
with open(config_path) as f:
    config = json.load(f)
config.setdefault('gateway', {})['bind'] = bind_mode
config['gateway'].setdefault('controlUi', {})['allowInsecureAuth'] = allow_insecure
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')
" 2>/dev/null && log_json INFO entrypoint "Gateway security config updated" "{\"bind\":\"$BIND_MODE\",\"insecure_auth\":$ALLOW_INSECURE_AUTH}" \
             || log_json WARN entrypoint "Could not update gateway security config"
    fi
fi

# --- Generate scanner auth token if missing ---
if [ ! -f "$HOME/.tweek/.scanner_token" ]; then
    python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$HOME/.tweek/.scanner_token"
    chmod 600 "$HOME/.tweek/.scanner_token"
    audit_log token_generated "{\"type\":\"scanner\"}"
    log_json INFO entrypoint "Generated scanner auth token"
fi

# --- Generate gateway auth token if not provided ---
if [ -z "${OPENCLAW_GATEWAY_TOKEN:-}" ]; then
    export OPENCLAW_GATEWAY_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    audit_log token_generated "{\"type\":\"gateway\",\"source\":\"auto\"}"
    log_json INFO entrypoint "Generated gateway token (value not logged for security)"
fi

# --- Lock down security configs ---
# Make config files read-only to raise the bar against a compromised agent
# disabling its own safety checks. chmod 444 prevents naive modification;
# a sophisticated attacker could chmod it back (owner can always chmod own files),
# but the real defense is Tweek blocking chmod/sed/python commands that target
# config paths. This is defense-in-depth, not a sole barrier.
#
# Additionally, store a SHA-256 fingerprint of each config file. The post-startup
# security audit can detect tampering by comparing against these checksums.
LOCKED_FILES=0
CONFIG_HASHES=""
for cfg in "$HOME/.openclaw/openclaw.json" "$HOME/.tweek/config.yaml"; do
    if [ -f "$cfg" ]; then
        chmod 444 "$cfg" 2>/dev/null && LOCKED_FILES=$((LOCKED_FILES + 1))
        # Record hash for tamper detection
        HASH=$(sha256sum "$cfg" 2>/dev/null | cut -d' ' -f1 || echo "unknown")
        CONFIG_HASHES="$CONFIG_HASHES\"$(basename "$cfg")\":\"$HASH\","
    fi
done
# Lock the Tweek plugin config if it exists (on read-only root FS, this is belt-and-suspenders)
if [ -f /usr/local/lib/node_modules/openclaw/extensions/tweek-security/openclaw.plugin.json ]; then
    chmod 444 /usr/local/lib/node_modules/openclaw/extensions/tweek-security/openclaw.plugin.json 2>/dev/null && LOCKED_FILES=$((LOCKED_FILES + 1))
fi
# Write config hashes for post-startup tamper detection
if [ -n "$CONFIG_HASHES" ]; then
    CONFIG_HASHES="{${CONFIG_HASHES%,}}"
else
    CONFIG_HASHES="{}"
fi
echo "$CONFIG_HASHES" > "$HOME/.openclaw/.config_hashes" 2>/dev/null || true
chmod 444 "$HOME/.openclaw/.config_hashes" 2>/dev/null || true
log_json INFO entrypoint "Security configs locked (read-only)" "{\"files_locked\":$LOCKED_FILES,\"hashes\":$CONFIG_HASHES}"
audit_log configs_locked "{\"files_locked\":$LOCKED_FILES,\"hashes\":$CONFIG_HASHES}"

# --- Start Tweek scanner server ---
SCANNER_START=$(date +%s%3N)
log_json INFO scanner "Starting Tweek scanner server" "{\"port\":$SCANNER_PORT}"
python3 -c "from tweek.integrations.openclaw_server import run_server; run_server($SCANNER_PORT)" &
SCANNER_PID=$!

# Wait for scanner to be healthy
log_json INFO scanner "Waiting for scanner server..."
for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:$SCANNER_PORT/health" > /dev/null 2>&1; then
        SCANNER_READY=$(date +%s%3N)
        SCANNER_MS=$(( SCANNER_READY - SCANNER_START ))
        log_json INFO scanner "Scanner server ready" "{\"startup_ms\":$SCANNER_MS}"
        break
    fi
    if ! kill -0 "$SCANNER_PID" 2>/dev/null; then
        log_json ERROR scanner "Scanner server failed to start"
        audit_log error "{\"component\":\"scanner\",\"reason\":\"process_exited\"}"
        exit 1
    fi
    sleep 1
done

if ! curl -sf "http://127.0.0.1:$SCANNER_PORT/health" > /dev/null 2>&1; then
    log_json ERROR scanner "Scanner server did not become healthy in 30s"
    audit_log error "{\"component\":\"scanner\",\"reason\":\"health_timeout\"}"
    exit 1
fi

# --- Start OpenClaw gateway ---
GATEWAY_START=$(date +%s%3N)
log_json INFO gateway "Starting OpenClaw gateway" "{\"port\":$GATEWAY_PORT}"
openclaw gateway --port "$GATEWAY_PORT" --token "$OPENCLAW_GATEWAY_TOKEN" --bind "$BIND_MODE" --allow-unconfigured &
GATEWAY_PID=$!

# Wait for gateway to be healthy
log_json INFO gateway "Waiting for gateway..."
for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:$GATEWAY_PORT/health" > /dev/null 2>&1; then
        GATEWAY_READY=$(date +%s%3N)
        GATEWAY_MS=$(( GATEWAY_READY - GATEWAY_START ))
        log_json INFO gateway "Gateway ready" "{\"startup_ms\":$GATEWAY_MS}"
        break
    fi
    if ! kill -0 "$GATEWAY_PID" 2>/dev/null; then
        log_json ERROR gateway "Gateway failed to start"
        audit_log error "{\"component\":\"gateway\",\"reason\":\"process_exited\"}"
        exit 1
    fi
    sleep 1
done

# --- Startup complete ---
BOOT_END=$(date +%s%3N)
TOTAL_MS=$(( BOOT_END - BOOT_START ))
SCANNER_ELAPSED=${SCANNER_MS:-0}
GATEWAY_ELAPSED=${GATEWAY_MS:-0}

log_json INFO entrypoint "Hard Shell is running" "{\"scanner_ms\":$SCANNER_ELAPSED,\"gateway_ms\":$GATEWAY_ELAPSED,\"total_ms\":$TOTAL_MS,\"bind\":\"$BIND_MODE\"}"
audit_log ready "{\"scanner_ms\":$SCANNER_ELAPSED,\"gateway_ms\":$GATEWAY_ELAPSED,\"total_ms\":$TOTAL_MS,\"preset\":\"$TWEEK_PRESET\",\"bind\":\"$BIND_MODE\"}"

echo "[hard-shell] ================================================"
echo "[hard-shell] Hard Shell is running."
echo "[hard-shell]   Gateway:  http://127.0.0.1:$GATEWAY_PORT"
echo "[hard-shell]   Scanner:  http://127.0.0.1:$SCANNER_PORT (internal)"
echo "[hard-shell]   Preset:   $TWEEK_PRESET"
echo "[hard-shell]   Bind:     $BIND_MODE"
echo "[hard-shell]   Startup:  ${TOTAL_MS}ms"
echo "[hard-shell] ================================================"
echo "[hard-shell]"
echo "[hard-shell] Next steps:"
echo "[hard-shell]   Run 'hard-shell url' on the host to get the full gateway URL"
echo "[hard-shell]   Open the URL in your browser to start using your AI agent"
if [ -z "$MODEL" ]; then
    echo "[hard-shell]"
    echo "[hard-shell]   WARNING: No LLM API key detected."
    echo "[hard-shell]   Run 'hard-shell apikey' on the host to configure a provider."
    echo "[hard-shell]   Supported: Anthropic, OpenAI, Google, xAI"
    echo "[hard-shell]   Custom endpoint? Add OPENAI_BASE_URL to .env — see README."
fi
echo "[hard-shell] ================================================"

# --- Post-startup security hardening ---
# Re-harden credentials dir (OpenClaw may have created it during startup)
if [ -d "$HOME/.openclaw/credentials" ]; then
    chmod 700 "$HOME/.openclaw/credentials" 2>/dev/null || true
fi

log_json INFO entrypoint "Running post-startup security checks..."

# Fix known issues automatically
if command -v openclaw &> /dev/null; then
    openclaw doctor --fix 2>&1 | while IFS= read -r line; do
        [ -n "$line" ] && log_json INFO doctor "$line"
    done || true

    # Run deep security audit and log results
    openclaw security audit --deep 2>&1 | while IFS= read -r line; do
        [ -n "$line" ] && log_json INFO security-audit "$line"
    done || true

    audit_log security_audit "{\"doctor_fix\":true,\"deep_audit\":true}"
    log_json INFO entrypoint "Post-startup security checks complete"
fi

# Wait for either process to exit
wait -n "$SCANNER_PID" "$GATEWAY_PID" 2>/dev/null || true
cleanup
