// Internationalization (i18n): Language switching for the OpenWorker GUI.
//
// Pattern mirrors theme.ts — the preference lives in localStorage (per-device, like
// OS language), applies immediately, and stays in sync across components via a
// custom event + React hook. No external i18n library; a small typed dictionary
// and a t(key) function keep the bundle lean.
import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from "react";
import { translations, type Locale, type TranslationKey } from "./locales";

const KEY = "openwork-locale";
const PREF_EVENT = "openwork:locale-pref";

// Browser language → our supported locale. Used only on first run (no stored pref).
export function detectLocale(): Locale {
  try {
    const stored = localStorage.getItem(KEY);
    if (stored === "en" || stored === "zh") return stored;
  } catch {
    /* private mode etc. */
  }
  const nav = (navigator.language || "en").toLowerCase();
  return nav.startsWith("zh") ? "zh" : "en";
}

export function getLocalePref(): Locale {
  return detectLocale();
}

export function setLocalePref(locale: Locale) {
  try {
    localStorage.setItem(KEY, locale);
  } catch {
    /* best effort */
  }
  window.dispatchEvent(new CustomEvent(PREF_EVENT, { detail: locale }));
}

export const SUPPORTED_LOCALES: { value: Locale; label: string; native: string }[] = [
  { value: "en", label: "English", native: "English" },
  { value: "zh", label: "Chinese (Simplified)", native: "简体中文" },
];

interface I18nContextValue {
  locale: Locale;
  setLocale: (l: Locale) => void;
  t: (key: TranslationKey, vars?: Record<string, string | number>) => string;
}

const I18nContext = createContext<I18nContextValue | null>(null);

export function I18nProvider({ children }: { children: ReactNode }) {
  const [locale, setLocaleState] = useState<Locale>(getLocalePref);

  useEffect(() => {
    const sync = (e: Event) => {
      const detail = (e as CustomEvent<Locale>).detail;
      setLocaleState(detail || getLocalePref());
    };
    window.addEventListener(PREF_EVENT, sync);
    return () => window.removeEventListener(PREF_EVENT, sync);
  }, []);

  // Keep <html lang> current so screen readers / browser translation pick the right language.
  useEffect(() => {
    document.documentElement.lang = locale;
  }, [locale]);

  const setLocale = useCallback((l: Locale) => {
    setLocaleState(l);
    setLocalePref(l);
  }, []);

  const t = useCallback(
    (key: TranslationKey, vars?: Record<string, string | number>) => {
      let str: string = translations[locale][key] ?? translations.en[key] ?? key;
      if (vars) {
        for (const [k, v] of Object.entries(vars)) {
          str = str.replace(new RegExp(`\\{${k}\\}`, "g"), String(v));
        }
      }
      return str;
    },
    [locale],
  );

  const value = useMemo(() => ({ locale, setLocale, t }), [locale, setLocale, t]);
  return <I18nContext.Provider value={value}>{children}</I18nContext.Provider>;
}

export function useI18n(): I18nContextValue {
  const ctx = useContext(I18nContext);
  if (!ctx) throw new Error("useI18n must be used within I18nProvider");
  return ctx;
}

// Convenience: translate outside React render (rare — for imperative strings like
// window.confirm). Falls back to the current stored locale.
export function tr(key: TranslationKey, vars?: Record<string, string | number>): string {
  const locale = getLocalePref();
  let str: string = translations[locale][key] ?? translations.en[key] ?? key;
  if (vars) {
    for (const [k, v] of Object.entries(vars)) {
      str = str.replace(new RegExp(`\\{${k}\\}`, "g"), String(v));
    }
  }
  return str;
}
