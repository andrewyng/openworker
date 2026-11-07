// Generate a deployment-scoped dashboard and local-only alert provisioning.
// Same expressions serve the shipped reference and the short fault qualification.
import { createHash } from "node:crypto";
import { writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";

export function build(options = {}) {
  const o = {
    deployment: "default",
    expectedSlots: 2,
    freshSeconds: 45,
    queueSeconds: 120,
    pendingSeconds: 60,
    intervalSeconds: 10,
    errorWindowSeconds: 300,
    ...options,
  };
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(o.deployment))
    throw Error("Invalid deployment identifier");
  for (const key of [
    "expectedSlots",
    "freshSeconds",
    "queueSeconds",
    "pendingSeconds",
    "intervalSeconds",
    "errorWindowSeconds",
  ])
    if (!Number.isInteger(o[key]) || o[key] < 1 || o[key] > 86400)
      throw Error(`Invalid ${key}`);
  if (o.intervalSeconds % 10)
    throw Error("Grafana evaluation interval must be a multiple of 10 seconds");
  const id = `ow-${createHash("sha256").update(o.deployment).digest("hex").slice(0, 12)}`;
  // Stable per mounted reference file: Grafana cannot change a provisioned
  // dashboard's UID in place when deployment configuration is regenerated.
  const dashboardId = "openworker-operations";
  const select = (m) =>
    `(${m} and on(job,instance) target_info{job="openworker",openworker_deployment="${o.deployment}"})`;
  const fresh = (m) => `(${select(m)} > time() - ${o.freshSeconds})`;
  const heartbeat = fresh(
    "openworker_server_sampler_heartbeat_timestamp_seconds",
  );
  const stateFresh = `(${fresh("openworker_server_state_observed_timestamp_seconds")} and on(job,instance) (${select("openworker_server_database_healthy")} == 1) and on(job,instance) ${heartbeat})`;
  const state = (m) =>
    `max(${select(`openworker_server_${m}`)} and on(job,instance) ${stateFresh})`;
  const worker = (m) =>
    `sum(${select(`openworker_worker_${m}`)} and on(job,instance) ${fresh("openworker_worker_heartbeat_timestamp_seconds")})`;
  const errors = (operation) =>
    `(sum((${select(`openworker_operation_last_error_timestamp_seconds{openworker_operation="${operation}"}`)} > bool time() - ${o.errorWindowSeconds})) or vector(0))`;
  const q = {
    monitoringMissing: `(absent(${heartbeat}) or vector(0))`,
    databaseUnhealthy: `min(${select("openworker_server_database_healthy")} and on(job,instance) ${heartbeat})`,
    active: state("sessions_active"),
    queued: state("turns_queued"),
    approvals: state("approvals_pending"),
    questions: state("questions_pending"),
    queueAge: state("oldest_queued_seconds"),
    stalled: state("sessions_stalled"),
    unknown: state("unknown_recent"),
    capacity: `(${worker("capacity")} or vector(0))`,
    occupied: worker("active"),
    stateAge: `time() - max(${select("openworker_server_state_observed_timestamp_seconds")})`,
    stateMissing: `(absent(${stateFresh}) or vector(0))`,
    modelErrors: errors("model.attempt"),
    toolErrors: errors("tool.execute"),
    drops: `(sum(delta(openworker_telemetry_dropped[${o.errorWindowSeconds}s]) and on(job,instance) target_info{job="openworker",openworker_deployment="${o.deployment}"}) or vector(0))`,
    exportFailures: `(sum(delta(openworker_telemetry_export_failures[${o.errorWindowSeconds}s]) and on(job,instance) target_info{job="openworker",openworker_deployment="${o.deployment}"}) or vector(0))`,
  };
  const definitions = [
    [
      "monitoring",
      "Telemetry missing",
      q.monitoringMissing,
      ">",
      0,
      "critical",
      "Check sampler/API process, collector and network. Missing data is unknown, not an empty queue.",
    ],
    [
      "database",
      "Database observer unavailable",
      q.databaseUnhealthy,
      "<",
      1,
      "critical",
      "Check PostgreSQL reachability and bounded query latency. Restart the failed sampler/API after repair; this alert does not prove every API request failed.",
    ],
    [
      "state",
      "Durable state observation stale",
      q.stateMissing,
      ">",
      0,
      "warning",
      "Do not trust queue or approval counts. Check observer, database and telemetry delivery.",
    ],
    [
      "capacity",
      "Worker capacity below target",
      q.capacity,
      "<",
      o.expectedSlots,
      "critical",
      "Inspect worker supervision, database and OpenShell. Restart only after checking durable lease and unknown-effect state. Capacity is advertised, not an end-to-end readiness check.",
    ],
    [
      "queue",
      "Queued work aging",
      q.queueAge,
      ">",
      o.queueSeconds,
      "warning",
      "Check compatible pool capacity, permissions and parked work. Do not blindly resubmit turns.",
    ],
    [
      "stalled",
      "Potentially stalled runs",
      q.stalled,
      ">",
      0,
      "warning",
      "Inspect durable progress and long-running tools. Waiting approvals/questions are excluded. Never auto-retry an uncertain external effect.",
    ],
    [
      "unknown",
      "Recent uncertain effects",
      q.unknown,
      ">",
      0,
      "critical",
      "Inspect execution records and reconcile the external system before retrying. Clearing means no recent transitions, NOT that earlier effects were resolved.",
    ],
    [
      "model",
      "Observed model errors",
      q.modelErrors,
      ">",
      0,
      "warning",
      "Inspect sanitized traces/provider health and retry outcomes. Counts processes with a recent exported error, not errors or billing. The first error is detectable without a counter baseline.",
    ],
    [
      "tool",
      "Observed tool errors",
      q.toolErrors,
      ">",
      0,
      "warning",
      "Inspect durable tool result and environment health; distinguish a safe failure from an uncertain effect.",
    ],
    [
      "drops",
      "Telemetry records dropped",
      q.drops,
      ">",
      0,
      "warning",
      "Inspect collector latency/backpressure. The agent is not blocked by telemetry; exported evidence may be incomplete.",
    ],
    [
      "export",
      "Telemetry export failures",
      q.exportFailures,
      ">",
      0,
      "warning",
      "Check collector and TLS configuration. During complete export failure rely on the missing-telemetry alert instead.",
    ],
  ];
  const rules = definitions.map(
    ([name, title, expr, cmp, threshold, severity, description]) => ({
      uid: `${id}-${name}`,
      title: `${o.deployment} · ${title}`,
      condition: "B",
      data: [
        {
          refId: "A",
          datasourceUid: "prometheus",
          relativeTimeRange: { from: 600, to: 0 },
          model: {
            refId: "A",
            expr,
            instant: true,
            range: false,
            intervalMs: 1000,
            maxDataPoints: 43200,
          },
        },
        {
          refId: "B",
          datasourceUid: "__expr__",
          relativeTimeRange: { from: 0, to: 0 },
          model: {
            refId: "B",
            type: "math",
            expression: `$A ${cmp} ${threshold}`,
          },
        },
      ],
      noDataState: "OK",
      execErrState: "Error",
      for: `${o.pendingSeconds}s`,
      annotations: {
        summary: title,
        description,
        runbook: "deploy/reference/OPERATIONS.md",
        __dashboardUid__: dashboardId,
        __panelId__: "13",
      },
      labels: { service: "openworker", deployment: o.deployment, severity },
      isPaused: false,
    }),
  );
  // Grafana file provisioning cannot create a contact point without receivers.
  // An always-muted, loopback-only placeholder prevents external notifications.
  // This policy belongs ONLY in the owned single-tenant reference Grafana.
  const alerts = {
    apiVersion: 1,
    groups: [
      {
        orgId: 1,
        name: `${id}-operations`,
        folder: "OpenWorker",
        interval: `${o.intervalSeconds}s`,
        rules,
      },
    ],
    contactPoints: [
      {
        orgId: 1,
        name: "OpenWorker local only",
        receivers: [
          {
            uid: "ow-local-muted",
            type: "webhook",
            disableResolveMessage: true,
            settings: { url: "http://127.0.0.1:9" },
          },
        ],
      },
    ],
    muteTimes: [
      {
        orgId: 1,
        name: "openworker-local-muted",
        time_intervals: [
          { times: [{ start_time: "00:00", end_time: "24:00" }] },
        ],
      },
    ],
    policies: [
      {
        orgId: 1,
        receiver: "OpenWorker local only",
        group_by: ["alertname", "deployment"],
        routes: [
          {
            receiver: "OpenWorker local only",
            mute_time_intervals: ["openworker-local-muted"],
          },
        ],
      },
    ],
  };
  const panels = [];
  const stat = (title, expr, x, y, description, unit = "short") =>
    panels.push({
      id: panels.length + 1,
      title,
      type: "stat",
      description,
      datasource: { type: "prometheus", uid: "prometheus" },
      gridPos: { x, y, w: 6, h: 4 },
      targets: [{ refId: "A", expr, instant: true }],
      fieldConfig: {
        defaults: {
          unit,
          noValue: "Unknown",
          color: { mode: "fixed", fixedColor: "blue" },
        },
        overrides: [],
      },
      options: {
        reduceOptions: { calcs: ["lastNotNull"], values: false },
        colorMode: "value",
        graphMode: "none",
        textMode: "auto",
      },
    });
  stat(
    "Active sessions",
    q.active,
    0,
    0,
    "Current active turns; paused/terminal sessions excluded. Max across fresh API replicas, not a sum.",
  );
  stat(
    "Queued turns",
    q.queued,
    6,
    0,
    "Durable queued submissions, including work waiting behind a parked turn.",
  );
  stat(
    "Awaiting approval",
    q.approvals,
    12,
    0,
    "Unanswered approval interactions for the current turn.",
  );
  stat(
    "Awaiting answer",
    q.questions,
    18,
    0,
    "Unanswered questions for the current turn.",
  );
  stat(
    "Fresh worker slots",
    q.capacity,
    0,
    4,
    `Expected ${o.expectedSlots}. Only heartbeats newer than ${o.freshSeconds}s count. A heartbeat is not proof of healthy execution.`,
  );
  stat(
    "Occupied slots",
    q.occupied,
    6,
    4,
    "Includes startup, active work and draining. Not a tool count.",
  );
  stat(
    "Oldest queued turn",
    q.queueAge,
    12,
    4,
    "Time since durable submission, including queued work behind an approval.",
    "s",
  );
  stat(
    "Potential stalls",
    q.stalled,
    18,
    4,
    "Active sessions without a recent durable event; legitimate quiet tools can trigger this. Waiting interactions excluded.",
  );
  stat(
    "Recent uncertain transitions",
    q.unknown,
    0,
    8,
    "Count of completion/recovery transitions with unknown effects within configured observer window; not unresolved-effect inventory.",
  );
  stat(
    "Database observer healthy",
    q.databaseUnhealthy,
    6,
    8,
    "1 = all fresh observers healthy; 0 = at least one failed. Missing = unknown, not a database-wide health assertion.",
  );
  stat(
    "State observation age",
    q.stateAge,
    12,
    8,
    "Age of latest successful PG snapshot. Requires synchronized clocks.",
    "s",
  );
  stat(
    "Telemetry missing (1 = yes)",
    q.monitoringMissing,
    18,
    8,
    "Detects absence even if the collector retains old samples.",
  );
  panels.push({
    id: 13,
    title: "Operational alerts",
    type: "alertlist",
    gridPos: { x: 0, y: 12, w: 24, h: 8 },
    options: {
      viewMode: "list",
      groupMode: "default",
      maxItems: 20,
      sortOrder: 1,
      dashboardAlerts: true,
      alertName: "",
      stateFilter: {
        firing: true,
        pending: true,
        noData: true,
        error: true,
        normal: false,
      },
      showOptions: "current",
    },
  });
  panels.push({
    id: 14,
    title: "Observed errors and telemetry loss",
    type: "timeseries",
    gridPos: { x: 0, y: 20, w: 24, h: 7 },
    description:
      "Processes with a recent exported model/tool error; telemetry-loss curves are rolling gauge deltas and can miss initial drops/resets. Absence is not proof of zero failures.",
    datasource: { type: "prometheus", uid: "prometheus" },
    targets: [
      ["Processes with model errors", q.modelErrors],
      ["Processes with tool errors", q.toolErrors],
      ["Dropped records", q.drops],
      ["Export failures", q.exportFailures],
    ].map(([name, expr], i) => ({
      refId: String.fromCharCode(65 + i),
      expr,
      legendFormat: name,
    })),
    fieldConfig: { defaults: { min: 0, noValue: "Unknown" }, overrides: [] },
  });
  const dashboard = {
    uid: dashboardId,
    title: `OpenWorker · ${o.deployment} · Operations`,
    schemaVersion: 39,
    version: 1,
    editable: false,
    tags: ["openworker", "operations"],
    timezone: "browser",
    refresh: "10s",
    time: { from: "now-30m", to: "now" },
    panels,
    links: [
      {
        title: "Harness traces and operation metrics (all deployments)",
        url: "/d/openworker-harness",
        type: "link",
      },
    ],
  };
  return { options: o, id, queries: q, dashboard, alerts };
}
export async function write(root, options) {
  const result = build(options);
  for (const [name, value] of [
    ["operations-dashboard.json", result.dashboard],
    ["operations-alerts.json", result.alerts],
  ])
    await writeFile(join(root, name), JSON.stringify(value, null, 2) + "\n", {
      mode: 0o600,
    });
  return result;
}
if (
  process.argv[1] &&
  resolve(process.argv[1]) === fileURLToPath(import.meta.url)
) {
  const [root, deployment, slots] = process.argv.slice(2);
  if (!root || !deployment)
    throw Error(
      "Usage: node operations.mjs STATE_DIR DEPLOYMENT [EXPECTED_SLOTS]",
    );
  await write(resolve(root), { deployment, expectedSlots: Number(slots ?? 2) });
}
