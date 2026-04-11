/**
 * useCommandQueue — submits commands respecting mission state.
 *
 * If idle: POST /mission/start (treat command as new mission)
 * If executing/planning: queue for drain after mission_completed
 * If suspended: POST /command/inject immediately (safe injection point)
 */
import { useCallback } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { injectCommand, startMission } from '@/api/astroplan'
import { useMissionStore } from '@/stores/mission'
import { useCommandStore } from '@/stores/command'
import type { Command } from '@/stores/command'

export function useCommandQueue() {
  const { t } = useTranslation()

  const submit = useCallback(
    async (text: string): Promise<Command | null> => {
      if (!text.trim()) return null

      const missionStatus = useMissionStore.getState().status
      const selectedLab = useMissionStore.getState().selectedLab
      const commandStore = useCommandStore.getState()

      // Record in history immediately
      const cmd = commandStore.enqueue(text)
      commandStore.addToHistory({ ...cmd, status: 'queued' })

      try {
        if (missionStatus === 'idle' || missionStatus === 'completed' || missionStatus === 'failed') {
          // Start a new mission
          await startMission({ mission: text, lab: selectedLab })
          commandStore.updateStatus(cmd.id, 'applied')
          useMissionStore.getState().setActiveMission(text, selectedLab)
          return cmd
        }

        if (missionStatus === 'suspended') {
          // Safe injection point
          await injectCommand({ command: text })
          commandStore.updateStatus(cmd.id, 'injected')
          return cmd
        }

        // planning | executing — queue and notify backend
        const res = await injectCommand({ command: text })
        if (res.queued) {
          commandStore.updateStatus(cmd.id, 'queued')
          toast.info(t('command.queueInfo'))
        } else {
          commandStore.updateStatus(cmd.id, 'applied')
        }
        return cmd
      } catch {
        commandStore.updateStatus(cmd.id, 'failed')
        toast.error(t('errors.commandFailed'))
        return null
      }
    },
    [t],
  )

  return { submit }
}
