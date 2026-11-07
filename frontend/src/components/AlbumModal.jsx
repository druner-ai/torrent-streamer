import { Disc3, Heart, ListMusic, ListPlus, Play, Trash2, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api.js'
import { formatSize, formatTime, statusLabel } from '../utils.js'
import TrackRow from './TrackRow.jsx'

export default function AlbumModal({
  album,
  albumId,
  open,
  onClose,
  onPlayTrack,
  onAddToQueue,
  onToggleFavorite,
  onDelete,
  player,
  notify,
}) {
  const [data, setData] = useState(null)
  const [loading, setLoading] = useState(true)
  const [loadError, setLoadError] = useState(null)
  const [coverFailed, setCoverFailed] = useState(false)
  const [plOpen, setPlOpen] = useState(false)
  const [plList, setPlList] = useState([])
  const [plNew, setPlNew] = useState('')

  useEffect(() => {
    if (!open || albumId == null) return
    let alive = true
    setLoading(true)
    setCoverFailed(false)

    async function load() {
      try {
        const d = await api.albumFiles(albumId)
        if (alive) {
          setData(d)
          setLoadError(null)
        }
      } catch (e) {
        if (alive) setLoadError(e.message)
      } finally {
        if (alive) setLoading(false)
      }
    }

    load()
    const id = setInterval(load, 3000)
    return () => {
      alive = false
      clearInterval(id)
    }
  }, [open, albumId])

  useEffect(() => {
    if (!open) return
    function onKey(e) {
      if (e.key === 'Escape') onClose()
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])

  const files = data?.files || []
  const totals = useMemo(() => {
    const duration = files.reduce((sum, f) => sum + (Number(f.duration) || 0), 0)
    const size = files.reduce((sum, f) => sum + (Number(f.size) || 0), 0)
    return { duration, size, count: files.length }
  }, [files])

  const firstReady = files.findIndex((f) => f.state === 'ready')
  const downloading = album?.status === 'downloading'

  if (!open) return null

  const handlePlay = () => {
    if (firstReady >= 0) onPlayTrack(firstReady)
    else notify('Нет готовых треков', 'info')
  }

  const handleQueue = () => {
    if (firstReady >= 0) onAddToQueue(firstReady, 'end')
    else notify('Нет готовых треков', 'info')
  }

  const handleDelete = () => {
    if (window.confirm('Удалить альбом из библиотеки?')) onDelete(albumId)
  }

  async function openPlaylists() {
    const opening = !plOpen
    setPlOpen(opening)
    if (opening) {
      try {
        const d = await api.playlists()
        setPlList(d.playlists || [])
      } catch {
        // список останется пустым — можно создать новый
      }
    }
  }

  async function addToPlaylist(pl) {
    try {
      await api.playlistAdd(pl.id, Number(albumId), -1)
      notify(`Добавлено в «${pl.name}»`, 'success')
      setPlOpen(false)
    } catch (e) {
      notify(e.message, 'error')
    }
  }

  async function createAndAdd() {
    const name = plNew.trim()
    if (!name) return
    try {
      const d = await api.playlistCreate(name)
      await api.playlistAdd(d.id, Number(albumId), -1)
      notify(`Создан «${name}», альбом добавлен`, 'success')
      setPlNew('')
      setPlOpen(false)
    } catch (e) {
      notify(e.message, 'error')
    }
  }

  return (
    <>
      <div className="animate-fade-in fixed inset-0 z-[45] bg-overlay" onClick={onClose} />
      <div className="fixed inset-x-0 bottom-0 z-[50] flex max-h-[85dvh] flex-col overflow-hidden rounded-t-xl bg-surface-3 shadow-4 md:left-1/2 md:top-1/2 md:max-h-[85dvh] md:w-[720px] md:max-w-[calc(100vw-32px)] md:-translate-x-1/2 md:-translate-y-1/2 md:rounded-lg">
        <button
          type="button"
          onClick={onClose}
          aria-label="Закрыть"
          className="absolute right-4 top-4 z-10 flex h-8 w-8 items-center justify-center rounded-full bg-surface-2 text-text-secondary transition-colors hover:text-text-primary"
        >
          <X size={18} />
        </button>

        {downloading && album?.progress != null && (
          <div className="h-1 w-full bg-surface-inset">
            <div
              className="h-full bg-primary transition-all"
              style={{ width: `${Math.min(100, album.progress)}%` }}
            />
          </div>
        )}

        <div className="flex gap-4 p-5 pb-4 md:gap-6 md:p-8 md:pb-6">
          <div className="h-28 w-28 shrink-0 overflow-hidden rounded-md shadow-2 md:h-40 md:w-40">
            {coverFailed ? (
              <div className="flex h-full w-full items-center justify-center bg-surface-2">
                <Disc3 size={40} className="text-text-muted" />
              </div>
            ) : (
              <img
                src={api.coverUrl(albumId)}
                alt={data?.name || ''}
                onError={() => setCoverFailed(true)}
                className="h-full w-full object-cover"
              />
            )}
          </div>

          <div className="min-w-0 flex-1 pr-8">
            <p className="font-mono text-[11px] font-semibold uppercase tracking-[0.07em] text-text-muted">
              Альбом{data?.year ? ` · ${data.year}` : ''}
            </p>
            <h1 className="mt-1 truncate text-[24px] font-bold leading-[32px] tracking-tight text-text-primary">
              {data?.name || album?.name || 'Загрузка…'}
            </h1>
            <p className="truncate text-[19px] font-semibold leading-[26px] text-text-secondary">
              {data?.artist || album?.artist || ''}
            </p>
            <p className="mt-1 font-mono text-[12px] text-text-muted">
              {totals.count} трек. · {formatTime(totals.duration)} · {formatSize(totals.size)}
            </p>

            {downloading && (
              <p className="mt-1 font-mono text-[11px] text-info">
                {statusLabel(album.status)} {Math.round(album.progress || 0)}%
              </p>
            )}

            <div className="mt-4 flex flex-wrap items-center gap-2 md:gap-3">
              <button
                type="button"
                onClick={handlePlay}
                disabled={loading}
                className="flex h-10 items-center gap-2 rounded-sm bg-primary px-5 text-[14px] font-medium text-on-primary transition-colors hover:bg-primary-hover active:bg-primary-pressed disabled:opacity-50 md:px-6"
              >
                <Play size={16} className="fill-current" />
                Играть
              </button>
              <button
                type="button"
                onClick={handleQueue}
                disabled={loading}
                className="flex h-10 items-center gap-2 rounded-sm border border-border-strong px-4 text-[14px] font-medium text-text-primary transition-colors hover:bg-surface-2 disabled:opacity-50"
              >
                <ListPlus size={16} />
                В очередь
              </button>
              <div className="relative">
                <button
                  type="button"
                  onClick={openPlaylists}
                  aria-label="Добавить в плейлист"
                  title="В плейлист"
                  className={`flex h-10 w-10 items-center justify-center rounded-sm border border-border-strong transition-colors hover:bg-surface-2 ${
                    plOpen ? 'text-primary' : 'text-text-secondary'
                  }`}
                >
                  <ListMusic size={18} />
                </button>
                {plOpen && (
                  <div className="absolute left-0 top-11 z-20 w-56 rounded-md border border-border bg-surface-3 p-2 shadow-4">
                    {plList.map((pl) => (
                      <button
                        key={pl.id}
                        type="button"
                        onClick={() => addToPlaylist(pl)}
                        className="block w-full truncate rounded-sm px-2 py-1.5 text-left text-[13px] text-text-primary transition-colors hover:bg-surface-2"
                      >
                        {pl.name} <span className="text-text-muted">({pl.count})</span>
                      </button>
                    ))}
                    {plList.length === 0 && (
                      <p className="px-2 py-1 text-[12px] text-text-muted">
                        Плейлистов пока нет
                      </p>
                    )}
                    <div className="mt-1 flex gap-1 border-t border-border-subtle pt-1.5">
                      <input
                        value={plNew}
                        onChange={(e) => setPlNew(e.target.value)}
                        onKeyDown={(e) => e.key === 'Enter' && createAndAdd()}
                        placeholder="Новый плейлист…"
                        className="min-w-0 flex-1 rounded-sm bg-surface-inset px-2 py-1 text-[12.5px] text-text-primary placeholder:text-text-muted focus:outline-none"
                      />
                      <button
                        type="button"
                        onClick={createAndAdd}
                        className="rounded-sm bg-primary px-2 text-[12px] font-medium text-on-primary transition-colors hover:bg-primary-hover"
                      >
                        +
                      </button>
                    </div>
                  </div>
                )}
              </div>
              <button
                type="button"
                onClick={() => onToggleFavorite(albumId)}
                aria-label={album?.favorite ? 'Убрать из избранного' : 'В избранное'}
                className={`flex h-10 w-10 items-center justify-center rounded-sm border border-border-strong transition-colors hover:bg-surface-2 ${
                  album?.favorite ? 'text-primary' : 'text-text-secondary'
                }`}
              >
                <Heart size={18} className={album?.favorite ? 'fill-primary' : ''} />
              </button>
              <button
                type="button"
                onClick={handleDelete}
                aria-label="Удалить альбом"
                className="flex h-10 w-10 items-center justify-center rounded-sm text-text-secondary transition-colors hover:bg-danger/10 hover:text-danger"
              >
                <Trash2 size={18} />
              </button>
            </div>
          </div>
        </div>

        <div className="min-h-0 flex-1 overflow-y-auto border-t border-border-subtle">
          {loading && (
            <div className="space-y-2 p-4">
              {Array.from({ length: 5 }).map((_, i) => (
                <div key={i} className="h-12 animate-pulse rounded-md bg-surface-2" />
              ))}
            </div>
          )}

          {!loading && loadError && (
            <div className="m-4 rounded-sm bg-danger/10 p-3 text-[14px] text-danger">
              {loadError}
            </div>
          )}

          {!loading && !loadError && files.length === 0 && (
            <div className="flex flex-col items-center gap-2 py-12 text-center">
              <Disc3 size={32} className="text-text-muted" />
              <p className="text-[14px] text-text-secondary">
                Треков пока нет — альбом ещё скачивается
              </p>
            </div>
          )}

          {!loading &&
            !loadError &&
            files.map((file, i) => (
              <TrackRow
                key={`${file.path}-${i}`}
                track={file}
                index={i}
                playing={
                  player.album_id === Number(albumId) && player.track_index === i
                }
                onPlay={() => onPlayTrack(i)}
              />
            ))}
        </div>
      </div>
    </>
  )
}
