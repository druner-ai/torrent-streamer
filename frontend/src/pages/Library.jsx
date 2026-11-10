import { ChevronDown, Disc3, ListMusic, Mic2, Music4, Play, Plus, Search, Trash2, X } from 'lucide-react'
import { useEffect, useMemo, useRef, useState } from 'react'
import AlbumGrid from '../components/AlbumGrid.jsx'
import PlaylistModal from '../components/PlaylistModal.jsx'
import { api } from '../api.js'
import { formatTime } from '../utils.js'

function fmtTime(s) {
  s = Math.max(0, Math.floor(s || 0))
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`
}

/* Компактный фильтр-дропдаун (вариант B): одна строка, меню со счётчиками.
   Определён на уровне модуля, чтобы не ремонтироваться при ререндерах родителя. */
function FilterDropdown({ label, options, value, onChange, labelFor = (v) => v }) {
  const [open, setOpen] = useState(false)
  const ref = useRef(null)
  useEffect(() => {
    if (!open) return undefined
    const onDown = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOpen(false)
    }
    document.addEventListener('mousedown', onDown)
    return () => document.removeEventListener('mousedown', onDown)
  }, [open])
  if (!options.length) return null
  return (
    <div ref={ref} className="relative shrink-0">
      <button
        type="button"
        onClick={() => setOpen((o) => !o)}
        className={`flex h-9 items-center gap-1.5 rounded-sm border px-3 text-[12.5px] transition-colors ${
          value
            ? 'border-primary/50 bg-primary-tint text-primary'
            : 'border-border-subtle bg-surface-1 text-text-secondary hover:bg-surface-2'
        }`}
      >
        <span className="text-[10.5px] uppercase tracking-wide opacity-60">{label}</span>
        <span className="max-w-[150px] truncate font-medium">{value ? labelFor(value) : 'Все'}</span>
        <ChevronDown size={13} className="shrink-0 opacity-70" />
      </button>
      {open && (
        <div className="absolute left-0 top-[42px] z-30 max-h-[320px] w-64 overflow-auto rounded-md border border-border-subtle bg-surface-2 py-1 shadow-3">
          <button
            type="button"
            onClick={() => {
              onChange('')
              setOpen(false)
            }}
            className={`flex w-full items-center justify-between px-3 py-2 text-left text-[12.5px] transition-colors hover:bg-surface-3 ${
              !value ? 'text-primary' : 'text-text-secondary'
            }`}
          >
            Все
          </button>
          {options.map(([v, n]) => (
            <button
              key={v}
              type="button"
              onClick={() => {
                onChange(value === v ? '' : v)
                setOpen(false)
              }}
              className={`flex w-full items-center justify-between gap-3 px-3 py-2 text-left text-[12.5px] transition-colors hover:bg-surface-3 ${
                value === v ? 'text-primary' : 'text-text-secondary'
              }`}
            >
              <span className="truncate">{labelFor(v)}</span>
              {n != null && (
                <span className="shrink-0 font-mono text-[10.5px] text-text-muted">{n}</span>
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
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
  onPlayTrack,
}) {
  const [playlists, setPlaylists] = useState([])
  const [newPl, setNewPl] = useState('')
  const [openPlId, setOpenPlId] = useState(null)
  const [tab, setTab] = useState('albums')
  const [fGenre, setFGenre] = useState('')
  const [fFormat, setFFormat] = useState('')
  const [fYear, setFYear] = useState('')
  const [fSource, setFSource] = useState('')
  const [fFav, setFFav] = useState(false)
  const [sort, setSort] = useState('added')
  const [trk, setTrk] = useState({ items: [], total: 0, loading: false })

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
  const filtersActive = Boolean(q || fGenre || fFormat || fYear || fSource || fFav)

  const filtered = useMemo(() => {
    let list = albums
    if (q) {
      list = list.filter(
        (a) =>
          (a.name || '').toLowerCase().includes(q) ||
          (a.artist || '').toLowerCase().includes(q),
      )
    }
    if (fGenre) list = list.filter((a) => (a.genre || '') === fGenre)
    if (fFormat) list = list.filter((a) => (a.format || '') === fFormat)
    if (fYear) list = list.filter((a) => String(a.year || '').slice(0, 4) === fYear)
    if (fSource === 'folder') list = list.filter((a) => a.origin === 'folder')
    if (fSource === 'torrent') list = list.filter((a) => a.origin === 'torrent')
    if (fSource === 'cloud') list = list.filter((a) => a.origin === 'cloud')
    if (fFav) list = list.filter((a) => a.favorite)
    if (sort !== 'added') {
      list = [...list]
      if (sort === 'artist') {
        list.sort(
          (a, b) =>
            (a.artist || '\uffff').localeCompare(b.artist || '\uffff', 'ru') ||
            (a.name || '').localeCompare(b.name || '', 'ru'),
        )
      } else if (sort === 'year') {
        list.sort((a, b) => String(b.year || '').localeCompare(String(a.year || '')))
      } else if (sort === 'name') {
        list.sort((a, b) => (a.name || '').localeCompare(b.name || '', 'ru'))
      }
    }
    return list
  }, [albums, q, fGenre, fFormat, fYear, fSource, fFav, sort])

  const facets = useMemo(() => {
    const count = (vals) => {
      const m = new Map()
      for (const v of vals) if (v) m.set(v, (m.get(v) || 0) + 1)
      return [...m.entries()].sort((a, b) => b[1] - a[1])
    }
    const artists = new Set()
    for (const a of albums) artists.add((a.artist || '').trim() || 'Неизвестный исполнитель')
    return {
      genres: count(albums.map((a) => a.genre)).slice(0, 24),
      formats: count(albums.map((a) => a.format)),
      years: count(albums.map((a) => String(a.year || '').slice(0, 4)))
        .sort((a, b) => b[0].localeCompare(a[0]))
        .slice(0, 24),
      hasFolders: albums.some((a) => a.origin === 'folder'),
      hasCloud: albums.some((a) => a.origin === 'cloud'),
      artistCount: artists.size,
    }
  }, [albums])

  const artistGroups = useMemo(() => {
    if (tab !== 'artists') return []
    const map = new Map()
    for (const a of filtered) {
      const key = (a.artist || '').trim() || 'Неизвестный исполнитель'
      if (!map.has(key)) map.set(key, [])
      map.get(key).push(a)
    }
    return [...map.entries()]
      .sort((a, b) => a[0].localeCompare(b[0], 'ru'))
      .map(([artist, list]) => ({ artist, list }))
  }, [tab, filtered])

  const trackParams = () => ({
    q: query.trim(),
    genre: fGenre,
    format: fFormat,
    year: fYear,
    source: fSource,
    fav: fFav ? 1 : '',
  })

  useEffect(() => {
    if (tab !== 'tracks') return undefined
    let cancelled = false
    setTrk((t) => ({ ...t, loading: true }))
    api
      .libraryTracks({ ...trackParams(), limit: 100 })
      .then((d) => {
        if (!cancelled) setTrk({ items: d.tracks, total: d.total, loading: false })
      })
      .catch((e) => {
        if (!cancelled) {
          setTrk((t) => ({ ...t, loading: false }))
          notify?.(e.message, 'error')
        }
      })
    return () => {
      cancelled = true
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [tab, query, fGenre, fFormat, fYear, fSource, fFav])

  function loadMoreTracks() {
    const offset = trk.items.length
    setTrk((t) => ({ ...t, loading: true }))
    api
      .libraryTracks({ ...trackParams(), limit: 100, offset })
      .then((d) =>
        setTrk((t) => ({ items: [...t.items, ...d.tracks], total: d.total, loading: false })),
      )
      .catch((e) => {
        setTrk((t) => ({ ...t, loading: false }))
        notify?.(e.message, 'error')
      })
  }

  const TABS = [
    ['albums', 'Альбомы', Disc3, albums.length],
    ['artists', 'Исполнители', Mic2, facets.artistCount],
    ['tracks', 'Треки', Music4, trk.total || null],
  ]

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

      <div className="mb-4 flex items-center gap-6 overflow-x-auto border-b border-border-subtle">
        {TABS.map(([id, label, Icon, count]) => (
          <button
            key={id}
            type="button"
            onClick={() => setTab(id)}
            className={`-mb-px flex shrink-0 items-center gap-2 border-b-2 px-0.5 pb-3 pt-1.5 text-[13.5px] font-medium transition-colors ${
              tab === id
                ? 'border-primary text-text-primary'
                : 'border-transparent text-text-muted hover:text-text-secondary'
            }`}
          >
            <Icon size={15} /> {label}
            {count != null && <span className="font-mono text-[11px] opacity-70">{count}</span>}
          </button>
        ))}
      </div>

      <div className="mb-6 flex items-center gap-2 overflow-x-auto pb-1">
        <FilterDropdown label="Жанр" options={facets.genres} value={fGenre} onChange={setFGenre} />
        <FilterDropdown label="Формат" options={facets.formats} value={fFormat} onChange={setFFormat} />
        <FilterDropdown label="Год" options={facets.years} value={fYear} onChange={setFYear} />
        <FilterDropdown
          label="Источник"
          options={[
            ...(facets.hasCloud ? [['cloud']] : []),
            ...(facets.hasFolders ? [['torrent'], ['folder']] : []),
          ]}
          labelFor={(v) =>
            v === 'torrent' ? 'Торренты' : v === 'folder' ? 'Медиапапки' : 'Облако'
          }
          value={fSource}
          onChange={setFSource}
        />
        <button
          type="button"
          onClick={() => setFFav(!fFav)}
          aria-label="Избранное"
          title="Избранное"
          className={`h-9 shrink-0 rounded-sm border px-3 text-[12.5px] transition-colors ${
            fFav
              ? 'border-warning/50 bg-warning/10 text-warning'
              : 'border-border-subtle bg-surface-1 text-text-secondary hover:bg-surface-2'
          }`}
        >
          ★
        </button>
        {filtersActive && (
          <button
            type="button"
            onClick={() => {
              onQueryChange('')
              setFGenre('')
              setFFormat('')
              setFYear('')
              setFSource('')
              setFFav(false)
            }}
            className="flex shrink-0 items-center gap-1 px-2 text-[12.5px] text-text-muted transition-colors hover:text-danger"
          >
            <X size={13} /> Сбросить
          </button>
        )}
        <select
          value={sort}
          onChange={(e) => setSort(e.target.value)}
          disabled={tab === 'tracks'}
          aria-label="Сортировка"
          className="ml-auto h-9 shrink-0 rounded-sm border border-border-subtle bg-surface-1 px-2 text-[12.5px] text-text-secondary focus:outline-none disabled:opacity-40"
        >
          <option value="added">Сначала новые</option>
          <option value="artist">По исполнителю</option>
          <option value="year">По году</option>
          <option value="name">По названию</option>
        </select>
      </div>

      {tab === 'albums' && (
        <>
          {!filtersActive && history.length > 0 && (
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

          {!filtersActive && (
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
        </>
      )}

      {tab === 'artists' &&
        (artistGroups.length === 0 ? (
          <p className="py-12 text-center text-[13.5px] text-text-muted">Ничего не найдено</p>
        ) : (
          <div className="space-y-10">
            {artistGroups.map(({ artist, list }) => (
              <section key={artist}>
                <div className="mb-5 flex items-center gap-4">
                  <h2 className="shrink-0 text-[19px] font-semibold leading-[26px] text-text-primary">
                    {artist}
                  </h2>
                  <span className="font-mono text-[11px] text-text-muted">{list.length}</span>
                  <div className="h-px flex-1 bg-border-subtle" />
                </div>
                <AlbumGrid
                  albums={list}
                  loading={false}
                  onOpen={onOpen}
                  onPlay={onPlay}
                  onQueue={onQueue}
                  onToggleFavorite={onToggleFavorite}
                  isPlayingAlbum={isPlayingAlbum}
                />
              </section>
            ))}
          </div>
        ))}

      {tab === 'tracks' && (
        <div>
          <p className="mb-3 font-mono text-[11.5px] text-text-muted">
            Всего треков: {trk.total}
          </p>
          <div className="space-y-0.5">
            {trk.items.map((t, i) => (
              <div
                key={`${t.album_id}-${t.track_index}-${i}`}
                className="group flex items-center gap-3 rounded-md px-2 py-1.5 hover:bg-surface-1"
              >
                <button
                  type="button"
                  onClick={() => onPlayTrack?.(t.album_id, t.track_index)}
                  aria-label="Играть трек"
                  className="flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-text-secondary transition-colors hover:bg-surface-2 hover:text-primary"
                >
                  <Play size={14} className="fill-current" />
                </button>
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[13.5px] font-medium text-text-primary">{t.title}</p>
                  <button
                    type="button"
                    onClick={() => onOpen(t.album_id)}
                    className="block max-w-full truncate text-left text-[11.5px] text-text-muted hover:text-text-primary hover:underline"
                  >
                    {t.artist || 'Неизвестный исполнитель'} — {t.album}
                  </button>
                </div>
                {t.format && (
                  <span className="hidden shrink-0 rounded-sm bg-surface-2 px-1.5 py-0.5 font-mono text-[10.5px] text-text-muted sm:block">
                    {t.format}
                  </span>
                )}
                <span className="w-12 shrink-0 text-right font-mono text-[11.5px] text-text-muted">
                  {formatTime(t.duration)}
                </span>
              </div>
            ))}
          </div>
          {trk.loading && (
            <p className="py-4 text-center text-[12.5px] text-text-muted">Загрузка…</p>
          )}
          {!trk.loading && trk.items.length < trk.total && (
            <button
              type="button"
              onClick={loadMoreTracks}
              className="mt-3 w-full rounded-sm bg-surface-1 py-2 text-[13px] text-text-secondary transition-colors hover:bg-surface-2"
            >
              Показать ещё ({trk.total - trk.items.length})
            </button>
          )}
          {!trk.loading && trk.items.length === 0 && (
            <p className="py-8 text-center text-[13px] text-text-muted">Треки не найдены</p>
          )}
        </div>
      )}

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
