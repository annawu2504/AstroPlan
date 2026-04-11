/**
 * Dagre layout worker — runs heavy layout computation off the main thread.
 * Exposed via Comlink so callers await the result without blocking UI.
 */
import * as Comlink from 'comlink'
import Dagre from '@dagrejs/dagre'

export interface LayoutNode {
  id: string
  width: number
  height: number
}

export interface LayoutEdge {
  source: string
  target: string
}

export interface LayoutResult {
  positions: Record<string, { x: number; y: number }>
  graphWidth: number
  graphHeight: number
}

const NODE_W = 180
const NODE_H = 60

const worker = {
  compute(
    nodes: LayoutNode[],
    edges: LayoutEdge[],
    direction: 'TB' | 'LR' = 'TB',
  ): LayoutResult {
    const g = new Dagre.graphlib.Graph()
    g.setDefaultEdgeLabel(() => ({}))
    g.setGraph({
      rankdir: direction,
      nodesep: 40,
      ranksep: 60,
      marginx: 20,
      marginy: 20,
    })

    for (const node of nodes) {
      g.setNode(node.id, { width: node.width ?? NODE_W, height: node.height ?? NODE_H })
    }
    for (const edge of edges) {
      g.setEdge(edge.source, edge.target)
    }

    Dagre.layout(g)

    const positions: Record<string, { x: number; y: number }> = {}
    for (const nodeId of g.nodes()) {
      const n = g.node(nodeId)
      positions[nodeId] = { x: n.x - (n.width ?? NODE_W) / 2, y: n.y - (n.height ?? NODE_H) / 2 }
    }

    const graph = g.graph()
    return {
      positions,
      graphWidth: graph.width ?? 800,
      graphHeight: graph.height ?? 600,
    }
  },
}

Comlink.expose(worker)
