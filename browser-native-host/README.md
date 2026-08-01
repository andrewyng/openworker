# OpenWorker Chrome native host

`com.openworker.browser` is the local, exact-origin trust boundary between the
OpenWorker Chrome extension and the desktop sidecar. Chrome starts this executable
only for extension `djnbhkmnbmjobnphflaopcpfkifbgekl`; the executable independently
checks the origin argument before reading any message.

## Runtime descriptor

The desktop shell atomically writes a private descriptor while it is running:

```json
{
  "version": 1,
  "server_url": "http://127.0.0.1:50300",
  "api_token": "the-per-launch-sidecar-token",
  "pid": 12345,
  "expires_at": 0
}
```

The path is `~/.config/coworker/browser-native-host.json` on macOS and
`%APPDATA%\coworker\browser-native-host.json` on Windows. `expires_at: 0` means
process-lifetime: the descriptor remains valid only while `pid` is alive. A nonzero
value is an additional Unix-time expiry. On Unix the host rejects symlinks,
non-regular files, and files with any group/other permission bits.

The host accepts only numeric loopback HTTP, never accepts a URL or credential from
the extension, keeps both the desktop launch token and bridge session token out of
the extension process, and proxies only the closed command-transport allowlist.

## Desktop/server integration hooks

1. At desktop launch, write the descriptor mode `0600` after choosing the sidecar
   port and launch token; remove it at normal shutdown. Atomic replacement plus the
   live-PID check makes a crash-stale descriptor fail closed.
2. Run the bundled host once with `--install` at app startup/update. It writes the
   exact-origin Chrome manifest (and the required HKCU registry value on Windows).
3. Add an app-authenticated `POST /v1/browser-extension/native/connect`. It must
   require `X-OpenWorker-Token`, require `transport == "native_messaging"`, require
   the exact extension ID above, create a Chrome bridge session without a pairing
   challenge, and return `session_id` plus `session_token`. The native host removes
   `session_token` before replying to Chrome.
4. Keep the existing bearer-authenticated `poll`, `results`, `events`, and
   `disconnect` endpoints. No extension CORS or localhost host permission is needed.

The extension still requires an explicit user click on **Share this tab** before it
attaches `chrome.debugger`; native connection never grants a tab automatically.
