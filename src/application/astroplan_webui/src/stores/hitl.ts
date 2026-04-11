import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import { createSelectors } from '@/lib/utils'
import type { HITLGateSchema } from '@/types/astroplan'

interface HitlState {
  pendingGates: HITLGateSchema[]
  resolvedGates: Array<{ gate: HITLGateSchema; approved: boolean; resolvedAt: number }>

  pushGate: (gate: HITLGateSchema) => void
  resolveGate: (gateId: string, approved: boolean) => void
  applySnapshot: (gates: HITLGateSchema[]) => void
  clear: () => void

  activeGate: () => HITLGateSchema | null
}

const useHitlStoreBase = create<HitlState>()(
  persist(
    (set, get) => ({
      pendingGates: [],
      resolvedGates: [],

      pushGate: (gate) =>
        set((s) => ({
          pendingGates: s.pendingGates.some((g) => g.gate_id === gate.gate_id)
            ? s.pendingGates
            : [...s.pendingGates, gate],
        })),

      resolveGate: (gateId, approved) =>
        set((s) => {
          const gate = s.pendingGates.find((g) => g.gate_id === gateId)
          const newPending = s.pendingGates.filter((g) => g.gate_id !== gateId)
          const newResolved = gate
            ? [...s.resolvedGates, { gate, approved, resolvedAt: Date.now() }]
            : s.resolvedGates
          return { pendingGates: newPending, resolvedGates: newResolved }
        }),

      applySnapshot: (gates) => set({ pendingGates: gates }),

      clear: () => set({ pendingGates: [], resolvedGates: [] }),

      activeGate: () => get().pendingGates[0] ?? null,
    }),
    {
      name: 'hitl-storage',
      storage: createJSONStorage(() => sessionStorage),
      // Only persist pending gates so reloading restores them
      partialize: (s) => ({ pendingGates: s.pendingGates }),
    },
  ),
)

export const useHitlStore = createSelectors(useHitlStoreBase)
