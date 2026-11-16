import { DatabaseSync } from "node:sqlite";
import { GameProgress } from "./game-progress.js";
import { createHash, randomUUID } from "node:crypto";
import { existsSync, mkdirSync, lstatSync } from "node:fs";
import { join, dirname } from "node:path";
import {
  type ProjectFiles,
  validateFiles,
  projectPath,
  fingerprint,
  digest,
  type ProjectFile,
  fileBytes,
  fileSize,
  textFile,
  isBinaryPath,
  binaryAsset,
  ASSET_LIMIT,
  PROJECT_LIMIT,
} from "./project.js";
import { starterFiles } from "./templates.js";
import type { Brief, Game, Revision, GameStatus } from "../shared/contracts.js";
import {
  atomicWrite,
  checkId,
  createDataDirectory,
  readBounded,
  readBytes,
  realDirectory,
  scoped,
} from "./files.js";
import { validateBrief } from "./settings.js";
type Row = Record<string, unknown>;
export type FaultPoint = "revision_written" | "catalog_committed";
export class Library {
  private db!: DatabaseSync;
  private summaryDigests = new Map<
    string,
    {
      revision: string | null;
      mtime: number;
      size: number;
      ino: number;
      digest: string;
    }
  >();
  readonly progress: GameProgress;
  constructor(
    readonly dataDir: string,
    private fault?: (point: FaultPoint) => void,
  ) {
    createDataDirectory(dataDir);
    const path = join(dataDir, "library.sqlite");
    if (existsSync(path) && lstatSync(path).isSymbolicLink())
      throw new Error("The game catalog must not be a link.");
    try {
      this.db = new DatabaseSync(path);
      this.db.exec(
        "PRAGMA foreign_keys=ON; PRAGMA journal_mode=WAL; PRAGMA synchronous=FULL; PRAGMA busy_timeout=5000;",
      );
      const version = Number(
        this.db.prepare("PRAGMA user_version").get()!.user_version,
      );
      if (version > 4)
        throw new Error("This game library requires a newer Arcade version.");
      // Keep a consistent, restorable pre-upgrade copy (including committed WAL data).
      if (version > 0 && version < 4)
        this.db
          .prepare("VACUUM INTO ?")
          .run(
            join(dataDir, `library-before-progress-v4-${randomUUID()}.sqlite`),
          );
      if (version === 0)
        this.db.exec(`BEGIN;
        CREATE TABLE games(id TEXT PRIMARY KEY,title TEXT NOT NULL,directory TEXT NOT NULL UNIQUE,created_at TEXT NOT NULL,updated_at TEXT NOT NULL,brief TEXT NOT NULL,session TEXT,model TEXT,revision_id TEXT,status TEXT NOT NULL,error TEXT,thumbnail INTEGER NOT NULL DEFAULT 0);
        CREATE TABLE revisions(id TEXT PRIMARY KEY,game_id TEXT NOT NULL REFERENCES games(id),created_at TEXT NOT NULL,hash TEXT NOT NULL,source TEXT NOT NULL);
        CREATE TABLE effects(effect_key TEXT PRIMARY KEY,game_id TEXT NOT NULL REFERENCES games(id),revision_id TEXT NOT NULL REFERENCES revisions(id),hash TEXT NOT NULL);
        PRAGMA user_version=1; COMMIT;`);
      if (version < 2)
        this.db.exec(
          "BEGIN; ALTER TABLE games ADD COLUMN submission_id TEXT; PRAGMA user_version=2; COMMIT;",
        );
      if (version < 3)
        this.db.exec(
          "BEGIN; ALTER TABLE revisions ADD COLUMN format TEXT NOT NULL DEFAULT 'html'; PRAGMA user_version=3; COMMIT;",
        );
      if (version < 4)
        this.db.exec(`BEGIN;
        CREATE TABLE IF NOT EXISTS game_saves(scope TEXT NOT NULL,slot TEXT NOT NULL,version INTEGER NOT NULL,payload TEXT NOT NULL,revision TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(scope,slot));
        CREATE TABLE IF NOT EXISTS game_scores(scope TEXT NOT NULL,board TEXT NOT NULL,score REAL NOT NULL,ordering TEXT NOT NULL,updated_at TEXT NOT NULL,PRIMARY KEY(scope,board));
        PRAGMA user_version=4; COMMIT;`);
      this.progress = new GameProgress(this.db);
    } catch {
      // Failed schema validation must release the handle too (Windows locks it).
      try {
        this.db?.close();
      } catch {}
      throw new Error(
        "The game catalog could not be opened. Your games have been preserved.",
      );
    }
  }
  close(): void {
    this.db.close();
  }
  // UI hint only. Publication and tool reads still use draft() and validate bytes.
  draftSummaryDigest(id: string): string {
    const game = this.get(id),
      path = scoped(game.directory, "draft.json"),
      stat = existsSync(path) ? lstatSync(path) : null,
      old = this.summaryDigests.get(id);
    if (
      stat &&
      old &&
      old.revision === game.revisionId &&
      old.mtime === stat.mtimeMs &&
      old.size === stat.size &&
      old.ino === stat.ino
    )
      return old.digest;
    const d = this.draft(id),
      fresh = lstatSync(path);
    if (this.summaryDigests.size >= 128)
      this.summaryDigests.delete(this.summaryDigests.keys().next().value!);
    this.summaryDigests.set(id, {
      revision: game.revisionId,
      mtime: fresh.mtimeMs,
      size: fresh.size,
      ino: fresh.ino,
      digest: d.digest,
    });
    return d.digest;
  }
  private game(row: Row): Game {
    return {
      id: String(row.id),
      title: String(row.title),
      directory: String(row.directory),
      createdAt: String(row.created_at),
      updatedAt: String(row.updated_at),
      brief: JSON.parse(String(row.brief)),
      session: row.session ? JSON.parse(String(row.session)) : null,
      model: row.model ? JSON.parse(String(row.model)) : null,
      revisionId: row.revision_id ? String(row.revision_id) : null,
      status: row.status as GameStatus,
      error: row.error ? String(row.error) : null,
      thumbnail: !!row.thumbnail,
      submissionId: row.submission_id ? String(row.submission_id) : null,
    };
  }
  list(): Game[] {
    return this.db
      .prepare("SELECT * FROM games ORDER BY updated_at DESC,id LIMIT 10000")
      .all()
      .map((r) => this.game(r));
  }
  get(id: string): Game {
    checkId(id);
    const row = this.db.prepare("SELECT * FROM games WHERE id=?").get(id);
    if (!row) throw new Error("Game not found.");
    return this.game(row);
  }
  findSession(id: string): Game | undefined {
    return this.list().find((g) => g.session?.session_id === id);
  }
  create(
    root: string,
    title: string,
    brief: Brief,
    model: Game["model"],
  ): Game {
    brief = validateBrief(brief);
    const base = realDirectory(root);
    const id = `game-${randomUUID()}`;
    const clean = title.trim().slice(0, 80) || "Untitled game";
    const slug =
      clean
        .normalize("NFKD")
        .replace(/[^a-zA-Z0-9]+/g, "-")
        .replace(/^-|-$/g, "")
        .slice(0, 40)
        .toLowerCase() || "game";
    const directory = scoped(base, `${slug}-${id}`);
    mkdirSync(directory, { mode: 0o700 });
    mkdirSync(scoped(directory, "revisions"), { mode: 0o700 });
    const now = new Date().toISOString();
    this.db
      .prepare(
        "INSERT INTO games(id,title,directory,created_at,updated_at,brief,model,status) VALUES(?,?,?,?,?,?,?,'draft')",
      )
      .run(
        id,
        clean,
        directory,
        now,
        now,
        JSON.stringify(validateBrief(brief)),
        model ? JSON.stringify(model) : null,
      );
    this.manifest(id);
    return this.get(id);
  }
  attach(id: string, session: NonNullable<Game["session"]>): void {
    this.db
      .prepare("UPDATE games SET session=?,updated_at=? WHERE id=?")
      .run(JSON.stringify(session), new Date().toISOString(), this.get(id).id);
    this.manifest(id);
  }
  setModel(id: string, model: NonNullable<Game["model"]>): void {
    this.db
      .prepare("UPDATE games SET model=? WHERE id=?")
      .run(JSON.stringify(model), this.get(id).id);
    this.manifest(id);
  }
  status(id: string, status: GameStatus, error: string | null = null): void {
    this.db
      .prepare("UPDATE games SET status=?,error=?,updated_at=? WHERE id=?")
      .run(status, error, new Date().toISOString(), this.get(id).id);
  }
  submission(id: string, submissionId: string): void {
    this.db
      .prepare(
        "UPDATE games SET submission_id=?,status='building',error=NULL,updated_at=? WHERE id=?",
      )
      .run(submissionId, new Date().toISOString(), this.get(id).id);
  }
  rename(id: string, title: string): void {
    if (typeof title !== "string" || !title.trim() || title.length > 80)
      throw new Error("Invalid game title.");
    this.db
      .prepare("UPDATE games SET title=? WHERE id=?")
      .run(title.trim(), this.get(id).id);
    this.manifest(id);
  }
  revisions(id: string): Revision[] {
    this.get(id);
    return this.db
      .prepare(
        "SELECT * FROM revisions WHERE game_id=? ORDER BY created_at DESC,id DESC",
      )
      .all(id)
      .map((r) => ({
        id: String(r.id),
        gameId: String(r.game_id),
        createdAt: String(r.created_at),
        hash: String(r.hash),
        source: r.source as Revision["source"],
      }));
  }
  project(id: string, revisionId?: string): ProjectFiles {
    const game = this.get(id),
      revision = revisionId ?? game.revisionId;
    if (!revision) throw new Error("This game has not been built yet.");
    checkId(revision);
    const row = this.db
      .prepare("SELECT hash,format FROM revisions WHERE id=? AND game_id=?")
      .get(revision, id);
    if (!row) throw new Error("Game revision not found.");
    if (row.format === "project") {
      const directory = scoped(game.directory, "revisions", revision);
      const manifest = JSON.parse(
        readBounded(scoped(directory, "arcade-project.json"), 100_000),
      );
      if (
        ![1, 2].includes(manifest.version) ||
        !Array.isArray(manifest.files) ||
        manifest.files.length > 64
      )
        throw new Error("Invalid revision manifest.");
      const files: ProjectFiles = Object.create(null);
      let totalBytes = 0;
      for (const file of manifest.files) {
        projectPath(file.path);
        if (Object.hasOwn(files, file.path))
          throw new Error("Repeated revision path.");
        const bytes = readBytes(
          scoped(directory, file.path),
          isBinaryPath(file.path) ? ASSET_LIMIT : 3_000_000,
        );
        totalBytes += bytes.length;
        if (totalBytes > PROJECT_LIMIT)
          throw new Error("Revision exceeds the project size limit.");
        const content = isBinaryPath(file.path)
          ? binaryAsset(file.path, bytes)
          : bytes.toString("utf8");
        if (digest(bytes) !== file.hash)
          throw new Error("Revision integrity check failed.");
        files[file.path] = content;
      }
      validateFiles(files, true, true);
      if (fingerprint(files) !== row.hash)
        throw new Error("Revision integrity check failed.");
      return files;
    }
    const html = readBounded(
      scoped(game.directory, "revisions", revision, "index.html"),
    );
    if (createHash("sha256").update(html).digest("hex") !== row.hash)
      throw new Error(
        "This revision has changed on disk. Restore a saved revision before editing.",
      );
    return { "index.html": html };
  }
  html(id: string, revisionId?: string): string {
    return textFile(this.project(id, revisionId)["index.html"]);
  }
  asset(id: string, revision: string, path: string): ProjectFile {
    projectPath(path);
    const file = this.project(id, revision)[path];
    if (file === undefined) throw new Error("File not found.");
    return file;
  }
  assertRevision(id: string, revision: string): void {
    checkId(id);
    checkId(revision);
    if (
      !this.db
        .prepare("SELECT 1 FROM revisions WHERE game_id=? AND id=?")
        .get(id, revision)
    )
      throw new Error("Game revision not found.");
  }
  fileList(id: string): { path: string; bytes: number }[] {
    const game = this.get(id);
    return game.revisionId
      ? Object.entries(this.project(id)).map(([path, text]) => ({
          path,
          bytes: fileSize(text),
        }))
      : [];
  }
  publish(
    id: string,
    html: string,
    effectKey: string,
    source: Revision["source"] = "agent",
  ): Revision {
    const game = this.get(id);
    this.validateHtml(html);
    const hash = createHash("sha256").update(html).digest("hex");
    const previous = this.db
      .prepare("SELECT * FROM effects WHERE effect_key=?")
      .get(effectKey);
    if (previous) {
      if (previous.game_id !== id || previous.hash !== hash)
        throw new Error(
          "A tool invocation cannot publish different content twice.",
        );
      this.repair(id);
      return this.revisions(id).find((r) => r.id === previous.revision_id)!;
    }
    const revision: Revision = {
      id: `rev-${randomUUID()}`,
      gameId: id,
      createdAt: new Date().toISOString(),
      hash,
      source,
    };
    const directory = scoped(game.directory, "revisions", revision.id);
    mkdirSync(directory, { mode: 0o700 });
    atomicWrite(scoped(directory, "index.html"), html);
    this.fault?.("revision_written");
    this.db.exec("BEGIN IMMEDIATE");
    try {
      this.db
        .prepare(
          "INSERT INTO revisions(id,game_id,created_at,hash,source) VALUES(?,?,?,?,?)",
        )
        .run(revision.id, id, revision.createdAt, hash, source);
      this.db
        .prepare("INSERT INTO effects VALUES(?,?,?,?)")
        .run(effectKey, id, revision.id, hash);
      this.db
        .prepare(
          "UPDATE games SET revision_id=?,thumbnail=0,updated_at=? WHERE id=?",
        )
        .run(revision.id, revision.createdAt, id);
      this.db.exec("COMMIT");
    } catch (error) {
      this.db.exec("ROLLBACK");
      throw error;
    }
    this.fault?.("catalog_committed");
    this.repair(id);
    return revision;
  }
  publishProject(
    id: string,
    input: ProjectFiles,
    effectKey: string,
    source: Revision["source"] = "agent",
  ): Revision {
    const game = this.get(id),
      files = validateFiles(input, true, true),
      hash = fingerprint(files);
    const previous = this.db
      .prepare("SELECT * FROM effects WHERE effect_key=?")
      .get(effectKey);
    if (previous) {
      if (previous.game_id !== id || previous.hash !== hash)
        throw new Error(
          "A tool invocation cannot publish different content twice.",
        );
      this.repair(id);
      return this.revisions(id).find((r) => r.id === previous.revision_id)!;
    }
    const revision: Revision = {
      id: `rev-${randomUUID()}`,
      gameId: id,
      createdAt: new Date().toISOString(),
      hash,
      source,
    };
    const directory = scoped(game.directory, "revisions", revision.id);
    mkdirSync(directory, { mode: 0o700 });
    for (const [path, content] of Object.entries(files)) {
      const dest = scoped(directory, path);
      mkdirSync(dirname(dest), { recursive: true, mode: 0o700 });
      atomicWrite(dest, fileBytes(content));
    }
    atomicWrite(
      scoped(directory, "arcade-project.json"),
      JSON.stringify({
        version: Object.values(files).some((f) => typeof f !== "string")
          ? 2
          : 1,
        files: Object.entries(files).map(([path, content]) => ({
          path,
          hash: digest(fileBytes(content)),
        })),
      }),
    );
    this.fault?.("revision_written");
    this.db.exec("BEGIN IMMEDIATE");
    try {
      this.db
        .prepare(
          "INSERT INTO revisions(id,game_id,created_at,hash,source,format) VALUES(?,?,?,?,?,'project')",
        )
        .run(revision.id, id, revision.createdAt, hash, source);
      this.db
        .prepare("INSERT INTO effects VALUES(?,?,?,?)")
        .run(effectKey, id, revision.id, hash);
      this.db
        .prepare(
          "UPDATE games SET revision_id=?,thumbnail=0,updated_at=? WHERE id=?",
        )
        .run(revision.id, revision.createdAt, id);
      this.db.exec("COMMIT");
    } catch (e) {
      this.db.exec("ROLLBACK");
      throw e;
    }
    this.fault?.("catalog_committed");
    this.repair(id);
    return revision;
  }
  draft(id: string): {
    base: string | null;
    files: ProjectFiles;
    digest: string;
  } {
    const game = this.get(id),
      path = scoped(game.directory, "draft.json");
    if (existsSync(path)) {
      const d = JSON.parse(readBounded(path, 64_000_000));
      if (d.base === game.revisionId) {
        const files = validateFiles(d.files, false, true);
        return { base: d.base, files, digest: fingerprint(files) };
      }
    }
    const files = game.revisionId
      ? this.project(id)
      : starterFiles(game.brief.engine ?? "canvas");
    return this.saveDraft(id, { base: game.revisionId, files });
  }
  private saveDraft(
    id: string,
    d: { base: string | null; files: ProjectFiles },
  ) {
    const files = validateFiles(d.files, false, true);
    const value = { base: d.base, files, digest: fingerprint(files) };
    atomicWrite(
      scoped(this.get(id).directory, "draft.json"),
      JSON.stringify(value),
    );
    return value;
  }
  writeDraft(id: string, path: string, content: string) {
    projectPath(path);
    if (isBinaryPath(path))
      throw new Error(
        "Import binary assets using Arcade's file picker. Text tools cannot write binary files.",
      );
    if (/^vendor\//i.test(path))
      throw new Error("Bundled library files are read-only.");
    if (typeof content !== "string" || Buffer.byteLength(content) > 90_000)
      throw new Error("Write at most 90 KB per file.");
    const d = this.draft(id);
    d.files[path] = content;
    return this.saveDraft(id, d).digest;
  }
  importAssets(id: string, incoming: ProjectFiles) {
    const d = this.draft(id);
    for (const [path, value] of Object.entries(incoming)) {
      projectPath(path);
      if (!path.startsWith("assets/imported/") || !isBinaryPath(path))
        throw new Error("Imported assets must use the assets/imported folder.");
      if (Object.hasOwn(d.files, path))
        throw new Error(
          "An asset with this name already exists; rename the file before importing.",
        );
      d.files[path] = value;
    }
    return this.saveDraft(id, d).digest;
  }
  deleteDraft(id: string, path: string) {
    projectPath(path);
    if (path.toLowerCase() === "index.html" || /^vendor\//i.test(path))
      throw new Error("Cannot remove the entrypoint or bundled library.");
    const d = this.draft(id);
    delete d.files[path];
    return this.saveDraft(id, d).digest;
  }
  stageCatalogAsset(id: string, incoming: ProjectFiles) {
    const d = this.draft(id);
    // Caller resolves a fixed catalog ID; no paths/bytes are accepted from the agent.
    for (const [path, value] of Object.entries(incoming)) {
      projectPath(path);
      if (!path.startsWith("assets/library/"))
        throw Error("Invalid catalog path");
      if (
        Object.hasOwn(d.files, path) &&
        JSON.stringify(d.files[path]) !== JSON.stringify(value)
      )
        throw Error(
          "A modified asset already uses this path; it was not overwritten.",
        );
      d.files[path] = value;
    }
    return this.saveDraft(id, d).digest;
  }
  publishDraft(id: string, expected: string, effectKey: string): Revision {
    const previous = this.db
      .prepare("SELECT * FROM effects WHERE effect_key=?")
      .get(effectKey);
    if (previous) {
      if (previous.game_id !== id || previous.hash !== expected)
        throw new Error("Conflicting publication retry.");
      this.repair(id);
      return this.revisions(id).find((r) => r.id === previous.revision_id)!;
    }
    const d = this.draft(id);
    if (d.digest !== expected)
      throw new Error(
        "The draft changed after approval was requested. Inspect it and request approval again.",
      );
    return this.publishProject(id, d.files, effectKey);
  }
  restore(id: string, revisionId: string): void {
    const files = this.project(id, revisionId);
    // Reset explicitly: an old draft may share this revision ID but contain edits.
    this.saveDraft(id, { base: revisionId, files });
    this.db
      .prepare(
        "UPDATE games SET revision_id=?,thumbnail=0,updated_at=? WHERE id=?",
      )
      .run(revisionId, new Date().toISOString(), id);
    this.repair(id);
  }
  repair(id: string): void {
    const game = this.get(id);
    if (game.revisionId) {
      const files = this.project(id);
      atomicWrite(
        scoped(game.directory, "index.html"),
        Object.keys(files).length === 1
          ? textFile(files["index.html"])
          : "<!doctype html><html><body><h1>Arcade project</h1><p>Play in Arcade or export the complete project as a ZIP. All versioned files are in the revisions folder.</p></body></html>",
      );
    }
    this.manifest(id);
  }
  duplicate(id: string, root: string): Game {
    const original = this.get(id),
      files = this.project(id);
    const game = this.create(
      root,
      `${original.title} copy`,
      original.brief,
      original.model,
    );
    this.publishProject(game.id, files, `duplicate:${game.id}`, "duplicate");
    this.status(game.id, "ready");
    return this.get(game.id);
  }
  thumbnail(id: string, revisionId: string, bytes: Buffer): void {
    const game = this.get(id);
    if (game.revisionId !== revisionId || bytes.length > 2_000_000) return;
    atomicWrite(scoped(game.directory, "thumbnail.png"), bytes);
    this.db
      .prepare("UPDATE games SET thumbnail=1 WHERE id=? AND revision_id=?")
      .run(id, revisionId);
  }
  thumbnailBytes(id: string): Buffer {
    const game = this.get(id);
    if (!game.thumbnail) throw new Error("No thumbnail.");
    const path = scoped(game.directory, "thumbnail.png");
    const info = lstatSync(path);
    if (!info.isFile() || info.size > 2_000_000)
      throw new Error("Invalid thumbnail.");
    return readBytes(path, 2_000_000);
  }
  private manifest(id: string): void {
    const g = this.get(id);
    atomicWrite(
      scoped(g.directory, "game.json"),
      JSON.stringify(
        {
          version: 1,
          id: g.id,
          title: g.title,
          brief: g.brief,
          revisionId: g.revisionId,
          model: g.model,
          updatedAt: g.updatedAt,
        },
        null,
        2,
      ),
    );
  }
  private validateHtml(html: unknown): asserts html is string {
    if (
      typeof html !== "string" ||
      Buffer.byteLength(html) > 400_000 ||
      !/<(?:!doctype\s+html|html)[\s>]/i.test(html) ||
      !/<\/html\s*>/i.test(html)
    )
      throw new Error("Publish a complete HTML document smaller than 400 KB.");
  }
}
