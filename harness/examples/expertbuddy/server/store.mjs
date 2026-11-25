import { mkdir, readFile, writeFile, rename, open } from "node:fs/promises";
import { join } from "node:path";
import { AppError } from "./domain.mjs";
/** Single-writer demo store. A mounted edge volume persists photos and atomically replaced records. */
export class Store {
  constructor(dir) {
    this.dir = dir;
    this.tail = Promise.resolve();
    this.records = [];
  }
  async init() {
    await mkdir(join(this.dir, "photos"), { recursive: true, mode: 0o700 });
    try {
      this.records = JSON.parse(
        await readFile(join(this.dir, "inspections.json"), "utf8"),
      );
    } catch (e) {
      if (e.code !== "ENOENT") throw e;
    }
    return this;
  }
  all() {
    return structuredClone(this.records);
  }
  get(id) {
    const r = this.records.find((r) => r.id === id);
    if (!r) throw new AppError(404, "Inspection not found.");
    return structuredClone(r);
  }
  transaction(fn) {
    const job = this.tail.then(async () => {
      const next = structuredClone(this.records);
      const out = await fn(next);
      const file = join(this.dir, "inspections.next");
      await writeFile(file, JSON.stringify(next), { mode: 0o600 });
      const f = await open(file, "r");
      try {
        await f.sync();
      } finally {
        await f.close();
      }
      await rename(file, join(this.dir, "inspections.json"));
      const d = await open(this.dir, "r");
      try {
        await d.sync();
      } finally {
        await d.close();
      }
      this.records = next;
      return structuredClone(out);
    });
    this.tail = job.catch(() => {});
    return job;
  }
  async insert(record) {
    return this.transaction((rows) => {
      const old = rows.find((r) => r.requestId === record.requestId);
      if (old) {
        if (old.requestHash !== record.requestHash)
          throw new AppError(
            409,
            "This request ID already belongs to another photo.",
          );
        return old;
      }
      if (rows.length >= 1000)
        throw new AppError(409, "The demo inspection store is full.");
      record.code = `EB-${String(rows.length + 1).padStart(4, "0")}`;
      rows.unshift(record);
      return record;
    });
  }
  async update(id, fn) {
    return this.transaction((rows) => {
      const r = rows.find((x) => x.id === id);
      if (!r) throw new AppError(404, "Inspection not found.");
      fn(r);
      r.revision++;
      r.updatedAt = new Date().toISOString();
      return r;
    });
  }
}
