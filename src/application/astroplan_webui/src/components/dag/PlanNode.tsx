/**
 * PlanNode — custom React Flow node renderer.
 *
 * Visual encoding:
 *   PENDING  → gray border
 *   RUNNING  → blue border + animated pulse ring
 *   COMPLETED → green border
 *   FAILED   → red border
 *   SKIPPED  → yellow border, reduced opacity
 *   frozen   → lock icon overlay
 *   interruptible=false → shield icon
 */
import { memo } from 'react'
import { Handle, Position } from '@xyflow/react'
import { Lock, Shield, CheckCircle2, XCircle, Clock, Loader2 } from 'lucide-react'
import { cn } from '@/lib/utils'
import type { NodeStatusEnum } from '@/types/astroplan'
import type { PlanNodeSchema } from '@/types/astroplan'

export interface PlanNodeData extends PlanNodeSchema {
  status: NodeStatusEnum
  isFrozen: boolean
}

const STATUS_STYLES: Record<NodeStatusEnum, string> = {
  pending: 'border-gray-400 bg-gray-50 text-gray-700 dark:bg-gray-800 dark:text-gray-300',
  running:
    'border-blue-500 bg-blue-50 text-blue-800 dark:bg-blue-900/40 dark:text-blue-200 ring-2 ring-blue-300 ring-offset-1 animate-pulse',
  completed: 'border-green-500 bg-green-50 text-green-800 dark:bg-green-900/40 dark:text-green-200',
  failed: 'border-red-500 bg-red-50 text-red-800 dark:bg-red-900/40 dark:text-red-200',
  skipped:
    'border-yellow-400 bg-yellow-50 text-yellow-700 dark:bg-yellow-900/30 dark:text-yellow-300 opacity-60',
}

const StatusIcon = ({ status }: { status: NodeStatusEnum }) => {
  switch (status) {
    case 'running':
      return <Loader2 className="h-3 w-3 animate-spin" />
    case 'completed':
      return <CheckCircle2 className="h-3 w-3" />
    case 'failed':
      return <XCircle className="h-3 w-3" />
    case 'skipped':
      return <Clock className="h-3 w-3" />
    default:
      return null
  }
}

function PlanNodeComponent({ data, selected }: { data: PlanNodeData; selected: boolean }) {
  return (
    <div
      className={cn(
        'relative flex w-[180px] flex-col rounded-md border-2 px-2.5 py-1.5 text-xs shadow-sm transition-shadow',
        STATUS_STYLES[data.status],
        selected && 'ring-2 ring-primary ring-offset-2',
      )}
      data-testid={`plan-node-${data.node_id}`}
      data-status={data.status}
    >
      <Handle type="target" position={Position.Top} className="!h-1.5 !w-1.5" />

      {/* Header row */}
      <div className="flex items-center gap-1">
        <StatusIcon status={data.status} />
        {data.isFrozen && <Lock className="h-3 w-3 text-gray-400" />}
        {!data.interruptible && <Shield className="h-3 w-3 text-orange-500" title="Non-interruptible" />}
        <span className="flex-1 truncate font-semibold leading-tight" title={data.skill_name}>
          {data.skill_name}
        </span>
      </div>

      {/* Lineage ID (subtle) */}
      <span className="mt-0.5 truncate font-mono text-[10px] opacity-50">{data.lineage_id}</span>

      <Handle type="source" position={Position.Bottom} className="!h-1.5 !w-1.5" />
    </div>
  )
}

export const PlanNode = memo(PlanNodeComponent)
PlanNode.displayName = 'PlanNode'
