import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { SkillsTab } from "./SkillsTab";

// SKILLS-SPEC §4.6 GUI — Settings ▸ Skills: list + badges, form validation, the three add
// modes (write / upload-with-preview / draft-never-auto-saves), scope picker visibility.

type Call = { url: string; method: string; body: any };

function stubFetch(routes: { match: string; method?: string; json: any }[]) {
  const calls: Call[] = [];
  const fn = vi.fn(async (url: string, init?: RequestInit) => {
    const method = (init?.method || "GET").toUpperCase();
    calls.push({ url, method, body: init?.body ? JSON.parse(String(init.body)) : undefined });
    for (const r of routes) {
      if (url.includes(r.match) && (!r.method || r.method === method)) {
        return { ok: true, json: async () => r.json } as Response;
      }
    }
    return { ok: true, json: async () => ({}) } as Response;
  });
  vi.stubGlobal("fetch", fn);
  return calls;
}

const ROW = {
  name: "weekly-report",
  description: "Monday status report",
  instructions: "1. Collect updates\n2. Write it up",
  scope: "global",
  source: "local",
  enabled: true,
  path: "/skills/weekly-report",
};

const UPLOADED_ROW = {
  ...ROW,
  name: "greet",
  description: "says hello",
  source: "uploaded",
  enabled: false,
};

const LIST = { skills: [ROW, UPLOADED_ROW] };

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("SkillsTab", () => {
  it("renders rows with provenance badges and dims disabled skills", async () => {
    stubFetch([{ match: "/v1/skills", method: "GET", json: LIST }]);
    render(<SkillsTab />);
    expect(await screen.findByText("weekly-report")).toBeTruthy();
    expect(screen.getByText("Monday status report")).toBeTruthy();
    expect(screen.queryByText("global")).toBeNull(); // no scope badges — global-only (§4.7)
    expect(screen.getByText("uploaded")).toBeTruthy(); // provenance badge stays
    const toggles = screen.getAllByRole("switch");
    expect((toggles[0] as HTMLInputElement).checked).toBe(true);
    expect((toggles[1] as HTMLInputElement).checked).toBe(false);
  });

  it("blocks Save until name and instructions are filled", async () => {
    stubFetch([{ match: "/v1/skills", method: "GET", json: { skills: [] } }]);
    render(<SkillsTab />);
    fireEvent.click(await screen.findByText("New skill"));
    const save = screen.getByText("Save skill") as HTMLButtonElement;
    expect(save.disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "greet" } });
    expect(save.disabled).toBe(true); // instructions still empty
    fireEvent.change(screen.getByLabelText("Instructions"), {
      target: { value: "Say hello." },
    });
    expect(save.disabled).toBe(false);
  });

  it("creates a skill (global, no scope field) and refreshes the list", async () => {
    const calls = stubFetch([
      { match: "/v1/skills", method: "GET", json: { skills: [] } },
      { match: "/v1/skills", method: "POST", json: { ok: true } },
    ]);
    render(<SkillsTab />);
    fireEvent.click(await screen.findByText("New skill"));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "greet" } });
    fireEvent.change(screen.getByLabelText("Instructions"), {
      target: { value: "Say hello." },
    });
    fireEvent.click(screen.getByText("Save skill"));
    await waitFor(() => {
      const post = calls.find((c) => c.method === "POST" && c.url.endsWith("/v1/skills"));
      expect(post?.body).toMatchObject({ name: "greet", instructions: "Say hello." });
      expect(post?.body.workspace).toBeUndefined(); // global-only: no scope/workspace sent
    });
    // list re-fetched after save
    expect(calls.filter((c) => c.method === "GET" && c.url.includes("/v1/skills")).length).toBeGreaterThan(1);
  });

  it("edit prefills the form (name locked, body loaded) and PATCHes on save", async () => {
    const calls = stubFetch([
      { match: "/v1/skills", method: "GET", json: LIST },
      { match: "/v1/skills/weekly-report", method: "PATCH", json: { ok: true } },
    ]);
    render(<SkillsTab />);
    await screen.findByText("weekly-report");
    fireEvent.click(screen.getAllByTitle("Edit")[0]);
    const name = screen.getByLabelText("Name") as HTMLInputElement;
    expect(name.value).toBe("weekly-report");
    expect(name.disabled).toBe(true);
    const body = screen.getByLabelText("Instructions") as HTMLTextAreaElement;
    expect(body.value).toContain("Collect updates");
    fireEvent.change(body, { target: { value: "New steps" } });
    fireEvent.click(screen.getByText("Save skill"));
    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH");
      expect(patch?.url).toContain("/v1/skills/weekly-report");
      expect(patch?.body.instructions).toBe("New steps");
    });
  });

  it("delete is two-step: arm, then DELETE on confirm", async () => {
    const calls = stubFetch([
      { match: "/v1/skills", method: "GET", json: LIST },
      { match: "/v1/skills/weekly-report", method: "DELETE", json: { ok: true } },
    ]);
    render(<SkillsTab />);
    await screen.findByText("weekly-report");
    // arm via the trash button (renders "Confirm delete" once armed)
    fireEvent.click(screen.getByLabelText("Delete weekly-report"));
    expect(calls.some((c) => c.method === "DELETE")).toBe(false);
    const confirm = await screen.findByText("Confirm delete");
    fireEvent.click(confirm);
    await waitFor(() => {
      expect(calls.some((c) => c.method === "DELETE" && c.url.includes("weekly-report"))).toBe(true);
    });
  });

  it("the enabled switch PATCHes {enabled}", async () => {
    const calls = stubFetch([
      { match: "/v1/skills", method: "GET", json: LIST },
      { match: "/v1/skills/weekly-report", method: "PATCH", json: { ok: true } },
    ]);
    render(<SkillsTab />);
    await screen.findByText("weekly-report");
    fireEvent.click(screen.getByLabelText("weekly-report enabled"));
    await waitFor(() => {
      const patch = calls.find((c) => c.method === "PATCH");
      expect(patch?.body).toMatchObject({ enabled: false });
    });
  });

  it("upload shows the parsed preview and installs nothing until confirmed", async () => {
    const calls = stubFetch([
      { match: "/v1/skills/upload/confirm", method: "POST", json: { ok: true } },
      {
        match: "/v1/skills/upload",
        method: "POST",
        json: {
          ok: true,
          token: "t1",
          name: "greet",
          description: "says hello",
          instructions: "Say hello warmly.",
          files: ["notes.txt"],
        },
      },
      { match: "/v1/skills", method: "GET", json: { skills: [] } },
    ]);
    render(<SkillsTab />);
    const input = (await screen.findByLabelText("Upload a skill archive")) as HTMLInputElement;
    const file = new File([new Uint8Array([80, 75, 3, 4])], "greet.zip", { type: "application/zip" });
    fireEvent.change(input, { target: { files: [file] } });
    await screen.findByText("Review before installing");
    expect(screen.getByText("Say hello warmly.")).toBeTruthy();
    expect(screen.getByText(/notes\.txt/)).toBeTruthy();
    expect(calls.some((c) => c.url.includes("/upload/confirm"))).toBe(false); // preview ≠ install
    fireEvent.click(screen.getByText("Install skill"));
    await waitFor(() => {
      const confirm = calls.find((c) => c.url.includes("/upload/confirm"));
      expect(confirm?.body).toMatchObject({ token: "t1" });
    });
  });

  it("draft fills the editor and never saves by itself", async () => {
    const calls = stubFetch([
      {
        match: "/v1/skills/draft",
        method: "POST",
        json: { ok: true, name: "weekly-report", description: "Monday report", instructions: "1. Collect" },
      },
      { match: "/v1/skills", method: "GET", json: { skills: [] } },
    ]);
    render(<SkillsTab />);
    fireEvent.change(await screen.findByLabelText("Describe the skill"), {
      target: { value: "monday reports" },
    });
    fireEvent.click(screen.getByText("Draft with OpenWorker"));
    await waitFor(() => {
      expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("weekly-report");
    });
    expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe("1. Collect");
    // review-before-save: the draft call happened, but no create POST did
    expect(calls.some((c) => c.url.includes("/draft"))).toBe(true);
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/v1/skills"))).toBe(false);
  });

  it("offers no scope UI at all — skills are global (§4.7)", async () => {
    stubFetch([{ match: "/v1/skills", method: "GET", json: { skills: [] } }]);
    render(<SkillsTab />);
    fireEvent.click(await screen.findByText("New skill"));
    expect(screen.queryByText("Available in")).toBeNull();
    expect(screen.queryByLabelText("Everywhere")).toBeNull();
    expect(screen.queryByLabelText("Only one project")).toBeNull();
    expect(screen.queryByText(/Move to/)).toBeNull();
  });

  it("shows the new-session confirmation line after creating a skill", async () => {
    stubFetch([
      { match: "/v1/skills", method: "GET", json: { skills: [] } },
      { match: "/v1/skills", method: "POST", json: { ok: true } },
    ]);
    render(<SkillsTab />);
    fireEvent.click(await screen.findByText("New skill"));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "greet" } });
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "x" } });
    fireEvent.click(screen.getByText("Save skill"));
    const status = await screen.findByRole("status");
    expect(status.textContent).toContain("next message");
  });

  it("revise-in-place: with a draft in the form, the box revises it and never saves", async () => {
    const calls = stubFetch([
      {
        match: "/v1/skills/draft",
        method: "POST",
        json: { ok: true, name: "greet", description: "v2 desc", instructions: "v2 steps" },
      },
      { match: "/v1/skills", method: "GET", json: { skills: [] } },
    ]);
    render(<SkillsTab />);
    fireEvent.click(await screen.findByText("New skill"));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "greet" } });
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "v1 steps" } });
    // With the editor open the box flips to revise mode
    expect(screen.getByText("Ask OpenWorker to revise")).toBeTruthy();
    expect(screen.getByText(/Not a chat/)).toBeTruthy(); // the mental-model caption
    fireEvent.change(screen.getByLabelText("Revise the draft"), {
      target: { value: "shorter please" },
    });
    fireEvent.click(screen.getByText("Revise"));
    await waitFor(() => {
      const draft = calls.find((c) => c.url.includes("/draft"));
      // current form contents (hand-edits included) + the note ride together
      expect(draft?.body.current).toMatchObject({ name: "greet", instructions: "v1 steps" });
      expect(draft?.body.feedback).toBe("shorter please");
    });
    expect((screen.getByLabelText("Instructions") as HTMLTextAreaElement).value).toBe("v2 steps");
    expect(calls.some((c) => c.method === "POST" && c.url.endsWith("/v1/skills"))).toBe(false);
  });

  it("surfaces server-side validation errors", async () => {
    stubFetch([
      { match: "/v1/skills", method: "GET", json: { skills: [] } },
      { match: "/v1/skills", method: "POST", json: { ok: false, error: "A skill named 'x' already exists in that scope." } },
    ]);
    render(<SkillsTab />);
    fireEvent.click(await screen.findByText("New skill"));
    fireEvent.change(screen.getByLabelText("Name"), { target: { value: "x" } });
    fireEvent.change(screen.getByLabelText("Instructions"), { target: { value: "y" } });
    fireEvent.click(screen.getByText("Save skill"));
    expect(await screen.findByRole("alert")).toBeTruthy();
    expect(screen.getByText(/already exists/)).toBeTruthy();
  });
});
