// Lightweight dependency-free i18n for the OpenWorker GUI.
//
// Design notes:
// - English strings are used directly as translation keys, so any string not
//   present in the target dictionary falls back to English automatically.
// - The chosen language is persisted in localStorage and defaults to the
//   browser/system language when it is Chinese, otherwise English.
// - `t(key, vars)` supports `{{name}}` interpolation inside dictionary values.

import {
  createContext,
  useContext,
  useEffect,
  useState,
  type ReactNode,
} from "react";
import { zhCN } from "./translations/zh-CN";

export type Lang = "en" | "zh-CN";

const STORAGE_KEY = "openworker:lang";

export const LANG_LABELS: Record<Lang, string> = {
  en: "English",
  "zh-CN": "简体中文",
};

function detectLang(): Lang {
  try {
    const saved = localStorage.getItem(STORAGE_KEY);
    if (saved === "en" || saved === "zh-CN") return saved;
  } catch {
    /* localStorage unavailable — fall through to system detection */
  }
  if (typeof navigator !== "undefined" && /^zh\b/i.test(navigator.language)) {
    return "zh-CN";
  }
  return "en";
}

export function interpolate(
  str: string,
  vars?: Record<string, string | number>,
): string {
  if (!vars) return str;
  let out = str;
  for (const [k, v] of Object.entries(vars)) {
    out = out.split(`{{${k}}}`).join(String(v));
  }
  return out;
}

export function translate(key: string, lang: Lang, vars?: Record<string, string | number>): string {
  const out: string = lang === "zh-CN" && key in zhCN ? zhCN[key] : key;
  return interpolate(out, vars);
}

interface I18nContextValue {
  lang: Lang;
  setLang: (lang: Lang) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18nContextValue>({
  lang: "en",
  setLang: () => {},
  t: (key: string, vars?: Record<string, string | number>) =>
    interpolate(key, vars),
});

export function I18nProvider({ children }: { children: ReactNode }) {
  const [lang, setLangState] = useState<Lang>(() => detectLang());

  useEffect(() => {
    try {
      localStorage.setItem(STORAGE_KEY, lang);
    } catch {
      /* ignore */
    }
  }, [lang]);

  const setLang = (next: Lang) => setLangState(next);

  const t = (key: string, vars?: Record<string, string | number>) =>
    translate(key, lang, vars);

  return (
    <I18nContext.Provider value={{ lang, setLang, t }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n(): I18nContextValue {
  return useContext(I18nContext);
}
