import type { OpenClawConfig } from "./types.js";
import type { AgentNetworkConfig, AgentRateLimitConfig } from "./types.agent-defaults.js";

export const DEFAULT_AGENT_MAX_CONCURRENT = 4;
export const DEFAULT_SUBAGENT_MAX_CONCURRENT = 8;
// Keep depth-1 subagents as leaves unless config explicitly opts into nesting.
export const DEFAULT_SUBAGENT_MAX_SPAWN_DEPTH = 1;

export const DEFAULT_RATE_LIMIT_TOKENS_PER_SECOND = 10;
export const DEFAULT_RATE_LIMIT_BURST_CAPACITY = 20;
export const DEFAULT_RATE_LIMIT_ACQUIRE_TIMEOUT_MS = 60_000;

export const DEFAULT_NETWORK_MAX_CONNECTIONS = 6;
export const DEFAULT_NETWORK_KEEP_ALIVE_TIMEOUT_MS = 30_000;
export const DEFAULT_NETWORK_KEEP_ALIVE_MAX_TIMEOUT_MS = 600_000;

export function resolveAgentMaxConcurrent(cfg?: OpenClawConfig): number {
  const raw = cfg?.agents?.defaults?.maxConcurrent;
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return Math.max(1, Math.floor(raw));
  }
  return DEFAULT_AGENT_MAX_CONCURRENT;
}

export function resolveSubagentMaxConcurrent(cfg?: OpenClawConfig): number {
  const raw = cfg?.agents?.defaults?.subagents?.maxConcurrent;
  if (typeof raw === "number" && Number.isFinite(raw)) {
    return Math.max(1, Math.floor(raw));
  }
  return DEFAULT_SUBAGENT_MAX_CONCURRENT;
}

export function resolveRateLimitConfig(cfg?: OpenClawConfig): AgentRateLimitConfig {
  const raw = cfg?.agents?.defaults?.rateLimit;
  return {
    tokensPerSecond:
      typeof raw?.tokensPerSecond === "number" && Number.isFinite(raw.tokensPerSecond)
        ? Math.max(0.01, raw.tokensPerSecond)
        : DEFAULT_RATE_LIMIT_TOKENS_PER_SECOND,
    burstCapacity:
      typeof raw?.burstCapacity === "number" && Number.isFinite(raw.burstCapacity)
        ? Math.max(1, raw.burstCapacity)
        : DEFAULT_RATE_LIMIT_BURST_CAPACITY,
    acquireTimeoutMs:
      typeof raw?.acquireTimeoutMs === "number" && Number.isFinite(raw.acquireTimeoutMs)
        ? Math.max(1000, raw.acquireTimeoutMs)
        : DEFAULT_RATE_LIMIT_ACQUIRE_TIMEOUT_MS,
    providers: raw?.providers,
  };
}

export function resolveNetworkConfig(cfg?: OpenClawConfig): AgentNetworkConfig {
  const raw = cfg?.agents?.defaults?.network;
  return {
    maxConnectionsPerOrigin:
      typeof raw?.maxConnectionsPerOrigin === "number" && Number.isFinite(raw.maxConnectionsPerOrigin)
        ? Math.max(1, raw.maxConnectionsPerOrigin)
        : DEFAULT_NETWORK_MAX_CONNECTIONS,
    keepAliveTimeoutMs:
      typeof raw?.keepAliveTimeoutMs === "number" && Number.isFinite(raw.keepAliveTimeoutMs)
        ? Math.max(1000, raw.keepAliveTimeoutMs)
        : DEFAULT_NETWORK_KEEP_ALIVE_TIMEOUT_MS,
    keepAliveMaxTimeoutMs:
      typeof raw?.keepAliveMaxTimeoutMs === "number" && Number.isFinite(raw.keepAliveMaxTimeoutMs)
        ? Math.max(1000, raw.keepAliveMaxTimeoutMs)
        : DEFAULT_NETWORK_KEEP_ALIVE_MAX_TIMEOUT_MS,
  };
}
