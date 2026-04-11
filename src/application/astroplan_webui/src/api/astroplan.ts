/**
 * AstroPlan HTTP + SSE client.
 *
 * All types come from src/types/astroplan.ts which is AUTO-GENERATED.
 * Run `bun run generate:types` after changing backend schemas.
 */
import axios from 'axios'
import { BACKEND_BASE } from '@/lib/constants'
import type {
  HITLGateSchema,
  HitlRespondRequest,
  HitlRespondResponse,
  InjectCommandRequest,
  InjectCommandResponse,
  LabListResponse,
  PlanSnapshotSchema,
  StartMissionRequest,
  StartMissionResponse,
  HealthResponse,
} from '@/types/astroplan'

const http = axios.create({ baseURL: BACKEND_BASE, timeout: 10_000 })

// ---------------------------------------------------------------------------
// Health
// ---------------------------------------------------------------------------

export async function getHealth(): Promise<HealthResponse> {
  const { data } = await http.get<HealthResponse>('/health')
  return data
}

// ---------------------------------------------------------------------------
// Labs
// ---------------------------------------------------------------------------

export async function getLabs(): Promise<string[]> {
  const { data } = await http.get<LabListResponse>('/labs')
  return data.labs
}

// ---------------------------------------------------------------------------
// Mission
// ---------------------------------------------------------------------------

export async function startMission(body: StartMissionRequest): Promise<StartMissionResponse> {
  const { data } = await http.post<StartMissionResponse>('/mission/start', body)
  return data
}

export async function stopMission(): Promise<StartMissionResponse> {
  const { data } = await http.post<StartMissionResponse>('/mission/stop')
  return data
}

// ---------------------------------------------------------------------------
// Plan snapshot (used on SSE reconnect)
// ---------------------------------------------------------------------------

export async function getPlanSnapshot(): Promise<PlanSnapshotSchema> {
  const { data } = await http.get<PlanSnapshotSchema>('/plan/snapshot')
  return data
}

// ---------------------------------------------------------------------------
// HITL
// ---------------------------------------------------------------------------

export async function respondHitl(body: HitlRespondRequest): Promise<HitlRespondResponse> {
  const { data } = await http.post<HitlRespondResponse>('/hitl/respond', body)
  return data
}

// ---------------------------------------------------------------------------
// Command injection
// ---------------------------------------------------------------------------

export async function injectCommand(body: InjectCommandRequest): Promise<InjectCommandResponse> {
  const { data } = await http.post<InjectCommandResponse>('/command/inject', body)
  return data
}

// ---------------------------------------------------------------------------
// SSE event types (mirrored for frontend routing)
// ---------------------------------------------------------------------------

export type SseEventType =
  | 'plan_generated'
  | 'node_status'
  | 'replan_triggered'
  | 'hitl_suspended'
  | 'hitl_resumed'
  | 'mission_completed'
  | 'mission_failed'
  | 'command_queued'
  | 'command_applied'
  | 'legacy_tree'

export interface SseEvent {
  event: SseEventType
  revision_id?: string
  timestamp: number
  payload: Record<string, unknown>
}

export interface PlanGeneratedPayload {
  plan: {
    revision_id: string
    nodes: PlanNodePayload[]
    edges: EdgePayload[]
  }
  tree?: string
}

export interface PlanNodePayload {
  node_id: string
  lineage_id: string
  skill_name: string
  params: Record<string, unknown>
  depends_on: string[]
  required_roles: string[]
  tool_hints: string[]
  interruptible: boolean
}

export interface EdgePayload {
  from: string
  to: string
}

export interface NodeStatusPayload {
  node_id: string
  lineage_id: string
  status: string
}

export interface ReplanTriggeredPayload {
  failed_lineage: string
  old_revision_id: string
  reason: string
}

export interface HitlSuspendedPayload {
  gate: HITLGateSchema
}

export interface HitlResumedPayload {
  gate_id: string
  approved: boolean
}

export interface MissionCompletedPayload {
  status: string
  total_steps: number
  replan_count: number
}
