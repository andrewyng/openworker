// Local QA only. Deterministic response source, never a live-model substitute.
import { writeFile, mkdir } from "node:fs/promises";
import { resolve } from "node:path";
import { modelFixture, makePdf } from "../test/fixtures.mjs";
import { createApplication } from "../web/server.mjs";
const fixture = await modelFixture();
Object.assign(process.env, {
  OPENWORKER_MODEL: "fixture",
  OPENWORKER_MODEL_URL: fixture.url,
  OPENAI_API_KEY: "fixture-only",
});
const application = await createApplication({
  stateDir: resolve(".state/preview"),
});
await mkdir(".state", { recursive: true });
await writeFile(".state/sample.pdf", makePdf("Orbit revenue is 42 credits."));
application.server.listen(4352, "127.0.0.1", () =>
  console.log("Fixture preview: http://127.0.0.1:4352/"),
);
// Electron receives this exact model endpoint via a local env file; no real secrets.
await writeFile(
  ".state/fixture.env",
  `OPENWORKER_MODEL=fixture\nOPENWORKER_MODEL_URL=${fixture.url}\nOPENAI_API_KEY=fixture-only\n`,
);
for (const sig of ["SIGINT", "SIGTERM"])
  process.once(sig, async () => {
    await application.close();
    await fixture.close();
    process.exit(0);
  });
