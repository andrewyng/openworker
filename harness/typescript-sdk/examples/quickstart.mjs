import { mkdir, writeFile } from "node:fs/promises";
import { resolve, join } from "node:path";
import { fileURLToPath } from "node:url";
import { createInterface } from "node:readline/promises";
import { Harness } from "../dist/index.js";

const executable = process.env.OPENWORKER_BIN;
const model = process.env.OPENWORKER_MODEL;
const scripted = process.env.OW_EXAMPLE_SCRIPTED === "1";
if (!executable || (!scripted && (!model || !process.env.OPENAI_API_KEY))) {
  throw Error(
    "Set OPENWORKER_BIN, OPENWORKER_MODEL and OPENAI_API_KEY. Never put a key in an agent definition.",
  );
}
const root = resolve(process.argv[2] ?? ".openworker-example");
const workspace = join(root, "workspace");
await mkdir(workspace, { recursive: true });
const servers = join(root, "servers.json");
await writeFile(
  servers,
  JSON.stringify({
    version: 1,
    servers: [
      {
        namespace: "example",
        transport: "stdio",
        program: process.execPath,
        args: [fileURLToPath(new URL("./echo-mcp.mjs", import.meta.url))],
      },
    ],
  }),
  { flag: "wx", mode: 0o600 },
).catch((e) => {
  if (e.code !== "EEXIST") throw e;
});

const harness = await Harness.start({
  executable,
  stateDir: join(root, "state"),
  sandbox: "off",
  scripted,
  agents: [
    {
      id: "assistant",
      instructions: "Use example.echo to repeat the user text.",
      tools: ["example.echo"],
      servers,
      ...(scripted ? {} : { model: { connection: "primary", model } }),
    },
  ],
});
const terminal = createInterface({
  input: process.stdin,
  output: process.stdout,
});
try {
  if (!scripted)
    await harness.setConnection({
      coworker: "assistant",
      id: "primary",
      label: "OpenAI",
      provider: "openai",
      apiKey: process.env.OPENAI_API_KEY,
    });
  const session = await harness.session("assistant", { workspace });
  await writeFile(
    join(root, "session.json"),
    JSON.stringify(session.reference()),
    { mode: 0o600 },
  );
  const input = scripted
    ? 'TEST_RESOURCE_GRANTS [{"name":"example__echo","arguments":{"text":"Hello OpenWorker"}}]'
    : "Please echo: Hello OpenWorker";
  const result = await session.runWith(input, async (interruption) => {
    if (interruption.kind === "question") {
      return { answer: await terminal.question(`${interruption.question} `) };
    }
    console.log(interruption.tool, interruption.arguments);
    const choice = await terminal.question("Allow once? [y/N] ");
    return { decision: choice.toLowerCase() === "y" ? "once" : "deny" };
  });
  console.log(result.status, result.finalOutput);
} finally {
  terminal.close();
  await harness.shutdown();
}
