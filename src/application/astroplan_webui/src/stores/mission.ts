import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import { createSelectors } from '@/lib/utils'
import type { MissionStatusEnum, PlanSnapshotSchema } from '@/types/astroplan'

export interface MissionRecord {
  id: string
  lab: string
  mission: string
  status: MissionStatusEnum
  totalSteps: number
  replanCount: number
  startedAt: number
  finishedAt: number | null
}

interface MissionState {
  status: MissionStatusEnum
  activeMission: string | null
  selectedLab: string
  executionLog: MissionRecord[]
  currentRecord: MissionRecord | null

  setStatus: (s: MissionStatusEnum) => void
  setActiveMission: (mission: string | null, lab: string) => void
  finaliseRecord: (status: MissionStatusEnum, totalSteps: number, replanCount: number) => void
  applySnapshot: (snapshot: PlanSnapshotSchema) => void
  setSelectedLab: (lab: string) => void
}

const useMissionStoreBase = create<MissionState>()(
  persist(
    (set, get) => ({
      status: 'idle',
      activeMission: null,
      selectedLab: 'Fluid-Lab-Demo',
      executionLog: [],
      currentRecord: null,

      setStatus: (status) => set({ status }),

      setActiveMission: (activeMission, lab) => {
        const record: MissionRecord = {
          id: crypto.randomUUID(),
          lab,
          mission: activeMission ?? '',
          status: 'planning',
          totalSteps: 0,
          replanCount: 0,
          startedAt: Date.now(),
          finishedAt: null,
        }
        set({ activeMission, selectedLab: lab, currentRecord: record, status: 'planning' })
      },

      finaliseRecord: (status, totalSteps, replanCount) =>
        set((s) => {
          if (!s.currentRecord) return {}
          const finished: MissionRecord = {
            ...s.currentRecord,
            status,
            totalSteps,
            replanCount,
            finishedAt: Date.now(),
          }
          return {
            status,
            currentRecord: null,
            executionLog: [finished, ...s.executionLog].slice(0, 50),
          }
        }),

      applySnapshot: (snapshot) =>
        set({
          status: snapshot.mission_status,
          activeMission: snapshot.active_mission ?? null,
          selectedLab: snapshot.selected_lab,
        }),

      setSelectedLab: (selectedLab) => set({ selectedLab }),
    }),
    {
      name: 'mission-storage',
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({ executionLog: s.executionLog, selectedLab: s.selectedLab }),
    },
  ),
)

export const useMissionStore = createSelectors(useMissionStoreBase)
