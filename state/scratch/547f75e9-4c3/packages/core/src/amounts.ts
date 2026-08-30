/**
 * Money as integer micro-units (1/1,000,000 of a currency unit), like x402.
 * Avoids floating point in anything that touches a ceiling or a ledger.
 */
export type Micros = number;

export const MICRO_PER_UNIT = 1_000_000;

export interface Price {
  unitsMicros: number;
  currency: string;
}

export interface Quote extends Price {
  rail: string;
  settlement: 'immediate' | 'batch';
  expiresAt?: string;
}

export function micros(units: number): Micros {
  if (!Number.isFinite(units)) throw new RangeError(`micros(): non-finite unit value ${units}`);
  const v = Math.round(units * MICRO_PER_UNIT);
  if (!Number.isSafeInteger(v)) throw new RangeError(`micros(): precision overflow for ${units}`);
  return v;
}

export function formatMicros(micros_: Micros, currency = 'USD'): string {
  return `${(micros_ / MICRO_PER_UNIT).toFixed(2)} ${currency}`;
}

export function addMicros(a: Micros, b: Micros): Micros {
  if (!Number.isSafeInteger(a) || !Number.isSafeInteger(b)) {
    throw new RangeError('addMicros(): unsafe integer input');
  }
  return a + b;
}
