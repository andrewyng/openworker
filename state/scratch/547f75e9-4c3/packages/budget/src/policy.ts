import type { FailureClass, Price, UrlString } from '@mwb/core';

/**
 * Budget decision returned by the BudgetEngine before the broker does any
 * network work, or after it observes the origin's response.
 *
 * The point: a "generic failure" is no longer reportable. Every terminal
 * state maps to one of the three classes so an agent (or a human dashboard)
 * can say exactly why the cost decision happened.
 */
export type PreFlightDecision =
  | {
      kind: 'allow';
      /** The price quote the engine will pay without further action. */
      quote: Price;
      /** Rail the quote came from; the broker pays through this rail. */
      rail: string;
      note?: string;
    }
  | {
      kind: 'require-settlement';
      /** Quote that needs an explicit settlement authorization. */
      quote: Price;
      rail: string;
      reason: string;
    }
  | {
      kind: 'deny';
      failureClass: Extract<FailureClass, 'blocked' | 'token-rejected'>;
      code: string;
      reason: string;
    };

export type OutcomeDecision =
  | { kind: 'record-paid'; failureClass?: never }
  | { kind: 'record-denied'; failureClass: FailureClass; code?: string; reason: string };

/**
 * A line-item: the recurring ceiling that groups URLs (e.g. all fetches to
 * news-site.com, or a domain group). Treated like a monthly credit line.
 */
export interface LineItem {
  /** Glob over host + path. Supports `*` anywhere. */
  match: string;
  /** Max spend per calendar day, in micros. */
  dailyCeilingMicros: number;
  /** Hard cap over the line item's lifetime, in micros. */
  lifetimeCeilingMicros: number;
  currency: string;
  label?: string;
}

/**
 * Tenant-level policy. All ceilings are in micros (1/1,000,000 of a unit).
 * Defaults are chosen to be strict enough to be useful and loose enough
 * not to block a demo: one fetch must always fit inside every ceiling.
 */
export interface BudgetPolicy {
  /** Absolute cap per fetch, in micros. Default 10_000 (0.01). */
  perCallCeilingMicros: number;
  /** Cap per tenant per rolling hour, in micros. Default 100_000 (0.10). */
  hourlyTenantCeilingMicros: number;
  /** Cap per tenant per rolling day, in micros. Default 1_000_000 (1.00). */
  dailyTenantCeilingMicros: number;
  currency: string;
  /** Line items are consulted after the per-call check; a fetch can fit
   * the per-call rule but still exceed the line-item rule. */
  lineItems: LineItem[];
}

export const defaultPolicy: BudgetPolicy = Object.freeze({
  perCallCeilingMicros: 10_000,
  hourlyTenantCeilingMicros: 100_000,
  dailyTenantCeilingMicros: 1_000_000,
  currency: 'USD',
  lineItems: [],
});

export function matchLineItem(policy: BudgetPolicy, url: UrlString): LineItem | undefined {
  const u = new URL(url);
  const haystack = u.host + u.pathname;
  return policy.lineItems.find((li) => globMatch(li.match, haystack) ?? false);
}

/** A very small glob: `*` matches any run of chars. Sufficient for the
 *  line-item matcher; not a general-purpose path matcher on purpose. */
export function globMatch(pattern: string, haystack: string): boolean {
  const re = new RegExp(
    '^' +
      pattern
        .split('*')
        .map((part) => part.replace(/[.*+?^${}()|[\]\\]/g, '\\$&'))
        .join('.*') +
      '$'
  );
  return re.test(haystack);
}
