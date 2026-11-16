import { test } from "node:test";
import assert from "node:assert/strict";
import { build } from "./operations.mjs";
test("rules are deployment-scoped, replica-deduplicated and freshness guarded", () => {
  const { alerts, queries: q, dashboard } = build({ deployment: "demo" });
  assert.equal(alerts.groups[0].rules.length, 11);
  assert.match(q.queued, /^max\(/);
  assert.match(q.queued, /state_observed_timestamp_seconds/);
  assert.match(q.queued, /database_healthy/);
  assert.match(q.capacity, /heartbeat_timestamp_seconds/);
  assert.match(q.monitoringMissing, /absent/);
  assert.ok(
    Object.values(q).every((q) => q.includes('openworker_deployment="demo"')),
  );
  assert.ok(
    alerts.groups[0].rules.every(
      (r) => r.uid.length <= 40 && r.execErrState === "Error",
    ),
  );
  assert.equal(
    alerts.contactPoints[0].receivers[0].settings.url,
    "http://127.0.0.1:9",
  );
  assert.deepEqual(alerts.policies[0].routes[0].mute_time_intervals, [
    "openworker-local-muted",
  ]);
  assert.equal(dashboard.panels[0].fieldConfig.defaults.noValue, "Unknown");
  assert.throws(() => build({ deployment: 'bad"label' }));
  assert.throws(() => build({ expectedSlots: 0 }));
  assert.throws(() => build({ intervalSeconds: 1 }));
});
