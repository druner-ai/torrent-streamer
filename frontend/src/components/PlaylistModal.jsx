import { ListMusic, Play, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function PlaylistModal({ playlistId, onClose, notify, onPlayPlaylist }) {
  const [data, setData] = useState(null)
  const [error, setError] = useState(null)

  useEffect(() => {
    if (playlistId == null) return
    let alive = true
    setError(null)
    api
      .playlistGet(playlistId)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e.message))
    return () => {
      alive = false
    }
  }, [playlistId])

  useEffect(() => {
    if (playlistId == null) return
    function onKey(e) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [playlistId, onClose])

  async function removeItem(itemId) {
    try {
      await api.playlistItemRemove(playlistId, itemId)
      setData((d) => d && { ...d, items: d.items.filter((i) => i.id !== itemId) })
    } catch (e) {
      notify(e.message, 'error')
    }
  }

  if (playlistId == null) return null
  const items = data?.items || []

  return (
    <>
      <div className="animate-fade-in fixed inset-0 z-[45] bg-overlay" onClick={onClose} />
      <div className="fixed inset-x-0 bottom-0 z-[50] flex max-h-[80dvh] flex-col overflow-hidden rounded-t-xl bg-surface-3 shadow-4 md:left-1/2 md:top-1/2 md:w-[560px] md:max-w-[calc(100vw-32px)] md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-lg">
        <div className="flex items-center gap-3 border-b border-border-subtle p-4 md:px-6">
          <ListMusic size={20} className="shrink-0 text-primary" />
          <h2 className="truncate text-[17px] font-semibold text-text-primary">
            {data?.name || 'Плейлист'}
          </h2>
          <span className="shrink-0 font-mono text-[12px] text-text-muted">
            {items.length} трек.
          </span>
          <div className="ml-auto flex items-center gap-2">
            <button
              type="button"
              onClick={() => onPlayPlaylist(playlistId, 0)}
              disabled={!items.length}
              className="flex h-9 items-center gap-2 rounded-sm bg-primary px-4 text-[13px] font-medium text-on-primary transition-colors hover:bg-primary-hover disabled:opacity-50"
            >
              <Play size={15} className="fill-current" />
              Играть
            </button>
            <button
              type="button"
              onClick={onClose}
              aria-label="Закрыть"
              className="flex h-8 w-8 items-center justify-center rounded-full bg-surface-2 text-text-secondary transition-colors hover:text-text-primary"
            >
              <X size={16} />
            </button>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto">
          {error && (
            <div className="m-4 rounded-sm bg-danger/10 p-3 text-[13px] text-danger">
              {error}
            </div>
          )}
          {!error && !data && (
            <div className="space-y-2 p-4">
              {Array.from({ length: 4 }).map((_, i) => (
                <div key={i} className="h-11 animate-pulse rounded-md bg-surface-2" />
              ))}
            </div>
          )}
          {!error && data && items.length === 0 && (
            <p className="py-10 text-center text-[13px] text-text-muted">
              Пусто — добавляйте альбомы кнопкой «В плейлист» в карточке альбома
            </p>
          )}
          {items.map((it, i) => (
            <div
              key={it.id}
              className="group flex items-center gap-3 border-b border-border-subtle px-4 py-2.5 last:border-0 hover:bg-surface-2 md:px-6"
            >
              <button
                type="button"
                onClick={() => onPlayPlaylist(playlistId, i)}
                aria-label="Играть с этого трека"
                className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full text-text-muted transition-colors hover:bg-surface-3 hover:text-primary"
              >
                <Play size={13} className="fill-current" />
              </button>
              <div className="min-w-0 flex-1">
                <p className="truncate text-[13px] text-text-primary">
                  {it.track || `трек ${(it.track_index || 0) + 1}`}
                </p>
                <p className="truncate text-[11.5px] text-text-muted">
                  {it.artist}
                  {it.album ? ` · ${it.album}` : ''}
                </p>
              </div>
              <button
                type="button"
                onClick={() => removeItem(it.id)}
                aria-label="Убрать из плейлиста"
                className="shrink-0 rounded-sm p-1.5 text-text-muted opacity-0 transition-all hover:text-danger group-hover:opacity-100"
              >
                <X size={14} />
              </button>
            </div>
          ))}
        </div>
      </div>
    </>
  )
}
