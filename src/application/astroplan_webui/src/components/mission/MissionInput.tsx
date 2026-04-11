import { useState, useEffect, useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { toast } from 'sonner'
import { Play, StopCircle } from 'lucide-react'
import { startMission, stopMission, getLabs } from '@/api/astroplan'
import { useMissionStore } from '@/stores/mission'
import { Button } from '@/components/ui/Button'
import { Textarea } from '@/components/ui/Textarea'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/Select'

export function MissionInput() {
  const { t } = useTranslation()
  const status = useMissionStore.use.status()
  const selectedLab = useMissionStore.use.selectedLab()

  const [mission, setMission] = useState('')
  const [labs, setLabs] = useState<string[]>([])
  const [lab, setLab] = useState(selectedLab)
  const [submitting, setSubmitting] = useState(false)
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const isRunning = status === 'planning' || status === 'executing' || status === 'suspended'

  useEffect(() => {
    getLabs().then(setLabs).catch(() => setLabs(['Fluid-Lab-Demo']))
  }, [])

  const handleSubmit = async () => {
    if (!mission.trim() || isRunning) return
    setSubmitting(true)
    try {
      await startMission({ mission: mission.trim(), lab })
      useMissionStore.getState().setActiveMission(mission.trim(), lab)
      toast.success(t('mission.startSuccess'))
      setMission('')
    } catch {
      toast.error(t('errors.missionStartFailed'))
    } finally {
      setSubmitting(false)
    }
  }

  const handleStop = async () => {
    try {
      await stopMission()
    } catch {
      // ignore
    }
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && (e.ctrlKey || e.metaKey)) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className="space-y-3">
      {/* Lab selector */}
      <div className="space-y-1">
        <label className="text-sm font-medium">{t('mission.labSelector')}</label>
        <Select value={lab} onValueChange={(v) => { setLab(v); useMissionStore.getState().setSelectedLab(v) }}>
          <SelectTrigger className="w-full" aria-label={t('mission.labSelector')}>
            <SelectValue placeholder={t('mission.labPlaceholder')} />
          </SelectTrigger>
          <SelectContent>
            {labs.map((l) => (
              <SelectItem key={l} value={l}>
                {l}
              </SelectItem>
            ))}
          </SelectContent>
        </Select>
      </div>

      {/* Mission textarea */}
      <div className="space-y-1">
        <label className="text-sm font-medium">{t('mission.missionLabel')}</label>
        <Textarea
          ref={textareaRef}
          value={mission}
          onChange={(e) => setMission(e.target.value)}
          onKeyDown={handleKeyDown}
          placeholder={t('mission.missionPlaceholder')}
          disabled={isRunning}
          rows={4}
          aria-label={t('mission.missionLabel')}
        />
        <p className="text-xs text-muted-foreground">Ctrl+Enter {t('mission.submit')}</p>
      </div>

      {/* Buttons */}
      <div className="flex gap-2">
        <Button
          className="flex-1"
          onClick={handleSubmit}
          disabled={!mission.trim() || isRunning || submitting}
        >
          <Play className="h-4 w-4" />
          {t('mission.submit')}
        </Button>
        {isRunning && (
          <Button variant="destructive" onClick={handleStop} aria-label={t('mission.stop')}>
            <StopCircle className="h-4 w-4" />
            {t('mission.stop')}
          </Button>
        )}
      </div>
    </div>
  )
}
