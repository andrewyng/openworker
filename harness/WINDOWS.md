# Native Windows harness

Build and run the Rust harness natively on Windows. No Python, WSL2 or Docker is
required for the explicit sandbox-off desktop profile. Node.js is needed to build
TypeScript/Electron apps, not to execute an embedded Rust harness.

## Build

Install the pinned Rust toolchain, Visual Studio C++ Build Tools and Windows SDK.
ARM64 dependencies also need Clang. Use a native Windows build host:

```powershell
cd harness
cargo build --release --locked -p openworker-sdk --bin openworker-harness
cargo build --release --locked -p native-tools-runtime
```

If Clang is not on PATH, add the architecture-appropriate Visual Studio LLVM
directory. The helper `tests/windows-build.ps1` supports workspace checks and
builds, but inspect its toolchain paths for your installation.

The SDK host is `target/release/openworker-harness.exe`. Build on the same target
architecture as the intended app. Rust/MSVC builds may require the corresponding
Visual C++ Redistributable; validate it in your installer and clean-machine tests.

## Execution boundary

Use `Sandbox::Off` / `sandbox: "off"` explicitly for this Windows desktop profile.
The Rust workspace in this branch does not provide a Windows kernel sandbox.
NVIDIA OpenShell is the Linux reference execution environment; it is not silently
substituted or disabled on Windows.

Approvals remain independent of execution location. Host Rust callbacks and host
MCP processes have the host account's privileges. Working-directory selection
alone is not file-system confinement. Owned process Job Objects manage lifecycle,
not a security boundary against hostile code.

State directories use Windows ACL protection. Secure static provider credentials
use DPAPI; short-lived access tokens stay in memory. See
[Models and credentials](../docs/harness/guides/providers.md).

## Integrate

Start with the [Desktop app guide](../docs/harness/guides/desktop.md) or
[Arcade Studio](examples/arcade/README.md). For device and computer-use tools, see
[the custom host example](examples/native-tools-runtime/README.md).

```powershell
cargo test --locked -p openworker-sdk --features test-support --test provider_auth
cargo test --locked -p notification-tools -p device-tools -p computer-tools --lib
npm --prefix typescript-sdk ci
npm --prefix typescript-sdk test
```

Desktop capture/input tests require explicit user consent and an isolated test
window. A passing scripted test or ARM64 VM run is not physical x64/OEM hardware
certification. Signed installer, clean-machine and partner-specific acceptance
remain separate release gates.
