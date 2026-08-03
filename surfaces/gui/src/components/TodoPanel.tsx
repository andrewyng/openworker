import type { TodoItem } from "../types";
import { useI18n } from "../i18n";

export function TodoPanel({ items }: { items: TodoItem[] }) {
  const { t } = useI18n();
  if (!items || items.length === 0) return null;
  const box = (s: string) => (s === "done" ? "☑" : s === "in_progress" ? "◉" : "☐");
  return (
    <div className="todo">
      <h4>{t("Tasks")}</h4>
      {items.map((it, i) => (
        <div className="item" key={i}>
          <span className="box">{box(it.status)}</span>
          <span className={it.status === "done" ? "done" : it.status === "in_progress" ? "doing" : ""}>
            {it.content}
          </span>
        </div>
      ))}
    </div>
  );
}
