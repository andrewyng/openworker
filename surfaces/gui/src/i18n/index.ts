import React, { createContext, useContext, useState, useEffect, useCallback } from "react";
import en, { type I18nStrings } from "./en";
import zhCN from "./zh-CN";

type Lang = "en" | "zh-CN";

const LANGS: Record<Lang, I18nStrings> = { en, "zh-CN": zhCN };
const STORAGE_KEY = "openworker:lang";

const I18nContext = createContext<{
  lang: Lang;
  setLang: (l: Lang) => void;
  t: I18nStrings;
}>({ lang: "en", setLang: () => {}, t: en });

function detectLang(): Lang {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored === "en" || stored === "zh-CN") return stored;
  } catch {}
  // Detect from browser
  if (typeof navigator !== "undefined") {
    const nav = navigator.language;
    if (nav.startsWith("zh")) return "zh-CN";
  }
  return "en";
}

export function I18nProvider({ children }: { children: React.ReactNode }) {
  const [lang, setLangState] = useState<Lang>(detectLang);

  const setLang = useCallback((l: Lang) => {
    setLangState(l);
    try {
      localStorage.setItem(STORAGE_KEY, l);
      document.documentElement.lang = l;
    } catch {}
  }, []);

  useEffect(() => {
    document.documentElement.lang = lang;
  }, [lang]);

  return (
    <I18nContext.Provider value={{ lang, setLang, t: LANGS[lang] }}>
      {children}
    </I18nContext.Provider>
  );
}

export function useI18n() {
  return useContext(I18nContext);
}

export function useT() {
  const { t } = useContext(I18nContext);
  return t;
}
