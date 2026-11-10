import assert from "node:assert/strict";
import { readFile, writeFile } from "node:fs/promises";
import { join } from "node:path";
import { repo } from "../helm/local.mjs";
const { chromium } = await import(
  process.env.OW_PLAYWRIGHT_MODULE || "playwright"
);
const root = join(repo, ".ow-aws"),
  origin = "http://127.0.0.1:54350";
const browser = await chromium.launch({ headless: true }),
  page = await browser.newPage();
try {
  await page.goto(origin);
  await page.locator("#key").waitFor();
  await page
    .locator("#key")
    .fill((await readFile(join(root, "access"), "utf8")).trim());
  await page.locator("#login-form button").click();
  await page.locator("#welcome").waitFor();
  const cmd =
    'if result=$(cat /opt/probe-denied/sentinel 2>&1); then echo UNEXPECTED_ACCESS; exit 1; else case "$result" in *"Permission denied"*) echo DENIED_SENTINEL;; *) echo UNEXPECTED_ERROR; exit 2;; esac; fi; test ! -S /var/run/docker.sock && test ! -e /var/run/secrets/kubernetes.io/serviceaccount/token && test -z "${OPENAI_API_KEY:-}" && echo BOUNDARIES_OK; grep -E "NoNewPrivs|Seccomp:" /proc/self/status';
  await page
    .locator("#prompt")
    .fill(
      "TEST_RESOURCE_GRANTS " +
        JSON.stringify([{ name: "run_shell", arguments: { command: cmd } }]),
    );
  await page.locator("#create-button").click();
  await page
    .getByRole("button", { name: "Allow once", exact: true })
    .waitFor({ timeout: 60000 });
  await page.getByRole("button", { name: "Allow once", exact: true }).click();
  await page.waitForFunction(
    () => document.querySelector("#status").textContent === "completed",
    {},
    { timeout: 60000 },
  );
  const session = new URL(page.url()).hash.slice(1);
  const result = await page.evaluate(async (sid) => {
    const s = await (await fetch("/api/sessions/" + sid)).json(),
      res = await fetch("/api/sessions/" + sid + "/events?after=0", {
        signal: AbortSignal.timeout(20000),
      }),
      reader = res.body.getReader(),
      decoder = new TextDecoder();
    let buffer = "",
      result;
    try {
      while (true) {
        const n = await reader.read();
        if (n.done) throw Error("Incomplete replay");
        buffer += decoder.decode(n.value, { stream: true });
        let at;
        while ((at = buffer.indexOf("\n\n")) >= 0) {
          const f = buffer.slice(0, at);
          buffer = buffer.slice(at + 2);
          const line = f.split("\n").find((l) => l.startsWith("data: "));
          if (!line) continue;
          const e = JSON.parse(line.slice(6));
          if (e.type === "tool_finished" && e.data.name === "run_shell")
            result = e.data.result;
          if (e.event_id >= s.cursor) return result;
        }
      }
    } finally {
      await reader.cancel();
    }
  }, session);
  assert.notEqual(result.isError, true);
  const shell = JSON.parse(result.content[0].text);
  assert.equal(shell.exit_code, 0);
  assert.match(shell.stdout, /DENIED_SENTINEL/);
  assert.match(shell.stdout, /BOUNDARIES_OK/);
  assert.match(shell.stdout, /NoNewPrivs:\s+1/);
  assert.match(shell.stdout, /Seccomp:\s+2/);
  await writeFile(
    join(root, "boundary-proof.json"),
    JSON.stringify(
      { time: new Date().toISOString(), session, result: shell },
      null,
      2,
    ),
    { mode: 0o600 },
  );
  console.log(
    "PASS actual harness-approved shell: public sentinel denied by kernel, NoNewPrivs=1, Seccomp=2, no Docker socket/Kubernetes token/provider key available.",
  );
} finally {
  await browser.close();
}
