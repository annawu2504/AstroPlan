/**
 * ApprovalDialog — blocking HITL gate review modal.
 *
 * - Auto-opens when autoOpenHitlDialog is true and pendingGates.length > 0
 * - Cannot be dismissed while a gate is pending (Escape is blocked)
 * - Shows timeout countdown
 * - Operator can edit params before approving
 * - Approve/Reject buttons disable optimistically on submit
 */
import { useEffect, useState, useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Shield, Clock } from 'lucide-react'
import { respondHitl } from '@/api/astroplan'
import { useHitlStore } from '@/stores/hitl'
import { useSettingsStore } from '@/stores/settings'
import {
  Dialog,
  DialogContent,
  DialogHeader,
  DialogTitle,
  DialogDescription,
  DialogFooter,
} from '@/components/ui/Dialog'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import type { HITLGateSchema } from '@/types/astroplan'

// ---------------------------------------------------------------------------
// Timeout countdown hook
// ---------------------------------------------------------------------------

function useCountdown(gate: HITLGateSchema | null): number {
  const [remaining, setRemaining] = useState(0)
  useEffect(() => {
    if (!gate) return
    const deadline = gate.created_at * 1000 + gate.timeout_s * 1000
    const tick = () => setRemaining(Math.max(0, Math.round((deadline - Date.now()) / 1000)))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [gate])
  return remaining
}

// ---------------------------------------------------------------------------
// Param editor
// ---------------------------------------------------------------------------

function ParamEditor({
  params,
  onChange,
}: {
  params: Record<string, unknown>
  onChange: (p: Record<string, unknown>) => void
}) {
  const { t } = useTranslation()
  const entries = Object.entries(params)
  if (entries.length === 0) return null
  return (
    <div className="space-y-2">
      <p className="text-sm font-medium text-muted-foreground">{t('hitl.editParams')}</p>
      {entries.map(([key, val]) => (
        <div key={key} className="flex items-center gap-2">
          <label className="w-32 shrink-0 text-sm font-mono">{key}</label>
          <input
            type="text"
            className="flex-1 rounded-md border border-input bg-background px-2 py-1 text-sm font-mono focus:outline-none focus:ring-1 focus:ring-ring"
            defaultValue={String(val)}
            onChange={(e) => {
              const raw = e.target.value
              const num = Number(raw)
              onChange({ ...params, [key]: isNaN(num) ? raw : num })
            }}
            aria-label={key}
          />
        </div>
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// Main dialog
// ---------------------------------------------------------------------------

export function ApprovalDialog() {
  const { t } = useTranslation()
  const hitlStore = useHitlStore.getState
  const pendingGates = useHitlStore.use.pendingGates()
  const autoOpen = useSettingsStore.use.autoOpenHitlDialog()

  const gate = pendingGates[0] ?? null
  const remaining = useCountdown(gate)

  const [editedParams, setEditedParams] = useState<Record<string, unknown>>({})
  const [submitting, setSubmitting] = useState(false)
  const [open, setOpen] = useState(false)

  // Sync open state with pending gates
  useEffect(() => {
    if (gate && autoOpen) {
      setOpen(true)
      setEditedParams(gate.params)
    } else if (!gate) {
      setOpen(false)
    }
  }, [gate, autoOpen])

  // Block Escape from closing while gate is pending
  const handleOpenChange = useCallback(
    (next: boolean) => {
      if (!next && gate) return // prevent close while gate pending
      setOpen(next)
    },
    [gate],
  )

  const respond = useCallback(
    async (approved: boolean) => {
      if (!gate) return
      setSubmitting(true)
      try {
        await respondHitl({
          gate_id: gate.gate_id,
          approved,
          updated_constraints: approved && Object.keys(editedParams).length > 0 ? editedParams : undefined,
        })
        hitlStore().resolveGate(gate.gate_id, approved)
        toast.success(approved ? t('hitl.approveSuccess') : t('hitl.rejectSuccess'))
      } catch {
        toast.error(t('errors.hitlRespondFailed'))
      } finally {
        setSubmitting(false)
      }
    },
    [gate, editedParams, t, hitlStore],
  )

  if (!gate) return null

  const moreCount = pendingGates.length - 1

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent
        className="max-w-xl"
        onEscapeKeyDown={(e) => {
          if (gate) e.preventDefault() // block Escape
        }}
        aria-label={t('hitl.title')}
      >
        <DialogHeader>
          <DialogTitle className="flex items-center gap-2">
            <Shield className="h-5 w-5 text-orange-500" />
            {t('hitl.title')}
          </DialogTitle>
          <DialogDescription>{gate.reason}</DialogDescription>
        </DialogHeader>

        <div className="space-y-3 text-sm">
          <div className="flex flex-wrap gap-2">
            <Badge variant="outline">
              {t('hitl.criticalState')}: {gate.critical_state}
            </Badge>
            <Badge variant="outline">
              {t('hitl.skill')}: {gate.skill_name}
            </Badge>
            <Badge variant={remaining < 30 ? 'destructive' : 'secondary'}>
              <Clock className="mr-1 h-3 w-3" />
              {remaining > 0
                ? t('hitl.timeout', { seconds: remaining })
                : t('hitl.timeoutExpired')}
            </Badge>
          </div>

          <ParamEditor
            params={editedParams}
            onChange={setEditedParams}
          />

          {moreCount > 0 && (
            <p className="text-xs text-muted-foreground">
              {t('hitl.moreGates', { count: moreCount })}
            </p>
          )}
        </div>

        <DialogFooter className="gap-2">
          <Button
            variant="destructive"
            disabled={submitting}
            onClick={() => respond(false)}
            data-testid="hitl-reject-btn"
          >
            {submitting ? t('hitl.approving') : t('hitl.reject')}
          </Button>
          <Button
            disabled={submitting}
            onClick={() => respond(true)}
            data-testid="hitl-approve-btn"
          >
            {submitting ? t('hitl.approving') : t('hitl.approve')}
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
