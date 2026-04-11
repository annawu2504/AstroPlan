import { useCallback } from 'react'
import { Toaster } from 'sonner'
import { SiteHeader } from '@/features/SiteHeader'
import { MissionControl } from '@/features/MissionControl'
import { PlanViewer } from '@/features/PlanViewer'
import { HitlConsole } from '@/features/HitlConsole'
import { CommandCenter } from '@/features/CommandCenter'
import { ApiSite } from '@/features/ApiSite'
import { ConnectionBanner } from '@/components/status/ConnectionBanner'
import { useSettingsStore, type TabId } from '@/stores/settings'
import { useSseStream } from '@/hooks/useSseStream'

const TAB_COMPONENTS: Record<TabId, React.ReactNode> = {
  mission: <MissionControl />,
  plan: <PlanViewer />,
  hitl: <HitlConsole />,
  command: <CommandCenter />,
  api: <ApiSite />,
}

export function App() {
  // Start SSE stream for the lifetime of the app
  useSseStream()

  const currentTab = useSettingsStore.use.currentTab()

  const handleTabChange = useCallback((tab: TabId) => {
    useSettingsStore.getState().setCurrentTab(tab)
  }, [])

  return (
    <div className="flex h-screen flex-col overflow-hidden bg-background text-foreground">
      <SiteHeader onTabChange={handleTabChange} />
      <ConnectionBanner />
      <main className="flex-1 overflow-hidden">
        {TAB_COMPONENTS[currentTab]}
      </main>
      <Toaster position="bottom-right" richColors closeButton />
    </div>
  )
}
