# Desktop track

Use the developer's chosen desktop UI. Electron is a convenient TypeScript path;
Rust/Tauri applications can embed the Rust SDK instead. Build the actual requested
product, not necessarily the reference directory summarizer.

## Code Atlas example

A useful first app lets a user choose a code directory, review the files to share,
request a summary and see streamed text with relative file references. Collect
that input in the application and give it to a tool-free agent using the bundled
[sdk.md](sdk.md) lifecycle. Use a new session for unrelated directories.

Bound the number of files and total input bytes. Skip symlinks, dependencies,
hidden directories, binary files and obvious credential files. Show which files
will be sent to the selected model. These exclusions do not guarantee that the
remaining files contain no secrets. Do not add shell execution or write access
when the task only calls for a summary.

## Electron process boundary

Keep the SDK, directory dialog, filesystem reads and provider credentials in the
main process. Expose specific actions through a context-isolated preload: choose
a directory, start/cancel a summary and receive text/status events. Check the IPC
sender and validate inputs. Do not enable Node integration in the renderer or
expose arbitrary filesystem/SDK calls through IPC. Render output with textContent
or a sanitized Markdown renderer.

Use the app's data directory for durable state. At startup, resolve the helper:

```typescript
import { app } from 'electron';
import { join } from 'node:path';
import { Harness, resolveRuntimeExecutable } from '@openworkerai/harness-sdk';

await app.whenReady();
const filename = process.platform === 'win32'
  ? 'openworker-harness.exe' : 'openworker-harness';
const executable = app.isPackaged
  ? join(process.resourcesPath, filename)
  : resolveRuntimeExecutable();
const harness = await Harness.start({
  stateDir: join(app.getPath('userData'), 'agent-state'),
  executable,
  sandbox: 'off',
});
```

Configure the provider and create the agent as shown in [sdk.md](sdk.md). Start
one harness at the application boundary; do not start a process on every click.
Guard duplicate submissions and display failure/cancellation states distinctly.
During shutdown, await harness.shutdown() before completing app exit; avoid an
unhandled asynchronous before-quit handler or recursive quit calls.

## Package the native helper

In the build script, use `resolveRuntimeExecutable()` to locate the installed
binary. Copy it through the packager's extraResources mechanism to the filename
used above, outside ASAR. Keep its executable permissions on macOS/Linux. Bundle
the matching version and architecture, including when cross-building an app.
The default npm installation selects the build machine's architecture, not an
arbitrary packaging target.

Make `@openworkerai/harness-sdk` a production dependency. Test the unpacked packaged app
with no `OPENWORKER_EXECUTABLE` override and without the OpenWorker checkout.
Then qualify installers, signing and notarization on the actual target OS.
Do not claim a complete Rust GUI when only a Rust agent-loop sample was built.

## Reference source when useful

A full maintained Code Atlas example is in `harness/examples/quickstart` of
https://github.com/andrewyng/openworker. It includes main/preload handlers, a
bounded directory reader, and a renderer. Reading it is optional for the bundled
integration above. If copying it, choose a matching revision, replace checkout
`file:` dependencies with the published SDK version, and preserve its limits.

Verify directory selection, content preview, streamed response, Stop, error
handling, shutdown and the packaged helper path. Synthetic provider tests verify
transport behavior; a live model test additionally evaluates actual responses.
