# Nexus fork releases

The app uses only `Pol-Lanski/openworker` for updates. The checked-in public key
is intentionally empty: update checks return no update, and download/install
commands refuse to proceed. Manual installers remain usable. No private key was
generated and no existing signing material was read or changed.

## Enable upstream integration after reviewing this change

1. Keep the full implementation on `codex/nexus-provider`. Commit any intended
   existing local Nexus compatibility changes separately before integrating upstream.
2. Bootstrap **only** `.github/workflows/sync-upstream.yml` and the
   `workflow_dispatch` trigger in the existing `ci.yml` onto the fork's default
   branch (`main`) through a reviewed PR. Do not copy the fork-policy CI step to
   main unless its script and fork configuration are also present. Scheduled
   workflows run from the default branch; the detector explicitly checks out the
   Nexus branch. Do not replace main with upstream or push to upstream.
3. In fork Actions settings allow Actions to create pull requests, and set
   repository variable `NEXUS_UPSTREAM_SYNC_ENABLED=true`. Until then both manual
   and scheduled runs are deliberately inert. No setting has been activated here.
4. Dispatch **Detect upstream release** once and inspect its PR and CI run. The
   normal daily schedule is 06:23 UTC. The detector queries the latest published
   stable upstream release, fetches its tag and records the resolved commit SHA.
   Repeated runs reuse the same PR branch; a retag gets a separate branch. Failed
   merges are aborted without pushing or opening a partial PR. Failures appear in
   Actions; configure GitHub Actions failure notifications as desired.
5. Require successful backend, GUI unit/typecheck, GUI e2e and fork-policy checks
   before manually merging the integration PR. Configure branch protection on
   `codex/nexus-provider` accordingly. No automatic merge is configured. The
   detector explicitly dispatches `ci.yml` on the candidate branch, because
   `GITHUB_TOKEN`-created pushes/PRs cannot be assumed to start unattended checks.
   If dispatch fails, the PR remains reviewable but must not be merged without CI.

The bot merges the release **commit**, not current upstream main. It records
`.github/nexus-upstream.json` with tag and SHA. Git conflicts or fork policy
regressions require human resolution; neither is silently overwritten. The script
must only run in a disposable clean checkout. It pushes normally, never forcibly.
A base branch change after CI requires refreshing the PR and rerunning checks.

## Build installers after integration

Use the existing Release workflow on the reviewed merged commit (or an `app-v*`
tag on that commit). This produces macOS Apple Silicon, Intel, and Windows
workflow artifacts without publishing. The workflow keeps the seven-day npm
quarantine and strict macOS bundle signature check. An upstream dependency newer
than seven days intentionally blocks the build: wait until it ages, then rerun.
Do not bypass the quarantine. Artifact downloads expire; GitHub Release assets
are the durable distribution route.

Build/PR automation does not alone produce signed automatic updates. A reviewed
`v*` tag creates a **draft** release, and must exactly match the app config version.
The release workflow currently runs separately from CI: tag only a commit whose
CI has passed. Publishing is a separate deliberate action.

## Configure automatic updates

Create a dedicated Tauri updater signing key pair using the official Tauri signer
in a secure environment. Commit only its public key to
`surfaces/gui/src-tauri/tauri.conf.json` (`plugins.updater.pubkey`). Store the
private key as fork Actions secret `TAURI_SIGNING_PRIVATE_KEY`; store its password
as `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` if protected. Back up the private key
securely; do not use the upstream key or rotate casually. The release preflight
rejects signing with an empty public key. Verify the generated artifacts using
the matching public key before publication; configuration preflight does not
prove the private and public keys match.

Bump the app version to a strictly increasing SemVer before tagging. In particular,
`0.2.1-nexus.1` is lower than an already installed stable `0.2.1`; do not rely on
such a suffix to migrate installations. Choose a release version after integrating
upstream, and keep the `v` tag and Tauri version identical. No version was invented
by this change. Publish only after installer and signed updater validation.

The existing manifest generator uses the current fork repository automatically
and pins artifact URLs to the release tag. A published release must include
`latest.json`, the platform updater artifacts, and their `.sig` files. A DMG alone
is a manual download. A draft release is not visible through the public latest
release endpoint. Require every intended platform before publication: the manifest
script permits missing platforms, so review the manifest explicitly.

Users who already installed the official app must install the fork once manually
to acquire its endpoint and public key. Old fork builds also retain upstream's
channel. After migration, future signed fork releases can update the fork.

Apple signing is independent of Tauri update signing. Ad-hoc macOS builds remain
possible but may require Gatekeeper intervention. For notarized distribution,
configure `APPLE_CERTIFICATE`, `APPLE_CERTIFICATE_PASSWORD`,
`APPLE_SIGNING_IDENTITY`, `APPLE_API_KEY_CONTENT`, `APPLE_API_KEY`, and
`APPLE_API_ISSUER`. Windows Authenticode is not configured by this workflow.

References: [GitHub workflow triggering](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)
and [Tauri updater signing](https://v2.tauri.app/plugin/updater/).
