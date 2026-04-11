import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { Satellite } from 'lucide-react'
import { Tabs, TabsList, TabsTrigger } from '@/components/ui/Tabs'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { useSettingsStore, type TabId } from '@/stores/settings'
import { useHitlStore } from '@/stores/hitl'
import i18n from '@/i18n'

export function SiteHeader({ onTabChange }: { onTabChange: (tab: TabId) => void }) {
  const { t } = useTranslation()
  const currentTab = useSettingsStore.use.currentTab()
  const language = useSettingsStore.use.language()
  const pendingGates = useHitlStore.use.pendingGates()

  const handleTabChange = useCallback(
    (tab: string) => onTabChange(tab as TabId),
    [onTabChange],
  )

  const toggleLanguage = useCallback(() => {
    const next = language === 'zh' ? 'en' : 'zh'
    useSettingsStore.getState().setLanguage(next)
    i18n.changeLanguage(next)
  }, [language])

  return (
    <header className="sticky top-0 z-50 flex h-12 w-full items-center border-b bg-background/95 px-4 backdrop-blur supports-[backdrop-filter]:bg-background/60">
      {/* Brand */}
      <div className="flex min-w-[220px] items-center gap-2">
        <Satellite className="h-5 w-5 text-blue-500" />
        <span className="font-bold">{t('siteHeader.title')}</span>
        <span className="hidden text-xs text-muted-foreground lg:inline">
          {t('siteHeader.subtitle')}
        </span>
      </div>

      {/* Tabs */}
      <Tabs value={currentTab} onValueChange={handleTabChange} className="flex-1">
        <TabsList className="mx-auto">
          <TabsTrigger value="mission">{t('siteHeader.tabs.mission')}</TabsTrigger>
          <TabsTrigger value="plan">{t('siteHeader.tabs.plan')}</TabsTrigger>
          <TabsTrigger value="hitl" className="relative" data-testid="hitl-tab">
            {t('siteHeader.tabs.hitl')}
            {pendingGates.length > 0 && (
              <Badge
                variant="destructive"
                className="absolute -right-1 -top-1 h-4 min-w-4 rounded-full px-1 text-[10px]"
                data-testid="hitl-tab-badge"
                aria-label={t('hitl.badgeLabel', { count: pendingGates.length })}
              >
                {pendingGates.length}
              </Badge>
            )}
          </TabsTrigger>
          <TabsTrigger value="command">{t('siteHeader.tabs.command')}</TabsTrigger>
          <TabsTrigger value="api">{t('siteHeader.tabs.api')}</TabsTrigger>
        </TabsList>
      </Tabs>

      {/* Right controls */}
      <div className="flex min-w-[220px] items-center justify-end gap-2">
        <Button
          variant="ghost"
          size="sm"
          onClick={toggleLanguage}
          aria-label={t('settings.language')}
        >
          {language === 'zh' ? 'EN' : '中文'}
        </Button>
      </div>
    </header>
  )
}
