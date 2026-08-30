import type {
  Clock,
  FetchOutcome,
  FailureClass,
  LedgerEntry,
} from '@mwb/core';

/**
 * The ledger is the broker's source of truth: every fetch the broker
 * attempted — fulfilled, denied, failed — appears exactly once.
 *
 * `seq` is monotonically increasing and gap-free; that invariant is what the
 * reconciliation step verifies. If the broker crashes or an origin
 * settlement is missing, reconciliation surfaces it instead of hiding it.
 */
export class AuditLedger {
  readonly entries: LedgerEntry[] = [];
  private seq = 0;
  private readonly clock: Clock;

  constructor(options?: { clock?: Clock }) {
    this.clock = options?.clock ?? { now: () => new Date().toISOString() };
  }

  /** Append one row for one fetch attempt. Idempotent per fetchId. */
  record(outcome: FetchOutcome, options?: { tenantId?: string }): LedgerEntry {
    const existing = this.entries.find((e) => e.fetchId === outcome.fetchId);
    if (existing) return existing;

    this.seq += 1;
    const now = this.clock.now();
    const row: LedgerEntry = {
      seq: this.seq,
      fetchId: outcome.fetchId,
      tenantId: options?.tenantId ?? outcome.tenantId ?? 'default',
      url: outcome.origin?.url ?? 'unknown',
      status: outcome.status === 'fulfilled' ? 'fulfilled' : 'denied',
      failureClass: outcome.failureClass,
      httpStatus: outcome.origin?.httpStatus,
      priceMicros: outcome.price?.unitsMicros,
      currency: outcome.price?.currency,
      rail: outcome.rail,
      licenseId: outcome.license?.id,
      keyId: outcome.identity?.keyId,
      settlementRef: outcome.settlementRef,
      startedAt: outcome.timestamps.startedAt,
      completedAt: outcome.timestamps.completedAt,
      recordedAt: now,
    };
    this.entries.push(row);
    return row;
  }

  /** Gap-free, monotonically increasing seq. */
  lastSeq(): number {
    return this.seq;
  }

  byTenant(tenantId: string): LedgerEntry[] {
    return this.entries.filter((e) => e.tenantId === tenantId);
  }

  byUrl(url: string): LedgerEntry[] {
    return this.entries.filter((e) => e.url === url);
  }

  toRows(): LedgerEntry[] {
    return this.entries.map((e) => structuredClone(e));
  }
}

export interface LedgerSummary {
  total: number;
  fulfilled: number;
  denied: number;
  byFailureClass: Record<string, number>;
  spendMicros: number;
  byRail: Record<string, number>;
  byLicense: Record<string, number>;
}

export function summarize(rows: LedgerEntry[]): LedgerSummary {
  const s: LedgerSummary = {
    total: rows.length,
    fulfilled: 0,
    denied: 0,
    byFailureClass: {},
    spendMicros: 0,
    byRail: {},
    byLicense: {},
  };
  for (const r of rows) {
    if (r.status === 'fulfilled') s.fulfilled += 1;
    else s.denied += 1;
    if (r.failureClass) s.byFailureClass[r.failureClass] = (s.byFailureClass[r.failureClass] ?? 0) + 1;
    if (r.status === 'fulfilled' && r.priceMicros != null) s.spendMicros += r.priceMicros;
    if (r.rail) s.byRail[r.rail] = (s.byRail[r.rail] ?? 0) + 1;
    if (r.licenseId) s.byLicense[r.licenseId] = (s.byLicense[r.licenseId] ?? 0) + 1;
  }
  return s;
}

export interface ReconciliationReport {
  ok: boolean;
  problems: string[];
  checks: {
    seqGapFree: boolean;
    deniedHasFailureClass: boolean;
    fulfilledHasSettlement: boolean;
    fulfilledHasPrice: boolean;
  };
}

/**
 * Reconcile the ledger against invariants:
 *  - seq is gap-free starting at 1
 *  - every denied row has a failureClass (the three-classes invariant)
 *  - every fulfilled row has a settlementRef (we paid, we can prove it)
 *  - every fulfilled row has a price (we can bill what we paid)
 */
export function reconcile(rows: LedgerEntry[]): ReconciliationReport {
  const problems: string[] = [];

  const seqGapFree =
    rows.length === 0 ||
    rows.every((r, i) => r.seq === i + 1);

  const deniedHaveFC = rows
    .filter((r) => r.status !== 'fulfilled')
    .every((r) => r.failureClass !== undefined);

  const fulfilledHasSett = rows
    .filter((r) => r.status === 'fulfilled')
    .every((r) => (r.settlementRef ?? '').length > 0);

  const fulfilledHasPrice = rows
    .filter((r) => r.status === 'fulfilled')
    .every((r) => r.priceMicros != null && r.priceMicros > 0);

  if (!seqGapFree) problems.push('seq is not gap-free starting at 1');
  if (!deniedHaveFC) problems.push('a denied row lacks a failureClass');
  if (!fulfilledHasSett)
    problems.push('a fulfilled row lacks a settlementRef (settlement not proven)');
  if (!fulfilledHasPrice) problems.push('a fulfilled row lacks a positive price');

  return {
    ok:
      seqGapFree && deniedHaveFC && fulfilledHasSett && fulfilledHasPrice,
    problems,
    checks: { seqGapFree, deniedHasFailureClass: deniedHaveFC, fulfilledHasSettlement: fulfilledHasSett, fulfilledHasPrice },
  };
}

export type { FailureClass };
