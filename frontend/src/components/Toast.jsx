import { AlertCircle, CheckCircle2, Info, X } from 'lucide-react'

const toneClass = {
  error: 'text-danger',
  success: 'text-success',
  info: 'text-info',
}

const iconFor = {
  error: AlertCircle,
  success: CheckCircle2,
  info: Info,
}

export default function Toast({ toasts, onDismiss }) {
  if (!toasts.length) return null

  return (
    <div className="fixed top-4 right-4 left-4 z-[60] flex flex-col items-center gap-2 md:left-auto md:items-end">
      {toasts.map((t) => {
        const Icon = iconFor[t.type] || Info
        return (
          <div
            key={t.id}
            className="animate-fade-in flex w-full max-w-sm items-start gap-3 rounded-lg bg-surface-3 p-3 shadow-3 ring-1 ring-border-subtle md:w-auto"
          >
            <Icon size={18} className={`mt-0.5 shrink-0 ${toneClass[t.type] || toneClass.info}`} />
            <p className="flex-1 text-[14px] leading-5 text-text-primary">{t.message}</p>
            <button
              type="button"
              onClick={() => onDismiss(t.id)}
              className="text-text-muted transition-colors hover:text-text-primary"
              aria-label="Закрыть"
            >
              <X size={16} />
            </button>
          </div>
        )
      })}
    </div>
  )
}
