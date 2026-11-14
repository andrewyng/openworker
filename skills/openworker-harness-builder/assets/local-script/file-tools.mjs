import { lstat, realpath, readdir, readFile, mkdir, rmdir, link, unlink } from 'node:fs/promises';
import { isAbsolute, join, resolve, relative, sep } from 'node:path';

// Application-level scope for an app-owned demo directory. This is not OS
// isolation: no other process should concurrently replace directory entries.
export async function fileTools(directory, activity = () => {}) {
  const root = await realpath(directory);
  if (!(await lstat(root)).isDirectory()) throw Error('Expected a directory');
  async function scoped(value, { missingLeaf = false } = {}) {
    if (typeof value !== 'string' || !value || value.includes('\0') ||
        isAbsolute(value) || /^[A-Za-z]:/.test(value) || value.includes('\\') ||
        value.split('/').some(part => part === '..' || part === ''))
      throw Error('Use a relative path inside the demo directory');
    const path = resolve(root, value);
    const rel = relative(root, path);
    if (rel === '..' || rel.startsWith('..' + sep) || isAbsolute(rel)) throw Error('Outside demo directory');
    const parts = rel ? rel.split(sep) : [];
    let current = root;
    for (let i = 0; i < parts.length; i++) {
      current = join(current, parts[i]);
      try {
        const stat = await lstat(current);
        if (stat.isSymbolicLink()) throw Error('Symlinks are not allowed');
        if (i < parts.length - 1 && !stat.isDirectory()) throw Error('Parent is not a directory');
      } catch (error) {
        if (error.code === 'ENOENT' && missingLeaf && i === parts.length - 1) return path;
        throw error;
      }
    }
    return path;
  }
  const schema = properties => ({ type: 'object', properties,
    required: Object.keys(properties), additionalProperties: false });
  const string = { type: 'string' };
  const tool = (name, description, inputSchema, execute) => ({ description, inputSchema,
    async execute(args, context) {
      context.signal.throwIfAborted();
      if (await realpath(context.workspace) !== root) throw Error('Wrong workspace');
      // Upsert activity by these IDs: callback delivery can precede the UI's
      // consumption of the durable tool_started event.
      activity({ phase: 'callback', name: `files.${name}`, args,
        sessionId: context.sessionId, turnId: context.turnId, callId: context.callId });
      await context.progress(`Running files.${name}`);
      const result = await execute(args);
      return result;
    },
  });
  return { namespace: 'files', tools: {
    list_files: tool('list_files', 'List one directory inside the demo. Use . for its root.',
      schema({ path: string }), async ({ path }) => {
        const entries = await readdir(await scoped(path), { withFileTypes: true });
        if (entries.length > 100) throw Error('Demo directory has too many entries');
        return entries.filter(e => !e.isSymbolicLink()).map(e => ({ name: e.name, directory: e.isDirectory() }));
      }),
    read_file: tool('read_file', 'Read a UTF-8 demo file, at most 64 KiB.',
      schema({ path: string }), async ({ path }) => {
        const file = await scoped(path);
        const stat = await lstat(file);
        if (!stat.isFile() || stat.size > 65536) throw Error('Expected a small regular file');
        return { text: await readFile(file, 'utf8') };
      }),
    create_directory: tool('create_directory', 'Create one subdirectory. Requires approval.',
      schema({ path: string }), async ({ path }) => {
        await mkdir(await scoped(path, { missingLeaf: true }));
        return { created: path };
      }),
    delete_directory: tool('delete_directory', 'Remove an empty subdirectory only. Requires approval.',
      schema({ path: string }), async ({ path }) => {
        const target = await scoped(path);
        if (target === root) throw Error('Cannot remove the demo root');
        await rmdir(target);
        return { removed: path };
      }),
    move_file: tool('move_file', 'Move one regular file without overwriting another file. Requires approval.',
      schema({ from: string, to: string }), async ({ from, to }) => {
        const source = await scoped(from);
        const destination = await scoped(to, { missingLeaf: true });
        if (!(await lstat(source)).isFile()) throw Error('Expected a regular file');
        // link is exclusive: an existing destination is never overwritten.
        // Both paths are on the same demo filesystem. No shell or Git access.
        await link(source, destination);
        try { await unlink(source); }
        catch {
          return { completed: false, destinationCreated: true, sourceRetained: true,
            message: 'Both paths may exist. Inspect them before any retry.' };
        }
        return { moved: { from, to } };
      }),
  } };
}
