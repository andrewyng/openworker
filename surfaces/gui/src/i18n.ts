import i18n from "i18next";
import { initReactI18next } from "react-i18next";

import en from "./locales/en.json";
import zh from "./locales/zh.json";

i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
    zh: { translation: zh },
  },
  lng: "zh",
  fallbackLng: "en",
  interpolation: {
    escapeValue: false,
  },
});

// Persistent locale override: the bundled zh.json is baked into this build, but a stale
// override in the app data dir survives app updates/reinstalls. We read it (deferred, never
// blocking startup) and deep-merge it over the bundled strings so any key the bundled file
// is missing still renders in Chinese. If no override exists yet, we write the current
// bundle once so a later rebuild (that might drift) keeps these translations.
const tauri = (globalThis as any).__TAURI__;
if (tauri?.core?.invoke) {
  window.setTimeout(async () => {
    try {
      const existing = await tauri.core.invoke("read_locale_override");
      if (typeof existing === "string" && existing) {
        const data = JSON.parse(existing);
        for (const lng of Object.keys(data)) {
          if (data[lng] && typeof data[lng] === "object") {
            i18n.addResourceBundle(lng, "translation", data[lng], true, true);
          }
        }
        if (i18n.isInitialized) i18n.emit("languageChanged");
      } else {
        const bundle = { en, zh };
        await tauri.core.invoke("write_locale_override", { content: JSON.stringify(bundle) });
      }
    } catch {
      // non-Tauri environment or file unavailable — bundled translations are used
    }
  }, 1500);
}

export default i18n;
