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
import zhTW from "./locales/zh-TW.json";

const STORAGE_KEY = "openworker.lang";

export const SUPPORTED_LANGS = ["en", "zh", "zh-TW"] as const;
export type Lang = (typeof SUPPORTED_LANGS)[number];

/** Keep the legacy `zh` preference Simplified; explicit scripts beat regions. */
function localeLanguage(locale: string): Lang {
  const [language, ...subtags] = locale.toLowerCase().split("-");
  if (language !== "zh") return "en";
  if (subtags.includes("hans")) return "zh";
  if (subtags.includes("hant") || subtags.some((tag) => ["tw", "hk", "mo"].includes(tag))) {
    return "zh-TW";
  }
  return "zh";
}

function syncDocumentLanguage() {
  document.documentElement.lang = getCurrentLanguage() === "zh" ? "zh-CN" : getCurrentLanguage();
}

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
  return localeLanguage(nav);
}

export async function initI18n() {
  await i18n.use(initReactI18next).init({
    resources: {
      en: { translation: en },
      zh: { translation: zh },
      "zh-TW": { translation: zhTW },
    },
    lng: resolveLang(),
    fallbackLng: "en",
    interpolation: { escapeValue: false }, // React already escapes
    returnNull: false,
  });
  syncDocumentLanguage();
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
  return i18n.changeLanguage(lang ?? resolveLang()).then((t) => {
    syncDocumentLanguage();
    return t;
  });
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
  return localeLanguage(i18n.language || "en");
}
