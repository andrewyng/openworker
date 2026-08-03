import { initReactI18next } from "react-i18next";
import i18n from "./i18n";
import en from "./locales/en.json";

// Tests assert against English UI text, so initialize i18n with the English
// bundle (the app defaults to zh, which would make t() return raw keys here).
i18n.use(initReactI18next).init({
  resources: {
    en: { translation: en },
  },
  lng: "en",
  fallbackLng: "en",
  interpolation: {
    escapeValue: false,
  },
});
