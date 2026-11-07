import {
  constants,
  lstatSync,
  realpathSync,
  mkdirSync,
  openSync,
  closeSync,
  fsyncSync,
  writeFileSync,
  renameSync,
  existsSync,
  fstatSync,
  readSync,
} from "node:fs";
import { dirname, isAbsolute, join, relative, resolve, sep } from "node:path";
import { randomUUID } from "node:crypto";

export function checkId(id: unknown): asserts id is string {
  if (typeof id !== "string" || !/^[a-z][a-z0-9-]{7,100}$/.test(id))
    throw new Error("Invalid game or revision identity.");
}
export function realDirectory(path: string): string {
  if (!isAbsolute(path)) throw new Error("Choose an absolute games directory.");
  const stat = lstatSync(path);
  if (!stat.isDirectory() || stat.isSymbolicLink())
    throw new Error("Choose a real directory, not a link.");
  return realpathSync(path);
}
export function scoped(root: string, ...parts: string[]): string {
  const canonical = realDirectory(root);
  const dest = resolve(canonical, ...parts);
  const rel = relative(canonical, dest);
  if (!rel || rel === ".." || rel.startsWith(`..${sep}`) || isAbsolute(rel))
    throw new Error("Path leaves the game directory.");
  let current = canonical;
  for (const part of rel.split(sep)) {
    current = join(current, part);
    if (
      existsSync(current) ||
      (() => {
        try {
          lstatSync(current);
          return true;
        } catch {
          return false;
        }
      })()
    ) {
      const stat = lstatSync(current);
      if (stat.isSymbolicLink())
        throw new Error("Links are not allowed in game storage.");
      const resolved = realpathSync(current),
        nested = relative(canonical, resolved);
      if (
        nested === ".." ||
        nested.startsWith(`..${sep}`) ||
        isAbsolute(nested)
      )
        throw new Error("Path leaves the game directory.");
    }
  }
  return dest;
}
export function atomicWrite(path: string, content: string | Buffer): void {
  if (existsSync(path) && !lstatSync(path).isFile())
    throw new Error("Cannot replace a linked or non-file destination.");
  // Check even broken links; existsSync alone does not see them.
  try {
    if (lstatSync(path).isSymbolicLink())
      throw new Error("Cannot write through links.");
  } catch (e) {
    if ((e as NodeJS.ErrnoException).code !== "ENOENT") throw e;
  }
  const temp = join(dirname(path), `.arcade-${randomUUID()}.tmp`);
  const fd = openSync(
    temp,
    constants.O_WRONLY |
      constants.O_CREAT |
      constants.O_EXCL |
      (constants.O_NOFOLLOW ?? 0),
    0o600,
  );
  try {
    writeFileSync(fd, content);
    fsyncSync(fd);
  } finally {
    closeSync(fd);
  }
  renameSync(temp, path);
}
export function readBounded(path: string, max = 512_000): string {
  return readBytes(path, max).toString("utf8");
}
export function readBytes(path: string, max = 2_000_000): Buffer {
  const info = lstatSync(path);
  if (!info.isFile() || info.isSymbolicLink() || info.size > max)
    throw new Error("Game file is missing or exceeds the size limit.");
  const fd = openSync(path, constants.O_RDONLY | (constants.O_NOFOLLOW ?? 0));
  try {
    const opened = fstatSync(fd);
    if (!opened.isFile() || opened.size > max)
      throw new Error("Game file exceeds the size limit.");
    const bytes = Buffer.alloc(opened.size);
    let offset = 0;
    while (offset < bytes.length) {
      const n = readSync(fd, bytes, offset, bytes.length - offset, null);
      if (!n) throw new Error("Game file changed while reading.");
      offset += n;
    }
    if (readSync(fd, Buffer.alloc(1), 0, 1, null) !== 0)
      throw new Error("Game file changed while reading.");
    return bytes;
  } finally {
    closeSync(fd);
  }
}
export function createDataDirectory(path: string): string {
  mkdirSync(path, { recursive: true, mode: 0o700 });
  return realDirectory(path);
}
