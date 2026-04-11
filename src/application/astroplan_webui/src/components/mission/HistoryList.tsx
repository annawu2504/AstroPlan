import { useTranslation } from 'react-i18next'
import { useMissionStore } from '@/stores/mission'
import { Badge } from '@/components/ui/Badge'
import { ScrollArea } from '@/components/ui/ScrollArea'
import { formatTime } from '@/lib/utils'
import type { MissionStatusEnum } from '@/types/astroplan'

const STATUS_VARIANT: Record<MissionStatusEnum, 'success' | 'destructive' | 'secondary' | 'outline' | 'warning'> = {
  completed: 'success',
  failed: 'destructive',
  idle: 'outline',
  planning: 'secondary',
  executing: 'secondary',
  suspended: 'warning',
}

export function HistoryList() {
  const { t } = useTranslation()
  const log = useMissionStore.use.executionLog()

  if (log.length === 0) {
    return (
      <p className="py-4 text-center text-sm text-muted-foreground">{t('mission.historyEmpty')}</p>
    )
  }

  return (
    <ScrollArea className="h-80">
      <div className="space-y-2 pr-2">
        {log.map((record) => (
          <div
            key={record.id}
            className="rounded-lg border bg-card p-3 text-sm"
          >
            <div className="flex items-start justify-between gap-2">
              <p className="flex-1 truncate font-medium" title={record.mission}>
                {record.mission}
              </p>
              <Badge variant={STATUS_VARIANT[record.status]}>
                {t(`mission.status.${record.status}`)}
              </Badge>
            </div>
            <div className="mt-1.5 flex flex-wrap gap-x-4 gap-y-1 text-xs text-muted-foreground">
              <span>
                {t('mission.historyItem.lab')}: {record.lab}
              </span>
              <span>
                {t('mission.historyItem.steps')}: {record.totalSteps}
              </span>
              <span>
                {t('mission.historyItem.replans')}: {record.replanCount}
              </span>
              <span>
                {t('mission.historyItem.time')}: {formatTime(record.startedAt)}
              </span>
            </div>
          </div>
        ))}
      </div>
    </ScrollArea>
  )
}
