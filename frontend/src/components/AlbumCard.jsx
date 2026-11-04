import { Disc3, Heart, Pause, Play } from 'lucide-react'
import { useState } from 'react'
import { api } from '../api.js'
import { formatBadge, isHiRes, statusLabel, statusTone } from '../utils.js'

const toneText = {
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-danger',
  info: 'text-info',
  muted: 'text-text-muted',
}

const toneBar = {
  success: 'bg-success',
  warning: 'bg-warning',
  danger: 'bg-danger',
  info: 'bg-info',
  muted: 'bg-text-muted',
}

export default function AlbumCard({ album, onOpen, onPlay, onToggleFavorite, isPlaying }) {
  const [coverFailed, setCoverFailed] = useState(false)
  const badge = formatBadge(album.format)
  const showProgress = album.status !== 'complete'
  const tone = statusTone(album.status)

  return (
    <div
      onClick={() => onOpen(album.id)}
      className="group w-full cursor-pointer rounded-md border border-border-subtle bg-surface-1 p-3 shadow-1 transition-all duration-200 hover:-translate-y-0.5 hover:bg-surface-2 hover:shadow-2"
    >
      <div className="relative aspect-square w-full overflow-hidden rounded-sm">
        {coverFailed || !album.id ? (
          <div className="flex h-full w-full items-center justify-center bg-surface-2">
            <Disc3 size={32} className="text-text-muted" />
          </div>
        ) : (
          <img
            src={api.coverUrl(album.id)}
            alt={album.name}
            loading="lazy"
            onError={() => setCoverFailed(true)}
            className={`h-full w-full object-cover ${isPlaying ? 'ring-1 ring-primary' : ''}`}
          />
        )}

        {badge && (
          <span
            className={`absolute left-2 top-2 rounded-xs px-2 py-0.5 font-mono text-[11px] font-semibold uppercase tracking-wider ${
              isHiRes(album.format)
                ? 'border border-[#7A5B17] bg-[#3D2E0E] text-[#F0B429]'
                : 'bg-[#262A33] text-[#A7ADBA]'
            }`}
          >
            {badge}
          </span>
        )}

        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onToggleFavorite(album)
          }}
          aria-label={album.favorite ? 'Убрать из избранного' : 'В избранное'}
          className="absolute right-2 top-2 rounded-full bg-black/45 p-1.5 text-text-primary opacity-90 backdrop-blur-sm transition-opacity hover:opacity-100 md:opacity-0 md:group-hover:opacity-100"
        >
          <Heart size={14} className={album.favorite ? 'fill-primary text-primary' : ''} />
        </button>

        <button
          type="button"
          onClick={(e) => {
            e.stopPropagation()
            onPlay(album)
          }}
          aria-label={isPlaying ? 'Пауза' : 'Играть'}
          className="absolute bottom-2 right-2 flex h-10 w-10 items-center justify-center rounded-full bg-primary text-on-primary shadow-2 transition-all duration-200 hover:scale-105 hover:bg-primary-hover active:bg-primary-pressed md:translate-y-1 md:opacity-0 md:group-hover:translate-y-0 md:group-hover:opacity-100"
        >
          {isPlaying ? <Pause size={18} className="fill-current" /> : <Play size={18} className="ml-0.5 fill-current" />}
        </button>
      </div>

      <h3 className="mt-3 truncate text-[16px] font-semibold leading-[22px] text-text-primary">
        {album.name || 'Без названия'}
      </h3>
      <p className="truncate text-[12.5px] leading-[17px] text-text-secondary">
        {album.artist || 'Неизвестный исполнитель'}
      </p>

      {showProgress && (
        <div className="mt-2">
          <div className="flex items-center justify-between font-mono text-[11px]">
            <span className={toneText[tone]}>{statusLabel(album.status)}</span>
            {album.status === 'downloading' && (
              <span className="tnum text-text-muted">{Math.round(album.progress || 0)}%</span>
            )}
          </div>
          <div className="mt-1 h-1 overflow-hidden rounded-full bg-surface-inset">
            <div
              className={`h-full rounded-full ${toneBar[tone]}`}
              style={{ width: `${Math.min(100, Math.max(0, album.progress || 0))}%` }}
            />
          </div>
        </div>
      )}
    </div>
  )
}
