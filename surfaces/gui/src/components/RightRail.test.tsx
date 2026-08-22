import { afterEach, describe, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen, waitFor } from "@testing-library/react";
import { RightRail } from "./RightRail";

type Call = { url: string; method: string; body: any };

function stubFetch() {
	const calls: Call[] = [];
	const fn = vi.fn(async (url: string, init?: RequestInit) => {
		const method = (init?.method || "GET").toUpperCase();
		calls.push({
			url,
			method,
			body: init?.body ? JSON.parse(String(init.body)) : undefined,
		});
		const json = url.includes("/artifacts") && method === "GET"
			? { artifacts: [] }
			: { ok: true };
		return { ok: true, json: async () => json } as Response;
	});
	vi.stubGlobal("fetch", fn);
	return calls;
}

afterEach(() => {
	cleanup();
	vi.unstubAllGlobals();
});

describe("RightRail", () => {
	it("opens the session working folder before any artifacts exist", async () => {
		const calls = stubFetch();
		render(
			<RightRail
				active
				sessionId="session-1"
				refreshKey={0}
				toolNames={[]}
				todo={[]}
				running={false}
			/>,
		);

		await screen.findByText("No previewable files yet.");
		fireEvent.click(
			screen.getByRole("button", { name: "Open session working folder" }),
		);

		await waitFor(() => {
			const call = calls.find(
				(item_call) => item_call.method === "POST"
					&& item_call.url.includes("/artifacts/reveal"),
			);
			expect(call?.body).toEqual({ path: "", mode: "reveal" });
		});
	});
});
