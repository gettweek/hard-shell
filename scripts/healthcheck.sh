#!/usr/bin/env bash
# Hard Shell healthcheck — verifies both services are responding

SCANNER_PORT="${TWEEK_SCANNER_PORT:-9878}"
GATEWAY_PORT="${OPENCLAW_GATEWAY_PORT:-18789}"

# Check scanner server
if ! curl -sf "http://127.0.0.1:$SCANNER_PORT/health" > /dev/null 2>&1; then
    echo "Scanner server unhealthy"
    exit 1
fi

# Check gateway
if ! curl -sf "http://127.0.0.1:$GATEWAY_PORT/health" > /dev/null 2>&1; then
    echo "Gateway unhealthy"
    exit 1
fi

exit 0
