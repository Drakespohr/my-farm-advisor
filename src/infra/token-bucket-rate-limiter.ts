/**
 * Token-bucket rate limiter.
 *
 * Tokens refill at a constant rate, allowing natural burst absorption
 * while smoothing sustained request patterns. Smoother than fixed-window
 * limiters because there is no boundary-edge burst.
 */

export type TokenBucketOptions = {
  /** Tokens added per second (refill rate). */
  tokensPerSecond: number;
  /** Maximum tokens the bucket can hold (burst capacity). */
  capacity: number;
  /** Initial tokens in the bucket (defaults to full capacity). */
  initialTokens?: number;
};

export type TokenBucketAcquireResult = {
  /** Whether the token was acquired immediately. */
  immediate: boolean;
  /** Milliseconds the caller had to wait (0 if immediate). */
  waitMs: number;
  /** Tokens remaining after acquisition. */
  remaining: number;
};

export class TokenBucketRateLimiter {
  private tokens: number;
  private capacity: number;
  private tokensPerSecond: number;
  private lastRefillMs: number;
  private pendingAcquires: Array<{
    resolve: (result: TokenBucketAcquireResult) => void;
    reject: (err: Error) => void;
    timestamp: number;
  }> = [];
  private refillTimer: ReturnType<typeof setTimeout> | null = null;

  constructor(options: TokenBucketOptions) {
    this.capacity = Math.max(1, options.capacity);
    this.tokensPerSecond = Math.max(0.01, options.tokensPerSecond);
    this.tokens =
      typeof options.initialTokens === "number" && Number.isFinite(options.initialTokens)
        ? Math.min(options.initialTokens, this.capacity)
        : this.capacity;
    this.lastRefillMs = Date.now();
  }

  /**
   * Acquire a token. Resolves immediately if a token is available,
   * otherwise waits until one refills.
   */
  async acquire(timeoutMs = 60_000): Promise<TokenBucketAcquireResult> {
    this.refill();

    if (this.tokens >= 1) {
      this.tokens -= 1;
      return { immediate: true, waitMs: 0, remaining: this.tokens };
    }

    const startMs = Date.now();

    return new Promise<TokenBucketAcquireResult>((resolve, reject) => {
      const timer = setTimeout(() => {
        const idx = this.pendingAcquires.findIndex((p) => p.resolve === resolve);
        if (idx !== -1) {
          this.pendingAcquires.splice(idx, 1);
        }
        reject(new Error(`Token acquire timed out after ${timeoutMs}ms`));
      }, timeoutMs);

      const entry = {
        resolve: (result: TokenBucketAcquireResult) => {
          clearTimeout(timer);
          resolve(result);
        },
        reject: (err: Error) => {
          clearTimeout(timer);
          reject(err);
        },
        timestamp: startMs,
      };

      this.pendingAcquires.push(entry);
      this.scheduleRefill();
    });
  }

  /**
   * Try to acquire a token without waiting.
   * Returns null if no token is available.
   */
  tryAcquire(): TokenBucketAcquireResult | null {
    this.refill();

    if (this.tokens < 1) {
      return null;
    }

    this.tokens -= 1;
    return { immediate: true, waitMs: 0, remaining: this.tokens };
  }

  /** Current tokens available (after refill). */
  get available(): number {
    this.refill();
    return this.tokens;
  }

  /** Milliseconds until the next token refills. */
  get nextRefillMs(): number {
    if (this.tokens >= 1) {
      return 0;
    }
    const elapsed = Date.now() - this.lastRefillMs;
    const intervalMs = 1000 / this.tokensPerSecond;
    return Math.max(0, intervalMs - elapsed);
  }

  /** Current capacity. */
  get maxCapacity(): number {
    return this.capacity;
  }

  /** Refill rate in tokens per second. */
  get rate(): number {
    return this.tokensPerSecond;
  }

  /** Reset the bucket to full capacity. */
  reset(): void {
    this.tokens = this.capacity;
    this.lastRefillMs = Date.now();
    this.flushPending();
  }

  /** Update the refill rate at runtime. */
  setRate(tokensPerSecond: number): void {
    this.refill();
    this.tokensPerSecond = Math.max(0.01, tokensPerSecond);
  }

  /** Update the capacity at runtime. */
  setCapacity(capacity: number): void {
    this.refill();
    this.capacity = Math.max(1, capacity);
    this.tokens = Math.min(this.tokens, this.capacity);
  }

  private refill(): void {
    const nowMs = Date.now();
    const elapsedMs = nowMs - this.lastRefillMs;
    if (elapsedMs <= 0) {
      return;
    }

    const gained = (elapsedMs / 1000) * this.tokensPerSecond;
    this.tokens = Math.min(this.capacity, this.tokens + gained);
    this.lastRefillMs = nowMs;
  }

  private scheduleRefill(): void {
    if (this.refillTimer !== null || this.pendingAcquires.length === 0) {
      return;
    }

    const tick = () => {
      this.refillTimer = null;
      this.refill();
      this.flushPending();

      if (this.pendingAcquires.length > 0) {
        this.scheduleRefill();
      }
    };

    const intervalMs = 1000 / this.tokensPerSecond;
    const waitMs = Math.max(1, intervalMs - (Date.now() - this.lastRefillMs));
    this.refillTimer = setTimeout(tick, waitMs);
  }

  private flushPending(): void {
    const toFlush = [...this.pendingAcquires];
    this.pendingAcquires = [];

    for (const entry of toFlush) {
      if (this.tokens >= 1) {
        this.tokens -= 1;
        const waitMs = Date.now() - entry.timestamp;
        entry.resolve({ immediate: false, waitMs, remaining: this.tokens });
      } else {
        this.pendingAcquires.push(entry);
      }
    }
  }
}
