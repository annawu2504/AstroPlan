/**
 * useDagLayout — converts plan nodes+edges to React Flow layout positions
 * using Dagre running in a Web Worker via Comlink.
 *
 * Layout is only recomputed when revisionId or layoutDirection changes,
 * NOT on every node status update (which only changes CSS classes).
 */
import { useState, useEffect, useRef, useCallback } from 'react'
import * as Comlink from 'comlink'
import type { Node as RFNode, Edge as RFEdge } from '@xyflow/react'
import type { PlanNodeSchema, EdgeSchema, NodeStatusEnum } from '@/types/astroplan'
import type { LayoutResult } from '@/workers/dagLayout.worker'

type WorkerApi = {
  compute: (
    nodes: { id: string; width: number; height: number }[],
    edges: { source: string; target: string }[],
    direction: 'TB' | 'LR',
  ) => LayoutResult
}

// Singleton worker — created once, reused
let _workerInstance: WorkerApi | null = null

function getWorker(): WorkerApi {
  if (!_workerInstance) {
    const w = new Worker(new URL('../workers/dagLayout.worker.ts', import.meta.url), {
      type: 'module',
    })
    _workerInstance = Comlink.wrap<WorkerApi>(w)
  }
  return _workerInstance
}

const NODE_W = 180
const NODE_H = 60

interface UseDagLayoutParams {
  nodes: PlanNodeSchema[]
  edges: EdgeSchema[]
  nodeStatuses: Record<string, NodeStatusEnum>
  frozenNodeIds: Set<string>
  revisionId: string | null
  direction: 'TB' | 'LR'
  simplified: boolean
}

interface UseDagLayoutResult {
  rfNodes: RFNode[]
  rfEdges: RFEdge[]
  isLayouting: boolean
  collapsedCount: number
}

/** Compute the simplified subgraph: active frontier + their ancestors. */
function computeSimplified(
  nodes: PlanNodeSchema[],
  edges: EdgeSchema[],
  statuses: Record<string, NodeStatusEnum>,
): { visible: Set<string>; collapsedCount: number } {
  const terminalStatuses: NodeStatusEnum[] = ['completed', 'skipped']
  const active = new Set<string>()

  // Build adjacency for ancestor lookup
  const parentOf: Record<string, string[]> = {}
  for (const n of nodes) {
    for (const dep of n.depends_on) {
      if (!parentOf[n.node_id]) parentOf[n.node_id] = []
      parentOf[n.node_id].push(dep)
    }
  }

  function addAncestors(id: string) {
    active.add(id)
    for (const parent of parentOf[id] ?? []) {
      if (!active.has(parent)) addAncestors(parent)
    }
  }

  for (const n of nodes) {
    const s = statuses[n.node_id]
    if (!s || !terminalStatuses.includes(s)) {
      addAncestors(n.node_id)
    }
  }

  return { visible: active, collapsedCount: nodes.length - active.size }
}

export function useDagLayout({
  nodes,
  edges,
  nodeStatuses,
  frozenNodeIds,
  revisionId,
  direction,
  simplified,
}: UseDagLayoutParams): UseDagLayoutResult {
  const [rfNodes, setRfNodes] = useState<RFNode[]>([])
  const [rfEdges, setRfEdges] = useState<RFEdge[]>([])
  const [isLayouting, setIsLayouting] = useState(false)
  const [collapsedCount, setCollapsedCount] = useState(0)
  const positionsRef = useRef<Record<string, { x: number; y: number }>>({})

  // Recompute layout only when revision or direction changes
  const prevRevisionKey = useRef<string>('')
  const revisionKey = `${revisionId}__${direction}`

  const recomputeLayout = useCallback(
    async (visibleNodes: PlanNodeSchema[], visibleEdges: EdgeSchema[]) => {
      setIsLayouting(true)
      try {
        const worker = getWorker()
        const result = await worker.compute(
          visibleNodes.map((n) => ({ id: n.node_id, width: NODE_W, height: NODE_H })),
          visibleEdges.map((e) => ({ source: e.from, target: e.to })),
          direction,
        )
        positionsRef.current = result.positions
      } catch {
        // Worker error — fall through with existing positions
      } finally {
        setIsLayouting(false)
      }
    },
    [direction],
  )

  // Effect 1: recompute layout positions when revision changes
  useEffect(() => {
    if (nodes.length === 0) {
      setRfNodes([])
      setRfEdges([])
      return
    }
    if (revisionKey === prevRevisionKey.current) return
    prevRevisionKey.current = revisionKey

    let visibleNodes = nodes
    let visibleEdges = edges
    let collapsed = 0

    if (simplified) {
      const { visible, collapsedCount: cc } = computeSimplified(nodes, edges, nodeStatuses)
      visibleNodes = nodes.filter((n) => visible.has(n.node_id))
      visibleEdges = edges.filter((e) => visible.has(e.from) && visible.has(e.to))
      collapsed = cc
    }

    setCollapsedCount(collapsed)
    recomputeLayout(visibleNodes, visibleEdges).then(() => {
      buildRfNodes(visibleNodes, visibleEdges)
    })
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [revisionKey, simplified, nodes.length])

  // Effect 2: rebuild RF nodes in-place when statuses change (no layout recompute)
  useEffect(() => {
    if (nodes.length === 0) return
    buildRfNodesFromPositions(nodes, edges)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [nodeStatuses, frozenNodeIds])

  function buildRfNodes(visibleNodes: PlanNodeSchema[], visibleEdges: EdgeSchema[]) {
    buildRfNodesFromPositions(visibleNodes, visibleEdges)
  }

  function buildRfNodesFromPositions(visibleNodes: PlanNodeSchema[], visibleEdges: EdgeSchema[]) {
    const pos = positionsRef.current
    const newNodes: RFNode[] = visibleNodes.map((n) => ({
      id: n.node_id,
      type: 'planNode',
      position: pos[n.node_id] ?? { x: 0, y: 0 },
      data: {
        ...n,
        status: nodeStatuses[n.node_id] ?? 'pending',
        isFrozen: frozenNodeIds.has(n.node_id),
      },
      width: NODE_W,
      height: NODE_H,
    }))

    const newEdges: RFEdge[] = visibleEdges.map((e) => ({
      id: `${e.from}→${e.to}`,
      source: e.from,
      target: e.to,
      type: 'smoothstep',
      animated: nodeStatuses[e.from] === 'running' || nodeStatuses[e.to] === 'running',
    }))

    setRfNodes(newNodes)
    setRfEdges(newEdges)
  }

  return { rfNodes, rfEdges, isLayouting, collapsedCount }
}
