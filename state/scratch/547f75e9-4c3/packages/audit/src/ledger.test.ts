import { describe, expect, it } from 'vitest';
import { AuditLedger, reconcile, summarize } from '@mwb/audit';
import { frozenClock, makeFetchId } from '@mwb/core';
import type { FetchOutcome } from '@mwb/core';
import { renderDashboard } from '@mwb/audit';

const clock = frozenClock();

function outcome(partial: Partial<FetchOutcome>): FetchOutcome {
  return {
    fetchId: partial.fetchId ?? makeFetchId(),
    status: 'fulfilled',
    reason: 'ok',
    origin: { url: 'https://news.example.com/wire' },
    timestamps: { startedAt: clock.now(), completedAt: clock.now() },
    ...partial,
  } as FetchOutcome;
}

describe('AuditLedger', () => {
  it('is idempotent per fetchId', () => {
    const ledger = new AuditLedger({ clock });
    const o = { fetchId: 'fetch_fixed1', status: 'fulfilled' as const };
    const a = ledger.record(outcome(o), { tenantId: 't' });
    const b = ledger.record(outcome(o), { tenantId: 't' });
    expect(a).toBe(b);
    expect(ledger.entries.length).toBe(1);
  });

  it('keeps seq gap-free', () => {
    const ledger = new AuditLedger({ clock });
    for (let i = 0; i < 5; i++) ledger.record(outcome({ fetchId: `fetch_n${i}` }));
    expect(ledger.entries.map((e) => e.seq)).toEqual([1, 2, 3, 4, 5]);
    expect(ledger.lastSeq()).toBe(5);
  });

  it('summarizes failure classes and spend separately', () => {
    const ledger = new AuditLedger({ clock });
    ledger.record(outcome({ fetchId: 'f1', price: { unitsMicros: 1000, currency: 'USD' }, rail: 'x402', settlementRef: 's1' }));
    ledger.record(outcome({ fetchId: 'f2', status: 'denied', failureClass: 'blocked', reason: 'policy' }));
    ledger.record(outcome({ fetchId: 'f3', status: 'denied', failureClass: 'payment-required', httpStatus: 402 as unknown as number, origin: { url: 'u', httpStatus: 402 } }));
    ledger.record(outcome({ fetchId: 'f4', status: 'denied', failureClass: 'token-rejected', origin: { url: 'u', httpStatus: 403 } }));
    const s = summarize(ledger.entries);
    expect(s.total).toBe(4);
    expect(s.fulfilled).toBe(1);
    expect(s.denied).toBe(3);
    expect(s.byFailureClass).toEqual({ blocked: 1, 'payment-required': 1, 'token-rejected': 1 });
    expect(s.spendMicros).toBe(1000);
  });
});

describe('reconcile', () => {
  it('passes on a healthy ledger', () => {
    const ledger = new AuditLedger({ clock });
    ledger.record(outcome({ fetchId: 'f1', price: { unitsMicros: 1000, currency: 'USD' }, settlementRef: 'settle_x', rail: 'x402' }));
    ledger.record(outcome({ fetchId: 'f2', status: 'denied', failureClass: 'blocked' }));
    const rep = reconcile(ledger.entries);
    expect(rep.ok).toBe(true);
    expect(rep.problems).toEqual([]);
  });

  it('flags a fulfilled row missing settlementRef', () => {
    const ledger = new AuditLedger({ clock });
    ledger.record(outcome({ fetchId: 'f1', price: { unitsMicros: 1000, currency: 'USD' } }));
    const rep = reconcile(ledger.entries);
    expect(rep.ok).toBe(false);
    expect(rep.checks.fulfilledHasSettlement).toBe(false);
  });

  it('flags a denied row without a failure class', () => {
    const ledger = new AuditLedger({ clock });
    ledger.record(outcome({ fetchId: 'f1', status: 'denied' }));
    const rep = reconcile(ledger.entries);
    expect(rep.checks.deniedHasFailureClass).toBe(false);
  });

  it('flags a seq gap', () => {
    const rows = [
      { seq: 1, fetchId: 'a', tenantId: 't', url: 'u', status: 'denied', failureClass: 'blocked', startedAt: 't', completedAt: 't', recordedAt: 't' },
      { seq: 3, fetchId: 'b', tenantId: 't', url: 'u', status: 'denied', failureClass: 'blocked', startedAt: 't', completedAt: 't', recordedAt: 't' },
    ];
    const rep = reconcile(rows);
    expect(rep.checks.seqGapFree).toBe(false);
  });
});

describe('dashboard', () => {
  it('renders a readable report with all three classes', () => {
    const ledger = new AuditLedger({ clock });
    ledger.record(outcome({ fetchId: 'f1', price: { unitsMicros: 1000, currency: 'USD' }, rail: 'x402', settlementRef: 's' }));
    ledger.record(outcome({ fetchId: 'f2', status: 'denied', failureClass: 'blocked' }));
    ledger.record(outcome({ fetchId: 'f3', status: 'denied', failureClass: 'payment-required' }));
    const text = renderDashboard(ledger.entries);
    expect(text).toContain('blocked=1');
    expect(text).toContain('payment-required=1');
    expect(text).toContain('total 3');
    expect(text).toContain('0.01 USD');
  });
});
