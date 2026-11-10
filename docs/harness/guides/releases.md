# Versions and compatibility

Keep the SDK and harness executable on the same version. During beta, pin a
repository revision or an explicitly published release rather than following a
moving branch in a production application.

## Published packages

The current npm release is **`@openworkerai/harness-sdk@0.1.0-beta.2`**.
`npm install @openworkerai/harness-sdk` installs it with the matching native runtime
for macOS, Windows or Linux, on ARM64 or x64. Linux packages require glibc.
Keep optional dependencies enabled, and commit your application's lockfile.
For an explicit version, use `npm install @openworkerai/harness-sdk@0.1.0-beta.2`.

The app-building skill is **`@openworkerai/build-with-sdk-skills@0.1.0-beta.3`**,
matched to SDK `0.1.0-beta.2`. Its npm version advances independently of the SDK.
Project and user-wide installation are available; see the
[Desktop](desktop.md#build-with-your-coding-agent) or
[Web](server.md#build-with-your-coding-agent) track.

## Rust from source

The Rust walkthroughs use the public repository. To add this tested revision to
your own Cargo project, use a Git dependency:

```toml
[dependencies]
openworker-sdk = { git = "https://github.com/andrewyng/openworker.git", rev = "ddb8b361bd5889703a429af8beaa804554bd858a" }
tokio = { version = "1", features = ["macros", "rt-multi-thread"] }
futures-util = "0.3"
anyhow = "1"
```

The npm release is not a crates.io publication. The repository examples already
use local path dependencies. Build a helper from the same revision when using
bundled coding tools or managed subprocess execution:

```sh
cargo build --manifest-path harness/Cargo.toml --locked --release \
  -p openworker-sdk --bin openworker-harness
```

That command runs from a checkout at the pinned revision. Set
`OPENWORKER_EXECUTABLE` to the helper's absolute path when required, or pass
`.executable(path)` to the harness builder. A text-only in-process agent does not
need a separate helper. See the [release tooling](../../../harness/release/README.md)
for source packaging and artifact validation.

For TypeScript, ship the matching native helper with your application. Packaged
desktop apps should keep it outside ASAR and resolve its absolute path. For Rust,
build the SDK and any ToolRunner/helper executable from the same revision.

## Upgrade an application

1. Back up durable state before changing versions.
2. Update the SDK and native executable together; retain the previous application
   package for rollback planning.
3. Read the [storage migration instructions](guardrails.md#upgrade-an-existing-installation)
   before restarting an application or service against existing state.
4. For services, drain workers and coordinate API/worker upgrades. Do not downgrade
   a database schema marker to make an older binary start.
5. Exercise your application's model connections, tools and session recovery on
   the target OS before distributing the updated package.

Protocol negotiation does not make arbitrary SDK and host releases interchangeable.
The application controls when it upgrades; the harness does not replace its running
executable automatically.

## Session and streaming API updates

The new turn options, history import, built-in tool controls and provider settings
are included in SDK/runtime `0.1.0-beta.2`. npm installs the matching runtime;
applications that configure an explicit executable must update it as well.
Rust source builds must rebuild the native harness alongside the SDK. Existing sessions and omitted
configuration keep their previous tool selection and per-call limits.

Rust applications using explicit `TurnLimits` or `TurnOptions` literals should
include the new fields or use `..Default::default()`. Tool event variants now
carry call and turn IDs, and there are new attempt/reasoning/diagnostic variants;
update exhaustive matches, or use `..` when ignoring additional fields. See
[session options](sessions.md) and [streaming and retries](streaming.md) for examples.

Rust code that previously handled `assistant_delta` through `RunEvent::Other`
should now match `RunEvent::TextDelta`. TypeScript's raw journal event names
remain unchanged.

See [troubleshooting](troubleshooting.md) for runtime discovery, version mismatch
and storage errors.
