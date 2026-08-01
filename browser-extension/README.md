# OpenWorker Browser Control extension

This Manifest V3 extension is the trust boundary for controlling tabs in the
user's existing Chrome profile. It is separate from OpenWorker's isolated in-app
browser.

## Safety model

- Chrome Native Messaging connects automatically to the signed desktop app. There
  is no bridge URL, pairing code, localhost host permission, or browser-visible
  bearer token.
- The stable unpacked-development extension ID is
  `djnbhkmnbmjobnphflaopcpfkifbgekl`. The native host manifest permits only that
  exact extension origin, and the native executable checks Chrome's origin
  argument independently.
- A user must click **Share this tab** in the extension popup for every tab. Native
  connection never attaches a tab, and there is no remote attach command.
- The `ON` badge is visible for every attached tab.
- Agent commands are a closed allowlist: tabs, snapshot, screenshot, click, fill,
  keypress, scroll, and internal live-target inspection. Arbitrary CDP is not
  exposed.
- Editable values are reduced to `empty` / `non-empty` in snapshots. Document
  and URL identity are revalidated before every ref-scoped action.
- Mutating request IDs are journaled before execution and never replayed after
  an unknown outcome. Consequential targets are reclassified from the live DOM
  and require the exact confirmation binding at input-dispatch time.
- Only shared tabs are returned by the `tabs` command.

Chrome displays its own debugger infobar while a tab is attached. That
browser-controlled disclosure must not be hidden.

## Local development

1. Build and launch OpenWorker once so it registers `com.openworker.browser`.
2. Open `chrome://extensions` and enable Developer mode.
3. Choose **Load unpacked** and select the bundled `browser-extension` directory:
   - macOS installed app:
     `/Applications/OpenWorker.app/Contents/Resources/browser-extension`
   - Windows installed app:
     `<OpenWorker installation directory>\browser-extension`
   - Source checkout: this `browser-extension` directory at the repository root.
4. Open the extension popup on a normal web tab and choose **Share this tab**.

The manifest's embedded public key makes the unpacked extension ID deterministic.
A future Chrome Web Store listing must be created with the corresponding extension
identity (or update the key, native-host allowlist, and ID-contract test together).

## Native transport contract

The extension opens `chrome.runtime.connectNative("com.openworker.browser")` and
sends versioned request envelopes:

```json
{
  "version": 1,
  "id": "opaque-request-id",
  "type": "connect|poll|results|events|disconnect",
  "payload": {}
}
```

The native host reads the private desktop runtime descriptor, authenticates to the
random-port sidecar, and calls:

| Request | Server endpoint | Authentication |
| --- | --- | --- |
| `connect` | `POST /v1/browser-extension/native/connect` | desktop `X-OpenWorker-Token` |
| `poll` | `POST /v1/browser-extension/poll` | bridge bearer held by native host |
| `results` | `POST /v1/browser-extension/results` | bridge bearer held by native host |
| `events` | `POST /v1/browser-extension/events` | bridge bearer held by native host |
| `disconnect` | `POST /v1/browser-extension/disconnect` | bridge bearer held by native host |

The connect response's session token never crosses into the extension. The full
desktop/server integration contract is documented in
`../browser-native-host/README.md`.
