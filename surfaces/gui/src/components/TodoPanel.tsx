import { useTranslation } from "react-i18next";
import type { TodoItem } from "../types";

export function TodoPanel({ items }: { items: TodoItem[] }) {
  const { t } = useTranslation();
  if (!items || items.length === 0) return null;
  const box = (s: string) => (s === "done" ? "☑" : s === "in_progress" ? "◉" : "☐");
  return (
    <div className="todo">
      <h4>{t("todo.title")}</h4>
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
