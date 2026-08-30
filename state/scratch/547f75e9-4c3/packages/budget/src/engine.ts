import { BudgetError, type Clock, type Micros, type Price, type UrlString } from '@mwb/core';
import {
  defaultPolicy,
  matchLineItem,
  type BudgetPolicy,
} from './policy.js';

/**
 * A meter is the accounting state for a tenant's spend.
 *
 * Buckets:
 *  - per-call  : one-shot check, not persistent here.
 *  - hourly    : rolling 1-hour window for the tenant.
 *  - daily     : rolling 24-hour window for the tenant.
 *  - line-item : per-line-item buckets (daily + lifetime).
 *
 * The engine does not persist by itself. `AuditLedger` (or a durable
 * implementation) persists records and rehydrates buckets if needed.
 */
export interface MeterState {
  tenantId: string;
  hourly: { windowStart: number; spentMicros: Micros };
  daily: { windowStart: number; spentMicros: Micros };
  lineItems: Record<string, { dailyMicros: number; lifetimeMicros: number }>;
}

export function emptyMeter(tenantId: string, clock: Clock): MeterState {
  return {
    tenantId,
    hourly: { windowStart: Date.parse(clock.now()), spentMicros: 0 },
    daily: { windowStart: Date.parse(clock.now()), spentMicros: 0 },
    lineItems: {},
  };
}

interface CheckResult {
  ok: boolean;
  code: string;
  reason: string;
  remainingMicros?: number;
}

function checkBucket(
  bucketMicros: number,
  ceilingMicros: number,
  priceMicros: number,
  label: string
): CheckResult {
  const remaining = ceilingMicros - bucketMicros;
  if (remaining < 0) return { ok: false, code: 'CEILING_EXCEEDED', reason: `${label}: already over ceiling` };
  if (priceMicros > remaining) {
    return {
      ok: false,
      code: 'PRICE_EXCEEDS_REMAINING',
      reason: `${label}: requested ${priceMicros} exceeds remaining ${remaining}`,
      remainingMicros: remaining,
    };
  }
  return { ok: true, code: 'OK', reason: '', remainingMicros: remaining };
}

function advanceWindow(
  bucket: { windowStart: number; spentMicros: number },
  nowMs: number,
  windowMs: number
): { windowStart: number; spentMicros: number } {
  if (nowMs - bucket.windowStart >= windowMs) return { windowStart: nowMs, spentMicros: 0 };
  return bucket;
}

const HOUR_MS = 3_600_000;
const DAY_MS = 86_400_000;
/**
 * The Budget Engine.
 *
 * Responsibilities (and only responsibilities):
 *  1. Refuse work that cannot fit inside a declared ceiling.
 *  2. Record work that has been done (paid or denied) into the meter.
 *  3. Tell the caller exactly which ceiling bit and why.
 *
 * It does NOT decide what to pay with, it does NOT sign anything, it
 * does NOT enforce licenses. Those are the Rail Adapter, Identity, and
 * License Engine. Keeping them separate is what makes each one swappable —
 * which is the whole point of "one contract."
 */
export class BudgetEngine {
  private readonly meters = new Map<string, MeterState>();
  private readonly clock: Clock;
  private readonly policy: BudgetPolicy;

  constructor(options?: { clock?: Clock; policy?: BudgetPolicy }) {
    this.clock = options?.clock ?? { now: () => new Date().toISOString() };
    this.policy = options?.policy ?? defaultPolicy;
  }

  get policy(): BudgetPolicy {
    return this.policy;
  }

  /**
   * Pre-flight: will this request fit?
   *
   * This is the ONLY pre-payment step that may raise BudgetError. The
   * broker surfaces it as `failureClass: 'blocked'` — a policy denial —
   * which the audit layer records as a `denied` row.
   *
   * Deliberately throws (not returns a decision object) because "does not
   * fit" is an exceptional condition for the caller and the audit trail
   * cares about the message, not a tagged union.
   */
  preflight(tenantId: string, price: Price, url?: UrlString): void {
    if (price.currency !== this.policy.currency) {
      throw new BudgetError(
        `Tenant currency mismatch: policy is ${this.policy.currency}, quote is ${price.currency}`,
        'CURRENCY_MISMATCH'
      );
    }
    if (!Number.isSafeInteger(price.unitsMicros) || price.unitsMicros <= 0) {
      throw new BudgetError(`Quote must be a positive safe integer, got ${price.unitsMicros}`, 'BAD_QUOTE');
    }
    if (price.unitsMicros > this.policy.perCallCeilingMicros) {
      throw new BudgetError(
        `Per-call ceiling exceeded: quote ${price.unitsMicros} > ${this.policy.perCallCeilingMicros}`,
        'PER_CALL_CEILING'
      );
    }

    const nowMs = Date.parse(this.clock.now());
    const state = this.getOrCreate(tenantId, nowMs);

    state.hourly = advanceWindow(state.hourly, nowMs, HOUR_MS);
    state.daily = advanceWindow(state.daily, nowMs, DAY_MS);

    const hourlyCheck = checkBucket(
      state.hourly.spentMicros,
      this.policy.hourlyTenantCeilingMicros,
      price.unitsMicros,
      `hourly tenant ceiling`
    );
    if (!hourlyCheck.ok) throw new BudgetError(hourlyCheck.reason, hourlyCheck.code);

    const dailyCheck = checkBucket(
      state.daily.spentMicros,
      this.policy.dailyTenantCeilingMicros,
      price.unitsMicros,
      `daily tenant ceiling`
    );
    if (!dailyCheck.ok) throw new BudgetError(dailyCheck.reason, dailyCheck.code);

    if (url) {
      const li = matchLineItem(this.policy, url);
      if (li) {
        if (price.currency !== li.currency) {
          throw new BudgetError(
            `Line-item currency mismatch: ${li.currency} vs quote ${price.currency}`,
            'LINE_ITEM_CURRENCY'
          );
        }
        const bucket = state.lineItems[li.match] ?? { dailyMicros: 0, lifetimeMicros: 0 };
        const dCheck = checkBucket(
          bucket.dailyMicros,
          li.dailyCeilingMicros,
          price.unitsMicros,
          `line-item daily (${li.match})`
        );
        if (!dCheck.ok) throw new BudgetError(dCheck.reason, dCheck.code);
        const lCheck = checkBucket(
          bucket.lifetimeMicros,
          li.lifetimeCeilingMicros,
          price.unitsMicros,
          `line-item lifetime (${li.match})`
        );
        if (!lCheck.ok) throw new BudgetError(lCheck.reason, lCheck.code);
      }
    }
  }

  /**
   * Record a completed paid fetch. Advances every meter that was charged
   * for this call. The broker calls this exactly once per settled fetch.
   */
  recordPaid(tenantId: string, price: Price, url: UrlString): void {
    const nowMs = Date.parse(this.clock.now());
    const state = this.getOrCreate(tenantId, nowMs);
    state.hourly = advanceWindow(state.hourly, nowMs, HOUR_MS);
    state.daily = advanceWindow(state.daily, nowMs, DAY_MS);
    state.hourly.spentMicros += price.unitsMicros;
    state.daily.spentMicros += price.unitsMicros;

    const li = matchLineItem(this.policy, url);
    if (li) {
      const bucket = state.lineItems[li.match] ?? { dailyMicros: 0, lifetimeMicros: 0 };
      bucket.dailyMicros += price.unitsMicros;
      bucket.lifetimeMicros += price.unitsMicros;
      state.lineItems[li.match] = bucket;
    }
  }

  /**
   * Record a denied fetch. Does NOT charge, but SHOULD be observed on
   * dashboards to distinguish "the agent tried and was stopped by us" from
   * "the agent tried and was stopped by the origin."
   *
   * Denial rows live in the audit ledger; this hook is kept so a future
   * in-memory rate limiter (e.g. deny-burst) can subscribe uniformly.
   */
  recordDenied(_tenantId: string): void {
    // Intentionally no-op in-memory; the audit ledger is the source of truth.
  }

  /** Snapshot of current meter state for the dashboard. */
  snapshot(tenantId: string): MeterState {
    const state = this.meters.get(tenantId);
    if (!state) throw new BudgetError(`Unknown tenant: ${tenantId}`, 'UNKNOWN_TENANT');
    return structuredClone(state);
  }

  private getOrCreate(tenantId: string, nowMs: number): MeterState {
    let state = this.meters.get(tenantId);
    if (!state) {
      state = emptyMeter(tenantId, { now: () => new Date(nowMs).toISOString() });
      this.meters.set(tenantId, state);
    }
    return state;
  }
}

// Re-export so imports don't need to reach into the policy module.
export { defaultPolicy, matchLineItem, globMatch } from './policy.js';
export type { BudgetPolicy, LineItem } from './policy.js';
