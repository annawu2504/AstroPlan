import { useState, useEffect } from 'react'
import { useTranslation } from 'react-i18next'
import { Code2, ExternalLink, RefreshCw } from 'lucide-react'
import { getHealth, getLabs } from '@/api/astroplan'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { ScrollArea } from '@/components/ui/ScrollArea'

interface HealthData {
  status: string
  mission_status: string
  pending_gates: number
  command_queue: number
  revisions: number
}

interface EndpointDoc {
  method: 'GET' | 'POST'
  path: string
  descKey: string
}

const ENDPOINTS: EndpointDoc[] = [
  { method: 'GET', path: '/health', descKey: 'api.endpoints.health' },
  { method: 'GET', path: '/labs', descKey: 'api.endpoints.labs' },
  { method: 'GET', path: '/events', descKey: 'api.endpoints.events' },
  { method: 'GET', path: '/plan/snapshot', descKey: 'api.endpoints.snapshot' },
  { method: 'POST', path: '/mission/start', descKey: 'api.endpoints.missionStart' },
  { method: 'POST', path: '/mission/stop', descKey: 'api.endpoints.missionStop' },
  { method: 'POST', path: '/hitl/respond', descKey: 'api.endpoints.hitlRespond' },
  { method: 'POST', path: '/command/inject', descKey: 'api.endpoints.commandInject' },
]

const METHOD_VARIANT: Record<'GET' | 'POST', 'outline' | 'secondary'> = {
  GET: 'outline',
  POST: 'secondary',
}

export function ApiSite() {
  const { t } = useTranslation()
  const [health, setHealth] = useState<HealthData | null>(null)
  const [labs, setLabs] = useState<string[]>([])
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = async () => {
    setLoading(true)
    setError(null)
    try {
      const [h, l] = await Promise.all([getHealth(), getLabs()])
      setHealth(h as unknown as HealthData)
      setLabs(l)
    } catch {
      setError(t('errors.fetchFailed'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    refresh()
  }, []) // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div className="flex h-full flex-col gap-4 overflow-hidden p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Code2 className="h-5 w-5 text-purple-500" />
          <h2 className="text-lg font-semibold">{t('siteHeader.tabs.api')}</h2>
        </div>
        <div className="flex items-center gap-2">
          <Button
            size="sm"
            variant="outline"
            onClick={refresh}
            disabled={loading}
            className="gap-1.5"
          >
            <RefreshCw className={`h-3.5 w-3.5 ${loading ? 'animate-spin' : ''}`} />
            {t('common.refresh')}
          </Button>
          <Button
            size="sm"
            variant="ghost"
            asChild
          >
            <a href="/docs" target="_blank" rel="noopener noreferrer" className="gap-1.5 inline-flex items-center">
              <ExternalLink className="h-3.5 w-3.5" />
              {t('api.swaggerDocs')}
            </a>
          </Button>
        </div>
      </div>

      <div className="flex flex-1 gap-4 overflow-hidden">
        {/* Left: server status */}
        <div className="flex w-72 flex-shrink-0 flex-col gap-4">
          <div className="rounded-lg border p-4">
            <h3 className="mb-3 text-sm font-medium">{t('api.serverStatus')}</h3>
            {error ? (
              <p className="text-sm text-destructive">{error}</p>
            ) : health ? (
              <div className="space-y-2 text-sm">
                <Row label={t('api.status')} value={
                  <Badge variant={health.status === 'ok' ? 'success' : 'destructive'}>
                    {health.status}
                  </Badge>
                } />
                <Row label={t('api.missionStatus')} value={
                  <Badge variant="secondary">{health.mission_status}</Badge>
                } />
                <Row label={t('api.pendingGates')} value={String(health.pending_gates)} />
                <Row label={t('api.commandQueue')} value={String(health.command_queue)} />
                <Row label={t('api.revisions')} value={String(health.revisions)} />
              </div>
            ) : (
              <p className="text-sm text-muted-foreground">{t('api.loading')}</p>
            )}
          </div>

          {labs.length > 0 && (
            <div className="rounded-lg border p-4">
              <h3 className="mb-3 text-sm font-medium">{t('api.availableLabs')}</h3>
              <div className="flex flex-wrap gap-2">
                {labs.map((lab) => (
                  <Badge key={lab} variant="outline">{lab}</Badge>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Right: endpoint reference */}
        <div className="flex flex-1 flex-col gap-3 overflow-hidden">
          <h3 className="text-sm font-medium text-muted-foreground">{t('api.endpointRef')}</h3>
          <ScrollArea className="flex-1">
            <div className="space-y-2 pr-2">
              {ENDPOINTS.map((ep) => (
                <div
                  key={`${ep.method}-${ep.path}`}
                  className="flex items-start gap-3 rounded-lg border bg-card p-3"
                >
                  <Badge variant={METHOD_VARIANT[ep.method]} className="mt-0.5 shrink-0 font-mono">
                    {ep.method}
                  </Badge>
                  <div>
                    <p className="font-mono text-sm">{ep.path}</p>
                    <p className="mt-0.5 text-xs text-muted-foreground">{t(ep.descKey)}</p>
                  </div>
                </div>
              ))}
            </div>
          </ScrollArea>
        </div>
      </div>
    </div>
  )
}

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-center justify-between">
      <span className="text-muted-foreground">{label}</span>
      {typeof value === 'string' ? <span>{value}</span> : value}
    </div>
  )
}
