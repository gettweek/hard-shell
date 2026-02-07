#!/usr/bin/env bash
# Hard Shell healthcheck — verifies both services are responding
# On failure, appends a structured entry to the app log.

SCANNER_PORT="${TWEEK_SCANNER_PORT:-9878}"
GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"
LOG_FILE="/home/node/logs/hard-shell.log"

log_failure() {
    local component="$1"
    local ts
    ts=$(date -u +"%Y-%m-%dT%H:%M:%S.%3NZ" 2>/dev/null || date -u +"%Y-%m-%dT%H:%M:%SZ")
    local json="{\"ts\":\"$ts\",\"level\":\"ERROR\",\"component\":\"healthcheck\",\"msg\":\"$component unhealthy\"}"
    if [ -d "$(dirname "$LOG_FILE")" ]; then
        echo "$json" >> "$LOG_FILE"
    fi
    echo "$component unhealthy"
}

# Check scanner server
if ! curl -sf "http://127.0.0.1:$SCANNER_PORT/health" > /dev/null 2>&1; then
    log_failure "scanner"
    exit 1
fi

# Check gateway
if ! curl -sf "http://127.0.0.1:$GATEWAY_PORT/health" > /dev/null 2>&1; then
    log_failure "gateway"
    exit 1
fi

exit 0
