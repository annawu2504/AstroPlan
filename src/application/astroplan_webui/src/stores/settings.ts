import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import { createSelectors } from '@/lib/utils'
import type { Language } from '@/i18n'

export type Theme = 'dark' | 'light' | 'system'
export type TabId = 'mission' | 'plan' | 'hitl' | 'command' | 'api'

interface SettingsState {
  theme: Theme
  language: Language
  currentTab: TabId
  autoOpenHitlDialog: boolean
  dagSimplifiedView: boolean
  dagLayoutDirection: 'TB' | 'LR'

  setTheme: (t: Theme) => void
  setLanguage: (l: Language) => void
  setCurrentTab: (t: TabId) => void
  setAutoOpenHitlDialog: (v: boolean) => void
  setDagSimplifiedView: (v: boolean) => void
  setDagLayoutDirection: (d: 'TB' | 'LR') => void
}

const useSettingsStoreBase = create<SettingsState>()(
  persist(
    (set) => ({
      // Default: Simplified Chinese as per spec
      theme: 'system',
      language: 'zh',
      currentTab: 'mission',
      autoOpenHitlDialog: true,
      dagSimplifiedView: false,
      dagLayoutDirection: 'TB',

      setTheme: (theme) => set({ theme }),
      setLanguage: (language) => set({ language }),
      setCurrentTab: (currentTab) => set({ currentTab }),
      setAutoOpenHitlDialog: (autoOpenHitlDialog) => set({ autoOpenHitlDialog }),
      setDagSimplifiedView: (dagSimplifiedView) => set({ dagSimplifiedView }),
      setDagLayoutDirection: (dagLayoutDirection) => set({ dagLayoutDirection }),
    }),
    {
      name: 'astroplan-settings',
      storage: createJSONStorage(() => localStorage),
      version: 1,
    },
  ),
)

export const useSettingsStore = createSelectors(useSettingsStoreBase)
