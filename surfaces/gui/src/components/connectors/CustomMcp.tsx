import { useEffect, useState } from "react";
import { getI18n, useTranslation } from "react-i18next";
import {
  addMcpServer,
  connectMcp,
  deleteMcpServer,
  getMcpTools,
  patchMcpServer,
  signoutMcp,
  type McpServer,
} from "../../api";
import { relTime } from "../../providers/ProviderSetup";
import { Icon } from "../Icon";
import { Toggle } from "../Toggle";
import {
  CHIP_ERR,
  CHIP_OFF,
  CHIP_OK,
  CHIP_WARN,
  GRP,
  GRP_H,
  PILL_ACCENT,
  PILL_QUIET,
  ROW,
} from "./ui";

// Custom/BYO MCP servers on the Connectors page (UX-DECISIONS §21 + UX-034: the
// separate MCP tab is retired). They render as a "Custom · MCP" group at the end
// of the Connected section — grouped, not interleaved with first-party rows, so
// the user-supplied trust tier stays visible. Status never claims "Connected"
// for a stdio entry: Live = a connection is open right now; Ready = the one-time
// Test passed (subtitle carries "tested ⟨when⟩", persisted server-side).

// Curated OAuth quick-adds: remote MCP servers with browser sign-in (OAuth 2.1 +
// DCR) — no keys to paste, tokens stay in the local secret store.
// `blurb` is an i18n key, resolved at render time.
export const MCP_PRESETS: {
  name: string;
  label: string;
  blurb: string;
  config: Record<string, any>;
}[] = [
  {
    name: "granola",
    label: "Granola",
    blurb: "mcp.preset_granola_blurb",
    config: { type: "http", url: "https://mcp.granola.ai/mcp", auth: "oauth" },
  },
];

export function mcpChip(s: McpServer) {
  const t = getI18n().t;
  const isOauth = s.auth === "oauth";
  if (!s.enabled) return <span className={CHIP_OFF}>● {t("mcp.status_off")}</span>;
  if (s.status === "authorizing")
    return <span className={CHIP_WARN}>● {isOauth ? t("mcp.status_signing_in") : t("mcp.status_testing")}</span>;
  if (s.status === "connected") return <span className={CHIP_OK}>● {t("connector.live")}</span>;
  if (s.auth_hint || s.status === "needs_auth")
    return <span className={CHIP_WARN}>● {t("mcp.status_needs_sign_in")}</span>;
  if (s.status === "error") return <span className={CHIP_ERR}>● {t("mcp.status_error")}</span>;
  if (s.last_test_at) return <span className={CHIP_OK}>● {t("connector.ready")}</span>;
  return <span className={CHIP_OFF}>● {t("mcp.status_not_tested")}</span>;
}

export function mcpStatusLine(s: McpServer): string {
  const t = getI18n().t;
  const bits: string[] = [s.transport];
  if (s.status === "connected" && s.tool_count != null)
    bits.push(t("mcp.tool_count", { count: s.tool_count }));
  else if (s.transport === "http" && s.config?.url) {
    try {
      bits.push(new URL(s.config.url).host);
    } catch {
      /* leave the host off a malformed url */
    }
  }
  // Live servers show it too — the visible receipt that clicking Test did
  // something (it re-round-trips the connection and refreshes the tool count).
  if (s.last_test_at) {
    const rel = relTime(s.last_test_at);
    if (rel) bits.push(t("mcp.tested_rel", { rel }));
  }
  return bits.join(" · ");
}

/** Neutral square badge for custom servers (no vendor logo to show). */
function McpGlyph() {
  return (
    <span className="w-[34px] h-[34px] rounded-lg bg-paper border border-line flex items-center justify-center text-muted shrink-0">
      <Icon name="code" size={16} />
    </span>
  );
}

export function CustomMcpGroup({
  servers: serversProp,
  onOpen,
  onChanged,
}: {
  servers: McpServer[];
  onOpen: (name: string) => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const servers = serversProp;
  // A probe in flight ("Testing…" / "Signing in…") settles server-side within seconds,
  // but this page has no standing MCP poll — the chip froze on Testing forever
  // (owner-hit 2026-08-21, add-by-URL against a guarded server). While any row is
  // authorizing, poll the parent's refresh until every row settles.
  const anyAuthorizing = servers.some((s) => s.status === "authorizing");
  useEffect(() => {
    if (!anyAuthorizing) return;
    const t = setInterval(onChanged, 1000);
    return () => clearInterval(t);
  }, [anyAuthorizing, onChanged]);
  const presets = MCP_PRESETS.filter((p) => !servers.some((s) => s.name === p.name));
  if (servers.length === 0 && presets.length === 0) return null;

  return (
    <>
      <div className={GRP_H}>{t("mcp.group_custom")}</div>
      <div className={GRP} data-testid="custom-mcp-group">
        {servers.map((s) => (
          <button
            key={s.name}
            data-testid={`mcp-row-${s.name}`}
            className={ROW + " w-full text-left hover:bg-paper/60"}
            onClick={() => onOpen(s.name)}
          >
            <McpGlyph />
            <span className="min-w-0 flex-1">
              <span className="font-medium text-[13px]">{s.name}</span>
              <span className="block text-[12px] text-muted truncate">{mcpStatusLine(s)}</span>
            </span>
            {mcpChip(s)}
            <span className="text-faint text-[14px] shrink-0">›</span>
          </button>
        ))}
        {presets.map((p) => (
          <div key={p.name} className={ROW} data-testid={`mcp-preset-${p.name}`}>
            <McpGlyph />
            <span className="min-w-0 flex-1">
              <span className="font-medium text-[13px]">{p.label}</span>
              <span className="block text-[12px] text-muted truncate">{t(p.blurb)}</span>
            </span>
            <span
              className={PILL_QUIET + " cursor-pointer"}
              role="button"
              onClick={async () => {
                await addMcpServer(p.name, p.config);
                await connectMcp(p.name); // opens the browser sign-in right away
                onChanged();
              }}
            >
              {t("connector.connect")}
            </span>
          </div>
        ))}
      </div>
    </>
  );
}

// -- Add custom server (UX-033 two-tab form, in the page's modal chrome) --------

const EXAMPLE = `{
  "filesystem": {
    "command": "npx",
    "args": ["-y", "@modelcontextprotocol/server-filesystem", "/path/to/dir"],
    "enabled": true
  }
}`;

const INPUT =
  "w-full text-[13px] px-3 py-2 rounded-lg border border-line bg-paper text-ink outline-none focus:border-accent";

// A friendly default server name from its URL: walk the hostname's labels left to
// right, skip the generic ones (mcp/api/data/www…), take the first distinctive label
// (mcp.linear.app → "linear", data.dlai.link → "dlai"); fall back to the 2nd-level
// domain. The user can always overtype it.
const GENERIC_LABELS = new Set(["www", "mcp", "api", "data", "remote", "server", "agent", "app"]);
function nameFromUrl(raw: string): string {
  try {
    const host = new URL(raw).hostname.toLowerCase();
    const labels = host.split(".").filter(Boolean);
    if (labels.length < 2) return "";
    const candidates = labels.slice(0, -1); // drop the TLD
    const pick = candidates.find((l) => !GENERIC_LABELS.has(l)) || candidates[candidates.length - 1];
    return pick.replace(/[^a-z0-9-]/g, "");
  } catch {
    return "";
  }
}

export function AddMcpModal({
  onClose,
  onChanged,
}: {
  onClose: () => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [tab, setTab] = useState<"url" | "json">("url");
  const [name, setName] = useState("");
  const [url, setUrl] = useState("");
  const [text, setText] = useState(EXAMPLE);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const saveUrl = async () => {
    setError(null);
    const n = name.trim();
    const u = url.trim();
    if (!n) {
      setError(t("mcp.err_name"));
      return;
    }
    if (!/^https?:\/\/\S+$/.test(u)) {
      setError(t("mcp.err_url"));
      return;
    }
    await addMcpServer(n, { type: "http", url: u });
    // Probe anonymously right away — the row shows Testing…, then Live, an
    // error, or Needs sign-in (401 → the OAuth switch on the detail page).
    await connectMcp(n);
    onChanged();
    onClose();
  };

  const saveJson = async () => {
    setError(null);
    let parsed: any;
    try {
      parsed = JSON.parse(text);
    } catch (e: any) {
      setError(t("mcp.err_invalid_json", { message: e.message }));
      return;
    }
    // Accept either {mcpServers:{...}}, {name:{...}}, or a single bare config.
    const map = parsed.mcpServers || parsed;
    const entries =
      map && typeof map === "object" && !map.command && !map.url ? Object.entries(map) : null;
    if (!entries || entries.length === 0) {
      setError(t("mcp.err_json_shape"));
      return;
    }
    for (const [n, config] of entries) {
      await addMcpServer(n, config as Record<string, any>);
    }
    onChanged();
    onClose();
  };

  const tabBtn = (active: boolean) =>
    "text-[12px] px-2.5 py-1 rounded-md border shrink-0 " +
    (active ? "border-accent text-accent font-medium" : "border-line text-muted hover:text-ink");

  return (
    <div className="fixed inset-0 z-40" data-testid="add-mcp-modal">
      <div className="absolute inset-0 bg-black/30" onClick={onClose} />
      <div className="absolute left-1/2 top-24 -translate-x-1/2 w-[540px] max-w-[92vw] rounded-xl2 border border-line bg-panel shadow-xl p-5 space-y-3">
        <div className="flex items-center justify-between">
          <div className="text-[14px] font-semibold">{t("mcp.add_title")}</div>
          <button className="text-faint hover:text-ink text-[16px] leading-none" onClick={onClose}>
            ×
          </button>
        </div>
        <div className="flex items-center gap-1.5">
          <button className={tabBtn(tab === "url")} onClick={() => setTab("url")} data-testid="mcp-add-tab-url">
            {t("mcp.tab_remote_url")}
          </button>
          <button className={tabBtn(tab === "json")} onClick={() => setTab("json")} data-testid="mcp-add-tab-json">
            {t("mcp.tab_json")}
          </button>
        </div>
        {tab === "url" ? (
          <>
            <div className="text-[13px] text-muted">{t("mcp.add_url_blurb")}</div>
            <input
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder={t("mcp.add_name_ph")}
              spellCheck={false}
              className={INPUT}
              data-testid="mcp-add-name"
            />
            <input
              value={url}
              onChange={(e) => {
                const u = e.target.value;
                setUrl(u);
                // Prefill the name once the URL looks real — never overwrite typing.
                if (!name.trim()) setName(nameFromUrl(u));
              }}
              placeholder="https://mcp.example.com/mcp"
              spellCheck={false}
              className={INPUT + " font-mono text-[12px]"}
              data-testid="mcp-add-url"
            />
          </>
        ) : (
          <>
            <div className="text-[13px] text-muted">{t("mcp.add_json_blurb")}</div>
            <textarea
              value={text}
              onChange={(e) => setText(e.target.value)}
              spellCheck={false}
              rows={9}
              className="w-full font-mono text-[12px] px-3 py-2.5 rounded-lg border border-line bg-paper text-ink outline-none focus:border-accent resize-y"
            />
          </>
        )}
        <div className="flex items-center gap-3">
          <button className={PILL_ACCENT} onClick={tab === "url" ? saveUrl : saveJson}>
            {tab === "url" ? t("mcp.add_and_test") : t("manage.add_btn")}
          </button>
          <button className="text-[13px] text-muted hover:text-ink" onClick={onClose}>
            {t("manage.cancel")}
          </button>
        </div>
        {error && <div className="text-[13px] text-danger">{error}</div>}
      </div>
    </div>
  );
}

// -- Detail subpage (§21): tools, Test, config, error excerpt, remove -----------

export function McpServerDetail({
  server,
  onChanged,
  onGone,
}: {
  server: McpServer;
  onChanged: () => void;
  onGone: () => void;
}) {
  const { t } = useTranslation();
  const [tools, setTools] = useState<{ name: string; description: string }[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [toolErr, setToolErr] = useState<string | null>(null);

  const isOauth = server.auth === "oauth";
  const authorizing = server.status === "authorizing";

  const runTest = async () => {
    await connectMcp(server.name);
    onChanged();
    // The connect runs as a background task; if the first refresh outpaced its
    // start, the chip never shows Testing and the page's poll misses the flip.
    window.setTimeout(onChanged, 600);
  };
  // Anonymous connect came back 401/403: the fix is sign-in, so switch the entry
  // to OAuth (DCR — nothing to register) and start the browser flow right away.
  const signInWithOauth = async () => {
    await patchMcpServer(server.name, { auth: "oauth" });
    await connectMcp(server.name);
    onChanged();
  };

  const loadTools = async () => {
    if (tools) {
      setTools(null);
      return;
    }
    setBusy(true);
    setToolErr(null);
    const res = await getMcpTools(server.name);
    setBusy(false);
    if (res.ok) setTools(res.tools);
    else setToolErr(res.error || t("mcp.err_failed_connect"));
  };

  return (
    <div className="space-y-4" data-testid={`mcp-detail-${server.name}`}>
      <div className="flex items-center gap-3">
        <McpGlyph />
        <div className="flex-1 min-w-0">
          <div className="text-[16px] font-semibold">{server.name}</div>
          <div className="text-[12px] text-muted">{mcpStatusLine(server)}</div>
        </div>
        {mcpChip(server)}
      </div>

      <div className={GRP}>
        <div className={ROW}>
          <span className="text-[13px] flex-1">{t("persona.enabled")}</span>
          <Toggle
            checked={server.enabled}
            onChange={async () => {
              await patchMcpServer(server.name, { enabled: !server.enabled });
              onChanged();
            }}
            title={t("mcp.enable_title")}
          />
        </div>
        <div className={ROW}>
          <span className="text-[13px] flex-1">
            {t("mcp.test_connection")}
            <span className="block text-[12px] text-faint">{t("mcp.test_desc")}</span>
          </span>
          {server.auth_hint && !isOauth ? (
            <span
              className={PILL_ACCENT + " cursor-pointer"}
              role="button"
              onClick={signInWithOauth}
              data-testid={`mcp-authfix-${server.name}`}
            >
              {t("gallery.sign_in")}
            </span>
          ) : isOauth && server.status === "needs_auth" ? (
            <span
              className={PILL_ACCENT + " cursor-pointer"}
              role="button"
              onClick={runTest}
              data-testid={`mcp-signin-${server.name}`}
            >
              {t("gallery.sign_in")}
            </span>
          ) : (
            <span
              className={PILL_QUIET + " cursor-pointer" + (authorizing ? " opacity-50" : "")}
              role="button"
              onClick={authorizing ? undefined : runTest}
              data-testid={`mcp-test-${server.name}`}
            >
              {authorizing ? t("mcp.status_testing") : t("provider.test_btn")}
            </span>
          )}
        </div>
        {server.last_error && server.status !== "connected" && (
          <div className="px-4 py-2.5 text-[13px] text-danger break-words">
            {server.last_error}
          </div>
        )}
        <div className={ROW}>
          <span className="text-[13px] flex-1">{t("available.tools")}</span>
          <button className="text-[13px] text-muted hover:text-ink" onClick={loadTools} disabled={busy}>
            {busy ? "…" : tools ? t("mcp.hide") : t("mcp.show")}
          </button>
        </div>
        {toolErr && <div className="px-4 py-2.5 text-[13px] text-danger">{toolErr}</div>}
        {tools && (
          <div className="px-4 py-3 flex flex-wrap gap-1.5">
            {tools.length === 0 && <div className="text-[12px] text-faint">{t("manage.mcp_no_tools")}</div>}
            {tools.map((t) => (
              <span
                key={t.name}
                title={t.description}
                className="font-mono text-[12px] px-1.5 py-0.5 rounded-md bg-paper border border-line"
              >
                {t.name}
              </span>
            ))}
          </div>
        )}
      </div>

      <div className={GRP}>
        <div className="px-4 py-3">
          <div className="text-[12px] font-semibold text-muted mb-1.5">{t("mcp.configuration")}</div>
          <pre className="font-mono text-[12px] text-muted whitespace-pre-wrap break-all">
            {JSON.stringify(server.config, null, 2)}
          </pre>
        </div>
      </div>

      <div className="flex items-center gap-4">
        {isOauth && server.status === "connected" && (
          <button
            className="text-[13px] text-muted hover:text-ink"
            onClick={async () => {
              await signoutMcp(server.name);
              onChanged();
            }}
            data-testid={`mcp-signout-${server.name}`}
          >
            {t("sidebar.sign_out")}
          </button>
        )}
        <button
          className="text-[13px] text-danger/80 hover:text-danger"
          onClick={async () => {
            await deleteMcpServer(server.name);
            onChanged();
            onGone();
          }}
          data-testid={`mcp-remove-${server.name}`}
        >
          {t("mcp.remove_server")}
        </button>
      </div>
    </div>
  );
}
