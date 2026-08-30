import {
  RailError,
  makeSettlementRef,
  type Clock,
  type PaymentRail,
  type PaymentRequest,
  type Quote,
  type RailAuthorization,
  type RailSettlement,
  type Micros,
} from '@mwb/core';

/**
 * A rail is a backend: quote → authorize → settle, all behind one
 * interface. Field names inside `payload` are intentionally opaque — the
 * specs churn (x402 v2, Cloudflare Pay Per Crawl are still being
 * finalized), so the broker never parses them. Draft-caveat discipline:
 * build against the interface, not a draft's field names.
 */
export interface RailBackend extends PaymentRail {}

/**
 * In-memory rail for tests, demos, and local dev. Mirrors the x402 shape
 * (micro-unit price, `X-PAYMENT` header) without an external service.
 * Deterministic: with a `frozenClock` and fixed input, the settlement
 * reference is stable so reconciliation can be asserted.
 */
export class InMemoryRail implements PaymentRail {
  readonly id: string;
  private readonly settlementSeed: string = '';
  private readonly failureMode: 'none' | 'quote' | 'authorize' | 'settle';

  constructor(options: {
    id: string;
    priceMicros: Micros;
    currency?: string;
    clock?: Clock;
    failureMode?: 'none' | 'quote' | 'authorize' | 'settle';
    /** Stable suffix seed so settlement refs are deterministic in tests. */
    settlementSeed?: string;
  }) {
    this.id = options.id;
    this.priceMicros = options.priceMicros;
    this.currency = options.currency ?? 'USD';
    this.clock = options.clock ?? { now: () => new Date().toISOString() };
    this.failureMode = options.failureMode ?? 'none';
    this.settlementSeed = options.settlementSeed ?? `${options.id}-seed`;
  }

  quote(_req: PaymentRequest): Promise<Quote> {
    if (this.failureMode === 'quote') {
      return Promise.reject(
        new RailError(`${this.id}: quote failed`, 'RAIL_QUOTE', undefined, true)
      );
    }
    const nowMs = Date.parse(this.clock.now());
    return Promise.resolve({
      unitsMicros: this.priceMicros,
      currency: this.currency,
      rail: this.id,
      settlement: 'immediate',
      expiresAt: new Date(nowMs + 60_000).toISOString(),
    });
  }

  authorize(req: PaymentRequest, quote: Quote): Promise<RailAuthorization> {
    if (this.failureMode === 'authorize') {
      return Promise.reject(
        new RailError(`${this.id}: authorize failed`, 'RAIL_AUTHORIZE', undefined, true)
      );
    }
    const payload = JSON.stringify({
      schema: 'urn:rail:' + this.id,
      amountMicros: quote.unitsMicros,
      currency: quote.currency,
      url: String(req.url),
      method: req.method,
    });
    return Promise.resolve({
      scheme: this.id,
      payload,
      settlementHeaders: { [`${this.id.replace(/[^a-z0-9]/gi, '-').toLowerCase()}-payment`]: 'authorized' },
    });
  }

  settle(req: PaymentRequest, authorization: RailAuthorization): Promise<RailSettlement> {
    if (this.failureMode === 'settle') {
      return Promise.reject(
        new RailError(`${this.id}: settle failed`, 'RAIL_SETTLE', undefined, true)
      );
    }
    const parsed = JSON.parse(authorization.payload) as { amountMicros: number; currency: string };
    return Promise.resolve({
      rail: this.id,
      // Deterministic from (seed, url) so tests can assert reconciliation.
      settlementRef: makeSettlementRef(this.id, deterministicRand(this.settlementSeed + String(req.url))),
      amountMicros: parsed.amountMicros,
      currency: parsed.currency,
      settledAt: this.clock.now(),
    });
  }
}

/** Self-hosted 402 rail: same interface, different origin. */
export class SelfHosted402Rail extends InMemoryRail {
  constructor(options: { priceMicros: Micros; clock?: Clock; tenantId?: string }) {
    super({
      id: 'self402',
      priceMicros: options.priceMicros,
      clock: options.clock,
      settlementSeed: `self402-${options.tenantId ?? 'tenant'}`,
    });
    this._tenantId = options.tenantId;
  }
  private _tenantId?: string;
  tenantId(): string | undefined {
    return this._tenantId;
  }
}

/**
 * Wraps an existing bridge client (e.g. an ap2-x402-bridge or AP2
 * rail-adapter-x402 instance) so it can be used as a rail. The broker sees
 * only this minimal shape — it does not depend on the bridge's own types.
 * That is the "one contract" boundary in practice.
 */
export interface BridgeClientShape {
  quote(input: { url: string; method: string; headers: Record<string, string> }): Promise<Quote>;
  authorize(input: {
    url: string;
    method: string;
    headers: Record<string, string>;
    quote: { unitsMicros: number; currency: string };
  }): Promise<RailAuthorization>;
  settle(input: {
    url: string;
    method: string;
    headers: Record<string, string>;
    authorization: RailAuthorization;
  }): Promise<RailSettlement>;
}

export class BridgeRail implements PaymentRail {
  constructor(
    private readonly idAndBridge: { id: string; bridge: BridgeClientShape }
  ) {}

  get id(): string {
    return this.idAndBridge.id;
  }

  quote(req: PaymentRequest): Promise<Quote> {
    return this.idAndBridge.bridge.quote({ url: String(req.url), method: req.method, headers: req.headers });
  }

  authorize(req: PaymentRequest, quote: Quote): Promise<RailAuthorization> {
    return this.idAndBridge.bridge.authorize({
      url: String(req.url),
      method: req.method,
      headers: req.headers,
      quote: { unitsMicros: quote.unitsMicros, currency: quote.currency },
    });
  }

  settle(req: PaymentRequest, authorization: RailAuthorization): Promise<RailSettlement> {
    return this.idAndBridge.bridge.settle({
      url: String(req.url),
      method: req.method,
      headers: req.headers,
      authorization,
    });
  }
}

/** Registry lets the broker pick a rail per URL/tenant without touching the agent. */
export class RailRegistry {
  private readonly rails = new Map<string, PaymentRail>();

  register(rail: PaymentRail): this {
    if (this.rails.has(rail.id)) {
      throw new RailError(`Rail already registered: ${rail.id} (deregister first)`, 'RAIL_REGISTER');
    }
    this.rails.set(rail.id, rail);
    return this;
  }

  deregister(id: string): boolean {
    return this.rails.delete(id);
  }

  get(id: string): PaymentRail {
    const r = this.rails.get(id);
    if (!r) throw new RailError(`No rail registered: ${id}`, 'RAIL_MISSING');
    return r;
  }

  get optional(id: string): PaymentRail | undefined {
    return this.rails.get(id);
  }

  ids(): string[] {
    return [...this.rails.keys()].sort();
  }
}

/** Deterministic PRNG from a seed (FNV-1a based) so settlement refs are stable in tests. */
function deterministicRand(seed: string): () => number {
  let h = 2166136261;
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i);
    h = Math.imul(h, 16777619);
  }
  return () => {
    h += 0x6d2b79f5;
    let t = h;
    t = Math.imul(t ^ (t >>> 15), t | 1);
    t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
  };
}
