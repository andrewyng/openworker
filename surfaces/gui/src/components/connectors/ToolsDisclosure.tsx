import { useTranslation } from "react-i18next";
import { updateConnectorTools, type Connector } from "../../api";
import { ApprovalChip, SavedTick, ToolRow, ToolsCountLine, useSavedTick } from "./ToolReview";
import { GRP, ROW } from "./ui";

// Collapsed-by-default Tools group, shared by every connector detail page
// (UX-DECISIONS §21): the lever exists everywhere but stays quiet. Rows, chips,
// count line, and the save receipt come from ToolReview.tsx — the SAME primitives
// the MCP tool review renders through (owner ask 2026-08-30: one dialect).
export function ToolsDisclosure({ c, onChanged }: { c: Connector; onChanged: () => void }) {
  const { t } = useTranslation();
  const [savedTick, flashTick] = useSavedTick();
  if (!c.tools?.length) return null;
  const enabled = c.tools.filter((tool) => tool.enabled).length;
  return (
    <div className={GRP + " mt-6"}>
      <details>
        <summary className={ROW + " cursor-pointer hover:bg-paper/60 list-none [&::-webkit-details-marker]:hidden"}>
          <span className="text-[13px] text-muted w-24 shrink-0">{t("connector.tools_label")}</span>
          <span className="min-w-0 flex-1 text-[13px] text-muted">
            {t("tools.enabled_count", { checked: enabled, total: c.tools.length })}
          </span>
          <SavedTick show={savedTick} testId={`connector-tools-saved-${c.name}`} />
        </summary>
        {/* The same explanation the MCP page gives — it is equally true here.
            The summary row above already carries the count. */}
        <div className="px-4 pb-1.5">
          <ToolsCountLine checked={enabled} total={c.tools.length} showCount={false} />
        </div>
        {c.tools.map((tool) => (
          <ToolRow
            key={tool.name}
            checked={tool.enabled}
            checkboxTestId={`connector-tool-check-${c.name}-${tool.name}`}
            onToggle={async () => {
              await updateConnectorTools(c.name, { [tool.name]: !tool.enabled });
              onChanged();
              flashTick();
            }}
            name={tool.label}
            title={`${tool.name} — ${tool.description}`}
            right={<ApprovalChip kind={tool.kind === "write" ? "asks_first" : "read"} />}
          />
        ))}
      </details>
    </div>
  );
}
