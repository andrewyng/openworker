import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { openRouterAuth, type OpenRouterAuthStatus } from "../api";
import { openExternal } from "../tauri";
import { OpenRouterSignIn } from "./OpenRouterSignIn";
import { ProviderForm, type ProviderSetupState } from "./ProviderSetup";

vi.mock("../api", () => ({ openRouterAuth: vi.fn() }));
vi.mock("../tauri", () => ({ openExternal: vi.fn() }));
vi.mock("react-i18next", () => ({ useTranslation: () => ({ t: translate }) }));
const translate = (key: string) => key;

const idle: OpenRouterAuthStatus = {
  connected: false,
  active: false,
  authorizing: false,
  attempt_id: null,
  authorize_url: null,
  error: null,
};
const pending: OpenRouterAuthStatus = {
  ...idle,
  authorizing: true,
  attempt_id: "attempt-1",
  authorize_url: "https://openrouter.ai/auth?code_challenge=test",
};
const connected: OpenRouterAuthStatus = {
  ...idle,
  connected: true,
  active: true,
};

beforeEach(() => {
  vi.resetAllMocks();
  vi.mocked(openRouterAuth).mockResolvedValue(idle);
});
afterEach(cleanup);

it("places account sign-in below the API key in the shared provider form", async () => {
  const info = {
    name: "openrouter",
    title: "OpenRouter",
    needs_key: true,
    configured: false,
    values: {},
    suggested_models: [],
    recommended_model: null,
    fields: [
      {
        key: "api_key",
        label: "API key",
        secret: true,
        required: true,
        help: "",
        placeholder: "sk-or-…",
      },
    ],
  };
  const ps: ProviderSetupState = {
    providers: [info],
    ordered: [info],
    info,
    sel: "openrouter",
    fields: {},
    refreshProviders: async () => {},
    setFieldValue: vi.fn(),
    dirty: false,
    verify: { state: "idle" },
    showEndpoint: false,
    setShowEndpoint: vi.fn(),
    keylessOk: new Set(),
    credentialed: false,
    savedState: false,
    secretFilled: false,
    openProvider: vi.fn(),
    backToGallery: vi.fn(),
    runTestAndSave: async () => true,
    removeKey: async () => {},
    cancelBackTimer: vi.fn(),
    statusFor: () => null,
    saveField: async () => {},
    fieldSaved: null,
  };
  render(<ProviderForm ps={ps} tp="shared" />);
  const key = screen.getByTestId("shared-field-api_key");
  const signIn = screen.getByTestId("shared-openrouter-signin");
  expect(
    key.compareDocumentPosition(signIn) & Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  await waitFor(() => expect(openRouterAuth).toHaveBeenCalledWith("status"));
});

it("opens browser login, allows reopening, and cancels the pending attempt", async () => {
  vi.mocked(openRouterAuth).mockImplementation(async (action) =>
    action === "signin" ? pending : idle,
  );
  render(<OpenRouterSignIn tp="test" onChanged={vi.fn(async () => {})} />);
  await waitFor(() => expect(openRouterAuth).toHaveBeenCalledWith("status"));
  fireEvent.click(screen.getByTestId("test-openrouter-signin"));
  await screen.findByRole("status");
  expect(openRouterAuth).toHaveBeenCalledWith("signin", { manual: false });
  expect(openExternal).toHaveBeenCalledWith(pending.authorize_url);
  fireEvent.click(screen.getByText("openrouter.reopen"));
  expect(openExternal).toHaveBeenCalledTimes(2);
  fireEvent.click(screen.getByText("openrouter.cancel"));
  await waitFor(() => expect(screen.queryByRole("status")).toBeNull());
  expect(openRouterAuth).toHaveBeenCalledWith(
    "cancel",
    expect.objectContaining({ attempt_id: "attempt-1" }),
  );
});

it("does not let an older status response replace a newly started login", async () => {
  let resolveStatus!: (status: OpenRouterAuthStatus) => void;
  const oldStatus = new Promise<OpenRouterAuthStatus>((resolve) => {
    resolveStatus = resolve;
  });
  vi.mocked(openRouterAuth).mockImplementation((action) =>
    action === "status" ? oldStatus : Promise.resolve(pending),
  );
  render(<OpenRouterSignIn tp="test" onChanged={vi.fn(async () => {})} />);
  fireEvent.click(screen.getByTestId("test-openrouter-signin"));
  await screen.findByRole("status");
  await act(async () => {
    resolveStatus(idle);
    await oldStatus;
  });
  expect(screen.getByRole("status")).toBeTruthy();
});

it("restores manual code entry when reopening an active manual login", async () => {
  vi.mocked(openRouterAuth).mockResolvedValue({
    ...pending,
    authorize_url:
      "https://openrouter.ai/auth?code_challenge=test&key_label=OpenWorker",
  });
  render(<OpenRouterSignIn tp="test" onChanged={vi.fn(async () => {})} />);
  await screen.findByRole("status");
  expect(screen.getByLabelText("openrouter.code")).toBeTruthy();
});

it("ignores a poll started during an action when it resolves after that action", async () => {
  vi.useFakeTimers();
  let finishAction!: (status: OpenRouterAuthStatus) => void;
  let finishPoll!: (status: OpenRouterAuthStatus) => void;
  const actionResponse = new Promise<OpenRouterAuthStatus>((resolve) => {
    finishAction = resolve;
  });
  const stalePoll = new Promise<OpenRouterAuthStatus>((resolve) => {
    finishPoll = resolve;
  });
  let polls = 0;
  vi.mocked(openRouterAuth).mockImplementation((action) => {
    if (action === "status")
      return ++polls === 1 ? Promise.resolve(idle) : stalePoll;
    return actionResponse;
  });
  try {
    render(<OpenRouterSignIn tp="test" onChanged={vi.fn(async () => {})} />);
    await act(async () => {});
    fireEvent.click(screen.getByTestId("test-openrouter-signin"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(2000);
    });
    await act(async () => {
      finishAction(pending);
      await actionResponse;
    });
    expect(screen.getByRole("status")).toBeTruthy();
    await act(async () => {
      finishPoll(idle);
      await stalePoll;
    });
    expect(screen.getByRole("status")).toBeTruthy();
  } finally {
    cleanup();
    vi.useRealTimers();
  }
});

it("submits a manual code bound to its attempt and shows connected state", async () => {
  vi.mocked(openRouterAuth).mockImplementation(async (action) => {
    if (action === "signin") return pending;
    if (action === "complete") return connected;
    return idle;
  });
  const changed = vi.fn(async () => {});
  render(<OpenRouterSignIn tp="test" onChanged={changed} />);
  fireEvent.click(screen.getByText("openrouter.manual"));
  const input = await screen.findByLabelText("openrouter.code");
  expect(openRouterAuth).toHaveBeenCalledWith("signin", { manual: true });
  expect(
    (screen.getByText("openrouter.connect") as HTMLButtonElement).disabled,
  ).toBe(true);
  fireEvent.change(input, { target: { value: "returned-code" } });
  fireEvent.click(screen.getByText("openrouter.connect"));
  await screen.findByTestId("test-openrouter-connected");
  expect(openRouterAuth).toHaveBeenCalledWith("complete", {
    code: "returned-code",
    attempt_id: "attempt-1",
  });
  expect(screen.getByText("openrouter.active")).toBeTruthy();
  expect(changed).toHaveBeenCalled();
});

it("shows provider errors and permits retry", async () => {
  vi.mocked(openRouterAuth).mockImplementation(async (action) =>
    action === "signin" ? { ...idle, error: "Code expired. Try again." } : idle,
  );
  render(<OpenRouterSignIn tp="test" onChanged={vi.fn(async () => {})} />);
  fireEvent.click(screen.getByTestId("test-openrouter-signin"));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "Code expired. Try again.",
  );
  expect(
    (screen.getByTestId("test-openrouter-signin") as HTMLButtonElement)
      .disabled,
  ).toBe(false);
});

it("shows a safe connection error when a request fails", async () => {
  vi.mocked(openRouterAuth).mockImplementation(async (action) => {
    if (action === "signin") throw new Error("private server response");
    return idle;
  });
  render(<OpenRouterSignIn tp="test" onChanged={vi.fn(async () => {})} />);
  fireEvent.click(screen.getByTestId("test-openrouter-signin"));
  expect((await screen.findByRole("alert")).textContent).toBe(
    "openrouter.unreachable",
  );
});

it("disconnects an existing account and links to remote key management", async () => {
  vi.mocked(openRouterAuth).mockImplementation(async (action) =>
    action === "status" ? connected : idle,
  );
  render(<OpenRouterSignIn tp="test" onChanged={vi.fn(async () => {})} />);
  await screen.findByTestId("test-openrouter-connected");
  fireEvent.click(screen.getByText("openrouter.manage"));
  expect(openExternal).toHaveBeenCalledWith("https://openrouter.ai/keys");
  fireEvent.click(screen.getByText("openrouter.disconnect"));
  await waitFor(() =>
    expect(screen.queryByTestId("test-openrouter-connected")).toBeNull(),
  );
  expect(openRouterAuth).toHaveBeenCalledWith("disconnect", expect.any(Object));
});
