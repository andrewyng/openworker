// Writer for src/gallery/states.contract.json. A vitest file so it can import the TypeScript
// state files directly; it does nothing unless asked:  npm run gallery:contract
import { writeFileSync } from "node:fs";
import path from "node:path";
import { it } from "vitest";

import { buildContract } from "../src/gallery/contract";

it.skipIf(process.env.UPDATE_CONTRACT !== "1")("writes states.contract.json", () => {
  const file = path.join(__dirname, "..", "src", "gallery", "states.contract.json");
  writeFileSync(file, JSON.stringify(buildContract(), null, 2) + "\n");
});
