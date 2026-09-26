// The staffing card's own dress: two-line worker rows, the note clamp, one-line buttons
// with the grant sentence in the primary button's title (worker-connector-grants spec §6).
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { Item } from "../types";
import { TeamRequestCard } from "./TeamRequestCard";

type TeamReq = Extract<Item, { kind: "teamreq" }>;

const base: TeamReq = {
  kind: "teamreq",
  members: [{ persona: "swe-worker", name: "nia", model: "claude-opus-4-8", reason: "Implements the fix." }],
  offer: { "swe-worker": ["github"] },
};

describe("TeamRequestCard", () => {
  afterEach(cleanup);

  it("keeps approval guidance collapsed and sends the exact human edit", () => {
    const onRespond = vi.fn();
    render(<TeamRequestCard item={{ ...base, members: [{ ...base.members[0], approval_guidance: "Run local tests. No pushes." }] }} onRespond={onRespond} />);
    expect(screen.queryByLabelText(/Approval guidance/)).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Advanced/ }));
    const input = screen.getByLabelText(/Approval guidance/);
    expect((input as HTMLTextAreaElement).value).toBe("Run local tests. No pushes.");
    fireEvent.change(input, { target: { value: "Local tests only.\n No publishing." } });
    fireEvent.click(screen.getByTestId("teamreq-approve"));
    expect(onRespond.mock.calls[0][3][0].approval_guidance).toBe("Local tests only.\n No publishing.");
  });

  it("sends an explicitly cleared paragraph instead of restoring the lead suggestion", () => {
    const onRespond = vi.fn();
    render(<TeamRequestCard item={{ ...base, members: [{ ...base.members[0], approval_guidance: "Suggested actions" }] }} onRespond={onRespond} />);
    fireEvent.click(screen.getByRole("button", { name: /Advanced/ }));
    fireEvent.change(screen.getByLabelText(/Approval guidance/), { target: { value: "" } });
    fireEvent.click(screen.getByTestId("teamreq-approve"));
    expect(onRespond.mock.calls[0][3][0].approval_guidance).toBe("");
  });

  it("puts name, persona and model on one line and the reason on its own", () => {
    render(<TeamRequestCard item={base} onRespond={vi.fn()} />);
    const row = screen.getByTestId("teamreq-row-0");
    const who = row.querySelector(".teamreq-who");
    expect(who?.textContent).toBe("nia — swe-worker · claude-opus-4-8");
    const reason = row.querySelector(".teamreq-reason");
    expect(reason?.textContent).toBe("Implements the fix.");
    expect(who?.contains(reason as Node)).toBe(false);
  });

  it("requests actionable changes and makes staffing distinct from assignment", () => {
    const onRespond = vi.fn();
    render(<TeamRequestCard item={base} onRespond={onRespond} />);
    const approve = screen.getByTestId("teamreq-approve");
    expect(approve.textContent).toBe("Create team");
    expect(screen.getByTestId("teamreq-card").textContent).toContain("does not start these tasks");
    fireEvent.click(screen.getByText("Request changes"));
    fireEvent.change(screen.getByRole("textbox"), { target: { value: "Add independent verification" } });
    fireEvent.click(screen.getByText("Send feedback"));
    expect(onRespond).toHaveBeenCalledWith(false, "Add independent verification");
  });

  it("an unsuggested default connector starts unticked, with no reason; ticks ride the approval", () => {
    const onRespond = vi.fn();
    render(<TeamRequestCard item={base} onRespond={onRespond} />);
    const github = screen.getByTestId("teamreq-connector-0-github") as HTMLInputElement;
    expect(github.checked).toBe(false);
    expect(screen.getByTestId("teamreq-row-0").querySelector(".teamreq-connector-reason")).toBeNull();
    fireEvent.click(github);
    fireEvent.click(screen.getByTestId("teamreq-approve"));
    expect(onRespond).toHaveBeenCalledWith(true, undefined, false, [
      { persona: "swe-worker", name: "nia", connectors: ["github"] },
    ]);
  });

  it("a suggestion the human unticks is not granted, and stays listed", () => {
    const onRespond = vi.fn();
    render(
      <TeamRequestCard
        item={{
          ...base,
          members: [{ ...base.members[0], connectors: ["github", "jira"] }],
          other_connected: ["jira"],
        }}
        onRespond={onRespond}
      />,
    );
    fireEvent.click(screen.getByTestId("teamreq-connector-0-jira"));
    expect((screen.getByTestId("teamreq-connector-0-jira") as HTMLInputElement).checked).toBe(false);
    expect(screen.getByTestId("teamreq-beyond-0")).toBeTruthy();
    fireEvent.click(screen.getByTestId("teamreq-approve"));
    expect(onRespond.mock.calls[0][3]).toEqual([
      { persona: "swe-worker", name: "nia", connectors: ["github"] },
    ]);
  });

  it("an empty default set shows nothing when other connectors can still be added", () => {
    render(
      <TeamRequestCard
        item={{ ...base, offer: { "swe-worker": [] }, other_connected: ["linear"] }}
        onRespond={vi.fn()}
      />,
    );
    expect(screen.getByTestId("teamreq-row-0").textContent).not.toContain("none available");
    expect(screen.queryByTestId("teamreq-beyond-0")).toBeNull();
    expect(screen.getByTestId("teamreq-add-0")).toBeTruthy();
  });

  it("clamps a long note behind More / Less and leaves a short one alone", () => {
    const long = "Three workers cover the plan. ".repeat(12).trim();
    render(<TeamRequestCard item={{ ...base, note: long }} onRespond={vi.fn()} />);
    const note = screen.getByText(long);
    expect(note.className).toContain("clamped");
    const toggle = screen.getByTestId("teamreq-note-toggle");
    expect(toggle.textContent).toBe("More");
    fireEvent.click(toggle);
    expect(note.className).not.toContain("clamped");
    expect(toggle.textContent).toBe("Less");
    cleanup();
    render(<TeamRequestCard item={{ ...base, note: "Short note." }} onRespond={vi.fn()} />);
    expect(screen.queryByTestId("teamreq-note-toggle")).toBeNull();
  });

  // Worker models on the card: the picker exists only when the server lists what can run.
  const withModels: TeamReq = {
    ...base,
    members: [
      { ...base.members[0], model: "anthropic:claude-opus-4-8", resolved_model: "anthropic:claude-opus-4-8" },
      {
        persona: "test-worker",
        name: "checks",
        model: "openai:gpt-5.6-sol",
        resolved_model: "anthropic:claude-sonnet-4-8",
        model_warning: "None of test-worker's recommended models can run here — using the lead's model.",
      },
    ],
    offer: { "swe-worker": ["github"], "test-worker": [] },
    model_options: {
      "swe-worker": ["anthropic:claude-opus-4-8", "anthropic:claude-sonnet-4-8"],
      "test-worker": [],
    },
    runnable_models: [
      { id: "anthropic:claude-opus-4-8", label: "Claude Opus 4.8" },
      { id: "anthropic:claude-sonnet-4-8", label: "Claude Sonnet 4.8" },
      { id: "ollama:qwen3", label: "Qwen 3 (local)" },
    ],
    lead_model: "anthropic:claude-sonnet-4-8",
  };

  it("without runnable_models the model stays plain text and decisions carry no model", () => {
    const onRespond = vi.fn();
    render(
      <TeamRequestCard
        item={{ ...base, members: [{ ...base.members[0], resolved_model: "claude-opus-4-8" }] }}
        onRespond={onRespond}
      />,
    );
    expect(screen.queryByTestId("teamreq-model-0")).toBeNull();
    expect(screen.getByTestId("teamreq-row-0").querySelector(".teamreq-who")?.textContent).toBe(
      "nia — swe-worker · claude-opus-4-8",
    );
    fireEvent.click(screen.getByTestId("teamreq-approve"));
    expect(onRespond.mock.calls[0][3]).toEqual([{ persona: "swe-worker", name: "nia", connectors: [] }]);
    expect("model" in onRespond.mock.calls[0][3][0]).toBe(false);
  });

  it("offers recommended then other models, defaults to resolved_model, and sends a model per worker", () => {
    const onRespond = vi.fn();
    render(<TeamRequestCard item={withModels} onRespond={onRespond} />);
    const nia = screen.getByTestId("teamreq-model-0") as HTMLSelectElement;
    expect(nia.value).toBe("anthropic:claude-opus-4-8");
    const groups = [...nia.querySelectorAll("optgroup")];
    expect(groups.map((g) => g.getAttribute("label"))).toEqual([
      "Recommended for this worker",
      "Other models on this machine",
    ]);
    expect([...groups[0].querySelectorAll("option")].map((o) => o.textContent)).toEqual([
      "Claude Opus 4.8",
      "Claude Sonnet 4.8",
    ]);
    expect([...groups[1].querySelectorAll("option")].map((o) => o.textContent)).toEqual(["Qwen 3 (local)"]);
    // an empty recommended list drops that group; the fallback is still selected
    const checks = screen.getByTestId("teamreq-model-1") as HTMLSelectElement;
    expect(checks.value).toBe("anthropic:claude-sonnet-4-8");
    expect([...checks.querySelectorAll("optgroup")].map((g) => g.getAttribute("label"))).toEqual([
      "Other models on this machine",
    ]);
    expect(screen.queryByTestId("teamreq-model-note-0")).toBeNull();
    fireEvent.click(screen.getByTestId("teamreq-approve"));
    expect(onRespond.mock.calls[0][3]).toEqual([
      { persona: "swe-worker", name: "nia", connectors: [], model: "anthropic:claude-opus-4-8" },
      { persona: "test-worker", name: "checks", connectors: [], model: "anthropic:claude-sonnet-4-8" },
    ]);
  });

  it("keeps the select filled when the initial model is not among the options", () => {
    render(
      <TeamRequestCard
        item={{ ...withModels, members: [{ ...base.members[0], resolved_model: "openai:gpt-5.6-sol" }] }}
        onRespond={vi.fn()}
      />,
    );
    const sel = screen.getByTestId("teamreq-model-0") as HTMLSelectElement;
    expect(sel.value).toBe("openai:gpt-5.6-sol");
    // The name only (no provider prefix); the exact id is the select's hover text.
    expect(sel.options[0].textContent).toBe("gpt-5.6-sol");
    expect(sel.getAttribute("title")).toBe("openai:gpt-5.6-sol");
    expect(sel.options[0].parentElement).toBe(sel);
  });

  it("warns on a fallback model until the human picks another, which then rides the approval", () => {
    const onRespond = vi.fn();
    render(<TeamRequestCard item={withModels} onRespond={onRespond} />);
    expect(screen.queryByTestId("teamreq-model-warn-0")).toBeNull();
    const glyph = screen.getByTestId("teamreq-model-warn-1");
    expect(glyph.getAttribute("role")).toBe("img");
    expect(glyph.getAttribute("title")).toContain("recommended models can run here");
    expect(glyph.getAttribute("aria-label")).toBe(glyph.getAttribute("title"));
    // no recommended list → the warning covers it, no note
    expect(screen.queryByTestId("teamreq-model-note-1")).toBeNull();
    fireEvent.change(screen.getByTestId("teamreq-model-1"), { target: { value: "ollama:qwen3" } });
    expect(screen.queryByTestId("teamreq-model-warn-1")).toBeNull();
    // back on the fallback → the warning returns
    fireEvent.change(screen.getByTestId("teamreq-model-1"), { target: { value: "anthropic:claude-sonnet-4-8" } });
    expect(screen.getByTestId("teamreq-model-warn-1")).toBeTruthy();
    fireEvent.change(screen.getByTestId("teamreq-model-1"), { target: { value: "ollama:qwen3" } });
    fireEvent.click(screen.getByTestId("teamreq-approve"));
    expect(onRespond.mock.calls[0][3][1]).toEqual({
      persona: "test-worker",
      name: "checks",
      connectors: [],
      model: "ollama:qwen3",
    });
  });

  it("notes a pick outside the worker's recommended models", () => {
    render(<TeamRequestCard item={withModels} onRespond={vi.fn()} />);
    expect(screen.queryByTestId("teamreq-model-note-0")).toBeNull();
    fireEvent.change(screen.getByTestId("teamreq-model-0"), { target: { value: "ollama:qwen3" } });
    const note = screen.getByTestId("teamreq-model-note-0");
    expect(note.textContent).toBe("Not one of this worker's recommended models.");
    expect(screen.getByTestId("teamreq-row-0").querySelector(".teamreq-who")?.contains(note)).toBe(false);
    fireEvent.change(screen.getByTestId("teamreq-model-0"), { target: { value: "anthropic:claude-sonnet-4-8" } });
    expect(screen.queryByTestId("teamreq-model-note-0")).toBeNull();
  });

  it("prefers the live composer mode over the payload's, and hides the line when neither is known", () => {
    render(<TeamRequestCard item={{ ...base, lead_mode: "auto-approve" }} leadMode="interactive" onRespond={vi.fn()} />);
    expect(screen.getByTestId("teamreq-approvals").textContent).toContain("Ask for approval");
    cleanup();
    render(<TeamRequestCard item={base} onRespond={vi.fn()} />);
    expect(screen.queryByTestId("teamreq-approvals")).toBeNull();
  });

  it("shows the model's name; the provider and raw id stay in the hover text", () => {
    const item: TeamReq = { ...base, members: [{ ...base.members[0], model: "anthropic:claude-opus-4-8" }] };
    // No curated labels (the Inbox list): the id without its provider prefix.
    render(<TeamRequestCard item={item} onRespond={vi.fn()} />);
    let model = screen.getByTestId("teamreq-row-0").querySelector(".teamreq-model") as HTMLElement;
    expect(model.textContent).toBe(" · claude-opus-4-8");
    expect(model.getAttribute("title")).toBe("anthropic:claude-opus-4-8");
    cleanup();
    // Curated labels (the session view passes /v1/settings.model_labels): the name only.
    render(
      <TeamRequestCard
        item={item}
        modelLabels={{ "anthropic:claude-opus-4-8": "Claude Opus 4.8 · Anthropic" }}
        onRespond={vi.fn()}
      />,
    );
    model = screen.getByTestId("teamreq-row-0").querySelector(".teamreq-model") as HTMLElement;
    expect(model.textContent).toBe(" · Claude Opus 4.8");
    expect(model.getAttribute("title")).toBe("Claude Opus 4.8 · Anthropic (anthropic:claude-opus-4-8)");
  });
});
