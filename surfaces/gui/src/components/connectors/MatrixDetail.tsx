import { useEffect, useState } from "react";
import { disconnectConnector, patchMatrixSettings, type Connector, type MatrixSettings } from "../../api";
import { ConnectorBadge } from "../../connectors/ConnectorIcon";
import { AllowlistBlock, ListeningSessionsBlock, UnauthorizedBlock } from "../ManageTabs";
import type { DetailProps } from "./ConnectorsSection";
import { ToolsDisclosure } from "./ToolsDisclosure";
import { FOOT, GRP, GRP_H, ROW } from "./ui";

const LABEL = "text-[12.5px] text-muted w-36 shrink-0";

function Toggle({
  checked,
  onChange,
  testId,
}: {
  checked: boolean;
  onChange: (v: boolean) => void;
  testId: string;
}) {
  return (
    <button
      type="button"
      role="switch"
      aria-checked={checked}
      data-testid={testId}
      className={
        "relative w-9 h-5 rounded-full transition-colors " + (checked ? "bg-accent" : "bg-faint")
      }
      onClick={() => onChange(!checked)}
    >
      <span
        className={
          "absolute top-0.5 left-0.5 w-4 h-4 rounded-full bg-white transition-transform " +
          (checked ? "translate-x-4" : "")
        }
      />
    </button>
  );
}

export function MatrixDetail({ c, onChanged }: DetailProps) {
  const ms = c.matrix_settings;
  const [busy, setBusy] = useState(false);
  const [local, setLocal] = useState<MatrixSettings | null>(ms ?? null);

  useEffect(() => {
    if (ms) setLocal(ms);
  }, [ms]);

  const save = async (patch: Partial<MatrixSettings>) => {
    setBusy(true);
    try {
      const res = await patchMatrixSettings(patch);
      if (res.settings) setLocal(res.settings);
      onChanged();
    } finally {
      setBusy(false);
    }
  };

  return (
    <div data-testid="matrix-detail">
      <div className="flex items-center gap-3.5 mb-5">
        <ConnectorBadge connector={c} size={44} title="Matrix" />
        <div className="min-w-0 flex-1">
          <h2 className="text-[20px] font-semibold tracking-tight leading-tight">Matrix</h2>
          <div className="text-[12.5px] text-muted flex items-center gap-1.5">
            <span className="w-2 h-2 rounded-full bg-ok" />
            <span data-testid="matrix-account">{c.account || local?.user_id || "Connected"}</span>
          </div>
        </div>
        <button
          className="text-[12.5px] text-danger/80 hover:text-danger shrink-0"
          onClick={async () => {
            await disconnectConnector("matrix");
            onChanged();
          }}
        >
          Disconnect
        </button>
      </div>

      {local && (
        <>
          <div className={GRP_H + " !mt-0"}>Homeserver</div>
          <div className={GRP} data-testid="matrix-homeserver">
            <div className={ROW}>
              <span className={LABEL}>URL</span>
              <span className="text-[13px] truncate">{local.homeserver_url}</span>
            </div>
            {local.user_id && (
              <div className={ROW}>
                <span className={LABEL}>Bot user</span>
                <span className="text-[13px] font-mono truncate">{local.user_id}</span>
              </div>
            )}
          </div>

          <div className={GRP_H}>Routing</div>
          <div className={GRP} data-testid="matrix-routing">
            <div className={ROW}>
              <span className={LABEL}>Require @mention</span>
              <Toggle
                testId="matrix-require-mention"
                checked={local.require_mention}
                onChange={(v) => save({ require_mention: v })}
              />
            </div>
            <div className={ROW}>
              <span className={LABEL}>Auto-thread replies</span>
              <Toggle
                testId="matrix-auto-thread"
                checked={local.auto_thread}
                onChange={(v) => save({ auto_thread: v })}
              />
            </div>
            <div className={ROW}>
              <span className={LABEL}>Per-user sessions</span>
              <Toggle
                testId="matrix-group-sessions"
                checked={local.group_sessions_per_user}
                onChange={(v) => save({ group_sessions_per_user: v })}
              />
            </div>
            <div className={ROW}>
              <span className={LABEL}>DM @mention → thread</span>
              <Toggle
                testId="matrix-dm-mention-threads"
                checked={local.dm_mention_threads}
                onChange={(v) => save({ dm_mention_threads: v })}
              />
            </div>
            <div className={ROW}>
              <span className={LABEL}>Session scope</span>
              <select
                className="text-[13px] bg-surface border border-line rounded px-2 py-1"
                data-testid="matrix-session-scope"
                value={local.session_scope}
                disabled={busy}
                onChange={(e) => save({ session_scope: e.target.value })}
              >
                <option value="auto">Auto (thread)</option>
                <option value="thread">Thread</option>
                <option value="room">Room</option>
              </select>
            </div>
            <div className={ROW}>
              <span className={LABEL}>Lifecycle reactions</span>
              <Toggle
                testId="matrix-lifecycle-reactions"
                checked={local.lifecycle_reactions}
                onChange={(v) => save({ lifecycle_reactions: v })}
              />
            </div>
          </div>

          <div className={GRP_H}>Room allowlists</div>
          <div className={GRP} data-testid="matrix-rooms">
            <RoomListEditor
              label="Allowed rooms"
              hint="Empty = all joined rooms. DMs always allowed."
              value={local.allowed_rooms}
              testId="matrix-allowed-rooms"
              onSave={(rooms) => save({ allowed_rooms: rooms })}
            />
            <RoomListEditor
              label="Free-response rooms"
              hint="No @mention required in these rooms."
              value={local.free_response_rooms}
              testId="matrix-free-response-rooms"
              onSave={(rooms) => save({ free_response_rooms: rooms })}
            />
          </div>
        </>
      )}

      <div className={GRP + " mt-4"}>
        <ConnectorToolsWrap c={c} onChanged={onChanged} />
        <AllowlistBlock c={c} onChanged={onChanged} />
        <UnauthorizedBlock c={c} onChanged={onChanged} />
        <ListeningSessionsBlock c={c} />
      </div>

      <div className={FOOT + " mt-2"}>
        E2EE requires libolm on this machine. Reconnect after changing homeserver credentials in
        Connectors.
      </div>
    </div>
  );
}

function ConnectorToolsWrap({ c, onChanged }: { c: Connector; onChanged: () => void }) {
  return <ToolsDisclosure c={c} onChanged={onChanged} />;
}

function RoomListEditor({
  label,
  hint,
  value,
  testId,
  onSave,
}: {
  label: string;
  hint: string;
  value: string[];
  testId: string;
  onSave: (rooms: string[]) => void;
}) {
  const [draft, setDraft] = useState(value.join(", "));
  return (
    <div className="py-2 border-b border-line/40 last:border-0">
      <div className="text-[13px] font-medium mb-1">{label}</div>
      <div className="text-[11.5px] text-muted mb-2">{hint}</div>
      <textarea
        className="w-full text-[12.5px] font-mono bg-surface border border-line rounded px-2 py-1.5 min-h-[52px]"
        data-testid={testId}
        value={draft}
        onChange={(e) => setDraft(e.target.value)}
        onBlur={() => {
          const rooms = draft
            .split(/[\n,]+/)
            .map((s) => s.trim())
            .filter(Boolean);
          onSave(rooms);
        }}
        placeholder="!abc:example.org"
      />
    </div>
  );
}
