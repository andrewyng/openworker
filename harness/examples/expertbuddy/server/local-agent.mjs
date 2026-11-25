import { mkdir } from "node:fs/promises";
import { join } from "node:path";
import { EdgeAgent, inspectionInput } from "./agent.mjs";
import { instruction } from "./catalog.mjs";
import { AppError } from "./domain.mjs";

// Local development uses the published managed-process SDK, not a mock service.
// No tools are registered: sandbox:off does not grant this agent filesystem access.
export class LocalAgent extends EdgeAgent {
  async connect() {
    if (!this.initializing) this.initializing = this.initialize().catch(error => {
      this.initializing = null;
      throw error;
    });
    return this.initializing;
  }
  async initialize() {
    if (!process.env.OPENAI_API_KEY) throw new Error("Set OPENAI_API_KEY in .env before starting local mode.");
    const { Harness } = await import("@openworkerai/harness-sdk");
    const stateDir = join(this.config.dataDir, "harness");
    await mkdir(stateDir, { recursive: true });
    const harness = await Harness.start({
      appId: "inspectionbuddy", stateDir, sandbox: "off",
      inheritEnv: ["OPENAI_API_KEY"],
      recording: { database: { type: "sqlite", path: join(stateDir, "traces.sqlite") } },
      ...(process.env.OPENWORKER_EXECUTABLE ? { executable: process.env.OPENWORKER_EXECUTABLE } : {}),
    });
    try {
      await harness.setModelProvider({ name: "OpenAI", api: "responses",
        baseUrl: "https://api.openai.com/v1",
        credentials: { apiKeyEnv: "OPENAI_API_KEY", storage: "memory" } });
      this.agent = harness.createAgent({
        name: this.config.agent, instructions: instruction,
        defaultModel: { provider: "OpenAI", name: this.config.model },
        tools: [], planning: false, memory: false, scheduling: false,
      });
      await this.agent.ready();
      this.service = harness;
      return harness;
    } catch (error) { await harness.shutdown(); throw error; }
  }
  async health() {
    try {
      await this.connect();
      return { connected: true, agent: this.config.agent, model: this.config.model };
    } catch {
      return { connected: false, agent: this.config.agent, model: this.config.model };
    }
  }
  async start(record, photo, onSaved) {
    await this.connect();
    const session = await this.agent.newSession();
    await onSaved({ session: session.reference() });
    const run = await session.send(await inspectionInput(record, photo), {
      submissionId: `inspection-${record.id}`,
      limits: { maxModelCalls: 6, maxOutputTokens: 12000 },
    });
    await onSaved({ session: session.reference(), submissionId: run.id, runId: run.id });
    return { session, run: this.wrap(session, run.id, 0) };
  }
  wrap(session, submissionId, cursor) {
    return {
      id: submissionId,
      async *events() {
        yield* await session.resumeStreamed({ submissionId, cursors: { [session.id]: cursor } });
      },
      result: () => session.resume({ submissionId }),
    };
  }
  async resume(record) {
    if (!record.service?.session || !record.service.submissionId)
      throw new AppError(409, "The previous request has no saved receipt. It will not be resubmitted automatically.");
    const harness = await this.connect();
    const session = await harness.resume(record.service.session);
    return { session, run: this.wrap(session, record.service.submissionId, record.cursor ?? 0) };
  }
  async consume(record, handle, onEvent) {
    // EdgeAgent shares report validation and event handling across both modes.
    for await (const item of handle.run.events()) {
      if (item.event.turn_id) handle.turnId = item.event.turn_id;
      await onEvent(item.event);
    }
    const result = await handle.run.result();
    if (result.status !== "completed") throw new AppError(502, "The local assessment did not complete. Its session is saved.");
    const { assessment } = await import("./domain.mjs");
    return { report: assessment(result.finalOutput ?? ""),
      trace: await this.trace(handle.session.reference(), handle.turnId).catch(() => null) };
  }
  async trace(reference, turnId) {
    const harness = await this.connect();
    await harness.observability.flush();
    return harness.observability.summary({ sessionId: reference.session_id, turnId });
  }
  async followup(record, question, onEvent, onSaved, key) {
    const harness = await this.connect(), session = await harness.resume(record.service.session);
    const run = await session.send(
      `FOLLOW_UP\nInspector question (untrusted text): ${question}\nRespond concisely about the previously attached image. Keep the final quality decision with the inspector.`,
      { submissionId: key, limits: { maxModelCalls: 4, maxOutputTokens: 12000 } });
    await onSaved({ submissionId: run.id, runId: run.id });
    let turnId;
    for await (const item of run.events()) {
      turnId = item.event.turn_id ?? turnId;
      await onEvent(item.event);
    }
    const result = await run.result();
    if (result.status !== "completed") throw new AppError(502, "The follow-up did not complete; the original assessment is preserved.");
    return { text: result.finalOutput ?? "", trace: await this.trace(session.reference(), turnId).catch(() => null), runId: run.id };
  }
  async close() {
    if (this.initializing) await this.initializing.catch(() => {});
    await this.service?.shutdown();
    this.service = null;
    this.initializing = null;
  }
}
