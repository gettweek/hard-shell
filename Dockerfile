# Hard Shell — Hardened OpenClaw + Tweek Distribution
# https://github.com/gettweek/hard-shell

# ---------------------------------------------------------------------------
# Stage 1: Build the Tweek OpenClaw plugin (TypeScript → JavaScript)
# ---------------------------------------------------------------------------
FROM node:22-bookworm AS plugin-builder

WORKDIR /build/tweek-plugin
COPY tweek-openclaw-plugin/package.json tweek-openclaw-plugin/package-lock.json ./
RUN npm ci --ignore-scripts
COPY tweek-openclaw-plugin/src ./src
COPY tweek-openclaw-plugin/tsconfig.json ./
RUN npm run build

# ---------------------------------------------------------------------------
# Stage 2: Install OpenClaw + Tweek
# ---------------------------------------------------------------------------
FROM node:22-bookworm AS builder

# Install Python 3 + pip for Tweek
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-pip \
    python3-venv \
    git \
    curl \
    && rm -rf /var/lib/apt/lists/*

# Install OpenClaw globally
RUN npm install -g openclaw@latest

# Install Tweek with all extras (LLM review, local ONNX models, MCP)
RUN python3 -m pip install --break-system-packages "tweek[all]"

# Install the pre-built Tweek plugin into OpenClaw's extensions directory
# OpenClaw discovers extensions from this path at startup
RUN OPENCLAW_EXT=/usr/local/lib/node_modules/openclaw/extensions/tweek-security && \
    mkdir -p "$OPENCLAW_EXT/dist"
COPY --from=plugin-builder /build/tweek-plugin/dist /usr/local/lib/node_modules/openclaw/extensions/tweek-security/dist
COPY --from=plugin-builder /build/tweek-plugin/package.json /usr/local/lib/node_modules/openclaw/extensions/tweek-security/package.json
COPY tweek-openclaw-plugin/openclaw.plugin.json /usr/local/lib/node_modules/openclaw/extensions/tweek-security/openclaw.plugin.json

# ---------------------------------------------------------------------------
# Stage 3: Runtime — slim image with only what's needed
# ---------------------------------------------------------------------------
FROM node:22-bookworm-slim

# Install Python runtime (no build tools) + tini for PID 1
RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 \
    python3-venv \
    curl \
    tini \
    procps \
    && rm -rf /var/lib/apt/lists/*

# Copy global npm packages (OpenClaw) from builder
COPY --from=builder /usr/local/lib/node_modules /usr/local/lib/node_modules
# Recreate the openclaw symlink (COPY flattens symlinks, breaking relative imports)
RUN ln -sf /usr/local/lib/node_modules/openclaw/openclaw.mjs /usr/local/bin/openclaw

# Copy Python packages (Tweek) from builder
COPY --from=builder /usr/local/lib/python3.11/dist-packages /usr/local/lib/python3.11/dist-packages
COPY --from=builder /usr/local/bin/tweek /usr/local/bin/tweek

# Copy Hard Shell config and scripts
COPY config/ /opt/hard-shell/config/
COPY scripts/entrypoint.sh /opt/hard-shell/entrypoint.sh
COPY scripts/healthcheck.sh /opt/hard-shell/healthcheck.sh
RUN chmod +x /opt/hard-shell/entrypoint.sh /opt/hard-shell/healthcheck.sh

# Create non-root user directories with correct permissions
RUN mkdir -p /home/node/.openclaw /home/node/.tweek /home/node/workspace \
    /home/node/.cache \
    && chown -R node:node /home/node \
    && chmod 700 /home/node/.openclaw /home/node/.tweek \
    && chmod 755 /home/node/workspace

# Switch to non-root user
USER node
WORKDIR /home/node

# Environment
ENV NODE_ENV=production
ENV TWEEK_PRESET=cautious
ENV OPENCLAW_GATEWAY_PORT=18789
ENV TWEEK_SCANNER_PORT=9878
ENV HARD_SHELL=1

# Expose gateway port only (scanner is internal)
EXPOSE 18789

# Health check
HEALTHCHECK --interval=30s --timeout=10s --retries=3 \
    CMD /opt/hard-shell/healthcheck.sh

# Use tini as init for proper signal handling
ENTRYPOINT ["tini", "--"]
CMD ["/opt/hard-shell/entrypoint.sh"]
