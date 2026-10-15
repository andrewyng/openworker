// #742: "Remove key…" only for a key the app stores. A key that comes from the provider's
// environment variable cannot be removed here, so the footer is empty for it.
import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { ProviderKeyFooter } from "./ManageTabs";
import type { ProviderInfo } from "../api";

afterEach(cleanup);

const base: ProviderInfo = {
  name: "openrouter",
  title: "OpenRouter",
  needs_key: true,
  configured: true,
  values: {},
  suggested_models: [],
  recommended_model: null,
  fields: [{ key: "api_key", label: "API key", secret: true, required: true, help: "", placeholder: "" }],
};

describe("ProviderKeyFooter", () => {
  it("offers Remove key for a stored key and removes on confirm", () => {
    const onRemove = vi.fn();
    vi.spyOn(window, "confirm").mockReturnValue(true);
    render(<ProviderKeyFooter info={{ ...base, key_source: "store" }} credentialed onRemove={onRemove} />);
    fireEvent.click(screen.getByTestId("set-remove-key"));
    expect(onRemove).toHaveBeenCalledTimes(1);
  });

  it("hides Remove key when the key comes from an environment variable", () => {
    render(
      <ProviderKeyFooter
        info={{ ...base, key_source: "env", env_key: "OPENROUTER_API_KEY" }}
        credentialed
        onRemove={vi.fn()}
      />,
    );
    expect(screen.queryByTestId("set-remove-key")).toBeNull();
  });

  it("still offers Remove key on an older backend that sends no key_source", () => {
    render(<ProviderKeyFooter info={base} credentialed onRemove={vi.fn()} />);
    expect(screen.getByTestId("set-remove-key")).toBeTruthy();
  });

  it("shows nothing for a provider without a key", () => {
    render(<ProviderKeyFooter info={{ ...base, configured: false }} credentialed={false} onRemove={vi.fn()} />);
    expect(screen.queryByTestId("set-remove-key")).toBeNull();
  });
});
