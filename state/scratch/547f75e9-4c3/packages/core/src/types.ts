import type { Price, Quote } from './amounts.js';

/** A string constrained to a valid absolute URL at construction. */
export type UrlString = string & { readonly __brand: 'UrlString' };

export function url(value: string): UrlString {
  try {
    return new URL(value) as unknown as UrlString;
  } catch (cause) {
    throw new TypeError(`url(): invalid absolute URL: ${value}`, { cause });
  }
}

export type HeaderRecord = Record<string, string>;

/**
 * The three distinct failure classes. Today's agents collapse all of these
 * into a generic fetch failure; the broker reports each separately so the
 * cost decision is visible.
 */
export type FailureClass = 'blocked' | 'payment-required' | 'token-rejected';

export type FetchStatus = 'fulfilled' | 'denied' | 'error';

export interface FetchParams {
  url: UrlString;
  headers?: HeaderRecord;
  /** Optional tenant/subject for multi-tenant accounting. */
  tenantId?: string;
  /** Extra budget context from the caller, merged under broker policy. */
  budgetOverride?: { perCallCeilingMicros?: number; lineItemCeilingMicros?: number };
}

export type RslClauseType =
  | 'attribution'
  | 'no-store'
  | 'no-derivative-works'
  | 'no-commercial-use'
  | 'pay-per-inference'
  | 'redistribution'
  | (string & {});

export type RslClauseValue = 'required' | 'permitted' | 'forbidden' | number;

export interface RslClause {
  type: string;
  value: RslClauseValue;
  params: Record<string, string | number>;
}

/** A parsed Rights Statement Language (RSL) document. */
export interface License {
  draft: string;
  id: string;
  title?: string;
  publishedAt?: string;
  clauses: RslClause[];
  rawSource: string;
}

/**
 * Obligations the broker derives from a license and enforces/surfaces for
 * this particular fetch. Surfaced in the outcome so the caller can comply.
 */
export interface LicenseObligation {
  clause: string;
  kind: 'notice' | 'store-prohibited' | 'inference-budget' | 'forbidden' | 'permitted';
  instruction: string;
  applied?: string;
}

export interface IdentityPresentation {
  keyId: string;
  algorithm: 'ed25519';
  signedHeaders: string[];
  timestamp: string;
  /** Opaque PACT-style attestation token where the origin accepts one. */
  pactToken?: string;
}

export interface FetchOutcome {
  fetchId: string;
  status: FetchStatus;
  /** Present iff status !== 'fulfilled'. The three distinct failure classes. */
  failureClass?: FailureClass;
  reason: string;
  origin?: { url: string; httpStatus?: number };
  content?: { body: string; contentType: string; storeable: boolean; attributionNotice?: string };
  license?: License;
  obligations?: LicenseObligation[];
  price?: Price;
  quote?: Quote;
  rail?: string;
  settlementRef?: string;
  identity?: IdentityPresentation;
  tenantId?: string;
  timestamps: { startedAt: string; completedAt: string };
  error?: { code: string; message: string };
}

/** A raw ledger row — the audit surface. */
export interface LedgerEntry {
  seq: number;
  fetchId: string;
  tenantId: string;
  url: string;
  status: FetchStatus;
  failureClass?: FailureClass;
  httpStatus?: number;
  priceMicros?: number;
  currency?: string;
  rail?: string;
  licenseId?: string;
  keyId?: string;
  settlementRef?: string;
  startedAt: string;
  completedAt: string;
  recordedAt: string;
}

/**
 * A payment rail: one interface, pluggable backends.
 * Backends: x402 facilitator, Cloudflare Pay Per Crawl, self-hosted 402.
 * The interface is stable; backend field names are not (draft-caveat discipline).
 */
export interface PaymentRequest {
  url: UrlString;
  method: string;
  headers: HeaderRecord;
  contentType?: string;
}

export interface RailAuthorization {
  /** Headers the origin expects on the settled request (e.g. X-PAYMENT). */
  settlementHeaders: HeaderRecord;
  scheme: string;
  payload: string;
}

export interface RailSettlement {
  rail: string;
  settlementRef: string;
  amountMicros: number;
  currency: string;
  settledAt: string;
}

export interface PaymentRail {
  readonly id: string;
  quote(req: PaymentRequest): Promise<Quote>;
  authorize(req: PaymentRequest, quote: Quote): Promise<RailAuthorization>;
  settle(req: PaymentRequest, authorization: RailAuthorization): Promise<RailSettlement>;
}
