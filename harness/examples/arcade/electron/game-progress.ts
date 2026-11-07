import type { DatabaseSync } from "node:sqlite";
import { validateProgressRequest } from "../shared/game-progress.js";
import type { Library } from "./library.js";
import type { StarterCatalog } from "./starters.js";
export function progressScope(
  url: string,
  library: Library,
  starters: StarterCatalog,
) {
  const u = new URL(url);
  if (
    u.protocol !== "arcade-preview:" ||
    u.username ||
    u.password ||
    u.port ||
    u.search ||
    u.hash ||
    u.pathname !== "/index.html"
  )
    throw new Error("Invalid game preview.");
  const [id, revision, ...extra] = u.hostname.split(".");
  if (!id || !revision || extra.length)
    throw new Error("Invalid game preview.");
  if (id.startsWith("starter-")) starters.asset(u.host, "index.html");
  else library.assertRevision(id, revision);
  return {
    scope: id.startsWith("starter-") ? `starter:${id.slice(8)}` : `game:${id}`,
    revision,
  };
}
export class GameProgress {
  constructor(private db: DatabaseSync) {}
  request(scope: string, revision: string, input: unknown): unknown {
    if (!/^(starter:[a-z0-9-]+|game:game-[a-z0-9-]+)$/.test(scope))
      throw new Error("Invalid game scope.");
    const r = validateProgressRequest(input),
      now = new Date().toISOString();
    if (r.op === "load" || r.op === "save" || r.op === "remove") {
      const row = this.db
        .prepare(
          "SELECT version,payload,updated_at FROM game_saves WHERE scope=? AND slot=?",
        )
        .get(scope, r.slot);
      if (r.op === "remove") {
        this.db
          .prepare("DELETE FROM game_saves WHERE scope=? AND slot=?")
          .run(scope, r.slot);
        return null;
      }
      if (row && Number(row.version) !== r.version)
        throw new Error(
          "INCOMPATIBLE_SAVE: saved progress uses a different format. It has not been changed.",
        );
      if (r.op === "load")
        return row
          ? {
              version: row.version,
              value: JSON.parse(String(row.payload)),
              updatedAt: row.updated_at,
            }
          : null;
      const payload = JSON.stringify(r.value),
        usage = this.db
          .prepare(
            "SELECT COUNT(*) AS n,COALESCE(SUM(length(CAST(payload AS BLOB))),0) AS bytes FROM game_saves WHERE scope=? AND slot<>?",
          )
          .get(scope, r.slot)!;
      if (
        Number(usage.n) >= 16 ||
        Number(usage.bytes) + Buffer.byteLength(payload) > 1024 * 1024
      )
        throw new Error("Game save quota exceeded.");
      this.db
        .prepare(
          "INSERT INTO game_saves(scope,slot,version,payload,revision,updated_at) VALUES(?,?,?,?,?,?) ON CONFLICT(scope,slot) DO UPDATE SET payload=excluded.payload,revision=excluded.revision,updated_at=excluded.updated_at",
        )
        .run(scope, r.slot, r.version, payload, revision, now);
      return { saved: true, updatedAt: now };
    }
    const previous = this.db
      .prepare(
        'SELECT score,ordering AS "order",updated_at AS updatedAt FROM game_scores WHERE scope=? AND board=?',
      )
      .get(scope, r.board);
    if (r.op === "best") return previous ?? null;
    if (previous && previous.order !== r.order)
      throw new Error("Score ordering cannot change for an existing board.");
    if (
      !previous &&
      Number(
        this.db
          .prepare("SELECT COUNT(*) AS n FROM game_scores WHERE scope=?")
          .get(scope)!.n,
      ) >= 64
    )
      throw new Error("Score board quota exceeded.");
    if (
      !previous ||
      (r.order === "lower"
        ? r.score < Number(previous.score)
        : r.score > Number(previous.score))
    )
      this.db
        .prepare(
          "INSERT INTO game_scores(scope,board,score,ordering,updated_at) VALUES(?,?,?,?,?) ON CONFLICT(scope,board) DO UPDATE SET score=excluded.score,updated_at=excluded.updated_at",
        )
        .run(scope, r.board, r.score, r.order, now);
    return this.db
      .prepare(
        'SELECT score,ordering AS "order",updated_at AS updatedAt FROM game_scores WHERE scope=? AND board=?',
      )
      .get(scope, r.board);
  }
}
