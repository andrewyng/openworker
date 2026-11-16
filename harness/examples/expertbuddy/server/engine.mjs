import { readFile } from "node:fs/promises";
import { join } from "node:path";
import { AppError } from "./domain.mjs";
export class InspectionEngine {
  constructor(store, agent) {
    this.store = store;
    this.agent = agent;
    this.listeners = new Map();
    this.active = new Set();
  }
  subscribe(id, fn) {
    const set = this.listeners.get(id) ?? new Set();
    set.add(fn);
    this.listeners.set(id, set);
    return () => {
      set.delete(fn);
      if (!set.size) this.listeners.delete(id);
    };
  }
  publish(record) {
    for (const fn of this.listeners.get(record.id) ?? []) fn(record);
  }
  async update(id, fn) {
    const r = await this.store.update(id, fn);
    this.publish(r);
    return r;
  }
  async event(id, e) {
    const labels = {
      turn_accepted: "Inspection queued",
      execution_segment_started: "Preparing inspection session",
      turn_started: "Reviewing photo and reference guidance",
      model_request: "Examining visible details",
      model_attempt_started: "Vision model is assessing the photo",
      model_attempt_finished: "Assessment received",
      tool_started: "Checking supporting context",
      tool_finished: "Supporting check completed",
      turn_completed: "Finalizing the assessment",
    };
    if (!labels[e.type]) return;
    await this.update(id, (r) => {
      r.cursor = e.event_id;
      r.activity.push({
        id: e.event_id,
        label: labels[e.type],
        type: e.type,
        at: new Date().toISOString(),
      });
      r.activity = r.activity.slice(-80);
    });
  }
  async assess(id, { recover = false } = {}) {
    if (this.active.has(id)) return;
    this.active.add(id);
    try {
      const r = this.store.get(id);
      const handle = recover
        ? await this.agent.resume(r)
        : await this.agent.start(
            r,
            {
              bytes: await readFile(
                join(this.store.dir, "photos", r.photoFile),
              ),
              mediaType: r.mediaType,
              name: r.photoName,
            },
            async (service) => {
              await this.update(id, (r) => {
                r.service = { ...r.service, ...service };
              });
            },
          );
      const result = await this.agent.consume(this.store.get(id), handle, (e) =>
        this.event(id, e),
      );
      await this.update(id, (r) => {
        r.report = result.report;
        r.trace = result.trace;
        r.status = "review";
        r.activity.push({
          label: "Assessment ready for inspector review",
          at: new Date().toISOString(),
          type: "assessment_ready",
        });
      });
    } catch (e) {
      await this.update(id, (r) => {
        r.status = "error";
        r.error =
          e instanceof AppError
            ? e.message
            : "The edge agent could not complete this assessment. Your photo and inspection record are saved.";
      });
      console.error("Inspection failed", { id, code: e.code ?? e.name });
    } finally {
      this.active.delete(id);
    }
  }
  async followup(id, question, key) {
    const r = this.store.get(id);
    if (!r.report || this.active.has(id))
      throw new AppError(409, "Wait for the current assessment to finish.");
    this.active.add(id);
    await this.update(id, (x) => {
      x.followupBusy = true;
      x.followupError = null;
      x.conversation.push({
        role: "user",
        text: question,
        at: new Date().toISOString(),
      });
    });
    try {
      const answer = await this.agent.followup(
        r,
        question,
        (e) => this.event(id, e),
        async (receipt) => {
          await this.update(id, (x) => {
            x.followupReceipt = receipt;
          });
        },
        key,
      );
      return await this.update(id, (x) => {
        x.conversation.push({
          role: "assistant",
          text: answer.text,
          at: new Date().toISOString(),
        });
        x.followupBusy = false;
        if (answer.trace)
          x.followupTraces = [
            ...(x.followupTraces ?? []),
            { runId: answer.runId, trace: answer.trace },
          ];
      });
    } catch (e) {
      await this.update(id, (x) => {
        x.followupBusy = false;
        x.followupError =
          "The follow-up could not finish. Your original assessment is unchanged.";
      });
      throw e;
    } finally {
      this.active.delete(id);
    }
  }
  async recover() {
    for (const r of this.store.all()) {
      if (
        !r.followupBusy &&
        r.followupReceipt?.runId &&
        this.agent.trace &&
        !(r.followupTraces ?? []).some(
          (t) => t.runId === r.followupReceipt.runId,
        )
      ) {
        try {
          const trace = await this.agent.trace(
            r.service.session,
            r.followupReceipt.runId,
          );
          await this.update(r.id, (x) => {
            x.followupTraces = [
              ...(x.followupTraces ?? []),
              { runId: r.followupReceipt.runId, trace },
            ];
          });
        } catch {}
      }
      if (r.status === "analyzing") void this.assess(r.id, { recover: true });
      if (r.followupBusy)
        await this.update(r.id, (x) => {
          x.followupBusy = false;
          x.followupError =
            "The application restarted during this follow-up. It has not been resubmitted automatically.";
        });
    }
  }
}
