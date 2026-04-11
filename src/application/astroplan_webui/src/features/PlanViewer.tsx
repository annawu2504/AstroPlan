import { useTranslation } from 'react-i18next'
import { DagCanvas } from '@/components/dag/DagCanvas'
import { usePlanStore } from '@/stores/plan'
import { ScrollArea } from '@/components/ui/ScrollArea'
import { Badge } from '@/components/ui/Badge'

function NodeDetailPanel() {
  const { t } = useTranslation()
  const selectedId = usePlanStore.use.selectedNodeId()
  const nodes = usePlanStore.getState().currentNodes()
  const node = nodes.find((n) => n.node_id === selectedId)

  if (!node) {
    return (
      <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
        {t('plan.nodeDetail')}
      </div>
    )
  }

  return (
    <ScrollArea className="h-full">
      <div className="space-y-3 p-4 text-sm">
        <h3 className="font-semibold">{t('plan.nodeDetail')}</h3>
        <Row label={t('plan.nodeId')} value={node.node_id} mono />
        <Row label={t('plan.lineageId')} value={node.lineage_id} mono />
        <Row label={t('plan.skillName')} value={node.skill_name} />
        <Row
          label={t('plan.interruptible')}
          value={
            node.interruptible ? (
              <Badge variant="outline">{t('plan.interruptible')}</Badge>
            ) : (
              <Badge variant="warning">{t('plan.nonInterruptible')}</Badge>
            )
          }
        />
        {Object.keys(node.params).length > 0 && (
          <div>
            <p className="mb-1 font-medium">{t('plan.params')}</p>
            <pre className="overflow-x-auto rounded bg-muted p-2 text-xs">
              {JSON.stringify(node.params, null, 2)}
            </pre>
          </div>
        )}
        {node.tool_hints.length > 0 && (
          <Row label={t('plan.toolHints')} value={node.tool_hints.join(', ')} />
        )}
        {node.required_roles.length > 0 && (
          <Row label={t('plan.requiredRoles')} value={node.required_roles.join(', ')} />
        )}
      </div>
    </ScrollArea>
  )
}

function Row({
  label,
  value,
  mono = false,
}: {
  label: string
  value: React.ReactNode
  mono?: boolean
}) {
  return (
    <div>
      <p className="text-xs text-muted-foreground">{label}</p>
      {typeof value === 'string' ? (
        <p className={mono ? 'font-mono text-xs' : ''}>{value}</p>
      ) : (
        value
      )}
    </div>
  )
}

export function PlanViewer() {
  return (
    <div className="flex h-full overflow-hidden">
      {/* Main canvas */}
      <div className="flex-1 overflow-hidden">
        <DagCanvas />
      </div>

      {/* Detail panel — fixed right sidebar */}
      <div className="flex w-64 flex-shrink-0 flex-col border-l">
        <NodeDetailPanel />
      </div>
    </div>
  )
}
