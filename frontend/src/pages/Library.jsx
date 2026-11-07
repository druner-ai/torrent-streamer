import { Play, Search, X } from 'lucide-react'
import AlbumGrid from '../components/AlbumGrid.jsx'
import { api } from '../api.js'

function fmtTime(s) {
  s = Math.max(0, Math.floor(s || 0))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

export default function Library({
  albums,
  loading,
  query,
  onQueryChange,
  onOpen,
  onPlay,
  onToggleFavorite,
  isPlayingAlbum,
  history = [],
  onResume,
  onHistoryRemove,
}) {
  const q = query.trim().toLowerCase()
  const filtered = q
    ? albums.filter(
        (a) =>
          (a.name || '').toLowerCase().includes(q) ||
          (a.artist || '').toLowerCase().includes(q),
      )
    : albums

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-8 md:px-6">
      <div className="mb-7 flex items-end justify-between gap-4">
        <h1 className="font-display text-[30px] font-bold leading-[38px] tracking-tight text-text-primary">
          Библиотека
        </h1>
        <div className="flex h-9 w-full max-w-[260px] items-center gap-2 rounded-sm border border-transparent bg-surface-inset px-3 focus-within:border-strong md:hidden">
          <Search size={16} className="shrink-0 text-text-muted" />
          <input
            value={query}
            onChange={(e) => onQueryChange(e.target.value)}
            placeholder="Поиск…"
            className="w-full bg-transparent text-[12.5px] text-text-primary placeholder:text-text-muted focus:outline-none"
          />
        </div>
      </div>

      {!q && history.length > 0 && (
        <div className="mb-8">
          <h2 className="mb-3 text-[13px] font-semibold uppercase tracking-wide text-text-muted">
            Продолжить слушать
          </h2>
          <div className="flex gap-3 overflow-x-auto pb-2">
            {history.map((h) => (
              <div key={h.album_id} className="group relative w-[132px] shrink-0">
                <button
                  type="button"
                  onClick={() => onResume(h.album_id, h.track_index || 0, h.position || 0)}
                  className="relative block w-full overflow-hidden rounded-md bg-surface-inset"
                  title="Продолжить с этого места"
                >
                  <img
                    src={api.coverUrl(h.album_id)}
                    alt={h.name || ''}
                    loading="lazy"
                    className="aspect-square w-full object-cover transition-transform group-hover:scale-105"
                  />
                  <span className="absolute inset-0 flex items-center justify-center bg-black/45 opacity-0 transition-opacity group-hover:opacity-100">
                    <span className="flex h-9 w-9 items-center justify-center rounded-full bg-primary text-on-primary">
                      <Play size={16} className="ml-0.5 fill-current" />
                    </span>
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => onHistoryRemove(h.album_id)}
                  aria-label="Убрать из истории"
                  className="absolute right-1 top-1 hidden rounded-full bg-black/60 p-1 text-text-secondary transition-colors hover:text-text-primary group-hover:block"
                >
                  <X size={12} />
                </button>
                <button
                  type="button"
                  onClick={() => onOpen(h.album_id)}
                  className="mt-1.5 block w-full truncate text-left text-[12px] font-medium text-text-primary hover:underline"
                >
                  {h.name}
                </button>
                <p className="truncate text-[11px] text-text-muted">
                  {h.artist} · трек {(h.track_index || 0) + 1} · {fmtTime(h.position)}
                </p>
              </div>
            ))}
          </div>
        </div>
      )}

      <AlbumGrid
        albums={filtered}
        loading={loading}
        onOpen={onOpen}
        onPlay={onPlay}
        onToggleFavorite={onToggleFavorite}
        isPlayingAlbum={isPlayingAlbum}
      />
    </div>
  )
}
