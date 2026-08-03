import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { getConnectors, getSessionConnections } from "../api";
import type { Attachment } from "../types";
import { ConnectorIcon } from "../connectors/ConnectorIcon";
import { indexConnectors, visualFor, type ConnectorMap } from "../connectors/visuals";
import { useRoots } from "../useRoots";
import { AddFolderForm } from "./AddFolderForm";

// Empty-state for a fresh Cowork session (§27): a greeting, exactly three concrete template
// tasks, and the composer — nothing else. Each task carries its own setup: no icon tiles (the
// title is the row), connector dots on the sub-line (brand color = connected and enabled for
// this session, grayscale = not — §23's vocabulary), and sub-line copy that is always the task's
// OUTCOME, never connection state. Sources ready → "Start →" on hover, click prefills the
// composer. Not ready → "Configure ›" always visible (for a gated row the setup action IS the
// row's meaning), opening the §23 Session settings drawer — no second setup surface here.

export function SessionIntro({
  sessionId,
  onOpenSessionSettings,
  onPrefill,
}: {
  sessionId: string;
  // Opens the §23 Session settings drawer (sources section) — the gated rows' Configure target.
  onOpenSessionSettings: () => void;
  onPrefill: (text: string, attachments?: Attachment[]) => void;
}) {
  const { roots, busy, error, addRoot } = useRoots(sessionId);
  const { t } = useTranslation();
  const [live, setLive] = useState<Set<string>>(new Set());
  const [byName, setByName] = useState<ConnectorMap>({});
  const [addingFolder, setAddingFolder] = useState(false);

  const folderPrompt = t("intro.folderPrompt");
  const hubspotPrompt = t("intro.hubspotPrompt");
  const ghSlackPrompt = t("intro.githubSlackPrompt");

  useEffect(() => {
    // Live = what this session can touch right now (connected AND not muted here) — the same
    // truth the §23 glance renders, so the dots here can never disagree with the row above.
    getSessionConnections(sessionId)
      .then((c) => setLive(new Set(c.connected.filter((x) => x.enabled).map((x) => x.connector))))
      .catch(() => {});
    getConnectors()
      .then((list) => setByName(indexConnectors(list)))
      .catch(() => {});
  }, [sessionId]);

  const shared = roots.filter((r) => !r.primary);
  const hubspotReady = live.has("hubspot");
  const ghSlackReady = live.has("github") && live.has("slack");

  const dot = (name: string, on: boolean) => (
    <span className={"task-dot" + (on ? "" : " off")} key={name}>
      <ConnectorIcon connector={visualFor(name, "connector", byName)} size={12} />
    </span>
  );

  const pickFolder = () => {
    // A shared folder already exists → straight to the prompt; otherwise share one first.
    if (shared.length > 0) onPrefill(folderPrompt);
    else setAddingFolder((v) => !v);
  };

  return (
    <div className="intro">
      <h1 className="greeting">
        <span className="mark">✦</span> {t("intro.whatProduce")}
      </h1>
      <p className="intro-lede">{t("intro.pickTask")}</p>

      <div className="intro-tasks">
        <button className="task-card" data-testid="intro-task-folder" onClick={pickFolder}>
          <span className="task-card-body">
            <span className="task-card-title">{t("intro.analyzeDir")}</span>
            <span className="task-card-sub">{t("intro.analyzeDirSub")}</span>
          </span>
          <span className="task-card-act">{t("intro.pickFolder")}</span>
        </button>
        {addingFolder && (
          <div className="intro-addfolder">
            <AddFolderForm
              startOpen
              busy={busy}
              onAdd={async (path, writable) => {
                const ok = await addRoot(path, writable);
                if (ok !== false) onPrefill(folderPrompt);
                return ok;
              }}
              onDismiss={() => setAddingFolder(false)}
            />
            {error && <div className="roots-err">{error}</div>}
          </div>
        )}

        <button
          className={"task-card" + (hubspotReady ? "" : " gated")}
          data-testid="intro-task-hubspot"
          onClick={() => (hubspotReady ? onPrefill(hubspotPrompt) : onOpenSessionSettings())}
        >
          <span className="task-card-body">
            <span className="task-card-title">{t("intro.hubspotReport")}</span>
            <span className="task-card-sub">
              {dot("hubspot", hubspotReady)}
              {t("intro.hubspotReportSub")}
            </span>
          </span>
          <span className="task-card-act">
            {hubspotReady ? t("intro.start") : t("intro.configure")}
          </span>
        </button>

        <button
          className={"task-card" + (ghSlackReady ? "" : " gated")}
          data-testid="intro-task-github-slack"
          onClick={() => (ghSlackReady ? onPrefill(ghSlackPrompt) : onOpenSessionSettings())}
        >
          <span className="task-card-body">
            <span className="task-card-title">{t("intro.githubSlackReport")}</span>
            <span className="task-card-sub">
              {dot("github", live.has("github"))}
              {dot("slack", live.has("slack"))}
              {t("intro.githubSlackReportSub")}
            </span>
          </span>
          <span className="task-card-act">
            {ghSlackReady ? t("intro.start") : t("intro.configure")}
          </span>
        </button>
      </div>
    </div>
  );
}
