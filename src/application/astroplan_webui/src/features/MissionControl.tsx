import { useTranslation } from 'react-i18next'
import { MissionInput } from '@/components/mission/MissionInput'
import { HistoryList } from '@/components/mission/HistoryList'
import { useMissionStore } from '@/stores/mission'
import { Badge } from '@/components/ui/Badge'
import type { MissionStatusEnum } from '@/types/astroplan'

const STATUS_VARIANT: Record<MissionStatusEnum, 'success' | 'destructive' | 'secondary' | 'outline' | 'warning'> = {
  completed: 'success',
  failed: 'destructive',
  idle: 'outline',
  planning: 'secondary',
  executing: 'secondary',
  suspended: 'warning',
}

export function MissionControl() {
  const { t } = useTranslation()
  const status = useMissionStore.use.status()
  const activeMission = useMissionStore.use.activeMission()

  return (
    <div className="flex h-full gap-4 overflow-hidden p-4">
      {/* Left: input */}
      <div className="flex w-[480px] flex-shrink-0 flex-col gap-4">
        <div className="flex items-center gap-2">
          <h2 className="text-lg font-semibold">{t('siteHeader.tabs.mission')}</h2>
          <Badge variant={STATUS_VARIANT[status]}>{t(`mission.status.${status}`)}</Badge>
        </div>

        {activeMission && (
          <div className="rounded-md bg-muted px-3 py-2 text-sm">
            <p className="text-xs text-muted-foreground">{t('mission.missionLabel')}</p>
            <p className="mt-0.5 line-clamp-2">{activeMission}</p>
          </div>
        )}

        <MissionInput />
      </div>

      {/* Right: history */}
      <div className="flex flex-1 flex-col gap-3 overflow-hidden">
        <h3 className="font-medium">{t('mission.history')}</h3>
        <HistoryList />
      </div>
    </div>
  )
}
