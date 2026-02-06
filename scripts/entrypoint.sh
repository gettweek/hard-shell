#!/usr/bin/env bash
set -euo pipefail

# Hard Shell Entrypoint
# Starts Tweek scanner server, then OpenClaw gateway with plugin

SCANNER_PORT="${TWEEK_SCANNER_PORT:-9878}"
GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"
TWEEK_PRESET="${TWEEK_PRESET:-cautious}"

SCANNER_PID=""
GATEWAY_PID=""

# --- Graceful shutdown ---
cleanup() {
    echo "[hard-shell] Shutting down..."
    if [ -n "$GATEWAY_PID" ] && kill -0 "$GATEWAY_PID" 2>/dev/null; then
        kill -TERM "$GATEWAY_PID"
        wait "$GATEWAY_PID" 2>/dev/null || true
    fi
    if [ -n "$SCANNER_PID" ] && kill -0 "$SCANNER_PID" 2>/dev/null; then
        kill -TERM "$SCANNER_PID"
        wait "$SCANNER_PID" 2>/dev/null || true
    fi
    echo "[hard-shell] Stopped."
    exit 0
}
trap cleanup SIGTERM SIGINT

# --- Apply default config if first run ---
if [ ! -f "$HOME/.openclaw/openclaw.json" ]; then
    echo "[hard-shell] First run — applying default OpenClaw + Tweek config..."
    mkdir -p "$HOME/.openclaw"
    cp /opt/hard-shell/config/openclaw.json "$HOME/.openclaw/openclaw.json"
fi

if [ ! -f "$HOME/.tweek/config.yaml" ]; then
    echo "[hard-shell] Applying default Tweek config (preset: $TWEEK_PRESET)..."
    mkdir -p "$HOME/.tweek"
    cp /opt/hard-shell/config/tweek.yaml "$HOME/.tweek/config.yaml"
fi

# --- Detect API key and configure LLM provider/model ---
# OpenClaw needs to know which provider and model to use.
# Detect from env vars and write into the config.
OPENCLAW_CONFIG="$HOME/.openclaw/openclaw.json"
MODEL=""
if [ -n "${ANTHROPIC_API_KEY:-}" ]; then
    MODEL="anthropic:claude-sonnet-4-5-20250929"
    echo "[hard-shell] Detected ANTHROPIC_API_KEY — using $MODEL"
elif [ -n "${GOOGLE_API_KEY:-}" ]; then
    MODEL="google:gemini-2.0-flash"
    echo "[hard-shell] Detected GOOGLE_API_KEY — using $MODEL"
elif [ -n "${OPENAI_API_KEY:-}" ]; then
    MODEL="openai:gpt-4o"
    echo "[hard-shell] Detected OPENAI_API_KEY — using $MODEL"
elif [ -n "${XAI_API_KEY:-}" ]; then
    MODEL="xai:grok-3"
    echo "[hard-shell] Detected XAI_API_KEY — using $MODEL"
else
    echo "[hard-shell] WARNING: No LLM API key found. Run './hard-shell apikey' on the host."
fi

if [ -n "$MODEL" ] && [ -f "$OPENCLAW_CONFIG" ]; then
    python3 -c "
import json, sys
config_path = '$OPENCLAW_CONFIG'
model = '$MODEL'
with open(config_path) as f:
    config = json.load(f)
config.setdefault('agents', {}).setdefault('defaults', {}).setdefault('model', {})['primary'] = model
with open(config_path, 'w') as f:
    json.dump(config, f, indent=2)
    f.write('\n')
" 2>/dev/null && echo "[hard-shell] Model configured in openclaw.json" || echo "[hard-shell] WARNING: Could not update model config"
fi

# --- Generate scanner auth token if missing ---
if [ ! -f "$HOME/.tweek/.scanner_token" ]; then
    python3 -c "import secrets; print(secrets.token_urlsafe(32))" > "$HOME/.tweek/.scanner_token"
    chmod 600 "$HOME/.tweek/.scanner_token"
    echo "[hard-shell] Generated scanner auth token."
fi

# --- Generate gateway auth token if not provided ---
if [ -z "${OPENCLAW_GATEWAY_TOKEN:-}" ]; then
    export OPENCLAW_GATEWAY_TOKEN=$(python3 -c "import secrets; print(secrets.token_urlsafe(32))")
    echo "[hard-shell] Generated gateway token (not logged for security)."
fi

# --- Start Tweek scanner server ---
# Note: The scanner module uses an obfuscated __main__ guard, so we call
# run_server() directly instead of using python3 -m
echo "[hard-shell] Starting Tweek scanner server on port $SCANNER_PORT..."
python3 -c "from tweek.integrations.openclaw_server import run_server; run_server($SCANNER_PORT)" &
SCANNER_PID=$!

# Wait for scanner to be healthy
echo "[hard-shell] Waiting for scanner server..."
for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:$SCANNER_PORT/health" > /dev/null 2>&1; then
        echo "[hard-shell] Scanner server ready."
        break
    fi
    if ! kill -0 "$SCANNER_PID" 2>/dev/null; then
        echo "[hard-shell] ERROR: Scanner server failed to start."
        exit 1
    fi
    sleep 1
done

if ! curl -sf "http://127.0.0.1:$SCANNER_PORT/health" > /dev/null 2>&1; then
    echo "[hard-shell] ERROR: Scanner server did not become healthy in 30s."
    exit 1
fi

# --- Start OpenClaw gateway ---
echo "[hard-shell] Starting OpenClaw gateway on port $GATEWAY_PORT..."
openclaw gateway --port "$GATEWAY_PORT" --token "$OPENCLAW_GATEWAY_TOKEN" --bind lan --allow-unconfigured &
GATEWAY_PID=$!

# Wait for gateway to be healthy
echo "[hard-shell] Waiting for gateway..."
for i in $(seq 1 30); do
    if curl -sf "http://127.0.0.1:$GATEWAY_PORT/health" > /dev/null 2>&1; then
        echo "[hard-shell] Gateway ready."
        break
    fi
    if ! kill -0 "$GATEWAY_PID" 2>/dev/null; then
        echo "[hard-shell] ERROR: Gateway failed to start."
        exit 1
    fi
    sleep 1
done

echo "[hard-shell] ================================================"
echo "[hard-shell] Hard Shell is running."
echo "[hard-shell]   Gateway:  http://127.0.0.1:$GATEWAY_PORT"
echo "[hard-shell]   Scanner:  http://127.0.0.1:$SCANNER_PORT (internal)"
echo "[hard-shell]   Preset:   $TWEEK_PRESET"
echo "[hard-shell] ================================================"

# Wait for either process to exit
wait -n "$SCANNER_PID" "$GATEWAY_PID" 2>/dev/null || true
cleanup
