/** Deterministic base64url for signatures and payloads. */
export function b64url(bytes: Uint8Array): string {
  return Buffer.from(bytes).toString('base64url');
}

export function fromB64url(s: string): Uint8Array {
  return new Uint8Array(Buffer.from(s, 'base64url'));
}

export function headerRecordToSortedEntries(headers: Record<string, string>): Array<[string, string]> {
  return Object.entries(headers)
    .map(([k, v]) => [k.toLowerCase(), v] as [string, string])
    .sort(([a], [b]) => (a < b ? -1 : a > b ? 1 : 0));
}
