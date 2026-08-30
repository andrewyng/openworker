/** Injectable clock + id for deterministic tests and audit replay. */
export interface Clock {
  now(): string;
}

export const systemClock: Clock = {
  now(): string {
    return new Date().toISOString();
  },
};

export const frozenClock = (t = '2026-08-18T00:00:00.000Z'): Clock => ({ now: () => t });

/** Random fetch ids (rfc9562-style ULID would be a drop-in upgrade). */
export function makeFetchId(rand: () => number = Math.random): string {
  const alphabet = '0123456789';
  let id = '';
  for (let i = 0; i < 16; i++) id += alphabet[Math.floor(rand() * 16)]!;
  return `fetch_${id}`;
}

export function makeSettlementRef(rail: string, rand: () => number = Math.random): string {
  let suffix = '';
  for (let i = 0; i < 12; i++) suffix += 'abcdefghijklmnopqrstuvwxyz0123456789'[Math.floor(rand() * 36)]!;
  return `settle_${rail}_${suffix}`;
}
