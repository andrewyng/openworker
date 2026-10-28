# coworker GUI (React + Tauri)

A thin client of the coworker server (OpenAI-compatible API + WS event/approval stream).
Same codebase runs in a browser (dev) and as the OpenWorker desktop app.

## First time: bootstrap the Python backend

A fresh checkout has no server to run — create the venv both flows below expect
(from the repo root):

```bash
bash packaging/setup_dev_env.sh   # → .venv (server + aisuite)
```

## Run it (browser, two terminals)

1. **Start the server** (needs a model key, e.g. `OPENAI_API_KEY`, in the environment —
   or add one later in the app's Settings), from the repo root:
   ```bash
   ./.venv/bin/openworker-server --cwd /path/to/your/project --port 8765
   ```
2. **Start the UI:**
   ```bash
   cd surfaces/gui
   npm install      # first time
   npm run dev      # → http://localhost:5173
   ```

Open http://localhost:5173. The UI talks to `http://127.0.0.1:8765` (override with
`VITE_COWORKER_HTTP` / `VITE_COWORKER_WS`). Start the server before Vite so the
UI can read its per-launch token from `<state-dir>/sidecar-8765.token`; restart
Vite if the server is restarted.

## Run the desktop app from source

The Tauri shell wraps the same UI and supervises the Python server itself — no separate
terminal. It needs the Rust toolchain (`rustup`) plus the venv from the bootstrap step;
in dev it finds the server at `.venv/bin/openworker-server` automatically (a
packaged sidecar binary is only produced by the release scripts in `packaging/`).

```bash
cd surfaces/gui
npm install        # first time
npm run tauri dev  # builds the shell, launches the window, starts the server
```

## Localization

The GUI uses i18next JSON resources in `src/locales/`, not gettext PO files.
Settings > General offers English, Simplified Chinese (`zh`, the existing saved
preference), Traditional Chinese (Taiwan, `zh-TW`), and Follow system.
System locales with `Hant`, or the TW/HK/MO regions, use the Taiwan translation;
an explicit `Hans` script uses Simplified Chinese even with a TW region.
Unsupported languages fall back to English. HK/MO currently share the Taiwan
translation; this is not a separate Hong Kong or Macau localization.

Use Taiwan terminology in `zh-TW.json`, for example 文件 → 檔案, 文件夹 → 資料夾,
保存 → 儲存, 默认 → 預設, 设置 → 設定, 服务器 → 伺服器, 密钥 → 金鑰, and
令牌 → 權杖. Preserve resource keys, interpolation placeholders, commands, URLs,
and product names. Translate ambiguous terms in context: Undo is 復原, while
revoking permission is 撤銷. Both Chinese resources omit English-only `_one`
plural variants. Locale contract tests check completeness and interpolation.

## Tests

```bash
npx tsc --noEmit && npx vitest run   # typecheck + unit
npx playwright test                  # hermetic e2e (mocked /v1 + WS, no Python needed)
```
