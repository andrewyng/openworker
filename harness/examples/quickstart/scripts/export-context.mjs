import { writeFile, readFile } from "node:fs/promises";
import { directorySnapshot } from "../dist/directory.js";
import { extractPdf } from "../web/pdf.mjs";
const [kind, input, output] = process.argv.slice(2);
if (!["code", "pdf"].includes(kind) || !input || !output)
  throw Error(
    "Usage: node scripts/export-context.mjs code|pdf INPUT OUTPUT.json",
  );
const data =
  kind === "code"
    ? await directorySnapshot(input)
    : await extractPdf(await readFile(input));
await writeFile(output, data.context, { mode: 0o600 });
