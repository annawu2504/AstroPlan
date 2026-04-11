import { useTranslation } from 'react-i18next'
import { Loader2, WifiOff } from 'lucide-react'
import { useConnectionStore } from '@/stores/connection'
import { cn } from '@/lib/utils'

export function ConnectionBanner() {
  const { t } = useTranslation()
  const sseStatus = useConnectionStore.use.sseStatus()
  const attempt = useConnectionStore.use.reconnectAttempts()
  const nextIn = useConnectionStore.use.nextReconnectIn()

  if (sseStatus === 'live') return null

  const isError = sseStatus === 'error'
  const isReconciling = sseStatus === 'reconciling'
  const isConnecting = sseStatus === 'connecting' || sseStatus === 'open'

  return (
    <div
      data-testid="connection-banner"
      className={cn(
        'flex items-center gap-2 px-3 py-1.5 text-xs',
        isError
          ? 'bg-red-500 text-white'
          : isReconciling
            ? 'bg-blue-500 text-white'
            : 'bg-yellow-400 text-yellow-900',
      )}
      role="status"
      aria-live="polite"
    >
      {isError ? (
        <WifiOff className="h-3.5 w-3.5 flex-shrink-0" />
      ) : (
        <Loader2 className="h-3.5 w-3.5 flex-shrink-0 animate-spin" />
      )}

      {isReconciling && <span>{t('connection.reconciling')}</span>}
      {isConnecting && <span>{t('connection.connecting')}</span>}
      {isError && (
        <span>
          {t('connection.reconnecting', { attempt })}{' '}
          {nextIn > 0 && `— ${t('connection.reconnectIn', { seconds: nextIn })}`}
        </span>
      )}
    </div>
  )
}
