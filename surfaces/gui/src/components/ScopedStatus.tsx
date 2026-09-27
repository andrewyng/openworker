// Shared status rows for machine-scoped Settings pages (UX-046): one loading
// treatment, one cached-view line, one unreachable message — so every scoped
// page speaks the same language.

import { useTranslation } from "react-i18next";

export function LoadingRow({ what }: { what: string }) {
  const { t } = useTranslation();
  return (
    <div
      className="flex items-center gap-2.5 px-4 py-3.5 text-meta text-muted"
      data-testid="scoped-loading"
    >
      <span className="w-[11px] h-[11px] rounded-full border-[1.5px] border-faint border-t-transparent animate-spin shrink-0" />
      {t("machines.status.loading", { what })}
    </div>
  );
}

export function CachedNote({ at }: { at: number }) {
  const { t } = useTranslation();
  const stamp = new Date(at).toLocaleTimeString(undefined, {
    hour: "numeric",
    minute: "2-digit",
  });
  return (
    <div
      className="flex items-center gap-2 mb-2.5 text-label text-faint"
      data-testid="scoped-cached"
    >
      <span className="w-[9px] h-[9px] rounded-full border-[1.5px] border-faint border-t-transparent animate-spin shrink-0" />
      {t("machines.status.cached", { stamp })}
    </div>
  );
}

export function UnreachableRow({ machineName }: { machineName: string }) {
  const { t } = useTranslation();
  return (
    <div className="px-4 py-3.5 text-meta text-muted" data-testid="scoped-unreachable">
      {t("machines.status.unreachable", { name: machineName })}
    </div>
  );
}
