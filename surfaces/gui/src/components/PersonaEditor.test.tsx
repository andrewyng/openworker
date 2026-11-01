import { afterEach, describe, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { PersonaEditor } from "./PersonaEditor";
import type { Persona, PersonaDefinition } from "../api";

const definition: PersonaDefinition = {
  id: "research",
  name: "Researcher",
  icon: "🔎",
  tagline: "Evidence first",
  description: "Research",
  system_prompt: "Find evidence and cite it.",
  tools: ["files"],
  connectors: ["github"],
  models: ["openai:gpt-5.5"],
  skills: ["researching"],
  mcp: ["documents"],
  requires_folder: false,
  subagents: true,
  scheduling: false,
  team: "",
  approval_guidance: "Ask before publishing.",
  default_permission_mode: "interactive",
  version: "2",
  group: "general",
  recommends: [
    { kind: "connector", ref: "github", tier: "core", reason: "Evidence" },
  ],
};
const persona: Persona = {
  ...definition,
  builtin: true,
  enabled: true,
  surfaced: true,
  default: false,
};
const catalog = {
  capabilities: [
    {
      id: "files",
      name: "Files",
      description: "Read and edit files",
      risk: ["read", "write_local"],
    },
    { id: "shell", name: "Shell", description: "Run commands", risk: ["exec"] },
  ],
  connectors: [{ id: "github", name: "GitHub" }],
  mcp: ["documents"],
  modes: ["interactive", "plan"],
};
const consent = {
  ...definition,
  risk: ["read", "write_local"],
  recommended_mode: "interactive",
};

function mockApi({
  rejectLoad = false,
  rejectSave = false,
  rejectEnable = false,
} = {}) {
  const calls: { path: string; body: any }[] = [];
  const custom = {
    ...persona,
    id: "research-copy",
    name: "Researcher copy",
    builtin: false,
    enabled: false,
  };
  vi.stubGlobal(
    "fetch",
    vi.fn(async (url: string, init?: RequestInit) => {
      const path = String(url);
      const body = init?.body ? JSON.parse(String(init.body)) : undefined;
      calls.push({ path, body });
      let result: unknown = {};
      if (path.endsWith("/definition")) result = { ok: true, definition };
      else if (path.endsWith("/v1/persona-editor")) {
        if (rejectLoad) throw new Error("Machine unavailable");
        result = catalog;
      } else if (path.endsWith("/preview"))
        result = {
          ok: true,
          consent,
          markdown: "---\nname: Researcher copy\n---\nInstructions",
        };
      else if (path.endsWith("/save"))
        result = rejectSave
          ? { ok: false, error: "Identifier already exists" }
          : {
              ok: true,
              consent: [consent],
              personas: [persona, { ...custom, ...body.definition }],
            };
      else if (init?.method === "POST")
        result = rejectEnable
          ? { ok: false, error: "Could not enable" }
          : { ok: true, personas: [{ ...custom, enabled: true }] };
      return { ok: true, json: async () => result } as Response;
    }),
  );
  return calls;
}

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

describe("PersonaEditor", () => {
  it("copies every template property, previews, saves disabled, enables and starts a trial on the same machine", async () => {
    const calls = mockApi();
    const onSaved = vi.fn();
    const onTry = vi.fn();
    render(
      <PersonaEditor
        personas={[persona]}
        templateId="research"
        machineId="machine-2"
        onClose={vi.fn()}
        onSaved={onSaved}
        onTry={onTry}
      />,
    );
    await screen.findByDisplayValue("Researcher copy");
    expect(
      (screen.getByLabelText("Instructions *") as HTMLTextAreaElement).value,
    ).toBe(definition.system_prompt);
    fireEvent.click(screen.getByText("Advanced settings"));
    expect(
      (screen.getByLabelText("researching") as HTMLInputElement).checked,
    ).toBe(true);
    expect(
      (
        screen.getByLabelText("Allow explorer subagents", {
          exact: false,
        }) as HTMLInputElement
      ).checked,
    ).toBe(true);
    fireEvent.click(
      screen.getByRole("button", { name: "Preview capabilities" }),
    );
    await screen.findByTestId("persona-editor-review");
    const preview = calls.find((c) => c.path.endsWith("/preview"))!;
    expect(preview.path).toContain("machine-2");
    expect(preview.body).toEqual({
      definition: {
        ...definition,
        id: "research-copy",
        name: "Researcher copy",
      },
      template_id: "research",
    });
    fireEvent.click(screen.getByRole("button", { name: "Save coworker" }));
    await screen.findByTestId("persona-editor-saved");
    expect(
      (
        screen.getByRole("button", {
          name: "Try a conversation",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Enable coworker" }));
    await screen.findByText("This coworker is enabled and ready to use.");
    fireEvent.click(screen.getByRole("button", { name: "Try a conversation" }));
    expect(onTry).toHaveBeenCalledWith("research-copy");
    expect(onSaved).toHaveBeenCalledTimes(2);
  });

  it("invalidates preview when any field changes and removes recommendations when a connector is removed", async () => {
    const calls = mockApi();
    render(
      <PersonaEditor
        personas={[persona]}
        templateId="research"
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    await screen.findByDisplayValue("Researcher copy");
    fireEvent.click(
      screen.getByRole("button", { name: "Preview capabilities" }),
    );
    await screen.findByTestId("persona-editor-review");
    fireEvent.change(screen.getByLabelText("Name *"), {
      target: { value: "My researcher" },
    });
    expect(screen.queryByRole("button", { name: "Save coworker" })).toBeNull();
    fireEvent.click(screen.getByLabelText("GitHub"));
    fireEvent.click(
      screen.getByRole("button", { name: "Preview capabilities" }),
    );
    await screen.findByTestId("persona-editor-review");
    const previews = calls.filter((c) => c.path.endsWith("/preview"));
    const last = previews[previews.length - 1];
    expect(last.body.definition.connectors).toEqual([]);
    expect(last.body.definition.recommends).toEqual([]);
  });

  it("keeps a failed save reviewable and does not claim success", async () => {
    mockApi({ rejectSave: true });
    const onSaved = vi.fn();
    render(
      <PersonaEditor
        personas={[persona]}
        templateId="research"
        onClose={vi.fn()}
        onSaved={onSaved}
      />,
    );
    await screen.findByDisplayValue("Researcher copy");
    fireEvent.click(
      screen.getByRole("button", { name: "Preview capabilities" }),
    );
    await screen.findByTestId("persona-editor-review");
    fireEvent.click(screen.getByRole("button", { name: "Save coworker" }));
    expect(await screen.findByRole("alert")).toHaveProperty(
      "textContent",
      "Identifier already exists",
    );
    expect(onSaved).not.toHaveBeenCalled();
    expect(screen.queryByTestId("persona-editor-saved")).toBeNull();
  });

  it("recovers from load failures without showing a usable empty editor", async () => {
    const calls = mockApi({ rejectLoad: true });
    render(
      <PersonaEditor
        personas={[persona]}
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    await screen.findByText("Machine unavailable");
    expect(
      screen.queryByRole("button", { name: "Preview capabilities" }),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() =>
      expect(
        calls.filter((c) => c.path.endsWith("/v1/persona-editor")),
      ).toHaveLength(2),
    );
  });

  it("locks the identity of a custom persona and uses edit rather than template semantics", async () => {
    const calls = mockApi();
    render(
      <PersonaEditor
        personas={[{ ...persona, builtin: false }]}
        editingId="research"
        onClose={vi.fn()}
        onSaved={vi.fn()}
      />,
    );
    await screen.findByDisplayValue("Researcher");
    expect(
      (screen.getByLabelText("Identifier *") as HTMLInputElement).disabled,
    ).toBe(true);
    expect(screen.queryByLabelText("Starting template")).toBeNull();
    fireEvent.click(
      screen.getByRole("button", { name: "Preview capabilities" }),
    );
    await screen.findByTestId("persona-editor-review");
    expect(
      calls.find((c) => c.path.endsWith("/preview"))!.body.editing_id,
    ).toBe("research");
    expect(
      calls.find((c) => c.path.endsWith("/preview"))!.body.template_id,
    ).toBeUndefined();
  });

  it("keeps trial disabled when enabling fails", async () => {
    mockApi({ rejectEnable: true });
    render(
      <PersonaEditor
        personas={[persona]}
        templateId="research"
        onClose={vi.fn()}
        onSaved={vi.fn()}
        onTry={vi.fn()}
      />,
    );
    await screen.findByDisplayValue("Researcher copy");
    fireEvent.click(
      screen.getByRole("button", { name: "Preview capabilities" }),
    );
    await screen.findByTestId("persona-editor-review");
    fireEvent.click(screen.getByRole("button", { name: "Save coworker" }));
    await screen.findByTestId("persona-editor-saved");
    fireEvent.click(screen.getByRole("button", { name: "Enable coworker" }));
    await screen.findByText("Could not enable");
    expect(
      (
        screen.getByRole("button", {
          name: "Try a conversation",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
  });
});
