import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { createBot, delegateBotTask, getBot, getBots, getBotRuntimes, setBotEnabled, type BotJob, type PersonalBot, type Machine } from "../api";

export function BotsView({ onOpen, onConfigureMachines }: {
  onOpen: (id: string, workspace: string, agent: string) => void;
  onConfigureMachines?: () => void;
}) {
  const { t } = useTranslation();
  const [bots, setBots] = useState<PersonalBot[]>([]);
  const [selected, setSelected] = useState<string | null>(null);
  const [jobs, setJobs] = useState<BotJob[]>([]);
  const [name, setName] = useState("");
  const [instructions, setInstructions] = useState("");
  const [cron, setCron] = useState("");
  const [prompt, setPrompt] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [loading, setLoading] = useState(true);
  const [runtimes, setRuntimes] = useState<Machine[]>([]);
  const [runtime, setRuntime] = useState("");
  const bot = bots.find(b => b.id === selected);
  const timezone = Intl.DateTimeFormat().resolvedOptions().timeZone || "UTC";

  useEffect(() => {
    let active = true;
    const refresh = async () => {
      try {
        const rows = await getBots();
        const selectedBot = rows.find(b => b.id === selected);
        const detail = selected && selectedBot && !selectedBot.machine_offline ? await getBot(selected) : null;
        const hosts = await getBotRuntimes();
        if (active) {
          setBots(rows);
          setRuntimes(hosts);
          setJobs(detail?.jobs || []);
          setLoading(false);
        }
      } catch (e) {
        if (active) { setError(String(e)); setLoading(false); }
      }
    };
    void refresh();
    const timer = setInterval(refresh, 5000);
    return () => { active = false; clearInterval(timer); };
  }, [selected]);

  const act = async (fn: () => Promise<void>) => {
    setBusy(true); setError("");
    try { await fn(); } catch (e) { setError(String(e)); } finally { setBusy(false); }
  };
  const field = "w-full rounded-lg border border-line bg-panel p-3 text-ink";
  return <main className="flex-1 min-w-0 overflow-y-auto bg-paper">
    <div className="max-w-4xl mx-auto px-7 py-6 space-y-5">
      <header><h1 className="text-xl font-semibold">{t("bots.title")}</h1>
        <p className="text-muted mt-2">{t("bots.description")}</p></header>
      {error && <p role="alert" className="text-danger">{error}</p>}
      <p className="text-sm text-muted">{t("bots.host_notice")}</p>
      <div className="flex flex-wrap gap-2" aria-label={t("bots.title")}>
        {bots.map(b => <button key={b.id} className={selected === b.id ? "btn-primary" : "btn-ghost"}
          onClick={() => { setSelected(b.id); setPrompt(""); setJobs([]); }}>
          {b.name} · {t(b.enabled ? "bots.active" : "bots.paused")}</button>)}
        <button className="btn-ghost" onClick={() => { setSelected(null); setJobs([]); }}>{t("bots.new")}</button>
      </div>
      {loading ? <p>{t("bots.loading")}</p> : bot ? <>
        <section className="rounded-xl border border-line bg-panel p-5 space-y-3">
          <h2 className="font-semibold">{bot.name}</h2>
          <p className="whitespace-pre-wrap">{bot.instructions}</p>
          <p className="text-sm text-muted">{bot.machine_name || t("bots.local")} · {t(bot.execution === "dedicated" ? "bots.dedicated" : "bots.shared")}</p>
          {bot.machine_offline && <p role="status">{t("bots.offline")}</p>}
          <p className="text-sm text-muted">{bot.cron ? `${bot.cron} · ${bot.timezone}` : t("bots.no_schedule")}</p>
          {bot.next_check && <p className="text-sm text-muted">{t("bots.next", { time: new Date(bot.next_check * 1000).toLocaleString() })}</p>}
          <div className="flex gap-3">
            <button className="btn-primary" onClick={() => onOpen(bot.session_id, bot.workspace, bot.agent)}>{t("bots.conversation")}</button>
            <button className="btn-ghost" disabled={busy || bot.machine_offline} onClick={() => void act(async () => {
              await setBotEnabled(bot.id, !bot.enabled); setBots(await getBots());
            })}>{t(bot.enabled ? "bots.pause" : "bots.resume")}</button>
          </div>
        </section>
        <form className="space-y-3" onSubmit={e => { e.preventDefault(); void act(async () => {
          await delegateBotTask(bot.id, prompt); setPrompt(""); setJobs((await getBot(bot.id)).jobs);
        }); }}>
          <label className="block">{t("bots.task")}<textarea className={field} value={prompt} maxLength={20000}
            onChange={e => setPrompt(e.target.value)} required rows={3} /></label>
          {!bot.enabled && <p className="text-muted text-sm">{t("bots.queue_paused")}</p>}
          <button className="btn-primary" disabled={busy || bot.machine_offline || !prompt.trim()}>{t("bots.delegate")}</button>
        </form>
        <section className="space-y-3"><h2 className="font-semibold">{t("bots.tasks")}</h2>
          {!jobs.length && <p className="text-muted">{t("bots.no_tasks")}</p>}
          {jobs.map(job => <article key={job.id} className="rounded-xl border border-line bg-panel p-4">
            <p className="text-sm text-muted">{t(`bots.states.${job.state}`)} · {t(job.trigger === "check-in" ? "bots.checkin" : "bots.delegation")}</p>
            <p className="whitespace-pre-wrap mt-2">{job.prompt}</p>
            {job.error && <p role="alert" className="text-danger mt-2">{job.error}</p>}
          </article>)}
        </section>
      </> : <form className="space-y-4 rounded-xl border border-line bg-panel p-5" onSubmit={e => {
        e.preventDefault(); void act(async () => {
          const body = { name, instructions, cron, timezone };
          const result = runtime ? await createBot(body, runtime) : await createBot(body);
          setBots(await getBots()); setSelected(result.bot.id); setName(""); setInstructions(""); setCron("");
        });
      }}>
        <label className="block">{t("bots.name")}<input className={field} value={name} onChange={e => setName(e.target.value)} maxLength={100} required /></label>
        <label className="block">{t("bots.location")}<select className={field} value={runtime} onChange={e => setRuntime(e.target.value)}>
          <option value="">{t("bots.local")}</option>
          {runtimes.map(m => <option key={m.id} value={m.id}>{m.name} · {t("bots.dedicated")}</option>)}
        </select></label>
        <p className="text-muted text-sm">{t("bots.cloud_help")}</p>
        {onConfigureMachines && <button type="button" className="btn-ghost" onClick={onConfigureMachines}>{t("bots.configure_cloud")}</button>}
        <label className="block">{t("bots.instructions")}<textarea className={field} value={instructions}
          onChange={e => setInstructions(e.target.value)} rows={5} maxLength={20000} required /></label>
        <label className="block">{t("bots.schedule")}<input className={field} value={cron} onChange={e => setCron(e.target.value)} placeholder="0 9 * * 1-5" /></label>
        <p className="text-muted text-sm">{t("bots.schedule_help", { timezone })}</p>
        <p className="text-muted text-sm">{t("bots.permissions")}</p>
        <button className="btn-primary" disabled={busy || !name.trim() || !instructions.trim()}>{t("bots.create")}</button>
      </form>}
    </div>
  </main>;
}
