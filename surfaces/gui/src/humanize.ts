// UX-015 (§33): tool calls render as English one-liners. The model does NOT emit a purpose
// per call — the stream is name+args+result — so the sentence is synthesized here from
// per-tool templates. `run_shell` is the exception: its optional `description` argument is
// model-written intent and is preferred when present. Fallback: "Used <tool> — <short args>".

import { shortArgs } from "./components/ApprovalCard";

// A one-line sentence in three segments so the UI can emphasize the object:
// "Read " + <b>runbook.md</b> + " from the shared folder".
export interface HumanLine {
  pre: string;
  obj?: string;
  post?: string;
}

const trunc = (s: string, n: number) => (s.length > n ? s.slice(0, n - 1) + "…" : s);
const baseName = (p: string) => p.replace(/\/+$/, "").split("/").pop() || p;

// send_message targets are "platform:chat" or "platform:chat:thread" — show the platform
// by name and the last human-ish segment of the chat id.
function messageTarget(target: string): { platform: string; tail: string } {
  const [platform, ...rest] = String(target).split(":");
  const chat = rest[0] || "";
  const tail = chat.includes("/") ? chat.split("/").pop() || chat : chat;
  const names: Record<string, string> = { slack: "Slack", telegram: "Telegram" };
  return { platform: names[platform] || platform, tail };
}

export function humanizeTool(name: string, args: any): HumanLine {
  const a = args && typeof args === "object" ? args : {};
  switch (name) {
    case "run_shell": {
      const cmd = trunc(String(a.command ?? ""), 60);
      const desc = typeof a.description === "string" && a.description.trim() ? a.description.trim() : "";
      const pre = a.run_in_background ? "已在后台启动：" : "运行了 ";
      return {
        pre,
        obj: cmd,
        ...(desc ? { post: ` — ${desc.charAt(0).toLowerCase()}${desc.slice(1)}` } : {}),
      };
    }
    case "shell_task_output":
      return { pre: "查看了一个后台命令" };
    case "shell_task_kill":
      return { pre: "停止了一个后台命令" };
    case "read_file":
      return { pre: "读取了 ", obj: baseName(String(a.path ?? "一个文件")) };
    case "write_file":
      return { pre: "写入了 ", obj: baseName(String(a.path ?? "一个文件")) };
    case "replace_in_file":
    case "apply_patch":
    case "apply_unified_diff":
      return { pre: "编辑了 ", obj: a.path ? baseName(String(a.path)) : "文件" };
    case "grep":
      return { pre: "在代码中搜索 ", obj: `“${trunc(String(a.pattern ?? ""), 40)}”` };
    case "git_log":
      return { pre: "查看了近期的 git 历史" };
    case "todo_write": {
      // `todos` is current; `items` renders histories from before the rename (the old
      // key breaks Together's GLM-5.2 chat template — see coworker/tools/todo.py).
      const items = Array.isArray(a.todos) ? a.todos : Array.isArray(a.items) ? a.items : [];
      if (items.length === 1) {
        const it = items[0] || {};
        const status = String(it.status || "").replace(/_/g, " ");
        return {
          pre: "更新了计划 — ",
          obj: `“${trunc(String(it.content ?? ""), 70)}”`,
          ...(status ? { post: ` → ${status}` } : {}),
        };
      }
      return { pre: `更新了计划 — ${items.length} 项` };
    }
    case "send_message": {
      const { platform, tail } = messageTarget(String(a.target ?? ""));
      if (!tail) return { pre: "发送了一条消息" };
      return { pre: `向 `, obj: tail, post: ` 发送了一条 ${platform} 消息` };
    }
    case "web_search":
      return { pre: "搜索了网络 — ", obj: `“${trunc(String(a.query ?? ""), 60)}”` };
    case "web_fetch": {
      let host = String(a.url ?? "");
      try {
        host = new URL(host).host || host;
      } catch {
        /* keep raw */
      }
      return { pre: "读取了一个网页 — ", obj: trunc(host, 50) };
    }
    case "explore":
      return { pre: "派出一个子 agent 去探查 — ", obj: `“${trunc(String(a.task ?? a.prompt ?? ""), 60)}”` };
    case "load_skill":
      // SKILLS-SPEC §4.1 #4 — the trust line: the transcript always shows the moment a
      // skill's instructions were picked up, model-invoked or forced via /skill.
      return { pre: "使用了技能：", obj: String(a.name ?? "") };
    case "ask_user":
      return { pre: "向你提了一个问题" };
    case "propose_plan":
      return { pre: "提出了一个计划" };
    case "request_directory":
      return { pre: "请求了文件夹访问权限 — ", obj: String(a.path ?? "") };
    default: {
      const rest = trunc(shortArgs(a), 80);
      return { pre: `使用了 ${name}`, ...(rest ? { post: ` — ${rest}` } : {}) };
    }
  }
}

// The approval card's headline (§35): the ask, phrased as the action being decided.
// run_shell leads with the model's own description ("Run a command — fetch stock data").
export function humanizeApprovalTitle(name: string, args: any): HumanLine {
  const a = args && typeof args === "object" ? args : {};
  switch (name) {
    case "write_file":
      return { pre: "写入 ", obj: baseName(String(a.path ?? "一个文件")) };
    case "replace_in_file":
    case "apply_patch":
    case "apply_unified_diff":
      return { pre: "编辑 ", obj: a.path ? baseName(String(a.path)) : "文件" };
    case "run_shell": {
      const desc = typeof a.description === "string" && a.description.trim() ? a.description.trim() : "";
      return {
        pre: "运行一个命令",
        ...(desc ? { post: ` — ${desc}` } : {}),
      };
    }
    case "send_message": {
      const { tail } = messageTarget(String(a.target ?? ""));
      return tail ? { pre: "向 ", obj: tail, post: " 发送一条消息" } : { pre: "发送一条消息" };
    }
    case "send_file": {
      const { tail } = messageTarget(String(a.target ?? ""));
      return tail ? { pre: "向 ", obj: tail, post: " 发送一个文件" } : { pre: "发送一个文件" };
    }
    case "create_scheduled_task":
      return a.title
        ? { pre: "创建自动化任务 ", obj: `“${trunc(String(a.title), 60)}”` }
        : { pre: "创建一个自动化任务" };
    case "save_skill":
      // SKILLS-SPEC §5.2/§7: "Add", never "install"; destination is "your skills".
      return a.name
        ? { pre: "将技能 ", obj: String(a.name), post: " 添加到你的技能" }
        : { pre: "向你的技能添加一个技能" };
    default:
      return { pre: `使用 ${name}` };
  }
}

// Approvals with no executed tool call (typically declined): the ask, phrased as intent.
export function humanizeAsk(name: string, args: any): HumanLine {
  const a = args && typeof args === "object" ? args : {};
  switch (name) {
    case "run_shell":
      return { pre: "想要运行 ", obj: trunc(String(a.command ?? ""), 60) };
    case "write_file":
      return { pre: "想要写入 ", obj: baseName(String(a.path ?? "一个文件")) };
    case "replace_in_file":
    case "apply_patch":
    case "apply_unified_diff":
      return { pre: "想要编辑 ", obj: a.path ? baseName(String(a.path)) : "文件" };
    case "send_message": {
      const { platform, tail } = messageTarget(String(a.target ?? ""));
      if (!tail) return { pre: "想要发送一条消息" };
      return { pre: `想要在 ${platform} 上向 `, obj: tail, post: ` 发送消息` };
    }
    default:
      return { pre: `想要使用 ${name}` };
  }
}
