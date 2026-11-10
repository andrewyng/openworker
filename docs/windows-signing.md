# Windows code signing

**Status:** the release pipeline is wired for Authenticode signing and activates
automatically when signing credentials are present as repo secrets (#36 / #37).
Releases stay unsigned until a signing credential is procured — this document is the
decision guide for that procurement and the runbook for turning it on.

## Why this matters beyond the SmartScreen popup

- **SmartScreen** shows "Windows protected your PC" for unsigned (and unknown)
  binaries. Users learn to click "More info → Run anyway" — training the exact
  behavior attackers rely on, which is what the audit in #36 flagged.
- **Smart App Control** (Windows 11) is the harder wall: on machines where SAC is on
  — it starts in evaluation mode on new Windows 11 installs and locks on for many
  consumer machines — unsigned binaries are **blocked, not warned**. There is no
  "Run anyway"; turning SAC off requires a decision the user can't revert without
  reinstalling Windows. For those users, unsigned = the app simply doesn't run.
- **SAC evaluates every PE that executes, not just the installer.** The app exe *and*
  the PyInstaller sidecar (`openworker-server.exe`) each get evaluated at run time.
  This is why `build_windows.ps1` signs the sidecar explicitly: Tauri's bundler signs
  only its own outputs, and `resources` ship verbatim. A signed installer with an
  unsigned sidecar installs fine and then fails at first backend start — the failure
  mode looks exactly like #382 and is miserable to diagnose from user reports.
- **AV/EDR reputation:** every release changes every file hash. Unsigned + new hash
  = recurring false-positive quarantines. A stable signing identity accrues
  reputation that carries across releases.

## What to procure

| Option | Ballpark cost | SmartScreen reputation | CI fit | Notes |
|---|---|---|---|---|
| **Azure Trusted Signing** | ~$10/month | Immediate (Microsoft-vouched) | Best — OIDC auth, no key custody | Public-trust identity validation currently requires an organization with 3+ years of verifiable history |
| **OV certificate** (cloud-HSM: DigiCert KeyLocker, SSL.com eSigner, …) | ~$250–600/yr | Accrues with download volume (expect weeks of warnings on a fresh identity) | Good — vendor CLI in CI | Since the 2023 CA/B rules, new code-signing keys must live in HSMs — plain exportable PFX files are largely a thing of the past |
| **EV certificate** (hardware token) | ~$300–700/yr | Immediate | Poor — a USB token can't live in a CI runner | Fine for locally-produced release builds; the cloud variants above fit CI better |

Recommendation: **Azure Trusted Signing if the org passes identity validation**;
otherwise an OV certificate with the CA's cloud signing CLI. (Prices and eligibility
rules move — verify against the provider before purchasing.)

## How the pipeline consumes it

`packaging/build_windows.ps1` resolves credentials from the environment, first match
wins. All of them absent → the build is unsigned and behaves exactly as today.

1. **`WINDOWS_SIGN_COMMAND`** — a full command template; `%1` is replaced with the
   path of the file to sign. This is the escape hatch that fits any cloud signing
   CLI. Example (Azure Trusted Signing via [trusted-signing-cli](https://github.com/Levminer/trusted-signing-cli),
   authenticated with `AZURE_TENANT_ID` / `AZURE_CLIENT_ID` / `AZURE_CLIENT_SECRET`):

   ```
   trusted-signing-cli -e https://eus.codesigning.azure.net -a <account> -c <profile> %1
   ```

   Note: Tauri substitutes `%1` in the *string* form of `signCommand` by splitting on
   spaces — if your tool's path contains spaces, use your own `--config` overlay with
   the `{cmd, args}` object form instead.

2. **`WINDOWS_CERTIFICATE_THUMBPRINT`** — SHA-1 thumbprint of a certificate already
   provisioned in the machine's certificate store (hardware token on a release
   machine, or a pre-provisioned runner). The script uses `signtool` from the
   installed Windows SDK.

3. **`WINDOWS_CERTIFICATE` + `WINDOWS_CERTIFICATE_PASSWORD`** — base64-encoded PFX,
   imported into `CurrentUser\My` for the duration of the build and then signed by
   thumbprint, so the password never appears on a command line or in a config file.

Optional: **`WINDOWS_TIMESTAMP_URL`** — RFC 3161 timestamp server (defaults to
DigiCert's). Timestamping is non-negotiable: it is what keeps signatures valid after
the certificate expires.

**What gets signed:** the sidecar `openworker-server.exe` (explicitly, before
bundling), then the app exe, the NSIS setup `.exe`, and the `.msi` (by Tauri during
`tauri build`). The script verifies every produced artifact carries a valid
signature and fails the build otherwise — a sign step that silently no-ops is worse
than an unsigned build, because nobody looks at a green release.

**For CI:** add the chosen secrets to the repo and the Windows job picks them up
(see the env block in `.github/workflows/release.yml`). Unset secrets arrive as
empty strings and are treated as absent, so forks and scratch runs keep building
unsigned without any configuration.

**Not in `tauri.conf.json`:** signing config deliberately travels through a
generated `--config` overlay, never the checked-in config. A hardcoded
`certificateThumbprint` makes `tauri build` fail on every machine that doesn't hold
that certificate — dev builds must stay green with zero setup.

## Verifying a release

```powershell
# Any of the produced artifacts:
signtool verify /pa /all .\OpenWorker-windows-setup.exe
Get-AuthenticodeSignature .\OpenWorker-windows.msi | Format-List

# The sidecar inside an installed copy:
Get-AuthenticodeSignature "$env:LOCALAPPDATA\OpenWorker\sidecar\openworker-server.exe"
```

First-launch expectations after signing is live:

| | Unsigned (today) | Signed, new identity (OV) | Signed, Trusted Signing / established identity |
|---|---|---|---|
| SmartScreen | Warns | Still warns until reputation accrues | No warning |
| Smart App Control | **Blocks** | Runs | Runs |

## Field notes (from shipping a Tauri + PyInstaller-sidecar app through OEM QA)

- Smart App Control blocked the *bundled sidecar binary*, not the installer — the
  app "installed fine" and then failed at first backend start. If a signed build
  still dies on a SAC machine, check the sidecar and any helper PEs first, not the
  installer signature.
- Reputation is per-identity *and* per-file: with an OV cert, expect the first
  signed release to still trip SmartScreen; it fades as downloads accrue under the
  same identity. Don't rotate identities — that resets the clock.
- Keep the unsigned path first-class forever (forks, scratch builds, contributors) —
  a build that *requires* credentials stops being reproducible by the community.
