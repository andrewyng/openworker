// T3-12: in Settings ▸ Coworkers the name opens the coworker's page, the default coworker's too.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";

vi.mock("../api", () => ({
  getPersonasIndex: vi.fn(async () => ({
    internal: false,
    personas: [
      { id: "cowork", name: "OpenWorker", tagline: "The default", enabled: true, default: true, surfaced: true },
      { id: "sec", name: "Security Coworker", tagline: "Finds risks", enabled: true, default: false, surfaced: true },
    ],
  })),
  getSessions: vi.fn(async () => []),
  getMachineSessionsList: vi.fn(async () => []),
  installPersona: vi.fn(),
  updatePersona: vi.fn(),
}));

import { PersonasTab } from "./PersonasTab";

afterEach(cleanup);

describe("PersonasTab", () => {
  it("opens a coworker's page from its name, the default one too (T3-12)", async () => {
    const open = vi.fn();
    render(<PersonasTab onOpenPersona={open} />);
    await waitFor(() => expect(screen.getByTestId("persona-open-cowork")).toBeTruthy());
    expect(screen.getByTestId("persona-default-tag")).toBeTruthy();
    fireEvent.click(screen.getByTestId("persona-open-cowork"));
    expect(open).toHaveBeenCalledWith("cowork");
    fireEvent.click(screen.getByTestId("persona-open-sec"));
    expect(open).toHaveBeenLastCalledWith("sec");
    // the sliders button is still there for the others
    expect(screen.getByTestId("persona-configure-sec")).toBeTruthy();
  });
});
