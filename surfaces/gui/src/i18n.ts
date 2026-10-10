/**
 * i18n initialization (react-i18next).
 *
 * Locale resources live in src/locales/*.json. English is the default and
 * fallback; the language follows the system locale unless the user picks one
 * explicitly in Settings (persisted in localStorage).
 */
import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";
import zh from "./locales/zh.json";

const STORAGE_KEY = "openworker.lang";

export const SUPPORTED_LANGS = ["en", "zh"] as const;
export type Lang = (typeof SUPPORTED_LANGS)[number];

/** The user's explicit choice wins; otherwise follow the system locale. */
function resolveLang(): Lang {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && (SUPPORTED_LANGS as readonly string[]).includes(stored)) {
      return stored as Lang;
    }
  } catch {
    /* localStorage unavailable — fall through to system locale */
  }
  const nav = (typeof navigator !== "undefined" && navigator.language) || "";
  return nav.toLowerCase().startsWith("zh") ? "zh" : "en";
}

export async function initI18n() {
  await i18n.use(initReactI18next).init({
    resources: {
      en: { translation: en },
      zh: { translation: zh },
    },
    lng: resolveLang(),
    fallbackLng: "en",
    interpolation: { escapeValue: false }, // React already escapes
    returnNull: false,
  });
  return i18n;
}

/** Switch language at runtime and persist the choice. Pass null to follow the system locale again. */
export function setLanguage(lang: Lang | null) {
  try {
    if (lang === null) localStorage.removeItem(STORAGE_KEY);
    else localStorage.setItem(STORAGE_KEY, lang);
  } catch {
    /* persistence failure shouldn't block the switch */
  }
  return i18n.changeLanguage(lang ?? resolveLang());
}

/** The user's persisted choice, or null when following the system locale. */
export function getStoredLanguage(): Lang | null {
  try {
    const stored = localStorage.getItem(STORAGE_KEY);
    if (stored && (SUPPORTED_LANGS as readonly string[]).includes(stored)) {
      return stored as Lang;
    }
  } catch {
    /* ignore */
  }
  return null;
}

export function getCurrentLanguage(): Lang {
  const l = i18n.language;
  return (l && l.startsWith("zh") ? "zh" : "en") as Lang;
}
