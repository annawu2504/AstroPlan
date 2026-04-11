/**
 * useSseStream — singleton SSE connection with buffered reconnect reconciliation.
 *
 * State machine:
 *   CONNECTING → OPEN → RECONCILING → LIVE
 *                  ↓                    ↓
 *                ERROR → CONNECTING ←───┘
 *
 * On reconnect: incoming events are buffered while /plan/snapshot is fetched.
 * Snapshot is applied as ground truth; buffered events newer than snapshot.as_of
 * are then replayed in order before switching to live delta mode.
 */
import { useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { SSE_URL, RECONNECT_BACKOFF } from '@/lib/constants'
import { getPlanSnapshot } from '@/api/astroplan'
import { useConnectionStore } from '@/stores/connection'
import { usePlanStore } from '@/stores/plan'
import { useHitlStore } from '@/stores/hitl'
import { useMissionStore } from '@/stores/mission'
import { useCommandStore } from '@/stores/command'
import type {
  SseEvent,
  PlanGeneratedPayload,
  NodeStatusPayload,
  ReplanTriggeredPayload,
  HitlSuspendedPayload,
  HitlResumedPayload,
  MissionCompletedPayload,
} from '@/api/astroplan'
import type { NodeStatusEnum } from '@/types/astroplan'

// ---------------------------------------------------------------------------
// Module-level singleton — one EventSource per browser tab
// ---------------------------------------------------------------------------

let _es: EventSource | null = null
let _isReconciling = false
let _buffer: SseEvent[] = []

// ---------------------------------------------------------------------------
// Delta dispatcher
// ---------------------------------------------------------------------------

function applyDelta(evt: SseEvent): void {
  const plan = usePlanStore.getState()
  const hitl = useHitlStore.getState()
  const mission = useMissionStore.getState()
  const command = useCommandStore.getState()

  switch (evt.event) {
    case 'plan_generated': {
      const p = evt.payload as unknown as PlanGeneratedPayload
      plan.addRevision(p.plan.revision_id, p.plan.nodes, p.plan.edges)
      mission.setActiveMission(mission.activeMission, mission.selectedLab)
      mission.setStatus('executing')
      break
    }
    case 'node_status': {
      const p = evt.payload as unknown as NodeStatusPayload
      plan.updateNodeStatus(p.node_id, p.status as NodeStatusEnum)
      break
    }
    case 'replan_triggered': {
      const p = evt.payload as unknown as ReplanTriggeredPayload
      plan.setReplanDiff({
        addedNodeIds: [],
        removedNodeIds: [p.failed_lineage],
        frozenNodeIds: [...plan.frozenNodeIds],
      })
      mission.setStatus('planning')
      break
    }
    case 'hitl_suspended': {
      const p = evt.payload as unknown as HitlSuspendedPayload
      hitl.pushGate(p.gate)
      mission.setStatus('suspended')
      break
    }
    case 'hitl_resumed': {
      const p = evt.payload as unknown as HitlResumedPayload
      hitl.resolveGate(p.gate_id, p.approved)
      if (mission.status === 'suspended') mission.setStatus('executing')
      break
    }
    case 'mission_completed': {
      const p = evt.payload as unknown as MissionCompletedPayload
      mission.finaliseRecord('completed', p.total_steps, p.replan_count)
      // Drain queued commands now that mission is done
      command.drainQueue()
      break
    }
    case 'mission_failed': {
      const p = evt.payload as unknown as MissionCompletedPayload
      mission.finaliseRecord('failed', p.total_steps, p.replan_count)
      command.drainQueue()
      break
    }
    case 'command_queued':
    case 'command_applied':
      // Already handled optimistically in CommandCenter
      break
  }
}

// ---------------------------------------------------------------------------
// Reconciliation: fetch snapshot, apply, drain buffer
// ---------------------------------------------------------------------------

async function reconcile(t: (key: string) => string): Promise<void> {
  _isReconciling = true
  useConnectionStore.getState().setStatus('reconciling')
  try {
    const snapshot = await getPlanSnapshot()
    // Apply snapshot as authoritative ground truth
    usePlanStore.getState().applySnapshot(snapshot)
    useHitlStore.getState().applySnapshot(snapshot.pending_gates)
    useMissionStore.getState().applySnapshot(snapshot)
    useConnectionStore.getState().setReconciledAt(Date.now())

    // Replay buffered events newer than the snapshot
    const fresh = _buffer.filter((e) => e.timestamp > snapshot.as_of)
    _buffer = []
    fresh.forEach(applyDelta)

    useConnectionStore.getState().setStatus('live')
    useConnectionStore.getState().resetReconnect()
    toast.success(t('connection.reconciled'))
  } catch {
    useConnectionStore.getState().setStatus('error')
    toast.error(t('errors.snapshotFailed'))
    _buffer = []
  } finally {
    _isReconciling = false
  }
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useSseStream(): void {
  const { t } = useTranslation()
  const reconnectTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)

  function connect(): void {
    if (_es) {
      _es.close()
      _es = null
    }

    useConnectionStore.getState().setStatus('connecting')
    _es = new EventSource(SSE_URL)

    _es.onopen = () => {
      // Immediately reconcile to get fresh state
      reconcile(t)
    }

    _es.onmessage = (raw: MessageEvent<string>) => {
      let evt: SseEvent
      try {
        evt = JSON.parse(raw.data) as SseEvent
      } catch {
        return
      }

      if (_isReconciling) {
        // Buffer while reconciling
        _buffer.push(evt)
        return
      }
      applyDelta(evt)
    }

    _es.onerror = () => {
      if (_es?.readyState === EventSource.CLOSED) {
        useConnectionStore.getState().setStatus('error')
        useConnectionStore.getState().incrementReconnect()
        const delay = useConnectionStore.getState().nextBackoffSeconds()
        reconnectTimerRef.current = setTimeout(() => connect(), delay * 1000)
      }
    }
  }

  useEffect(() => {
    connect()
    return () => {
      reconnectTimerRef.current && clearTimeout(reconnectTimerRef.current)
      _es?.close()
      _es = null
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])
}
