import { create } from 'zustand'
import { createSelectors } from '@/lib/utils'
import { RECONNECT_BACKOFF } from '@/lib/constants'

export type SseStatus = 'connecting' | 'open' | 'reconciling' | 'live' | 'error' | 'closed'

interface ConnectionState {
  sseStatus: SseStatus
  reconnectAttempts: number
  nextReconnectIn: number   // seconds shown in UI countdown
  reconciledAt: number | null
  backendHealthy: boolean

  setStatus: (s: SseStatus) => void
  setReconciledAt: (ts: number) => void
  incrementReconnect: () => void
  resetReconnect: () => void
  setBackendHealthy: (v: boolean) => void
  nextBackoffSeconds: () => number
}

const useConnectionStoreBase = create<ConnectionState>()((set, get) => ({
  sseStatus: 'connecting',
  reconnectAttempts: 0,
  nextReconnectIn: 0,
  reconciledAt: null,
  backendHealthy: true,

  setStatus: (sseStatus) => set({ sseStatus }),

  setReconciledAt: (ts) => set({ reconciledAt: ts }),

  incrementReconnect: () =>
    set((s) => {
      const attempts = s.reconnectAttempts + 1
      const delay = RECONNECT_BACKOFF[Math.min(attempts - 1, RECONNECT_BACKOFF.length - 1)]
      return { reconnectAttempts: attempts, nextReconnectIn: delay }
    }),

  resetReconnect: () =>
    set({ reconnectAttempts: 0, nextReconnectIn: 0 }),

  setBackendHealthy: (backendHealthy) => set({ backendHealthy }),

  nextBackoffSeconds: () => {
    const { reconnectAttempts } = get()
    return RECONNECT_BACKOFF[Math.min(reconnectAttempts, RECONNECT_BACKOFF.length - 1)]
  },
}))

export const useConnectionStore = createSelectors(useConnectionStoreBase)
