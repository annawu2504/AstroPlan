import { useEffect, useState } from 'react'
import { useTranslation } from 'react-i18next'
import { Shield, Clock } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import type { HITLGateSchema } from '@/types/astroplan'

interface Props {
  gate: HITLGateSchema
  onSelect: (gate: HITLGateSchema) => void
}

export function PendingGateCard({ gate, onSelect }: Props) {
  const { t } = useTranslation()
  const [remaining, setRemaining] = useState(0)

  useEffect(() => {
    const deadline = gate.created_at * 1000 + gate.timeout_s * 1000
    const tick = () => setRemaining(Math.max(0, Math.round((deadline - Date.now()) / 1000)))
    tick()
    const id = setInterval(tick, 1000)
    return () => clearInterval(id)
  }, [gate])

  return (
    <div className="flex items-start justify-between rounded-lg border border-orange-200 bg-orange-50 p-3 dark:border-orange-800 dark:bg-orange-900/20">
      <div className="space-y-1">
        <div className="flex items-center gap-2">
          <Shield className="h-4 w-4 text-orange-500" />
          <span className="font-medium">{gate.skill_name}</span>
          <Badge variant={remaining < 30 ? 'destructive' : 'warning'}>
            <Clock className="mr-1 h-3 w-3" />
            {remaining > 0
              ? t('hitl.timeout', { seconds: remaining })
              : t('hitl.timeoutExpired')}
          </Badge>
        </div>
        <p className="text-sm text-muted-foreground">{gate.reason}</p>
        <p className="text-xs text-muted-foreground">
          {t('hitl.criticalState')}: {gate.critical_state}
        </p>
      </div>
      <Button size="sm" variant="outline" onClick={() => onSelect(gate)}>
        {t('hitl.approve')}
      </Button>
    </div>
  )
}
