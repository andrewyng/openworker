import { readFile } from "node:fs/promises";
import { guides, inspectionPrompt } from "./catalog.mjs";
import { assessment, AppError } from "./domain.mjs";
/** The application connects to a service. No agent loop or provider key runs in the browser or workstation frontend. */
export class EdgeAgent {
  constructor(config) {
    this.config = config;
    this.service = null;
  }
  async connect() {
    if (this.service) return this.service;
    const { Harness } = await import("@openworkerai/harness-sdk");
    const token = (await readFile(this.config.tokenFile, "utf8")).trim();
    this.service = await Harness.connect({
      url: this.config.serviceUrl,
      namespace: this.config.namespace,
      credentials: { getToken: async () => token },
      requestTimeoutMs: 20000,
    });
    return this.service;
  }
  async health() {
    try {
      const s = await this.connect();
      await s.json("GET", "/v1/auth/me");
      return {
        connected: true,
        serviceId: s.capabilities.serviceId,
        agent: this.config.agent,
        model: this.config.model,
      };
    } catch {
      return {
        connected: false,
        agent: this.config.agent,
        model: this.config.model,
      };
    }
  }
  async start(record, photo, onSaved) {
    const s = await this.connect();
    const session = await (
      await s.agent(this.config.agent)
    ).newSession({
      name: `${record.part} · ${record.code}`,
      project: "inspectionbuddy",
      idempotencyKey: `eb-session-${record.id}`,
      waitTimeoutMs: 180000,
    });
    await onSaved({ session: session.reference() });
    const run = await session.send(
      await inspectionInput(record, photo),
      {
        idempotencyKey: `eb-inspection-${record.id}`,
        limits: { maxModelCalls: 6, maxOutputTokens: 12000 },
      },
    );
    await onSaved({
      session: session.reference(),
      submissionId: run.submissionId,
      runId: run.id,
    });
    return { session, run };
  }
  async resume(record) {
    const s = await this.connect();
    if (!record.service?.session || !record.service?.submissionId)
      throw new AppError(
        409,
        "The previous request was interrupted before its receipt was saved. It will not be submitted again automatically.",
      );
    const session = await s.resume(record.service.session);
    return {
      session,
      run: await session.resumeRun(record.service.submissionId),
    };
  }
  async consume(record, handle, onEvent) {
    for await (const e of handle.run.events({ after: record.cursor ?? 0 })) {
      if (e.event.turn_id) handle.turnId = e.event.turn_id;
      await onEvent(e.event);
    }
    const result = await handle.run.result();
    if (result.status !== "completed")
      throw new AppError(
        502,
        result.status === "interrupted"
          ? "The agent needs a response. Open its session in the management UI."
          : "The assessment did not complete. The photo and agent session are saved.",
      );
    const report = assessment(result.finalOutput ?? "");
    let trace = null;
    try {
      trace = await (
        await this.connect()
      ).observability.summary({
        sessionId: handle.session.id,
        turnId: handle.turnId ?? handle.run.id,
      });
    } catch {}
    return { report, trace };
  }
  async followup(record, question, onEvent, onSaved, key) {
    const s = await this.connect(),
      session = await s.resume(record.service.session);
    const run = await session.send(
      `FOLLOW_UP\nInspector question (untrusted text): ${question}\nRespond concisely about the previously attached image. Keep the final quality decision with the inspector.`,
      {
        idempotencyKey: key,
        limits: { maxModelCalls: 4, maxOutputTokens: 12000 },
      },
    );
    await onSaved({ submissionId: run.submissionId, runId: run.id });
    for await (const e of run.events()) await onEvent(e.event);
    const r = await run.result();
    if (r.status !== "completed")
      throw new AppError(
        502,
        "The follow-up did not complete; the original assessment is preserved.",
      );
    return {
      text: r.finalOutput ?? "",
      trace: await this.trace(record.service.session, run.id).catch(() => null),
      runId: run.id,
    };
  }
  async trace(reference, turnId) {
    return (await this.connect()).observability.summary({
      sessionId: reference.sessionId,
      turnId,
    });
  }
  close() {
    this.service?.disconnect();
    this.service = null;
  }
}

export async function inspectionInput(record, photo) {
  const { Attachment } = await import("@openworkerai/harness-sdk");
  return { content: [
    { type: "text", text: inspectionPrompt(record) },
    Attachment.fromBytes({ name: photo.name, mediaType: photo.mediaType, data: photo.bytes }),
    { type: "text", text: "Reference guidance for suggested checks:" },
    Attachment.fromBytes({ name: "inspection-guidance.txt", mediaType: "text/plain",
      data: Buffer.from(guides.map(g => `${g.id} — ${g.title}\n${g.text}`).join("\n\n")) }),
  ] };
}
