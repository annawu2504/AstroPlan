import i18n from 'i18next'
import { initReactI18next } from 'react-i18next'
import zh from './locales/zh.json'
import en from './locales/en.json'

i18n.use(initReactI18next).init({
  // Default is Simplified Chinese — no browser language detector installed
  lng: 'zh',
  fallbackLng: 'en',
  resources: {
    zh: { translation: zh },
    en: { translation: en },
  },
  interpolation: {
    escapeValue: false,
  },
  // Return key if translation is missing (prevents empty strings in production)
  returnNull: false,
})

export default i18n
export type Language = 'zh' | 'en'
