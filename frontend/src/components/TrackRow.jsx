import { AlertCircle, Clock, Loader2, Pause, Play, Volume2 } from 'lucide-react'
import { fileStateLabel, fileStateTone, formatTime } from '../utils.js'

const stateText = {
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-danger',
  info: 'text-info',
  muted: 'text-text-muted',
}

function StateIcon({ state, playing }) {
  if (playing) return <Volume2 size={16} className="text-primary" />
  switch (state) {
    case 'ready':
      return <Play size={16} className="fill-current text-text-secondary" />
    case 'downloading':
    case 'queued':
      return <Loader2 size={16} className="animate-spin text-info" />
    case 'stalled':
      return <Clock size={16} className="text-warning" />
    case 'error':
      return <AlertCircle size={16} className="text-danger" />
    default:
      return <Play size={16} className="text-text-muted" />
  }
}

export default function TrackRow({ track, index, playing, onPlay }) {
  const tone = fileStateTone(track.state)
  const ready = track.state === 'ready'

  return (
    <button
      type="button"
      onClick={onPlay}
      className={`flex w-full items-center gap-3 px-4 py-2.5 text-left transition-colors hover:bg-surface-2 ${
        playing ? 'bg-primary-tint' : ''
      }`}
    >
      <span className="w-8 shrink-0 text-center font-mono text-[12px] text-text-muted">
        {playing ? <Pause size={16} className="mx-auto text-primary" /> : String(index + 1).padStart(2, '0')}
      </span>

      <div className="min-w-0 flex-1">
        <p
          className={`truncate text-[14px] leading-5 ${
            playing ? 'font-medium text-primary' : 'text-text-primary'
          }`}
        >
          {track.title || track.name}
        </p>
        <p className={`font-mono text-[11px] leading-4 ${stateText[tone]}`}>
          {fileStateLabel(track)}
        </p>
        {track.state === 'downloading' && (
          <div className="mt-1 h-1 w-full max-w-[180px] overflow-hidden rounded-full bg-surface-inset">
            <div
              className="h-full rounded-full bg-info"
              style={{ width: `${Math.min(100, Math.max(0, (track.progress || 0) * 100))}%` }}
            />
          </div>
        )}
      </div>

      <span className="hidden shrink-0 font-mono text-[12px] text-text-muted sm:inline">
        {track.duration != null ? formatTime(track.duration) : '—'}
      </span>
      <span className="flex w-8 shrink-0 items-center justify-center">
        <StateIcon state={track.state} playing={playing} />
      </span>
      {!ready && !playing && <span className="sr-only">недоступно</span>}
    </button>
  )
}
