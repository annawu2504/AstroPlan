import { useTranslation } from 'react-i18next'
import { Shield, CheckCircle2, XCircle } from 'lucide-react'
import { useHitlStore } from '@/stores/hitl'
import { useSettingsStore } from '@/stores/settings'
import { ApprovalDialog } from '@/components/hitl/ApprovalDialog'
import { PendingGateCard } from '@/components/hitl/PendingGateCard'
import { ScrollArea } from '@/components/ui/ScrollArea'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { formatTime } from '@/lib/utils'
import type { HITLGateSchema } from '@/types/astroplan'

export function HitlConsole() {
  const { t } = useTranslation()
  const pendingGates = useHitlStore.use.pendingGates()
  const resolvedGates = useHitlStore.use.resolvedGates()
  const autoOpen = useSettingsStore.use.autoOpenHitlDialog()

  const handleSelectGate = (_gate: HITLGateSchema) => {
    // Opening ApprovalDialog is handled via autoOpen setting + store state.
    // Manually selecting a gate sets it as first in queue by re-ordering.
    useHitlStore.getState().pushGate(_gate)
  }

  const toggleAutoOpen = () => {
    useSettingsStore.getState().setAutoOpenHitlDialog(!autoOpen)
  }

  return (
    <div className="flex h-full flex-col gap-4 overflow-hidden p-4">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div className="flex items-center gap-2">
          <Shield className="h-5 w-5 text-orange-500" />
          <h2 className="text-lg font-semibold">{t('siteHeader.tabs.hitl')}</h2>
          {pendingGates.length > 0 && (
            <Badge variant="destructive">{pendingGates.length}</Badge>
          )}
        </div>
        <Button
          size="sm"
          variant={autoOpen ? 'default' : 'outline'}
          onClick={toggleAutoOpen}
        >
          {autoOpen ? t('hitl.autoOpenOn') : t('hitl.autoOpenOff')}
        </Button>
      </div>

      <div className="flex flex-1 gap-4 overflow-hidden">
        {/* Left: pending gates */}
        <div className="flex w-[420px] flex-shrink-0 flex-col gap-3">
          <h3 className="text-sm font-medium text-muted-foreground">{t('hitl.pendingTitle')}</h3>
          {pendingGates.length === 0 ? (
            <div className="flex flex-1 items-center justify-center rounded-lg border border-dashed py-12 text-sm text-muted-foreground">
              {t('hitl.noPending')}
            </div>
          ) : (
            <ScrollArea className="flex-1">
              <div className="space-y-3 pr-2">
                {pendingGates.map((gate) => (
                  <PendingGateCard
                    key={gate.gate_id}
                    gate={gate}
                    onSelect={handleSelectGate}
                  />
                ))}
              </div>
            </ScrollArea>
          )}
        </div>

        {/* Right: resolved history */}
        <div className="flex flex-1 flex-col gap-3 overflow-hidden">
          <h3 className="text-sm font-medium text-muted-foreground">{t('hitl.resolvedTitle')}</h3>
          {resolvedGates.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('hitl.noResolved')}</p>
          ) : (
            <ScrollArea className="flex-1">
              <div className="space-y-2 pr-2">
                {[...resolvedGates].reverse().map(({ gate, approved, resolvedAt }) => (
                  <div
                    key={`${gate.gate_id}-${resolvedAt}`}
                    className="flex items-start gap-3 rounded-lg border bg-card p-3 text-sm"
                  >
                    {approved ? (
                      <CheckCircle2 className="mt-0.5 h-4 w-4 flex-shrink-0 text-green-500" />
                    ) : (
                      <XCircle className="mt-0.5 h-4 w-4 flex-shrink-0 text-red-500" />
                    )}
                    <div className="min-w-0 flex-1">
                      <p className="font-medium">{gate.skill_name}</p>
                      <p className="text-xs text-muted-foreground line-clamp-1">{gate.reason}</p>
                    </div>
                    <div className="flex flex-shrink-0 flex-col items-end gap-1">
                      <Badge variant={approved ? 'success' : 'destructive'}>
                        {approved ? t('hitl.approved') : t('hitl.rejected')}
                      </Badge>
                      <span className="text-xs text-muted-foreground">{formatTime(resolvedAt)}</span>
                    </div>
                  </div>
                ))}
              </div>
            </ScrollArea>
          )}
        </div>
      </div>

      {/* Approval dialog — auto-opens when autoOpen and pendingGates present */}
      <ApprovalDialog />
    </div>
  )
}
