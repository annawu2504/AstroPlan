/**
 * DagCanvas — React Flow canvas with Dagre layout, toolbar, and node detail panel.
 *
 * Layout is recomputed in a Web Worker only when revision or direction changes.
 * Node status changes update CSS classes without triggering re-layout.
 */
import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import {
  ReactFlow,
  Background,
  Controls,
  MiniMap,
  useReactFlow,
  ReactFlowProvider,
  type NodeMouseHandler,
} from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import { AlignLeft, AlignCenter, AlertTriangle, ChevronDown, ChevronRight } from 'lucide-react'
import { Button } from '@/components/ui/Button'
import { Badge } from '@/components/ui/Badge'
import { PlanNode } from '@/components/dag/PlanNode'
import { useDagLayout } from '@/hooks/useDagLayout'
import { usePlanStore } from '@/stores/plan'
import { useSettingsStore } from '@/stores/settings'
import { MIN_CANVAS_WIDTH, MIN_CANVAS_HEIGHT, DAG_LARGE_GRAPH_THRESHOLD } from '@/lib/constants'
import { cn } from '@/lib/utils'

const nodeTypes = { planNode: PlanNode }

function DagCanvasInner() {
  const { t } = useTranslation()
  const { fitView } = useReactFlow()

  const currentRevisionId = usePlanStore.use.currentRevisionId()
  const nodeStatuses = usePlanStore.use.nodeStatuses()
  const frozenNodeIds = usePlanStore.use.frozenNodeIds()
  const simplifiedView = usePlanStore.use.simplifiedView()
  const layoutDirection = usePlanStore.use.layoutDirection()
  const setSelectedNodeId = usePlanStore.getState().setSelectedNodeId

  const currentNodes = usePlanStore.getState().currentNodes()
  const currentEdges = usePlanStore.getState().currentEdges()

  const settingsSimplified = useSettingsStore.use.dagSimplifiedView()
  const settingsDirection = useSettingsStore.use.dagLayoutDirection()

  const { rfNodes, rfEdges, isLayouting, collapsedCount } = useDagLayout({
    nodes: currentNodes,
    edges: currentEdges,
    nodeStatuses,
    frozenNodeIds,
    revisionId: currentRevisionId,
    direction: settingsDirection,
    simplified: settingsSimplified || simplifiedView,
  })

  const onNodeClick: NodeMouseHandler = useCallback(
    (_evt, node) => setSelectedNodeId(node.id),
    [setSelectedNodeId],
  )

  const isLarge = currentNodes.length > DAG_LARGE_GRAPH_THRESHOLD

  return (
    <div
      className="relative flex flex-col"
      style={{ minWidth: MIN_CANVAS_WIDTH, minHeight: MIN_CANVAS_HEIGHT }}
    >
      {/* Toolbar */}
      <div className="flex items-center gap-2 border-b bg-background px-3 py-1.5">
        {/* Layout direction */}
        <Button
          variant="outline"
          size="sm"
          onClick={() => useSettingsStore.getState().setDagLayoutDirection('TB')}
          className={cn(settingsDirection === 'TB' && 'bg-accent')}
          aria-label={t('plan.layoutTB')}
        >
          <AlignCenter className="h-3.5 w-3.5" />
          <span>{t('plan.layoutTB')}</span>
        </Button>
        <Button
          variant="outline"
          size="sm"
          onClick={() => useSettingsStore.getState().setDagLayoutDirection('LR')}
          className={cn(settingsDirection === 'LR' && 'bg-accent')}
          aria-label={t('plan.layoutLR')}
        >
          <AlignLeft className="h-3.5 w-3.5" />
          <span>{t('plan.layoutLR')}</span>
        </Button>

        <div className="mx-1 h-4 w-px bg-border" />

        {/* Simplified / Full view */}
        <Button
          variant={settingsSimplified ? 'default' : 'outline'}
          size="sm"
          onClick={() =>
            useSettingsStore.getState().setDagSimplifiedView(!settingsSimplified)
          }
        >
          {settingsSimplified ? (
            <ChevronDown className="h-3.5 w-3.5" />
          ) : (
            <ChevronRight className="h-3.5 w-3.5" />
          )}
          <span>{settingsSimplified ? t('plan.fullView') : t('plan.simplifiedView')}</span>
        </Button>

        {settingsSimplified && collapsedCount > 0 && (
          <span className="text-xs text-muted-foreground">
            {t('plan.collapsedNodes', { count: collapsedCount })}
          </span>
        )}

        <Button
          variant="ghost"
          size="sm"
          onClick={() => fitView({ padding: 0.1, duration: 300 })}
          aria-label={t('plan.fitView')}
        >
          {t('plan.fitView')}
        </Button>

        <div className="flex-1" />

        {currentRevisionId && (
          <Badge variant="outline" data-testid="revision-badge">
            {t('plan.revision')} {currentRevisionId}
          </Badge>
        )}

        {isLayouting && (
          <span className="text-xs text-muted-foreground">{t('common.loading')}</span>
        )}
      </div>

      {/* Large graph warning */}
      {isLarge && !settingsSimplified && (
        <div className="flex items-center gap-2 border-b bg-yellow-50 px-3 py-1.5 text-sm text-yellow-800 dark:bg-yellow-900/30 dark:text-yellow-200">
          <AlertTriangle className="h-4 w-4 flex-shrink-0" />
          <span>{t('plan.largeGraphWarning', { count: currentNodes.length })}</span>
          <Button
            variant="ghost"
            size="sm"
            className="ml-auto h-6 text-yellow-800 hover:bg-yellow-100 dark:text-yellow-200"
            onClick={() => useSettingsStore.getState().setDagSimplifiedView(true)}
          >
            {t('plan.switchToSimplified')}
          </Button>
        </div>
      )}

      {/* Empty state */}
      {rfNodes.length === 0 && !isLayouting ? (
        <div className="flex flex-1 items-center justify-center text-sm text-muted-foreground">
          {t('plan.noData')}
        </div>
      ) : (
        <div className="flex-1">
          <ReactFlow
            nodes={rfNodes}
            edges={rfEdges}
            nodeTypes={nodeTypes}
            onNodeClick={onNodeClick}
            fitView
            fitViewOptions={{ padding: 0.1 }}
            attributionPosition="bottom-right"
            minZoom={0.1}
            maxZoom={2}
          >
            <Background />
            <Controls />
            <MiniMap
              nodeColor={(n) => {
                const status = (n.data as { status?: string })?.status
                const colors: Record<string, string> = {
                  pending: '#9ca3af',
                  running: '#3b82f6',
                  completed: '#22c55e',
                  failed: '#ef4444',
                  skipped: '#eab308',
                }
                return colors[status ?? 'pending'] ?? '#9ca3af'
              }}
              zoomable
              pannable
            />
          </ReactFlow>
        </div>
      )}
    </div>
  )
}

export function DagCanvas() {
  return (
    <ReactFlowProvider>
      <DagCanvasInner />
    </ReactFlowProvider>
  )
}
