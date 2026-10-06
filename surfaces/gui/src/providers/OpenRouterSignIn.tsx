import { useEffect, useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { openExternal } from "../tauri";
import { openRouterAuth, type OpenRouterAuthStatus } from "../api";

export function OpenRouterSignIn({
  tp,
  onChanged,
}: {
  tp: string;
  onChanged: () => Promise<void>;
}) {
  const { t } = useTranslation();
  const [status, setStatus] = useState<OpenRouterAuthStatus | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [code, setCode] = useState("");
  const generation = useRef(0);
  const acting = useRef(false);
  const mounted = useRef(true);
  const manual =
    !!status?.authorize_url &&
    !new URL(status.authorize_url).searchParams.has("callback_url");

  useEffect(() => {
    let alive = true;
    mounted.current = true;
    let timer: ReturnType<typeof setTimeout>;
    const poll = async () => {
      const version = generation.current;
      try {
        const next = await openRouterAuth("status");
        if (alive && version === generation.current && !acting.current) {
          setStatus(next);
          setError("");
        }
      } catch {
        if (alive && version === generation.current && !acting.current)
          setError(t("openrouter.unreachable"));
      }
      if (alive) timer = setTimeout(poll, 2000);
    };
    void poll();
    return () => {
      alive = false;
      mounted.current = false;
      clearTimeout(timer);
    };
  }, [t]);

  useEffect(() => {
    if (status && !status.authorizing) void onChanged();
  }, [status?.connected, status?.active, status?.authorizing]);

  const action = async (
    name: "signin" | "complete" | "cancel" | "disconnect",
    useManual = false,
  ) => {
    const version = ++generation.current;
    acting.current = true;
    setBusy(true);
    setError("");
    try {
      const next = await openRouterAuth(
        name,
        name === "signin"
          ? { manual: useManual }
          : {
              code,
              attempt_id: status?.attempt_id,
            },
      );
      if (!mounted.current || version !== generation.current) return;
      setStatus(next);
      if (name === "signin") {
        setCode("");
        if (next.authorize_url) await openExternal(next.authorize_url);
      }
      if (!next.authorizing) await onChanged();
    } catch {
      if (mounted.current && version === generation.current)
        setError(t("openrouter.unreachable"));
    } finally {
      if (version === generation.current) {
        // Also invalidate polls started while the action was awaiting its response.
        generation.current++;
        acting.current = false;
        if (mounted.current) setBusy(false);
      }
    }
  };
  const button =
    "rounded-lg border border-line px-3 py-2 text-[13px] text-ink hover:border-lineStrong disabled:opacity-40";
  return (
    <section
      className="mt-4 border-t border-line pt-4"
      aria-label={t("openrouter.title")}
    >
      <p className="text-[13px] font-medium text-ink">
        {t("openrouter.title")}
      </p>
      <p className="text-[12px] text-faint mt-1 mb-3">{t("openrouter.note")}</p>
      {status?.connected && (
        <p
          className="text-[12px] text-ok mb-2"
          data-testid={`${tp}-openrouter-connected`}
        >
          {t(status.active ? "openrouter.active" : "openrouter.saved")}
        </p>
      )}
      {status?.authorizing ? (
        <div className="space-y-2">
          <p role="status" className="text-[12px] text-muted">
            {t("openrouter.waiting")}
          </p>
          {status.authorize_url && (
            <button
              className={button}
              onClick={() => void openExternal(status.authorize_url!)}
            >
              {t("openrouter.reopen")}
            </button>
          )}
          <button
            className={button + " ml-2"}
            onClick={() => void action("cancel")}
          >
            {t("openrouter.cancel")}
          </button>
          {manual && (
            <div>
              <label
                className="block text-[12px] text-muted"
                htmlFor={`${tp}-openrouter-code`}
              >
                {t("openrouter.code")}
              </label>
              <input
                id={`${tp}-openrouter-code`}
                className="w-full rounded-lg border border-line bg-panel px-3 py-2 text-[13px]"
                autoComplete="off"
                value={code}
                onChange={(e) => setCode(e.target.value)}
              />
              <button
                className={button + " mt-2"}
                disabled={busy || !code.trim()}
                onClick={() => void action("complete")}
              >
                {t("openrouter.connect")}
              </button>
            </div>
          )}
        </div>
      ) : (
        <div className="flex flex-wrap gap-2">
          <button
            className="rounded-lg border border-accent bg-accent px-4 py-2 text-[13px] font-medium text-white disabled:opacity-40"
            data-testid={`${tp}-openrouter-signin`}
            disabled={busy}
            onClick={() => void action("signin")}
          >
            {t("openrouter.signin")}
          </button>
          <button
            className={button}
            disabled={busy}
            onClick={() => void action("signin", true)}
          >
            {t("openrouter.manual")}
          </button>
          {status?.connected && (
            <button
              className={button}
              disabled={busy}
              onClick={() => void action("disconnect")}
            >
              {t("openrouter.disconnect")}
            </button>
          )}
        </div>
      )}
      {status?.connected && (
        <button
          className="mt-2 text-[12px] text-muted underline"
          onClick={() => void openExternal("https://openrouter.ai/keys")}
        >
          {t("openrouter.manage")}
        </button>
      )}
      {(error || status?.error) && (
        <p role="alert" className="mt-2 text-[12px] text-warnInk">
          {error || status?.error}
        </p>
      )}
    </section>
  );
}
