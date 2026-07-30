import { useEffect, useState } from "react";
import en from "./locales/en.json";
import zhCN from "./locales/zh_CN.json";

export type LanguagePref = "en" | "zh-CN";

const KEY = "openwork-language";
const PREF_EVENT = "openwork:language-pref";
const resources: Record<LanguagePref, Record<string, string>> = {
  en,
  "zh-CN": zhCN,
};

export function getLanguagePref(): LanguagePref {
  try {
    return localStorage.getItem(KEY) === "zh-CN" ? "zh-CN" : "en";
  } catch {
    return "en";
  }
}

function apply(pref: LanguagePref) {
  document.documentElement.lang = pref;
}

export function setLanguagePref(pref: LanguagePref) {
  try {
    localStorage.setItem(KEY, pref);
  } catch {
    /* The preference still applies for this session when storage is unavailable. */
  }
  apply(pref);
  window.dispatchEvent(new CustomEvent<LanguagePref>(PREF_EVENT, { detail: pref }));
}

export function initLanguage() {
  apply(getLanguagePref());
}

function translate(language: LanguagePref, key: string, vars?: Record<string, string | number>) {
  let value = resources[language][key] ?? resources.en[key] ?? key;
  for (const [name, replacement] of Object.entries(vars || {})) {
    value = value.split(`{{${name}}}`).join(String(replacement));
  }
  return value;
}

export function useLanguage(): {
  language: LanguagePref;
  setLanguage: (language: LanguagePref) => void;
  t: (key: string, vars?: Record<string, string | number>) => string;
} {
  const [language, setCurrentLanguage] = useState<LanguagePref>(getLanguagePref);

  useEffect(() => {
    const sync = (event: Event) =>
      setCurrentLanguage((event as CustomEvent<LanguagePref>).detail || getLanguagePref());
    window.addEventListener(PREF_EVENT, sync);
    return () => window.removeEventListener(PREF_EVENT, sync);
  }, []);

  return {
    language,
    setLanguage: setLanguagePref,
    t: (key, vars) => translate(language, key, vars),
  };
}
