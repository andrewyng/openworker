// Fails when states.contract.json is stale: the server-side check reads that file, so it
// must always say what the state files say.
import { describe, expect, it } from "vitest";

import { buildContract } from "./contract";
import committed from "./states.contract.json";

describe("gallery contract file", () => {
  it("matches the state files (else: npm run gallery:contract)", () => {
    expect(committed).toEqual(buildContract());
  });
});
