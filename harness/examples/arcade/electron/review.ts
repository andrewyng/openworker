import { randomUUID } from "node:crypto";
import type { Library } from "./library.js";
import {
  assetMime,
  fileBytes,
  textFile,
  type ProjectFiles,
} from "./project.js";
import { temporaryPreviewHtml } from "./temporary-preview.js";

/** One bounded, immutable comparison. No publication and no real save bridge. */
export class DraftReview {
  private entry?: {
    token: string;
    draft: ProjectFiles;
    current: ProjectFiles | null;
  };
  close() {
    this.entry = undefined;
  }
  open(library: Library, id: string, expected: string) {
    const draft = library.draft(id),
      game = library.get(id);
    if (draft.digest !== expected)
      throw Error(
        "The draft changed after this approval was requested. Ask the agent to submit the current draft.",
      );
    const current = game.revisionId ? library.project(id) : null;
    const token = `review-${randomUUID()}`;
    this.entry = { token, draft: draft.files, current };
    const paths = [
      ...new Set([...Object.keys(draft.files), ...Object.keys(current ?? {})]),
    ];
    return {
      digest: expected,
      baseRevision: game.revisionId,
      draftUrl: `arcade-preview://${token}.draft/index.html`,
      currentUrl: current
        ? `arcade-preview://${token}.current/index.html`
        : null,
      changes: paths
        .filter(
          (p) =>
            JSON.stringify(draft.files[p]) !== JSON.stringify(current?.[p]),
        )
        .map((path) => ({
          path,
          kind:
            !current || !Object.hasOwn(current, path)
              ? "added"
              : !Object.hasOwn(draft.files, path)
                ? "removed"
                : "modified",
        })),
    };
  }
  response(requestUrl: string): Response {
    try {
      if (/%|\\/.test(requestUrl)) throw Error();
      const u = new URL(requestUrl),
        e = this.entry;
      if (
        !e ||
        u.protocol !== "arcade-preview:" ||
        u.username ||
        u.password ||
        u.search ||
        u.hash ||
        /%|\\/.test(u.pathname)
      )
        throw Error();
      const files =
        u.host === `${e.token}.draft`
          ? e.draft
          : u.host === `${e.token}.current`
            ? e.current
            : null;
      const path = u.pathname.slice(1);
      if (!files || !Object.hasOwn(files, path)) throw Error();
      const origin = `arcade-preview://${u.host}`,
        f = files[path];
      // No SAVE_CLIENT: comparison never sends game-progress requests to the app.
      const html =
        typeof f === "string" ? temporaryPreviewHtml(textFile(f)) : "";
      return new Response(
        path.endsWith(".html") ? html : new Uint8Array(fileBytes(f)),
        {
          headers: {
            "Content-Type":
              assetMime(path) +
              (typeof f === "string" ? "; charset=utf-8" : ""),
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Access-Control-Allow-Origin": "*",
            "Content-Security-Policy": `default-src 'none'; script-src 'unsafe-inline' ${origin}; style-src 'unsafe-inline' ${origin}; img-src data: blob: ${origin}; media-src data: blob: ${origin}; font-src ${origin}; connect-src ${origin} blob:; frame-src 'none'; object-src 'none'; form-action 'none'; base-uri 'none'; worker-src 'none'`,
          },
        },
      );
    } catch {
      return new Response(
        "This comparison is no longer available. Reopen it from the pending approval.",
        {
          status: 404,
          headers: { "Content-Security-Policy": "default-src 'none'" },
        },
      );
    }
  }
}
