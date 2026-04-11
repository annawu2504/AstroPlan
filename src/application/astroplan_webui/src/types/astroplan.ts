/**
 * AUTO-GENERATED — do not edit manually.
 * Source: src/application/schemas.py → scripts/export_schema.py → openapi-typescript
 * Regenerate: bun run generate:types
 *
 * This placeholder is committed so the project compiles before the first
 * `generate:types` run. Run `bun run generate:types` to replace with the
 * real OpenAPI-derived types.
 */

export type NodeStatusEnum = 'pending' | 'running' | 'completed' | 'failed' | 'skipped'

export type MissionStatusEnum =
  | 'idle'
  | 'planning'
  | 'executing'
  | 'suspended'
  | 'completed'
  | 'failed'

export interface EdgeSchema {
  /** Source node_id */
  from: string
  /** Target node_id */
  to: string
}

export interface PlanNodeSchema {
  node_id: string
  /** Stable semantic ID; unchanged across replans */
  lineage_id: string
  skill_name: string
  params: Record<string, unknown>
  depends_on: string[]
  required_roles: string[]
  tool_hints: string[]
  /** False = requires HITL approval */
  interruptible: boolean
}

export interface PlanResponseSchema {
  revision_id: string
  nodes: PlanNodeSchema[]
  edges: EdgeSchema[]
}

export interface HITLGateSchema {
  gate_id: string
  critical_state: string
  reason: string
  skill_name: string
  params: Record<string, unknown>
  timeout_s: number
  /** Unix epoch seconds */
  created_at: number
}

export interface PlanSnapshotSchema {
  /** Unix epoch milliseconds of this snapshot */
  as_of: number
  revision_id?: string | null
  nodes: PlanNodeSchema[]
  edges: EdgeSchema[]
  node_statuses: Record<string, NodeStatusEnum>
  pending_gates: HITLGateSchema[]
  mission_status: MissionStatusEnum
  active_mission?: string | null
  selected_lab: string
  revisions: string[]
}

export interface StartMissionRequest {
  /** Natural-language mission description */
  mission: string
  lab: string
}

export interface StartMissionResponse {
  ok: boolean
  message: string
}

export interface HitlRespondRequest {
  gate_id: string
  approved: boolean
  updated_constraints?: Record<string, unknown> | null
}

export interface HitlRespondResponse {
  ok: boolean
  message: string
}

export interface InjectCommandRequest {
  /** Ground command or feedback text */
  command: string
}

export interface InjectCommandResponse {
  ok: boolean
  queued: boolean
  message: string
}

export interface HealthResponse {
  status: string
  mission_status: MissionStatusEnum
  pending_gates: number
  revision_id?: string | null
}

export interface LabListResponse {
  labs: string[]
}
