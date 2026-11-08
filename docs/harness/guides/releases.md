# Versions and compatibility

Keep the SDK and harness executable on the same version. During beta, pin a
repository revision or an explicitly published release rather than following a
moving branch in a production application.

## Install from a checkout

Source manifests are development packages. A version in a manifest does not mean
that the corresponding package has been published to npm or crates.io. Follow the
[desktop](desktop.md) or [web app](server.md) guide to build the examples from a
checkout, and use the [release tooling](../../../harness/release/README.md) when
preparing distributable artifacts.

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
require rebuilding the native harness alongside the SDK. They are not enabled by
updating a TypeScript declaration file alone. Existing sessions and omitted
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
