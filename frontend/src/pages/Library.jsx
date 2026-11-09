import { ListMusic, Play, Plus, Search, Trash2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import AlbumGrid from '../components/AlbumGrid.jsx'
import PlaylistModal from '../components/PlaylistModal.jsx'
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
  onQueue,
  onToggleFavorite,
  isPlayingAlbum,
  history = [],
  onResume,
  onHistoryRemove,
  notify,
  onPlayPlaylist,
}) {
  const [playlists, setPlaylists] = useState([])
  const [newPl, setNewPl] = useState('')
  const [openPlId, setOpenPlId] = useState(null)

  async function loadPlaylists() {
    try {
      const d = await api.playlists()
      setPlaylists(d.playlists || [])
    } catch {
      // молча — секция просто останется пустой
    }
  }

  useEffect(() => {
    loadPlaylists()
  }, [])

  async function createPlaylist() {
    const name = newPl.trim()
    if (!name) return
    try {
      await api.playlistCreate(name)
      setNewPl('')
      loadPlaylists()
    } catch (e) {
      notify?.(e.message, 'error')
    }
  }

  async function deletePlaylist(pl) {
    if (!window.confirm(`Удалить плейлист «${pl.name}»?`)) return
    try {
      await api.playlistDelete(pl.id)
      loadPlaylists()
    } catch (e) {
      notify?.(e.message, 'error')
    }
  }

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

      {!q && (
        <div className="mb-8">
          <h2 className="mb-3 text-[13px] font-semibold uppercase tracking-wide text-text-muted">
            Плейлисты
          </h2>
          <div className="space-y-1.5">
            {playlists.map((pl) => (
              <div
                key={pl.id}
                className="group flex items-center gap-3 rounded-md border border-border-subtle bg-surface-1 px-3 py-2"
              >
                <ListMusic size={17} className="shrink-0 text-primary" />
                <button
                  type="button"
                  onClick={() => setOpenPlId(pl.id)}
                  className="min-w-0 flex-1 truncate text-left text-[13.5px] font-medium text-text-primary hover:underline"
                >
                  {pl.name}{' '}
                  <span className="font-mono text-[11.5px] font-normal text-text-muted">
                    {pl.count} трек.
                  </span>
                </button>
                <button
                  type="button"
                  onClick={() => onPlayPlaylist(pl.id, 0)}
                  disabled={!pl.count}
                  aria-label="Играть плейлист"
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-text-secondary transition-colors hover:bg-surface-2 hover:text-primary disabled:opacity-40"
                >
                  <Play size={14} className="fill-current" />
                </button>
                <button
                  type="button"
                  onClick={() => deletePlaylist(pl)}
                  aria-label="Удалить плейлист"
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-text-muted opacity-0 transition-all hover:bg-danger/10 hover:text-danger group-hover:opacity-100"
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
            <div className="flex items-center gap-2 pt-1">
              <input
                value={newPl}
                onChange={(e) => setNewPl(e.target.value)}
                onKeyDown={(e) => e.key === 'Enter' && createPlaylist()}
                placeholder="Новый плейлист…"
                className="h-9 w-full max-w-[260px] rounded-sm border border-transparent bg-surface-inset px-3 text-[13px] text-text-primary placeholder:text-text-muted focus:border-strong focus:outline-none"
              />
              <button
                type="button"
                onClick={createPlaylist}
                aria-label="Создать плейлист"
                className="flex h-9 w-9 items-center justify-center rounded-sm bg-surface-1 text-text-secondary transition-colors hover:bg-surface-2 hover:text-primary"
              >
                <Plus size={16} />
              </button>
            </div>
          </div>
        </div>
      )}

      <AlbumGrid
        albums={filtered}
        loading={loading}
        onOpen={onOpen}
        onPlay={onPlay}
        onQueue={onQueue}
        onToggleFavorite={onToggleFavorite}
        isPlayingAlbum={isPlayingAlbum}
      />

      <PlaylistModal
        playlistId={openPlId}
        onClose={() => {
          setOpenPlId(null)
          loadPlaylists()
        }}
        notify={notify || (() => {})}
        onPlayPlaylist={onPlayPlaylist}
      />
    </div>
  )
}
