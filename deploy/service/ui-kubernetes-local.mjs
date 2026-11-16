// Adds the management UI + local TLS ingress to a dedicated kind qualification.
// No cloud changes, default kubecontext mutation, or credential output.
import { readFile, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { run } from "./stack.mjs";
const [action, directory, image] = process.argv.slice(2),
  root = resolve(directory || ".");
const m = JSON.parse(await readFile(join(root, "kubernetes.json"), "utf8"));
if (
  m.destroyed ||
  !m.context.startsWith("kind-") ||
  !/^ow-service-m4-[a-z0-9]+$/.test(m.namespace)
)
  throw Error("Live dedicated kind qualification required");
const kube = (args) =>
  run("kubectl", [
    "--kubeconfig",
    m.kubeconfig,
    "--context",
    m.context,
    "-n",
    m.namespace,
    ...args,
  ]);
if (action === "prepare") {
  const input = join(root, "input");
  await writeFile(
    join(input, "api.ext"),
    `subjectAltName=DNS:localhost,IP:127.0.0.1,DNS:ows-api.${m.namespace}.svc\nextendedKeyUsage=serverAuth\n`,
    { mode: 0o600 },
  );
  await run("openssl", [
    "x509",
    "-req",
    "-in",
    join(input, "api.csr"),
    "-CA",
    join(input, "api-ca.crt"),
    "-CAkey",
    join(input, "api-ca.key"),
    "-CAcreateserial",
    "-out",
    join(input, "api.crt"),
    "-days",
    "2",
    "-sha256",
    "-extfile",
    join(input, "api.ext"),
  ]);
  console.log("Local API certificate prepared for browser and service DNS.");
} else if (action === "install") {
  if (!/@sha256:[a-f0-9]{64}$/.test(image || ""))
    throw Error("UI image digest required");
  const ns = JSON.parse(
    await run("kubectl", [
      "--kubeconfig",
      m.kubeconfig,
      "--context",
      m.context,
      "get",
      "namespace",
      m.namespace,
      "-o",
      "json",
    ]),
  );
  if (ns.metadata.labels?.["openworker.io/qualification"] !== m.id)
    throw Error("Namespace ownership mismatch");
  const values = JSON.parse(await readFile(join(root, "values.json"), "utf8"));
  values.bootstrapEnabled = false;
  values.managementUI = {
    enabled: true,
    image,
    publicOrigin: "https://localhost:58644",
    serviceUrl: `https://ows-api.${m.namespace}.svc`,
    caSecret: "ow-ui-api-ca",
  };
  await writeFile(join(root, "values.json"), JSON.stringify(values, null, 2), {
    mode: 0o600,
  });
  const items = [
    {
      apiVersion: "v1",
      kind: "Secret",
      metadata: { name: "ow-ui-api-ca", namespace: m.namespace },
      type: "Opaque",
      data: {
        "ca.crt": (await readFile(join(root, "input/api-ca.crt"))).toString(
          "base64",
        ),
      },
    },
    {
      apiVersion: "v1",
      kind: "ConfigMap",
      metadata: { name: "ow-ui-ingress", namespace: m.namespace },
      data: {
        "nginx.conf": `pid /tmp/nginx.pid;
error_log /dev/stderr warn;
events { worker_connections 128; }
http { access_log off; client_body_temp_path /tmp/body; proxy_temp_path /tmp/proxy; fastcgi_temp_path /tmp/fastcgi; uwsgi_temp_path /tmp/uwsgi; scgi_temp_path /tmp/scgi;
server { listen 8443 ssl; ssl_certificate /tls/tls.crt; ssl_certificate_key /tls/tls.key; ssl_protocols TLSv1.2 TLSv1.3; client_max_body_size 160k;
location / { proxy_pass http://ows-ui; proxy_http_version 1.1; proxy_set_header Host $http_host; proxy_set_header Connection ""; proxy_buffering off; proxy_read_timeout 90s; }
} }`,
      },
    },
    {
      apiVersion: "apps/v1",
      kind: "Deployment",
      metadata: { name: "ow-ui-ingress", namespace: m.namespace },
      spec: {
        replicas: 1,
        selector: { matchLabels: { app: "ow-ui-ingress" } },
        template: {
          metadata: { labels: { app: "ow-ui-ingress" } },
          spec: {
            automountServiceAccountToken: false,
            securityContext: { fsGroup: 1000 },
            containers: [
              {
                name: "tls",
                image:
                  "nginx@sha256:42a516af16b852e33b7682d5ef8acbd5d13fe08fecadc7ed98605ba5e3b26ab8",
                command: [
                  "nginx",
                  "-c",
                  "/config/nginx.conf",
                  "-g",
                  "daemon off;",
                ],
                ports: [{ name: "https", containerPort: 8443 }],
                securityContext: {
                  runAsNonRoot: true,
                  runAsUser: 1000,
                  runAsGroup: 1000,
                  allowPrivilegeEscalation: false,
                  readOnlyRootFilesystem: true,
                  capabilities: { drop: ["ALL"] },
                  seccompProfile: { type: "RuntimeDefault" },
                },
                volumeMounts: [
                  { name: "config", mountPath: "/config", readOnly: true },
                  { name: "tls", mountPath: "/tls", readOnly: true },
                  { name: "tmp", mountPath: "/tmp" },
                ],
                readinessProbe: { tcpSocket: { port: "https" } },
                resources: {
                  requests: { cpu: "20m", memory: "32Mi" },
                  limits: { cpu: "200m", memory: "64Mi" },
                },
              },
            ],
            volumes: [
              { name: "config", configMap: { name: "ow-ui-ingress" } },
              {
                name: "tls",
                secret: { secretName: "ow-service-tls", defaultMode: 0o440 },
              },
              { name: "tmp", emptyDir: {} },
            ],
          },
        },
      },
    },
    {
      apiVersion: "v1",
      kind: "Service",
      metadata: { name: "ow-ui-ingress", namespace: m.namespace },
      spec: {
        selector: { app: "ow-ui-ingress" },
        ports: [{ name: "https", port: 443, targetPort: 8443 }],
      },
    },
  ];
  await run(
    "kubectl",
    ["--kubeconfig", m.kubeconfig, "--context", m.context, "apply", "-f", "-"],
    { input: JSON.stringify({ apiVersion: "v1", kind: "List", items }) },
  );
  await run(
    m.helm,
    [
      "upgrade",
      m.release,
      resolve("deploy/service/chart"),
      "--kubeconfig",
      m.kubeconfig,
      "--kube-context",
      m.context,
      "-n",
      m.namespace,
      "-f",
      join(root, "values.json"),
      "--wait",
      "--timeout",
      "180s",
    ],
    { inherit: true },
  );
  await kube(["rollout", "restart", "deployment/ow-ui-ingress"]);
  await kube([
    "rollout",
    "status",
    "deployment/ow-ui-ingress",
    "--timeout=120s",
  ]);
  console.log(
    "UI deployed. Forward service/ow-ui-ingress 58644:443 on 127.0.0.1.",
  );
} else
  throw Error(
    "Usage: ui-kubernetes-local.mjs prepare STATE | install STATE UI_IMAGE_DIGEST",
  );
