import { useState } from "react";
import { useTranslation } from "react-i18next";
import { setWorkspaceTrusted, type WorkspaceCommandTrust } from "../api";

export function WorkspaceTrustPrompt({
  request,
  onClose,
}: {
  request: WorkspaceCommandTrust;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState("");

  const trust = async () => {
    setSaving(true);
    setError("");
    const result = await setWorkspaceTrusted(request.workspace, true).catch(() => null);
    setSaving(false);
    if (!result?.ok) {
      setError(result?.error || t("workspace_trust.save_failed"));
      return;
    }
    onClose();
  };

  return (
    <div className="gate-overlay" role="dialog" aria-modal="true" aria-labelledby="workspace-trust-title">
      <div className="gate max-w-[560px]">
        <div className="gate-mark">✦</div>
        <h2 id="workspace-trust-title">{t("workspace_trust.title")}</h2>
        <p className="gate-sub">
          {t("workspace_trust.sub")}
        </p>
        <div className="rounded-lg border border-line bg-paper px-3 py-2.5 max-h-48 overflow-y-auto">
          {request.requested_commands.map((command) => (
            <code key={command} className="block text-[13px] py-1 text-ink">
              {command}
            </code>
          ))}
        </div>
        <div className="text-[12px] text-muted mt-2 break-all">{request.workspace}</div>
        {error && <div className="gate-error">{error}</div>}
        <div className="gate-foot justify-end gap-2">
          <button className="btn" onClick={onClose} disabled={saving}>
            {t("workspace_trust.keep_asking")}
          </button>
          <button className="btn primary" onClick={() => void trust()} disabled={saving}>
            {saving ? t("workspace_trust.saving") : t("workspace_trust.trust")}
          </button>
        </div>
      </div>
    </div>
  );
}
