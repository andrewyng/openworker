import { formatMicros } from '@mwb/core';
import type { LedgerEntry } from '@mwb/core';
import { summarize } from './ledger.js';

/**
 * A minimal, dependency-free text dashboard. Deliberately dumb: pipe its
 * output anywhere — CI logs, a status page, an ops channel — and the three
 * failure classes stay visible. The moat is *seeing* these rows, which is
 * what collapses into a generic failure today.
 */
export function renderDashboard(rows: LedgerEntry[], title = 'Metered Web Broker'): string {
  const s = summarize(rows);
  const bar = '─'.repeat(70);
  const lines: string[] = [];
  lines.push(`┌${bar}┐`);
  lines.push(`│ ${title.padEnd(70)} │`);
  lines.push(`└${bar}┘`);
  lines.push(`  total ${s.total}   fulfilled ${s.fulfilled}   denied ${s.denied}`);
  const classes = Object.entries(s.byFailureClass)
    .sort(([a], [b]) => (a < b ? -1 : 1))
    .map(([k, v]) => `${k}=${v}`)
    .join('  ');
  if (classes) lines.push(`  classes  ${classes}`);
  if (s.spendMicros > 0) lines.push(`  spend    ${formatMicros(s.spendMicros)}`);
  lines.push('');

  const cols: Array<[string, (r: LedgerEntry) => string]> = [
    ['seq', (r) => String(r.seq)],
    ['fetch_id', (r) => r.fetchId],
    ['status', (r) => r.status],
    ['class', (r) => r.failureClass ?? '-'],
    ['http', (r) => (r.httpStatus != null ? String(r.httpStatus) : '-')],
    ['price', (r) => (r.priceMicros != null ? formatMicros(r.priceMicros) : '-')],
    ['rail', (r) => r.rail ?? '-'],
  ];
  const widths = cols.map(([label, get]) =>
    Math.max(label.length, ...rows.map((r) => get(r).length))
  );
  const pad = (i: number, t: string) => t.padEnd(widths[i] ?? 0);
  lines.push(cols.map(([label], i) => pad(i, label.toUpperCase())).join('   '));
  lines.push('-'.join(widths.map((w) => '-'.repeat(w))).replace(/\s+/g, ''));
  for (const r of rows) lines.push(cols.map(([_l, get], i) => pad(i, get(r))).join('   '));

  return lines.join('\n');
}
