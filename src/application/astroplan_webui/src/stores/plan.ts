import { create } from 'zustand'
import { createSelectors } from '@/lib/utils'
import type { EdgeSchema, NodeStatusEnum, PlanNodeSchema, PlanSnapshotSchema } from '@/types/astroplan'

export interface ReplanDiff {
  addedNodeIds: string[]
  removedNodeIds: string[]
  frozenNodeIds: string[]
}

interface PlanState {
  // Keyed by revision_id
  revisions: string[]
  plansByRevision: Record<string, { nodes: PlanNodeSchema[]; edges: EdgeSchema[] }>
  currentRevisionId: string | null
  // Live node statuses (authoritative from SSE)
  nodeStatuses: Record<string, NodeStatusEnum>
  // Nodes that are frozen (completed in a prior revision)
  frozenNodeIds: Set<string>
  // Last replan diff
  replanDiff: ReplanDiff | null
  // Simplified view
  simplifiedView: boolean
  // DAG layout direction
  layoutDirection: 'TB' | 'LR'
  // Selected node for detail panel
  selectedNodeId: string | null

  addRevision: (revisionId: string, nodes: PlanNodeSchema[], edges: EdgeSchema[]) => void
  updateNodeStatus: (nodeId: string, status: NodeStatusEnum) => void
  setReplanDiff: (diff: ReplanDiff) => void
  setSimplifiedView: (v: boolean) => void
  setLayoutDirection: (d: 'TB' | 'LR') => void
  setSelectedNodeId: (id: string | null) => void
  applySnapshot: (snapshot: PlanSnapshotSchema) => void
  clear: () => void

  // Derived
  currentNodes: () => PlanNodeSchema[]
  currentEdges: () => EdgeSchema[]
}

const useplanStoreBase = create<PlanState>()((set, get) => ({
  revisions: [],
  plansByRevision: {},
  currentRevisionId: null,
  nodeStatuses: {},
  frozenNodeIds: new Set(),
  replanDiff: null,
  simplifiedView: false,
  layoutDirection: 'TB',
  selectedNodeId: null,

  addRevision: (revisionId, nodes, edges) =>
    set((s) => {
      // Compute frozen nodes: any node that was COMPLETED in the previous revision
      const newFrozen = new Set(s.frozenNodeIds)
      for (const [nid, status] of Object.entries(s.nodeStatuses)) {
        if (status === 'completed') newFrozen.add(nid)
      }
      // Initialise new nodes as pending unless already known
      const updatedStatuses = { ...s.nodeStatuses }
      for (const node of nodes) {
        if (!(node.node_id in updatedStatuses)) {
          updatedStatuses[node.node_id] = 'pending'
        }
      }
      return {
        revisions: s.revisions.includes(revisionId)
          ? s.revisions
          : [...s.revisions, revisionId],
        plansByRevision: {
          ...s.plansByRevision,
          [revisionId]: { nodes, edges },
        },
        currentRevisionId: revisionId,
        nodeStatuses: updatedStatuses,
        frozenNodeIds: newFrozen,
      }
    }),

  updateNodeStatus: (nodeId, status) =>
    set((s) => ({ nodeStatuses: { ...s.nodeStatuses, [nodeId]: status } })),

  setReplanDiff: (replanDiff) => set({ replanDiff }),

  setSimplifiedView: (simplifiedView) => set({ simplifiedView }),

  setLayoutDirection: (layoutDirection) => set({ layoutDirection }),

  setSelectedNodeId: (selectedNodeId) => set({ selectedNodeId }),

  applySnapshot: (snapshot) =>
    set(() => {
      const plans: Record<string, { nodes: PlanNodeSchema[]; edges: EdgeSchema[] }> = {}
      if (snapshot.revision_id) {
        plans[snapshot.revision_id] = { nodes: snapshot.nodes, edges: snapshot.edges }
      }
      return {
        revisions: snapshot.revisions,
        plansByRevision: plans,
        currentRevisionId: snapshot.revision_id ?? null,
        nodeStatuses: snapshot.node_statuses as Record<string, NodeStatusEnum>,
        frozenNodeIds: new Set<string>(),
        replanDiff: null,
      }
    }),

  clear: () =>
    set({
      revisions: [],
      plansByRevision: {},
      currentRevisionId: null,
      nodeStatuses: {},
      frozenNodeIds: new Set(),
      replanDiff: null,
      selectedNodeId: null,
    }),

  currentNodes: () => {
    const { currentRevisionId, plansByRevision } = get()
    if (!currentRevisionId) return []
    return plansByRevision[currentRevisionId]?.nodes ?? []
  },

  currentEdges: () => {
    const { currentRevisionId, plansByRevision } = get()
    if (!currentRevisionId) return []
    return plansByRevision[currentRevisionId]?.edges ?? []
  },
}))

export const usePlanStore = createSelectors(useplanStoreBase)
