import { useEffect, useRef, useState } from "react";
import { getI18n, useTranslation } from "react-i18next";
import {
  addMcpServer,
  connectMcp,
  convertMcpTrust,
  deleteMcpServer,
  getMcpTools,
  getMcpTrust,
  patchMcpServer,
  revealMcpConfig,
  revokeMcpTrust,
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
  TAG_QUIET,
} from "./ui";
import { SavedTick, ToolRow, ToolsCountLine, useSavedTick } from "./ToolReview";

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
  // One healthy word (owner call 2026-08-30): connected and merely-tested both
  // read Ready — problems get their own chips; the plumbing is not the user's.
  if (s.status === "connected") return <span className={CHIP_OK}>● {t("connector.ready")}</span>;
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
  // Not-yet-added preset OFFERS render among the AVAILABLE connectors (McpPresetRows),
  // never here: a row in this group means "a server you own". When Remove used to make
  // the offer reappear in this same section, it was indistinguishable from a server
  // that survived removal (owner catch 2026-08-30).
  if (servers.length === 0) return null;

  return (
    <>
      {/* Every group header counts its own rows (owner rule 2026-08-30) —
          matching "Connected · N". Ownership count, not health: a needs-sign-in
          server still lists (and counts) here; the per-row chips carry health. */}
      <div className={GRP_H + " flex items-baseline gap-2"}>
        <span>{t("mcp.group_custom", { count: servers.length })}</span>
        {/* ONE common window into the file all servers share (owner call
            2026-08-30): reveal mcp.json in the file manager — never auto-open,
            the default app for .json is a lottery across machines. */}
        <button
          className="font-normal normal-case tracking-normal text-faint hover:text-ink"
          title={t("mcp.config_reveal_tip")}
          data-testid="mcp-config-reveal"
          onClick={() => void revealMcpConfig()}
        >
          {t("mcp.config_reveal")}
        </button>
      </div>
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
      </div>
    </>
  );
}

/** The curated quick-add offers that are NOT yet added, filtered by the page's search. */
export function mcpPresetOffers(servers: McpServer[], query: string) {
  const q = query.trim().toLowerCase();
  return MCP_PRESETS.filter(
    (p) =>
      !servers.some((s) => s.name === p.name) &&
      (!q || p.label.toLowerCase().includes(q) || p.name.includes(q)),
  );
}

// Preset offer rows for the AVAILABLE group. Connect creates the entry, starts the
// browser sign-in, and navigates STRAIGHT to the detail page — so the connect-time
// tool review (OPE-136 §3) happens while the user is right there. Skipping that
// navigation left the server listless: every tool, including ones the server adds
// later, reached sessions until someone happened to open the detail page.
export function McpPresetRows({
  presets,
  onOpen,
  onChanged,
}: {
  presets: ReturnType<typeof mcpPresetOffers>;
  onOpen: (name: string) => void;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  return (
    <>
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
              onOpen(p.name); // land on the detail page: the tool review awaits there
            }}
          >
            {t("connector.connect")}
          </span>
        </div>
      ))}
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
  onAdded,
}: {
  onClose: () => void;
  onChanged: () => void;
  // OPE-136 §3: land the user on the server's detail page right after adding — the
  // connect moment is when the tool review has their attention.
  onAdded?: (name: string) => void;
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
    onAdded?.(n);
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
    // A single pasted server gets the same review moment; a bulk paste lands on the
    // list, where each row is one click from its review.
    if (entries.length === 1) onAdded?.(String(entries[0][0]));
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

// -- Connect-time tool review (OPE-136 §3: the existence lever) ---------------------
// One checkbox per tool the server OFFERS. Checked names are written to the entry's
// `include_tools` in mcp.json — an unchecked tool is never registered into a session:
// the model never sees its name or schema. Because include_tools is an include-list,
// tools the server ships later arrive UNCHECKED with a "new" badge (fail-closed
// growth). Descriptions are the server's own words — displayed as quotes, never
// interpreted. First review (no include_tools yet): everything starts checked, and
// saving writes the full list to lock the fail-closed property in.
export function McpToolReview({
  server,
  onChanged,
}: {
  server: McpServer;
  onChanged: () => void;
}) {
  const { t } = useTranslation();
  const [offered, setOffered] = useState<{ name: string; description: string }[] | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [checked, setChecked] = useState<Set<string> | null>(null);
  const [saving, setSaving] = useState(false);
  // OPE-136 §4/§5: standing trust — which tools don't ask, and whether the legacy
  // server-wide flag is still in the config (shown loud, with the migration button).
  const [trust, setTrust] = useState<{ tools: string[]; legacy: boolean } | null>(null);
  const [converting, setConverting] = useState(false);

  const loadTrust = async () => {
    const res = await getMcpTrust(server.name);
    if (res.ok) setTrust({ tools: res.tools, legacy: res.legacy_dont_ask });
  };
  useEffect(() => {
    void loadTrust();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [server.name]);

  const included: string[] | undefined = Array.isArray(server.config?.include_tools)
    ? server.config.include_tools.map(String)
    : undefined;
  const firstReview = included === undefined;
  // The DECLINED list (owner catch 2026-08-30): with only an include list, "you
  // reviewed this and said no" and "the server added this since your review" are
  // indistinguishable — both merely absent — so unchecking a tool earned it a
  // "new" badge. exclude_tools (already honored, subtractively, at wiring) records
  // the decline; "new" is now ONLY a name neither list has ever seen.
  const excluded: string[] = Array.isArray(server.config?.exclude_tools)
    ? server.config.exclude_tools.map(String)
    : [];

  const load = async () => {
    setBusy(true);
    setErr(null);
    const res = await getMcpTools(server.name);
    setBusy(false);
    if (!res.ok) {
      setErr(res.error || t("mcp.err_failed_connect"));
      return;
    }
    setOffered(res.tools);
    // First review: all checked. Later: the saved list, so anything the server
    // added since arrives unchecked.
    setChecked(new Set(firstReview ? res.tools.map((tool) => tool.name) : included));
  };

  // The review moment: a connected server's list loads without another click. For
  // anything else (needs sign-in, stdio not yet spawned) loading would trigger a
  // connect, so it stays behind the button.
  useEffect(() => {
    if (server.status === "connected" && offered === null && !busy && !err) void load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [server.status]);

  const dirty =
    offered !== null &&
    checked !== null &&
    (firstReview ||
      included.length !== checked.size ||
      included.some((n) => !checked.has(n)));

  // Post-review toggles auto-save (owner ask 2026-08-30) — a brief "Saved" tick
  // is the receipt. The FIRST review keeps its explicit button: that save is the
  // consent moment that creates the include list and locks in fail-closed growth.
  const [savedTick, flashTick] = useSavedTick();
  const persist = async (next: Set<string>, declined: string[]) => {
    if (!offered) return;
    setSaving(true);
    await patchMcpServer(server.name, {
      include_tools: offered.map((tool) => tool.name).filter((n) => next.has(n)),
      exclude_tools: declined,
    });
    setSaving(false);
    onChanged();
  };

  const save = async () => {
    // The first-review ceremony reviews the WHOLE visible list: everything
    // unchecked at the moment of "Keep these tools" is an explicit decline.
    if (!checked || !offered) return;
    await persist(
      checked,
      offered.map((tool) => tool.name).filter((n) => !checked.has(n)),
    );
  };

  const toggle = (name: string) => {
    if (!checked) return;
    const next = new Set(checked);
    const nowChecked = !next.has(name);
    if (nowChecked) next.add(name);
    else next.delete(name);
    setChecked(next);
    if (!firstReview) {
      // Delta, not a recompute: only the TOGGLED tool moves between lists. A
      // whole-list recompute would sweep un-acknowledged server-new tools into
      // the declined list on any unrelated toggle, silently killing their badge.
      const declined = nowChecked
        ? excluded.filter((n) => n !== name)
        : excluded.includes(name)
          ? excluded
          : [...excluded, name];
      void persist(next, declined).then(flashTick);
    }
  };

  return (
    <div className={GRP} data-testid={`mcp-tools-review-${server.name}`}>
      {/* The legacy server-wide don't-ask flag, finally VISIBLE (finding 3) — with the
          one-click migration to named, bounded, per-tool trust rules. */}
      {trust?.legacy && (
        <div
          className="px-4 py-3 flex items-start gap-2.5 border-b border-line"
          data-testid={`mcp-legacy-warn-${server.name}`}
        >
          <span className="text-warnInk shrink-0">
            <Icon name="warning" size={15} />
          </span>
          <span className="min-w-0 flex-1 text-[13px]">
            <span className="font-medium block">{t("mcp.legacy_warn_title")}</span>
            <span className="block text-[12px] text-muted">{t("mcp.legacy_warn_body")}</span>
          </span>
          <span
            className={PILL_ACCENT + " cursor-pointer shrink-0" + (converting ? " opacity-50" : "")}
            role="button"
            data-testid={`mcp-legacy-convert-${server.name}`}
            onClick={
              converting
                ? undefined
                : async () => {
                    setConverting(true);
                    const res = await convertMcpTrust(server.name);
                    setConverting(false);
                    if (res.ok) {
                      await loadTrust();
                      onChanged();
                    }
                  }
            }
          >
            {converting ? "…" : t("mcp.legacy_convert")}
          </span>
        </div>
      )}
      {(() => {
        const trustCount = trust?.tools.length ?? 0;
        // The reviewed list collapses to the same quiet summary row the connector
        // pages use (owner ask 2026-08-30: one dialect) — the first review stays
        // forced open, because the ceremony IS the open list.
        const reviewed = !firstReview && offered !== null && checked !== null;

        const finePrint = offered && checked && (
          <div className="px-4 pb-1.5">
            <ToolsCountLine
              checked={checked.size}
              total={offered.length}
              // The details summary above already carries the count.
              showCount={!reviewed}
              extra={
                <>
                  {" · "}
                  {t("mcp.tools_growth_note")}
                  {/* MCP rows carry no per-row risk chips (a server's word is not
                      evidence) — the blanket truth is stated ONCE instead. The
                      always-allowed count lives in the summary row, not here. */}
                  {" · "}
                  {t("tools.mcp_all_ask")}
                </>
              }
            />
          </div>
        );

        const errLine = err && (
          <div className="px-4 py-2.5 text-[13px] text-danger">{err}</div>
        );

        const list = offered && checked && (
          <div className="py-1 max-h-[320px] overflow-y-auto hairline-scroll">
            {offered.length === 0 && (
              <div className="text-[12px] text-faint px-4 py-1">{t("manage.mcp_no_tools")}</div>
            )}
            {offered.map((tool) => {
              // "new" = the SERVER's menu grew: a name neither list has ever seen.
              // A tool you declined sits on the exclude list — absent, but not new.
              const isNew =
                !firstReview &&
                !included.includes(tool.name) &&
                !excluded.includes(tool.name);
              return (
                <ToolRow
                  key={tool.name}
                  checked={checked.has(tool.name)}
                  checkboxTestId={`mcp-tool-check-${tool.name}`}
                  onToggle={() => toggle(tool.name)}
                  // Raw mono name, deliberately: the exact string IS the security
                  // identity (trust rules and collision defenses key on it).
                  name={tool.name}
                  mono
                  badge={
                    isNew ? (
                      <span
                        className="ml-1.5 text-[11px] px-1.5 rounded-full bg-warnSoft text-warnInk"
                        data-testid={`mcp-tool-new-${tool.name}`}
                      >
                        {t("mcp.tools_new_badge")}
                      </span>
                    ) : undefined
                  }
                  description={
                    tool.description ? (
                      <span className="block text-[12px] text-muted truncate" title={tool.description}>
                        “{tool.description}”
                      </span>
                    ) : undefined
                  }
                  // Right-aligned approval status (owner ask 2026-08-30): existence
                  // lives on the left (checkbox), granted authority on the right.
                  // Neutral chip, never success-colored. Absence of a chip IS the
                  // default ("asks each time"), so untrusted rows stay quiet. An
                  // unchecked-but-trusted tool keeps a dimmed chip: standing
                  // authority never disappears from the screen that audits it.
                  right={
                    trust?.tools.includes(tool.name) ? (
                      <span
                        className={
                          "flex items-center gap-1.5 shrink-0 self-start mt-0.5" +
                          (checked.has(tool.name) ? "" : " opacity-50")
                        }
                        data-testid={`mcp-tool-trusted-${tool.name}`}
                      >
                        <span
                          className={TAG_QUIET}
                          title={
                            checked.has(tool.name)
                              ? t("mcp.trust_tooltip")
                              : t("mcp.trust_idle_tooltip")
                          }
                        >
                          {t("mcp.trust_marker")}
                        </span>
                        <button
                          className="text-[11px] underline text-muted hover:text-danger"
                          data-testid={`mcp-tool-revoke-${tool.name}`}
                          onClick={async (e) => {
                            e.preventDefault();
                            await revokeMcpTrust(server.name, tool.name);
                            await loadTrust();
                          }}
                        >
                          {t("mcp.trust_revoke")}
                        </button>
                      </span>
                    ) : undefined
                  }
                />
              );
            })}
          </div>
        );

        if (reviewed)
          return (
            <details>
              <summary className={ROW + " cursor-pointer hover:bg-paper/60 list-none [&::-webkit-details-marker]:hidden"}>
                <span className="text-[13px] text-muted w-24 shrink-0">
                  {t("connector.tools_label")}
                </span>
                <span className="min-w-0 flex-1 text-[13px] text-muted">
                  {t("tools.enabled_count", { checked: checked!.size, total: offered!.length })}
                  {/* Granted authority stays advertised even while collapsed. */}
                  {trustCount > 0 && <> · {t("mcp.trust_count", { count: trustCount })}</>}
                </span>
                <SavedTick show={savedTick} testId={`mcp-tools-saved-${server.name}`} />
              </summary>
              {finePrint}
              {errLine}
              {list}
            </details>
          );

        return (
          <>
            <div className={ROW}>
              <span className="text-[13px] flex-1">
                {t("available.tools")}
                {offered && checked && (
                  <ToolsCountLine
                    checked={checked.size}
                    total={offered.length}
                    extra={
                      <>
                        {" · "}
                        {t("mcp.tools_growth_note")}
                      </>
                    }
                  />
                )}
              </span>
              {offered === null && (
                <button
                  className="text-[13px] text-muted hover:text-ink"
                  onClick={load}
                  disabled={busy}
                  data-testid={`mcp-tools-load-${server.name}`}
                >
                  {busy ? "…" : t("mcp.show")}
                </button>
              )}
              {dirty && firstReview && (
                <button
                  className={PILL_ACCENT + " cursor-pointer"}
                  onClick={save}
                  disabled={saving}
                  data-testid={`mcp-tools-save-${server.name}`}
                >
                  {saving ? "…" : t("mcp.tools_keep")}
                </button>
              )}
            </div>
            {errLine}
            {list}
          </>
        );
      })()}
    </div>
  );
}

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

  const isOauth = server.auth === "oauth";
  // LOCAL testing state (owner catch 2026-08-30): the server reports "connected"
  // ahead of "authorizing", so re-testing a HEALTHY server never flips the status
  // this page used to watch — the button looked dead. Track the click ourselves:
  // disabled + "Testing…" until the receipt (last_test_at) or an error moves,
  // with a timeout backstop so a wedged probe can't disable the button forever.
  const [testing, setTesting] = useState(false);
  // The green result line is TRANSIENT (owner call): it announces the completion,
  // then leaves — the durable "tested Nm ago" receipt lives in the header.
  const [freshResult, setFreshResult] = useState(false);
  const lastSeen = useRef({ at: server.last_test_at, err: server.last_error });
  useEffect(() => {
    const moved =
      server.last_test_at !== lastSeen.current.at ||
      server.last_error !== lastSeen.current.err;
    lastSeen.current = { at: server.last_test_at, err: server.last_error };
    if (moved && testing) {
      setTesting(false);
      setFreshResult(true);
      window.setTimeout(() => setFreshResult(false), 6000);
    }
  }, [server.last_test_at, server.last_error, testing]);
  const authorizing = server.status === "authorizing" || testing;

  const runTest = async () => {
    setTesting(true);
    window.setTimeout(() => setTesting(false), 20000); // backstop, never stuck
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
        {/* The Test RESULT lands where the click happened (owner catch 2026-08-30:
            "tested just now" only updated the header subtitle — same font, same
            color, nowhere near the button). TRANSIENT (second owner catch): shown
            for a few seconds after the test that just ran, then gone — a stale
            "server responded · 42m ago" read as if it were still announcing. The
            durable receipt stays in the header. Failures stay persistent in red. */}
        {freshResult && server.status === "connected" && server.last_test_at ? (
          <div
            className="px-4 py-2 text-[12px] text-ok"
            data-testid={`mcp-test-ok-${server.name}`}
          >
            ✓ {t("mcp.test_ok", { count: server.tool_count ?? 0, rel: relTime(server.last_test_at) })}
          </div>
        ) : null}
        {server.last_error && server.status !== "connected" && (
          <div className="px-4 py-2.5 text-[13px] text-danger break-words">
            {server.last_error}
          </div>
        )}
      </div>

      {/* OPE-136 §3: the tool review — which of this server's tools exist in sessions. */}
      <McpToolReview server={server} onChanged={onChanged} />

      {/* No Configuration mirror here anymore (owner call 2026-08-30): one file
          serves every server, so the ground truth is revealed from ONE common
          place — the "Show file" affordance under the Custom · MCP group. The
          file itself carries what this page doesn't row-ify (headers, stdio
          command/env, hand-edit parse checks). */}

      <div className="flex items-center gap-4">
        {isOauth && server.status === "connected" && (
          <button
            className="text-[13px] text-muted hover:text-ink"
            onClick={async () => {
              await signoutMcp(server.name);
              onChanged();
            }}
            data-testid={`mcp-signout-${server.name}`}
            title={t("mcp.signout_tip")}
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
          title={t("mcp.remove_tip")}
        >
          {t("mcp.remove_server")}
        </button>
      </div>
    </div>
  );
}
