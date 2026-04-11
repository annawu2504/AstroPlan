import { create } from 'zustand'
import { persist, createJSONStorage } from 'zustand/middleware'
import { createSelectors } from '@/lib/utils'
import { COMMAND_QUEUE_CAP } from '@/lib/constants'

export type CommandStatus = 'queued' | 'injected' | 'applied' | 'failed'

export interface Command {
  id: string
  text: string
  status: CommandStatus
  timestamp: number
}

interface CommandState {
  queue: Command[]
  history: Command[]

  enqueue: (text: string) => Command
  updateStatus: (id: string, status: CommandStatus) => void
  drainQueue: () => Command[]
  addToHistory: (cmd: Command) => void
  clear: () => void
}

const useCommandStoreBase = create<CommandState>()(
  persist(
    (set, get) => ({
      queue: [],
      history: [],

      enqueue: (text) => {
        const cmd: Command = {
          id: crypto.randomUUID(),
          text,
          status: 'queued',
          timestamp: Date.now(),
        }
        set((s) => {
          const newQueue = [...s.queue, cmd]
          // Enforce cap — drop oldest
          if (newQueue.length > COMMAND_QUEUE_CAP) newQueue.shift()
          return { queue: newQueue }
        })
        return cmd
      },

      updateStatus: (id, status) =>
        set((s) => ({
          queue: s.queue.map((c) => (c.id === id ? { ...c, status } : c)),
          history: s.history.map((c) => (c.id === id ? { ...c, status } : c)),
        })),

      drainQueue: () => {
        const { queue } = get()
        set({ queue: [] })
        return queue
      },

      addToHistory: (cmd) =>
        set((s) => ({ history: [cmd, ...s.history].slice(0, 100) })),

      clear: () => set({ queue: [], history: [] }),
    }),
    {
      name: 'command-storage',
      storage: createJSONStorage(() => localStorage),
      partialize: (s) => ({ history: s.history }),
    },
  ),
)

export const useCommandStore = createSelectors(useCommandStoreBase)
