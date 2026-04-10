/**
 * Per-provider rate limiter registry.
 *
 * Manages one token-bucket rate limiter per LLM provider (opencode, openai,
 * anthropic, etc.). Callers await `acquireProviderToken(providerId)` before
 * making an LLM API request.
 *
 * Configuration flows from `agents.defaults.rateLimit` with per-provider
 * overrides in `agents.defaults.rateLimit.providers[providerId]`.
 */

import { TokenBucketRateLimiter } from "./token-bucket-rate-limiter.js";

export type ProviderRateLimitConfig = {
  /** Tokens added per second (refill rate). */
  tokensPerSecond: number;
  /** Maximum tokens the bucket can hold (burst capacity). */
  burstCapacity: number;
  /** Timeout when waiting for a token (ms). */
  acquireTimeoutMs: number;
};

export type ProviderRateLimitStatus = {
  providerId: string;
  available: number;
  capacity: number;
  rate: number;
  nextRefillMs: number;
};

const DEFAULT_GLOBAL_TOKENS_PER_SECOND = 10;
const DEFAULT_GLOBAL_BURST_CAPACITY = 20;
const DEFAULT_ACQUIRE_TIMEOUT_MS = 60_000;

const registry = new Map<string, TokenBucketRateLimiter>();
let globalConfig: ProviderRateLimitConfig = {
  tokensPerSecond: DEFAULT_GLOBAL_TOKENS_PER_SECOND,
  burstCapacity: DEFAULT_GLOBAL_BURST_CAPACITY,
  acquireTimeoutMs: DEFAULT_ACQUIRE_TIMEOUT_MS,
};

/**
 * Configure the global rate limit defaults.
 * Call this during startup after loading config.
 */
export function configureProviderRateLimiter(params: {
  tokensPerSecond?: number;
  burstCapacity?: number;
  acquireTimeoutMs?: number;
  providers?: Record<string, { tokensPerSecond?: number; burstCapacity?: number }>;
}): void {
  if (typeof params.tokensPerSecond === "number" && Number.isFinite(params.tokensPerSecond)) {
    globalConfig.tokensPerSecond = Math.max(0.01, params.tokensPerSecond);
  }
  if (typeof params.burstCapacity === "number" && Number.isFinite(params.burstCapacity)) {
    globalConfig.burstCapacity = Math.max(1, params.burstCapacity);
  }
  if (typeof params.acquireTimeoutMs === "number" && Number.isFinite(params.acquireTimeoutMs)) {
    globalConfig.acquireTimeoutMs = Math.max(1000, params.acquireTimeoutMs);
  }

  if (params.providers) {
    for (const [providerId, override] of Object.entries(params.providers)) {
      const existing = registry.get(providerId);
      if (existing) {
        if (typeof override.tokensPerSecond === "number") {
          existing.setRate(override.tokensPerSecond);
        }
        if (typeof override.burstCapacity === "number") {
          existing.setCapacity(override.burstCapacity);
        }
      }
    }
  }
}

/**
 * Get or create a rate limiter for a provider.
 */
function getOrCreateLimiter(providerId: string): TokenBucketRateLimiter {
  const existing = registry.get(providerId);
  if (existing) {
    return existing;
  }

  const limiter = new TokenBucketRateLimiter({
    tokensPerSecond: globalConfig.tokensPerSecond,
    capacity: globalConfig.burstCapacity,
  });

  registry.set(providerId, limiter);
  return limiter;
}

/**
 * Acquire a rate-limit token for a provider.
 * Awaits if no token is immediately available (up to acquireTimeoutMs).
 *
 * @param providerId - Provider identifier (e.g. "opencode", "openai", "anthropic")
 * @param timeoutMs - Optional override for acquire timeout (ms)
 */
export async function acquireProviderToken(
  providerId: string,
  timeoutMs?: number,
): Promise<{ waitMs: number; remaining: number }> {
  const limiter = getOrCreateLimiter(providerId);
  const timeout = timeoutMs ?? globalConfig.acquireTimeoutMs;
  const result = await limiter.acquire(timeout);
  return { waitMs: result.waitMs, remaining: result.remaining };
}

/**
 * Try to acquire a token without waiting.
 * Returns null if no token is available.
 */
export function tryAcquireProviderToken(
  providerId: string,
): { waitMs: number; remaining: number } | null {
  const limiter = getOrCreateLimiter(providerId);
  const result = limiter.tryAcquire();
  if (result === null) {
    return null;
  }
  return { waitMs: result.waitMs, remaining: result.remaining };
}

/**
 * Get rate limit status for a provider.
 */
export function getProviderRateLimitStatus(providerId: string): ProviderRateLimitStatus | null {
  const limiter = registry.get(providerId);
  if (!limiter) {
    return null;
  }
  return {
    providerId,
    available: limiter.available,
    capacity: limiter.maxCapacity,
    rate: limiter.rate,
    nextRefillMs: limiter.nextRefillMs,
  };
}

/**
 * Get rate limit status for all registered providers.
 */
export function getAllProviderRateLimitStatuses(): ProviderRateLimitStatus[] {
  const statuses: ProviderRateLimitStatus[] = [];
  for (const providerId of Array.from(registry.keys())) {
    const status = getProviderRateLimitStatus(providerId);
    if (status) {
      statuses.push(status);
    }
  }
  return statuses;
}

/**
 * Reset a provider's rate limiter to full capacity.
 */
export function resetProviderRateLimiter(providerId: string): void {
  const limiter = registry.get(providerId);
  limiter?.reset();
}

/**
 * Reset all provider rate limiters.
 */
export function resetAllProviderRateLimiters(): void {
  for (const limiter of Array.from(registry.values())) {
    limiter.reset();
  }
}

/**
 * Remove a provider's rate limiter (e.g. on config reload).
 */
export function removeProviderRateLimiter(providerId: string): void {
  registry.delete(providerId);
}

/**
 * Clear the entire registry (useful for tests).
 */
export function clearProviderRateLimiters(): void {
  registry.clear();
}
