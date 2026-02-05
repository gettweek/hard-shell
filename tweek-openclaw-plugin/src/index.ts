/**
 * Tweek OpenClaw Plugin — Main Entry Point
 *
 * Registers the Tweek security plugin with the OpenClaw Gateway.
 * Uses the OpenClaw Plugin API (register/activate pattern) to hook into
 * the gateway lifecycle for tool screening and output scanning.
 *
 * Architecture:
 *   OpenClaw Gateway → Tweek Plugin (hooks) → Scanner Bridge (HTTP)
 *   → Tweek Python Scanning Server (localhost:9878)
 */

import fs from "fs";
import path from "path";
import { ScannerBridge } from "./scanner-bridge";
import { SkillGuard } from "./skill-guard";
import { ToolScreener } from "./tool-screener";
import { OutputScanner } from "./output-scanner";
import { resolveConfig, type TweekPluginConfig } from "./config";
import { formatStartupBanner } from "./notifications";

const PLUGIN_VERSION = "1.0.0";
const SCANNER_TOKEN_PATH = path.join(
  process.env.HOME ?? "/home/node",
  ".tweek",
  ".scanner_token"
);

/**
 * Read the scanner auth token from the filesystem.
 */
function readScannerToken(): string | undefined {
  try {
    return fs.readFileSync(SCANNER_TOKEN_PATH, "utf-8").trim();
  } catch {
    return undefined;
  }
}

/**
 * The Tweek Security plugin for OpenClaw.
 *
 * Matches the OpenClaw plugin definition pattern:
 *   { id, name, register(api), ... }
 *
 * The `api` object follows the actual OpenClawPluginApi type from
 * openclaw/plugin-sdk, providing:
 *   - api.logger: { info, warn, error, debug? }
 *   - api.pluginConfig: user config from openclaw.json
 *   - api.registerService({ id, start, stop? })
 *   - api.on(hookName, handler, opts?)
 *   - api.registerCommand({ name, description, handler })
 */
const tweekPlugin = {
  id: "tweek-security",
  name: "Tweek Security",
  version: PLUGIN_VERSION,
  description:
    "AI security scanning — tool screening, output scanning, and skill guard",

  /**
   * Register phase — set up hooks before Gateway starts processing.
   */
  register(api: any) {
    // Use OpenClaw's PluginLogger (has info, warn, error, optional debug)
    const log = (msg: string) => {
      try {
        api.logger.info(msg);
      } catch {
        console.error(msg);
      }
    };

    // Resolve configuration from plugin config in openclaw.json
    let config: TweekPluginConfig;
    try {
      const pluginConfig = api.pluginConfig ?? {};
      config = resolveConfig(pluginConfig);
    } catch (err) {
      log(`[Tweek] Config error: ${err}`);
      config = resolveConfig({ preset: "cautious" });
    }

    // Master switch — if disabled, register but don't activate hooks
    if (!config.enabled) {
      log(`[Tweek] Plugin disabled via configuration. Skipping hook registration.`);
      return;
    }

    // Read auth token for scanner communication
    const token = readScannerToken();
    if (!token) {
      log(
        `[Tweek] Warning: No scanner auth token found at ${SCANNER_TOKEN_PATH}`
      );
    }

    const scanner = new ScannerBridge(config.scannerPort, 30000, token);
    const skillGuard = new SkillGuard(scanner, config, log);
    const toolScreener = new ToolScreener(scanner, config, log);
    const outputScanner = new OutputScanner(scanner, config, log);

    // Register the scanning server as a managed service.
    // The scanner process is started by entrypoint.sh, so start() just
    // verifies connectivity and logs the startup banner.
    api.registerService({
      id: "tweek-scanner",
      start: async () => {
        const healthy = await scanner.isHealthy();
        log(
          formatStartupBanner(
            PLUGIN_VERSION,
            config.scannerPort,
            healthy,
            config.preset
          )
        );
        if (!healthy) {
          log(
            `[Tweek] WARNING: Scanner server not reachable at port ${config.scannerPort}. ` +
              `Tool screening will fail open (except in paranoid mode).`
          );
        }
      },
      stop: () => {
        // Scanner lifecycle managed by entrypoint.sh
      },
    });

    // Hook: before_tool_call — screen tool calls before execution.
    // Event shape: { toolName: string, params: Record<string, unknown> }
    // Return: { block?: boolean, blockReason?: string } | void
    api.on(
      "before_tool_call",
      async (
        event: { toolName: string; params: Record<string, unknown> },
        _ctx: { agentId?: string; sessionKey?: string; toolName: string }
      ) => {
        // Check for skill installation commands
        if (event.toolName === "bash" || event.toolName === "Bash") {
          const command = (event.params.command as string) ?? "";

          if (SkillGuard.isInstallCommand(command)) {
            const skillName = SkillGuard.extractSkillName(command);
            if (skillName) {
              log(`[Tweek] Detected skill install: ${skillName}`);
            }
          }
        }

        // Standard tool screening
        const result = await toolScreener.screen(event.toolName, event.params);

        if (result.block) {
          return {
            block: true,
            blockReason: result.blockReason,
          };
        }

        return undefined;
      }
    );

    // Hook: after_tool_call — scan tool output for credential leakage.
    // Event shape: { toolName, params, result?, error?, durationMs? }
    api.on(
      "after_tool_call",
      async (
        event: {
          toolName: string;
          params: Record<string, unknown>;
          result?: unknown;
          error?: string;
          durationMs?: number;
        },
        _ctx: { agentId?: string; sessionKey?: string; toolName: string }
      ) => {
        const output = event.result != null ? String(event.result) : "";
        if (!output) return;

        const result = await outputScanner.scan(event.toolName, output);

        if (result.blocked) {
          log(
            `[Tweek] Output from '${event.toolName}' contained security risk: ${result.reason}`
          );
        }
      }
    );

    log(
      `[Tweek] Security plugin v${PLUGIN_VERSION} registered (preset: ${config.preset})`
    );
  },
};

// Export for OpenClaw plugin loader
export default tweekPlugin;

// Named exports for testing and advanced usage
export { ScannerBridge } from "./scanner-bridge";
export { SkillGuard } from "./skill-guard";
export { ToolScreener } from "./tool-screener";
export { OutputScanner } from "./output-scanner";
export { resolveConfig, type TweekPluginConfig } from "./config";
export type {
  ScanReport,
  ScreeningDecision,
  OutputScanResult,
} from "./scanner-bridge";
export type { SkillGuardResult } from "./skill-guard";
export type { ToolScreenResult } from "./tool-screener";
export type { OutputScreenResult } from "./output-scanner";
