import assert from "node:assert/strict";
export function dockerObjectAbsent(error) {
  return /no such (object|container):/i.test(error.stderr ?? "");
}
// Destructive qualification accepts only an explicitly named, owned local fixture.
export function ownedLocalFixture(m, ns, confirmation) {
  assert.match(m.context, /^kind-[a-z0-9-]+$/);
  assert.match(m.namespace, /^ow-service-m4-resilience[a-z0-9]+$/);
  assert.equal(ns.metadata.name, m.namespace);
  assert.equal(ns.metadata.labels?.["openworker.io/qualification"], m.id);
  assert.equal(
    confirmation,
    m.id,
    "Pass the exact service ID to permit worker faults",
  );
  const u = new URL(m.url);
  assert.equal(u.protocol, "https:");
  assert(["localhost", "127.0.0.1", "[::1]"].includes(u.hostname));
  assert.equal(
    m.scripted,
    true,
    "Deterministic fixture only; never a customer service",
  );
}
export function cursorTail(before, after) {
  const last = before.at(-1)?.event_id ?? 0;
  assert(
    after.every((e) => e.event_id > last),
    "Replay must start strictly after saved cursor",
  );
  assert(
    after.every((e, i) => i === 0 || after[i - 1].event_id < e.event_id),
    "Events must be ordered and unique",
  );
}
export function noReplay(events, callId) {
  assert.equal(
    events.filter((e) => e.type === "tool_started" && e.data.call_id === callId)
      .length,
    1,
    "An uncertain effect must not be dispatched twice",
  );
}
