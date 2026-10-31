import { test } from "node:test";
import assert from "node:assert/strict";
import {
  ownedLocalFixture,
  cursorTail,
  noReplay,
  dockerObjectAbsent,
} from "./contract.mjs";
test("Docker absence accepts CLI casing, but daemon/permission failures are not deletion", () => {
  for (const stderr of [
    "Error: No such object: owned",
    "error: no such object: owned",
    "Error: No such container: owned",
  ])
    assert(dockerObjectAbsent({ stderr }));
  for (const stderr of [
    "Cannot connect to the Docker daemon",
    "permission denied",
    "context deadline exceeded",
    "no such network: owned",
  ])
    assert(!dockerObjectAbsent({ stderr }));
});
const m = {
  id: "ows-test",
  namespace: "ow-service-m4-resilience123",
  context: "kind-test",
  url: "https://localhost:58472",
  scripted: true,
};
const ns = {
  metadata: {
    name: m.namespace,
    labels: { "openworker.io/qualification": m.id },
  },
};
test("fault injection rejects foreign, live, remote and unconfirmed deployments", () => {
  ownedLocalFixture(m, ns, m.id);
  for (const changed of [
    { context: "production" },
    { namespace: "ow-service-m4-observe123" },
    { scripted: false },
    { url: "https://remote.example.com" },
  ])
    assert.throws(() => ownedLocalFixture({ ...m, ...changed }, ns, m.id));
  assert.throws(() =>
    ownedLocalFixture(m, { metadata: { ...ns.metadata, labels: {} } }, m.id),
  );
  assert.throws(() => ownedLocalFixture(m, ns, "other"));
});
test("reconnect oracle detects cursor duplicates, reordering and lost-outcome replays", () => {
  cursorTail([{ event_id: 8 }], [{ event_id: 9 }, { event_id: 11 }]);
  for (const v of [
    [{ event_id: 8 }],
    [{ event_id: 10 }, { event_id: 9 }],
    [{ event_id: 9 }, { event_id: 9 }],
  ])
    assert.throws(() => cursorTail([{ event_id: 8 }], v));
  const e = { type: "tool_started", data: { call_id: "call" } };
  noReplay([e, { type: "tool_finished", data: { call_id: "call" } }], "call");
  assert.throws(() => noReplay([e, e], "call"));
});
