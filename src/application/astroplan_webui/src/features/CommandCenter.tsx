import { useRef } from 'react'
import { useTranslation } from 'react-i18next'
import { Terminal, Clock, CheckCircle2, XCircle, Loader2, SendHorizonal } from 'lucide-react'
import { useCommandStore } from '@/stores/command'
import { useCommandQueue } from '@/hooks/useCommandQueue'
import { ScrollArea } from '@/components/ui/ScrollArea'
import { Badge } from '@/components/ui/Badge'
import { Button } from '@/components/ui/Button'
import { Textarea } from '@/components/ui/Textarea'
import { formatTime } from '@/lib/utils'
import type { CommandStatus } from '@/stores/command'

const STATUS_VARIANT: Record<CommandStatus, 'outline' | 'secondary' | 'success' | 'destructive'> = {
  queued: 'outline',
  injected: 'secondary',
  applied: 'success',
  failed: 'destructive',
}

const STATUS_ICON: Record<CommandStatus, React.ReactNode> = {
  queued: <Clock className="h-3.5 w-3.5" />,
  injected: <Loader2 className="h-3.5 w-3.5 animate-spin" />,
  applied: <CheckCircle2 className="h-3.5 w-3.5" />,
  failed: <XCircle className="h-3.5 w-3.5" />,
}

export function CommandCenter() {
  const { t } = useTranslation()
  const queue = useCommandStore.use.queue()
  const history = useCommandStore.use.history()
  const { submit } = useCommandQueue()
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  const handleSubmit = async () => {
    const text = textareaRef.current?.value.trim()
    if (!text) return
    await submit(text)
    if (textareaRef.current) textareaRef.current.value = ''
  }

  const handleKeyDown = (e: React.KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && e.ctrlKey) {
      e.preventDefault()
      handleSubmit()
    }
  }

  return (
    <div className="flex h-full flex-col gap-4 overflow-hidden p-4">
      {/* Header */}
      <div className="flex items-center gap-2">
        <Terminal className="h-5 w-5 text-blue-500" />
        <h2 className="text-lg font-semibold">{t('siteHeader.tabs.command')}</h2>
        {queue.length > 0 && (
          <Badge variant="secondary">
            {t('command.queueCount', { count: queue.length })}
          </Badge>
        )}
      </div>

      <div className="flex flex-1 gap-4 overflow-hidden">
        {/* Left: input + active queue */}
        <div className="flex w-[420px] flex-shrink-0 flex-col gap-3">
          {/* Input */}
          <div className="flex flex-col gap-2 rounded-lg border p-3">
            <p className="text-xs text-muted-foreground">{t('command.inputHint')}</p>
            <Textarea
              ref={textareaRef}
              rows={4}
              placeholder={t('command.placeholder')}
              onKeyDown={handleKeyDown}
              className="resize-none text-sm"
            />
            <Button onClick={handleSubmit} size="sm" className="self-end gap-1.5">
              <SendHorizonal className="h-4 w-4" />
              {t('command.submit')}
            </Button>
          </div>

          {/* Active queue */}
          {queue.length > 0 && (
            <div>
              <p className="mb-2 text-sm font-medium">{t('command.activeQueue')}</p>
              <div className="space-y-2">
                {queue.map((cmd) => (
                  <div
                    key={cmd.id}
                    className="flex items-center gap-2 rounded-md border bg-muted/50 px-3 py-2 text-sm"
                  >
                    <span className="flex-1 truncate font-mono text-xs">{cmd.text}</span>
                    <Badge variant={STATUS_VARIANT[cmd.status]} className="gap-1">
                      {STATUS_ICON[cmd.status]}
                      {t(`command.status.${cmd.status}`)}
                    </Badge>
                  </div>
                ))}
              </div>
            </div>
          )}
        </div>

        {/* Right: history */}
        <div className="flex flex-1 flex-col gap-3 overflow-hidden">
          <h3 className="text-sm font-medium text-muted-foreground">{t('command.history')}</h3>
          {history.length === 0 ? (
            <p className="text-sm text-muted-foreground">{t('command.historyEmpty')}</p>
          ) : (
            <ScrollArea className="flex-1">
              <div className="space-y-2 pr-2">
                {history.map((cmd) => (
                  <div
                    key={`${cmd.id}-${cmd.timestamp}`}
                    className="rounded-lg border bg-card p-3 text-sm"
                  >
                    <div className="flex items-start justify-between gap-2">
                      <p className="flex-1 font-mono text-xs leading-relaxed">{cmd.text}</p>
                      <Badge variant={STATUS_VARIANT[cmd.status]} className="gap-1 shrink-0">
                        {STATUS_ICON[cmd.status]}
                        {t(`command.status.${cmd.status}`)}
                      </Badge>
                    </div>
                    <p className="mt-1.5 text-xs text-muted-foreground">
                      {formatTime(cmd.timestamp)}
                    </p>
                  </div>
                ))}
              </div>
            </ScrollArea>
          )}
        </div>
      </div>
    </div>
  )
}
