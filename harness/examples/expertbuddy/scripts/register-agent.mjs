import { Harness } from "@openworkerai/harness-sdk";
import { readFile } from "node:fs/promises";
import { instruction } from "../server/catalog.mjs";
const principal = process.env.OW_APP_PRINCIPAL ?? "inspectionbuddy-app";
const agent = process.env.OW_AGENT ?? "inspectionbuddy@1";
const profile = process.env.OW_RUNTIME_PROFILE ?? "inspection@1";
const key = `${agent}-${profile}-${principal}`;
const token = (await readFile(process.env.OW_ADMIN_TOKEN_FILE, "utf8")).trim();
const service = await Harness.connect({
  url: process.env.OW_SERVICE_URL,
  namespace: process.env.OW_SERVICE_NAMESPACE ?? "local",
  credentials: { getToken: async () => token },
});
try {
  const model = process.env.OW_MODEL ?? "gpt-6-luna";
  await service.registerRuntimeProfile(
    {
      id: profile,
      provider: "openshell",
      cpu: 1,
      memoryMB: 512,
      sharing: "exclusive",
      approvalMode: "bypass",
      autoSkipQuestions: true,
      allowedPrincipals: [principal],
    },
    { idempotencyKey: `${key}-profile` },
  );
  await service.activateRuntimeProfile(profile, {
    idempotencyKey: `${key}-profile-active`,
  });
  await service.deployAgent(
    {
      id: agent,
      instructions: instruction,
      tools: ["read_file"],
      allowedPrincipals: [principal],
      runtimeProfiles: [profile],
      defaultRuntimeProfile: profile,
      defaultModel: { connection: "primary", model },
      limits: { maxModelCalls: 6, maxOutputTokens: 12000 },
    },
    { idempotencyKey: `${key}-agent` },
  );
  await service.activateAgent(agent, {
    alias: "inspectionbuddy",
    idempotencyKey: `${key}-agent-active`,
  });
  console.log(
    "InspectionBuddy inspection agent activated. The application should connect using a separate operator credential.",
  );
} finally {
  service.disconnect();
}
