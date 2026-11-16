/** Output already includes reasoning tokens; do not count them a second time. */
export function usageTotals(traces) {
  let input = 0,
    output = 0,
    known = false,
    partial = false;
  for (const trace of traces) {
    const u = trace?.summary?.usage;
    for (const kind of ["input", "output"]) {
      const m = u?.[kind];
      if (m?.total != null) {
        if (kind === "input") input += m.total;
        else output += m.total;
        known = true;
      } else partial = true;
      if (m?.unknown > 0) partial = true;
    }
  }
  return {
    input: known ? input : null,
    output: known ? output : null,
    total: known ? input + output : null,
    partial,
    turns: traces.length,
  };
}
