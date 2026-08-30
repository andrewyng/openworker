import { describe, expect, it } from 'vitest';
import { BudgetEngine, defaultPolicy, globMatch } from '@mwb/budget';
import { BudgetError, frozenClock } from '@mwb/core';
import type { UrlString } from '@mwb/core';

const price001 = { unitsMicros: 1_000, currency: 'USD' }; // 0.001
const u1 = 'https://news.example.com/wire/headline' as UrlString;

describe('BudgetEngine.preflight', () => {
  it('lets cheap quotes through', () => {
    const engine = new BudgetEngine({ clock: frozenClock() });
    expect(() => engine.preflight('tenant-a', price001, u1)).not.toThrow();
  });

  it('blocks a quote above the per-call ceiling', () => {
    const engine = new BudgetEngine({ clock: frozenClock() });
    expect(() => engine.preflight('tenant-a', { unitsMicros: 100_000, currency: 'USD' })).toThrowError(
      BudgetError
    );
  });

  it('blocks once the hourly tenant ceiling is consumed', () => {
    const clock = frozenClock();
    const policy = {
      ...defaultPolicy,
      perCallCeilingMicros: 10_000,
      hourlyTenantCeilingMicros: 2_000,
    };
    const engine = new BudgetEngine({ clock, policy });
    engine.preflight('t', price001);
    engine.recordPaid('t', price001, u1);
    expect(() => engine.preflight('t', price001)).toThrow(/hourly tenant ceiling/);
  });

  it('lets the hourly window roll over', () => {
    let t = '2026-08-18T00:00:00.000Z';
    const clock = { now: () => t };
    const policy = {
      ...defaultPolicy,
      perCallCeilingMicros: 10_000,
      hourlyTenantCeilingMicros: 2_000,
    };
    const engine = new BudgetEngine({ clock, policy });
    engine.recordPaid('t', price001, u1);
    engine.recordPaid('t', { unitsMicros: 1_500, currency: 'USD' }, u1);
    expect(() => engine.preflight('t', price001)).toThrow();
    t = '2026-08-18T02:00:00.000Z';
    expect(() => engine.preflight('t', price001)).not.toThrow();
  });

  it('enforces per-line-item daily ceilings', () => {
    const clock = frozenClock();
    const policy = {
      ...defaultPolicy,
      lineItems: [
        { match: '*.news.example.com/*', dailyCeilingMicros: 2_500, lifetimeCeilingMicros: 5_000, currency: 'USD' },
      ],
    };
    const engine = new BudgetEngine({ clock, policy });
    for (let i = 0; i < 2; i++) {
      engine.preflight('t', price001, u1);
      engine.recordPaid('t', price001, u1);
    }
    expect(() => engine.preflight('t', price001, u1)).toThrow(/line-item daily/);
    // Other hosts are unaffected.
    const u2 = 'https://other.example.org/a' as UrlString;
    engine.recordPaid('t', price001, u2);
  });

  it('raises a stable code on denial', () => {
    const engine = new BudgetEngine({ clock: frozenClock(), policy: { ...defaultPolicy, hourlyTenantCeilingMicros: 500 } });
    let caught: unknown;
    try {
      engine.recordPaid('t', { unitsMicros: 1_000, currency: 'USD' }, u1);
      engine.preflight('t', price001, u1);
    } catch (err) {
      caught = err;
    }
    expect(caught).toBeInstanceOf(BudgetError);
  });

  it('rejects cross-currency quotes', () => {
    const engine = new BudgetEngine({ clock: frozenClock() });
    expect(() => engine.preflight('t', { unitsMicros: 1_000, currency: 'EUR' })).toThrow(/currency/i);
  });
});

describe('globMatch', () => {
  it('matches host + path with *', () => {
    expect(globMatch('*.news.example.com/*', 'news.example.com/wire/headline')).toBe(true);
    expect(globMatch('news.example.com/*', 'other.example.org/a')).toBe(false);
  });
});

describe('meter snapshot', () => {
  it('tracks spend per tenant and per line item', () => {
    const clock = frozenClock();
    const engine = new BudgetEngine({
      clock,
      policy: {
        ...defaultPolicy,
        lineItems: [
          { match: 'news.example.com/*', dailyCeilingMicros: 100_000, lifetimeCeilingMicros: 100_000, currency: 'USD' },
        ],
      },
    });
    engine.recordPaid('t', price001, u1);
    const snap = engine.snapshot('t');
    expect(snap.hourly.spentMicros).toBe(1_000);
    expect(snap.daily.spentMicros).toBe(1_000);
    expect(snap.lineItems['news.example.com/*'].lifetimeMicros).toBe(1_000);
    // Snapshot is defensive.
    snap.hourly.spentMicros = 999_999;
    expect(engine.snapshot('t').hourly.spentMicros).toBe(1_000);
  });
});
