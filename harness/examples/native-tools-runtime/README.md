# Native tool integration example

A small SDK-capable executable demonstrating how an application links its own
Rust tool crates into an OpenWorker host. It is not a desktop application.

```sh
cd harness
cargo build --release --locked -p native-tools-runtime
cargo test --locked -p notification-tools -p device-tools -p computer-tools --lib
```

Use `target/release/native-tools-runtime` (`.exe` on Windows) as the SDK-managed
child executable. For embedded Rust, register the same crates in the registry
passed to `Harness::builder().tools(registry)`.

- `notification-tools`: `notifications.notify_user` emits a UI notification event.
  A separately invoked macOS dialog example is opt-in and not part of normal tests.
- `device-tools`: `device.computer_status`, `device.audio_status`,
  `device.set_volume`, `device.open_settings` use native Windows APIs; other
  platforms return unsupported, not simulated results.
- `computer-tools`: bounded Windows screenshot/input operations with explicit
  access grants, observation identities and expiry. See that crate's source and
  [computer-use boundary](../../partner/COMPUTER-USE.md).

Registration does not expose every tool to every agent. Select qualified tool
names in each agent/coworker definition. Native callbacks carry the host process's
privileges; approval and OS sandboxing are separate concerns. Do not grant access
to the desktop or change device settings in unattended test runs.
