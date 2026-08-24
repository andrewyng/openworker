import { describe, expect, it } from "vitest";
import {
  AUTO_APPROVE_NOTICE,
  modeNotice,
  modeNoticeStep,
  type ModeMark,
  type ModeNoticeState,
} from "./modeNotice";

// The transcript has to answer "which mode was this exchange under?" after the fact. The
// full explanation is a once-per-session thing; the markers are what make the log readable.
const S = "session-1";
const mark = (session: string, mode: string): ModeMark => ({ session, mode });

describe("modeNotice", () => {
  it("explains Auto-Approve in full the first time a session is in it", () => {
    const item = modeNotice("auto-approve", S, null, "");
    expect(item).not.toBeNull();
    expect(item).toMatchObject({ kind: "notice", tone: "info", title: "Auto-approve is on." });
    // The prose, not just the heading — the block layout keys off `title`.
    expect((item as { text: string }).text).toBe(AUTO_APPROVE_NOTICE);
  });

  it("marks a switch away from Auto-Approve with one line", () => {
    const item = modeNotice("discuss", S, mark(S, "auto-approve"), S);
    expect(item).toMatchObject({ kind: "notice", tone: "info", text: "Discuss is on." });
    expect(item).not.toHaveProperty("title"); // a marker, not the banner
  });

  it("marks a switch BACK to Auto-Approve without repeating the explanation", () => {
    // The reported behaviour: leaving and returning left no trace at all.
    const item = modeNotice("auto-approve", S, mark(S, "discuss"), S);
    expect(item).toMatchObject({ text: "Auto-approve is on." });
    expect(item).not.toHaveProperty("title");
  });

  it("uses the picker's own labels, so the transcript names what the user chose", () => {
    expect(modeNotice("interactive", S, mark(S, "discuss"), S)).toMatchObject({
      text: "Ask for approval is on.",
    });
    expect(modeNotice("auto", S, mark(S, "discuss"), S)).toMatchObject({
      text: "Bypass approvals is on.",
    });
  });

  it("says nothing when the mode has not changed", () => {
    expect(modeNotice("discuss", S, mark(S, "discuss"), "")).toBeNull();
    expect(modeNotice("auto-approve", S, mark(S, "auto-approve"), S)).toBeNull();
  });

  it("says nothing on the first render of a session that is not in Auto-Approve", () => {
    // No previous mark means nothing to compare against — announcing the starting mode
    // would put a marker at the top of every transcript.
    expect(modeNotice("interactive", S, null, "")).toBeNull();
  });

  it("treats switching sessions as a session change, not a mode change", () => {
    // Opening another session that happens to be in a different mode must not read as a
    // switch inside this one.
    expect(modeNotice("discuss", "session-2", mark(S, "auto-approve"), S)).toBeNull();
  });

  it("explains again in a new session, since the first banner scrolled away with the old one", () => {
    const item = modeNotice("auto-approve", "session-2", mark(S, "auto-approve"), S);
    expect(item).toMatchObject({ title: "Auto-approve is on." });
  });

  it("stays silent while the mode is still the previous session's (unconfirmed)", () => {
    // On a session switch the app's mode state is stale until the server's `ready` event.
    // Acting on it posted the OLD session's banner into a fresh transcript.
    const item = modeNotice("auto-approve", "session-2", mark(S, "auto-approve"), S);
    // The pure function alone can't tell stale from restored — that's modeNoticeStep's job;
    // this pins that the raw call WOULD banner here, so the step's gate is load-bearing.
    expect(item).toMatchObject({ title: "Auto-approve is on." });
  });

  it("never claims to detect prompt injection", () => {
    // The copy is load-bearing: the reviewer cannot spot a normalized injection (OPE-114),
    // so the banner must not imply it does. Guarded here because it is the exact overclaim
    // the wording was written to avoid.
    expect(AUTO_APPROVE_NOTICE.toLowerCase()).not.toContain("injection");
    expect(AUTO_APPROVE_NOTICE).toContain("aren't sandboxed");
    expect(AUTO_APPROVE_NOTICE).toContain("a false allow executes unchecked");
  });
});

// The step function is what the app actually drives: it owns the ordering rules the pure
// function can't see — chiefly that a (mode, session) pair means nothing until the server's
// `ready` event has confirmed the mode belongs to that session.
describe("modeNoticeStep", () => {
  const fresh: ModeNoticeState = { mark: null, bannerShownFor: "" };

  // The reported bug, replayed event for event: an Auto-approve session, then "New
  // session" (defaulting to Ask for approval). The old code showed the banner from the
  // stale mode, then a stray "Ask for approval is on." marker when `ready` landed.
  it("a new non-auto session shows neither the banner nor a marker", () => {
    // Session 1 is confirmed in auto-approve; the banner shows there.
    let r = modeNoticeStep(fresh, "auto-approve", "s1", "s1");
    expect(r.item).toMatchObject({ title: "Auto-approve is on." });
    // New session: sessionId flips first, mode still stale, ready not yet in. Silence.
    r = modeNoticeStep(r.state, "auto-approve", "s2", "s1");
    expect(r.item).toBeNull();
    // `ready` confirms s2 is in interactive. Still silence — nothing changed, s2-wise.
    r = modeNoticeStep(r.state, "interactive", "s2", "s2");
    expect(r.item).toBeNull();
  });

  it("an unconfirmed pair records nothing — no fake 'switch' when the truth lands", () => {
    // If the stale (auto-approve, s2) pair were recorded as s2's mode, ready's
    // "interactive" would read as a mode change and print a marker at the transcript top.
    let r = modeNoticeStep(fresh, "auto-approve", "s2", "s1");
    expect(r.state).toEqual(fresh);
    // And the banner must not be marked as "already shown" for s2 either — a session
    // genuinely restored into auto-approve still deserves its banner (next test).
    expect(r.state.bannerShownFor).toBe("");
  });

  it("a session RESTORED into auto-approve still gets the banner once confirmed", () => {
    // Switch from an auto-approve session to another auto-approve session: the mode
    // VALUE never changes, only the confirmation. The banner must still re-fire.
    let r = modeNoticeStep(fresh, "auto-approve", "s1", "s1");
    r = modeNoticeStep(r.state, "auto-approve", "s3", "s1"); // stale window
    expect(r.item).toBeNull();
    r = modeNoticeStep(r.state, "auto-approve", "s3", "s3"); // ready confirms
    expect(r.item).toMatchObject({ title: "Auto-approve is on." });
  });

  it("an in-session switch still gets its one-line marker", () => {
    let r = modeNoticeStep(fresh, "auto-approve", "s1", "s1");
    r = modeNoticeStep(r.state, "interactive", "s1", "s1");
    expect(r.item).toMatchObject({ text: "Ask for approval is on." });
    expect(r.item).not.toHaveProperty("title");
  });
});
