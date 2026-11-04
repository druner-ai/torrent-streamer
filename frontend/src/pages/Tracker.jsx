import { ChevronRight, Download, LayoutGrid, Loader2, RefreshCw, Rss, Search, Settings2, Sparkles, X } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api.js'
import { formatSize, seedersTone } from '../utils.js'

const toneText = {
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-danger',
}

const fmtBadge = (e) => {
  const f = (e.format || '').toUpperCase()
  const b = e.bitrate || ''
  const hi = /24\s*\/|DSD|SACD/i.test(f + ' ' + b)
  return (
    <span
      className={`shrink-0 rounded-xs px-2 py-0.5 font-mono text-[10.5px] font-semibold uppercase tracking-wider ${
        hi
          ? 'bg-[#3D2E0E] text-[#F0B429] ring-1 ring-[#7A5B17]'
          : 'bg-[#262A33] text-[#A7ADBA]'
      }`}
    >
      {[f, b].filter(Boolean).join(' ')}
    </span>
  )
}

const fmtDate = (ts) => (ts ? new Date(ts * 1000).toISOString().slice(0, 10) : '')

export default function Tracker({ notify, onAlbumAdded }) {
  const [tree, setTree] = useState([])
  const [catIdx, setCatIdx] = useState(null)
  const [groupIdx, setGroupIdx] = useState(null)
  const [subId, setSubId] = useState(null)
  const [sortMode, setSortMode] = useState('seeders')
  const [minSeeders, setMinSeeders] = useState(0)
  const [losslessOnly, setLosslessOnly] = useState(false)
  const [quality, setQuality] = useState('')
  const [format, setFormat] = useState('')
  const [rip, setRip] = useState('')
  const [source, setSource] = useState('')
  const [genre, setGenre] = useState('')
  const [facets, setFacets] = useState({ genres: [], formats: [], sources: [] })
  const [qInput, setQInput] = useState('')
  const [q, setQ] = useState('')
  const [groups, setGroups] = useState([])
  const [count, setCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [selected, setSelected] = useState(null)
  const [downloadingT, setDownloadingT] = useState(null)
  const [sectionsOpen, setSectionsOpen] = useState(false)
  const [draftEnabled, setDraftEnabled] = useState(null)
  const [treeSaving, setTreeSaving] = useState(false)
  const [treeRefreshing, setTreeRefreshing] = useState(false)
  const [reloadTick, setReloadTick] = useState(0)
  const [sectionQuery, setSectionQuery] = useState('')
  const [collapsedCats, setCollapsedCats] = useState({})

  const loadTree = async () => {
    try {
      const data = await api.catalogTree()
      setTree(data.categories || [])
    } catch (e) {
      notify(e.message, 'error')
    }
  }

  useEffect(() => {
    loadTree()
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [])

  useEffect(() => {
    const id = setTimeout(() => setQ(qInput.trim()), 300)
    return () => clearTimeout(id)
  }, [qInput])

  const leafForums = (category) => {
    const ids = []
    for (const g of category.forums) {
      if (g.subforums.length) {
        g.subforums.forEach((s) => {
          if (s.enabled) ids.push(s.id)
        })
      } else if (g.enabled) {
        ids.push(g.id)
      }
    }
    return ids
  }

  const allEnabledLeaves = () => tree.flatMap(leafForums)

  const visibleTree = useMemo(
    () => tree.filter((c) => leafForums(c).length > 0),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [tree],
  )

  const forumIds = useMemo(() => {
    if (subId) return [subId]
    const cat = catIdx != null ? visibleTree[catIdx] : null
    if (groupIdx != null && cat) {
      const g = cat.forums[groupIdx]
      if (!g) return []
      if (g.subforums.length) return g.subforums.filter((s) => s.enabled).map((s) => s.id)
      return [g.id]
    }
    if (cat) return leafForums(cat)
    return sortMode === 'new' ? allEnabledLeaves() : []
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [subId, catIdx, groupIdx, visibleTree, sortMode])

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    api
      .catalogGroups({
        forum_id: forumIds.length ? forumIds : undefined,
        min_seeders: minSeeders || undefined,
        lossless: losslessOnly ? true : undefined,
        q: q || undefined,
        format: format || undefined,
        source: source || undefined,
        rip: rip || undefined,
        quality: quality || undefined,
        genre: genre || undefined,
        sort: sortMode !== 'seeders' ? sortMode : undefined,
      })
      .then((data) => {
        if (!alive) return
        setGroups(data.groups || [])
        setCount(data.count ?? (data.groups || []).length)
        setFacets(data.facets || { genres: [], formats: [], sources: [] })
      })
      .catch((e) => {
        if (alive) setError(e.message)
      })
      .finally(() => {
        if (alive) setLoading(false)
      })
    return () => {
      alive = false
    }
  }, [forumIds, minSeeders, losslessOnly, q, sortMode, reloadTick, format, source, rip, quality, genre])

  const activeCategory = useMemo(
    () => (catIdx != null ? visibleTree[catIdx] : null),
    [visibleTree, catIdx],
  )
  const activeGroup = useMemo(
    () => (activeCategory && groupIdx != null ? activeCategory.forums[groupIdx] : null),
    [activeCategory, groupIdx],
  )

  async function download(edition) {
    setDownloadingT(edition.t)
    try {
      const res = await api.downloadTorrent(edition.t)
      notify(`Добавлено в библиотеку: ${res.name || edition.title}`, 'success')
      onAlbumAdded()
      setReloadTick((t) => t + 1)
      setSelected(null)
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setDownloadingT(null)
    }
  }

  function openSections() {
    const s = new Set()
    tree.forEach((c) =>
      c.forums.forEach((g) => {
        if (g.subforums.length) {
          g.subforums.forEach((sf) => sf.enabled && s.add(sf.id))
        } else if (g.enabled) {
          s.add(g.id)
        }
      }),
    )
    setDraftEnabled(s)
    setSectionsOpen(true)
  }

  function toggleDraft(id) {
    setDraftEnabled((prev) => {
      const n = new Set(prev)
      if (n.has(id)) n.delete(id)
      else n.add(id)
      return n
    })
  }

  async function saveSections() {
    setTreeSaving(true)
    try {
      const res = await api.saveCatalogTree([...(draftEnabled || [])])
      if (res.queued > 0) {
        notify(`Разделы сохранены — ${res.queued} новых подгружаются в фоне`, 'success')
      } else {
        notify('Разделы сохранены', 'success')
      }
      setSectionsOpen(false)
      await loadTree()
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setTreeSaving(false)
    }
  }

  async function refreshTree() {
    setTreeRefreshing(true)
    try {
      await api.refreshCatalogTree()
      await loadTree()
      notify('Дерево разделов обновлено', 'success')
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setTreeRefreshing(false)
    }
  }

  const chip = (active) =>
    `flex shrink-0 items-center gap-1.5 rounded-sm px-3 py-1.5 text-[12.5px] font-medium transition-colors ${
      active
        ? 'bg-primary-tint text-primary'
        : 'bg-surface-1 text-text-secondary hover:bg-surface-2'
    }`

  const subChip = (active) =>
    `shrink-0 rounded-sm px-3 py-1.5 text-[12px] transition-colors ${
      active
        ? 'bg-surface-2 text-primary ring-1 ring-border-strong'
        : 'bg-surface-1 text-text-muted hover:bg-surface-2 hover:text-text-primary'
    }`

  const filterRow = (label, options, value, onChange, useChip = chip) => (
    <div className="no-scrollbar -mx-4 mb-2 flex items-center gap-2 overflow-x-auto px-4 pb-1">
      <span className="shrink-0 text-[10.5px] font-semibold uppercase tracking-wider text-text-muted">
        {label}
      </span>
      {options.map(([v, lbl]) => (
        <button
          key={v || '_all'}
          type="button"
          onClick={() => onChange(v)}
          className={useChip(value === v)}
        >
          {lbl}
        </button>
      ))}
    </div>
  )

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-8 md:px-6">
      <div className="mb-7 flex items-center gap-3">
        <h1 className="font-display text-[30px] font-bold leading-[38px] tracking-tight text-text-primary">
          Трекер
        </h1>
        <button
          type="button"
          onClick={openSections}
          className="ml-auto flex h-9 items-center gap-2 rounded-sm bg-surface-1 px-3 text-[12.5px] font-medium text-text-secondary transition-colors hover:bg-surface-2 hover:text-text-primary"
        >
          <Settings2 size={15} />
          Разделы
        </button>
      </div>

      {/* категории + Новое */}
      <div className="no-scrollbar -mx-4 mb-3 flex gap-2 overflow-x-auto px-4 pb-1">
        <button type="button" onClick={() => { setCatIdx(null); setGroupIdx(null); setSubId(null); setSortMode('seeders') }} className={chip(catIdx === null && sortMode === 'seeders')}>
          <LayoutGrid size={14} />
          Все
        </button>
        <button type="button" onClick={() => { setCatIdx(null); setGroupIdx(null); setSubId(null); setSortMode('new') }} className={chip(sortMode === 'new')}>
          <Sparkles size={14} />
          Новое
        </button>
        <span className="h-5 w-px shrink-0 bg-border-subtle" />
        {visibleTree.map((c, i) => (
          <button
            key={c.category}
            type="button"
            onClick={() => { setCatIdx(i); setGroupIdx(null); setSubId(null); setSortMode('seeders') }}
            className={chip(catIdx === i && sortMode !== 'new')}
          >
            {c.category}
          </button>
        ))}
      </div>

      {/* группы категории */}
      {activeCategory && sortMode !== 'new' && (
        <div className="no-scrollbar -mx-4 mb-3 flex gap-2 overflow-x-auto px-4 pb-1">
          {activeCategory.forums.map((g, i) => (
            <button
              key={g.id}
              type="button"
              onClick={() => { setGroupIdx(i); setSubId(null) }}
              className={subChip(groupIdx === i)}
            >
              {g.title}
            </button>
          ))}
        </div>
      )}

      {/* подфорумы группы */}
      {activeGroup && activeGroup.subforums.length > 0 && sortMode !== 'new' && (
        <div className="no-scrollbar -mx-4 mb-3 flex gap-2 overflow-x-auto px-4 pb-1">
          {activeGroup.subforums.map((s) => (
            <button
              key={s.id}
              type="button"
              onClick={() => setSubId(s.id)}
              className={subChip(subId === s.id)}
            >
              {s.title}
            </button>
          ))}
        </div>
      )}

      {/* фильтры по тегам названия */}
      {filterRow('Качество', [['', 'Все'], ['hires', 'Hi-Res 24/96+'], ['cd', 'CD 16/44'], ['dsd', 'DSD/SACD'], ['lossy', 'MP3']], quality, setQuality)}
      {filterRow('Кодек', [['', 'Все'], ...facets.formats.map((f) => [f.name, f.name])], format, setFormat, subChip)}
      {filterRow('Раздача', [['', 'Все'], ['tracks', 'tracks'], ['image', 'image']], rip, setRip, subChip)}
      {filterRow('Источник', [['', 'Все'], ...facets.sources.map((s) => [s.name, s.name])], source, setSource, subChip)}
      {facets.genres.length > 0 && filterRow('Жанр', [['', 'Все'], ...facets.genres.map((g) => [g.name, g.name])], genre, setGenre, subChip)}

      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div className="flex h-9 flex-1 items-center gap-2 rounded-sm border border-transparent bg-surface-inset px-3 focus-within:border-strong md:max-w-[360px]">
          <Search size={16} className="shrink-0 text-text-muted" />
          <input
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="Поиск: исполнитель или альбом…"
            className="w-full bg-transparent text-[12.5px] text-text-primary placeholder:text-text-muted focus:outline-none"
          />
        </div>

        <label className="flex items-center gap-2 text-[12.5px] text-text-secondary">
          <span className="text-text-muted">Сиды ≥</span>
          <input
            type="number"
            min={0}
            value={minSeeders}
            onChange={(e) => setMinSeeders(Math.max(0, Number(e.target.value) || 0))}
            className="tnum h-9 w-16 rounded-sm border border-transparent bg-surface-inset px-2 text-[12.5px] text-text-primary focus:border-strong focus:outline-none"
          />
        </label>

        <label className="flex cursor-pointer items-center gap-2 text-[12.5px] text-text-secondary">
          <input
            type="checkbox"
            checked={losslessOnly}
            onChange={(e) => setLosslessOnly(e.target.checked)}
            className="h-4 w-4 rounded-xs border-border-strong bg-surface-inset accent-primary"
          />
          Только Lossless
        </label>

        <select
          value={sortMode}
          onChange={(e) => setSortMode(e.target.value)}
          className="h-9 rounded-sm border border-transparent bg-surface-inset px-2 text-[12.5px] text-text-primary focus:border-strong focus:outline-none"
        >
          <option value="seeders">По сидам</option>
          <option value="new">По дате</option>
          <option value="year">По году</option>
          <option value="name">По исполнителю</option>
        </select>

        <span className="font-mono text-[11px] text-text-muted">{count} релизов</span>
      </div>

      {error && (
        <div className="mb-4 rounded-sm bg-danger/10 p-3 text-[14px] text-danger">{error}</div>
      )}

      {loading ? (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5">
          {Array.from({ length: 10 }).map((_, i) => (
            <div key={i} className="aspect-square animate-pulse rounded-md bg-surface-1" />
          ))}
        </div>
      ) : groups.length === 0 ? (
        <div className="flex flex-col items-center gap-3 py-12">
          <Rss size={36} className="text-text-muted" />
          <p className="text-[14px] text-text-secondary">Ничего не найдено</p>
        </div>
      ) : (
        <div className="grid grid-cols-2 gap-3 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 xl:grid-cols-6">
          {groups.map((g) => {
            const top = [...g.editions].sort((a, b) => b.seeders - a.seeders)[0] || {}
            const tone = seedersTone(top.seeders || 0)
            return (
              <button
                key={g.release_key}
                type="button"
                onClick={() => setSelected(g)}
                className="group overflow-hidden rounded-md bg-surface-1 text-left shadow-1 transition-all hover:-translate-y-0.5 hover:bg-surface-2 hover:shadow-2"
              >
                <div className="relative aspect-square w-full overflow-hidden bg-surface-inset">
                  {g.cover_url ? (
                    <img
                      src={g.cover_url}
                      alt={g.album || g.artist || ''}
                      loading="lazy"
                      className="h-full w-full object-cover"
                      onError={(e) => {
                        e.currentTarget.style.display = 'none'
                      }}
                    />
                  ) : (
                    <div className="flex h-full w-full items-center justify-center">
                      <span className="font-display text-3xl font-bold text-text-disabled">
                        {(g.artist || g.album || '?').slice(0, 1).toUpperCase()}
                      </span>
                    </div>
                  )}
                  {g.count > 1 && (
                    <span className="absolute left-2 top-2 rounded-xs bg-black/60 px-1.5 py-0.5 font-mono text-[10.5px] font-semibold text-text-primary">
                      {g.count} изд.
                    </span>
                  )}
                  {g.in_library && (
                    <span className="absolute right-2 top-2 rounded-xs bg-success/20 px-1.5 py-0.5 text-[10.5px] font-semibold text-success">
                      ✓
                    </span>
                  )}
                </div>
                <div className="p-3">
                  <p className="truncate text-[13px] font-medium leading-5 text-text-primary">
                    {g.album || g.title || '—'}
                  </p>
                  <p className="truncate text-[12px] text-text-secondary">{g.artist || '—'}</p>
                  <p className="mt-1 flex items-center justify-between font-mono text-[10.5px] text-text-muted">
                    <span className={toneText[tone]}>{top.seeders || 0} сид.</span>
                    {sortMode === 'new' && g.last_post ? (
                      <span>{fmtDate(g.last_post)}</span>
                    ) : (
                      g.year && <span>{g.year}</span>
                    )}
                  </p>
                </div>
              </button>
            )
          })}
        </div>
      )}

      {/* модалка выбора издания */}
      {selected && (
        <div
          className="fixed inset-0 z-40 flex items-end justify-center bg-overlay md:items-center"
          onClick={() => setSelected(null)}
        >
          <div
            className="max-h-[85vh] w-full max-w-lg overflow-y-auto rounded-t-xl bg-surface-3 p-5 shadow-4 md:rounded-lg"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="mb-4 flex items-start gap-4">
              <div className="h-20 w-20 shrink-0 overflow-hidden rounded-md bg-surface-inset">
                {selected.cover_url ? (
                  <img src={selected.cover_url} alt="" className="h-full w-full object-cover" />
                ) : (
                  <div className="flex h-full w-full items-center justify-center font-display text-2xl font-bold text-text-disabled">
                    {(selected.artist || selected.album || '?').slice(0, 1).toUpperCase()}
                  </div>
                )}
              </div>
              <div className="min-w-0 flex-1">
                <h2 className="truncate text-[18px] font-semibold text-text-primary">
                  {selected.album || selected.title || '—'}
                </h2>
                <p className="truncate text-[14px] text-text-secondary">{selected.artist || '—'}</p>
                {selected.genre && (
                  <p className="mt-1 truncate text-[12px] text-text-muted">{selected.genre}</p>
                )}
              </div>
              <button
                type="button"
                onClick={() => setSelected(null)}
                className="rounded-sm p-1 text-text-muted transition-colors hover:bg-surface-2 hover:text-text-primary"
              >
                <X size={18} />
              </button>
            </div>

            <ul className="divide-y divide-border-subtle">
              {selected.editions.map((e) => {
                const tone = seedersTone(e.seeders || 0)
                return (
                  <li key={e.t} className="flex items-center gap-3 py-3">
                    <div className="min-w-0 flex-1">
                      <div className="flex items-center gap-2">{fmtBadge(e)}</div>
                      <p className="mt-1 flex flex-wrap items-center gap-x-3 gap-y-0.5 font-mono text-[11px] text-text-muted">
                        <span className={toneText[tone]}>
                          {e.seeders || 0} сид. · {e.leechers || 0} лич.
                        </span>
                        <span>{formatSize(e.size)}</span>
                        {e.year && <span>{e.year}</span>}
                      </p>
                    </div>
                    <button
                      type="button"
                      onClick={() => download(e)}
                      disabled={downloadingT === e.t || selected.in_library}
                      className={`flex h-9 shrink-0 items-center gap-2 rounded-sm px-3 text-[13px] font-medium transition-colors ${
                        selected.in_library
                          ? 'bg-surface-2 text-success'
                          : 'bg-primary text-on-primary hover:bg-primary-hover active:bg-primary-pressed disabled:opacity-60'
                      }`}
                    >
                      {selected.in_library ? (
                        <>✓ В библиотеке</>
                      ) : downloadingT === e.t ? (
                        <Loader2 size={15} className="animate-spin" />
                      ) : (
                        <Download size={15} />
                      )}
                      {!selected.in_library && 'Скачать'}
                    </button>
                  </li>
                )
              })}
            </ul>
          </div>
        </div>
      )}

      {/* модалка выбора разделов */}
      {sectionsOpen && (
        <div
          className="fixed inset-0 z-40 flex items-end justify-center bg-overlay md:items-center"
          onClick={() => setSectionsOpen(false)}
        >
          <div
            className="flex max-h-[85vh] w-full max-w-2xl flex-col rounded-t-xl bg-surface-3 pb-[env(safe-area-inset-bottom)] shadow-4 md:rounded-lg md:pb-0"
            onClick={(e) => e.stopPropagation()}
          >
            <div className="flex items-center gap-2 border-b border-border-subtle p-4">
              <h2 className="text-[17px] font-semibold text-text-primary">Разделы трекера</h2>
              <button
                type="button"
                onClick={refreshTree}
                disabled={treeRefreshing}
                className="ml-auto flex h-9 items-center gap-2 rounded-sm bg-surface-1 px-2.5 text-[12px] text-text-secondary transition-colors hover:bg-surface-2 disabled:opacity-60"
              >
                {treeRefreshing ? <Loader2 size={15} className="animate-spin" /> : <RefreshCw size={15} />}
                <span className="hidden sm:inline">Обновить</span>
              </button>
              <button
                type="button"
                onClick={saveSections}
                disabled={treeSaving}
                className="flex h-9 items-center gap-2 rounded-sm bg-primary px-3.5 text-[13px] font-medium text-on-primary transition-colors hover:bg-primary-hover disabled:opacity-60"
              >
                {treeSaving && <Loader2 size={14} className="animate-spin" />}
                Сохранить
              </button>
              <button
                type="button"
                onClick={() => setSectionsOpen(false)}
                className="rounded-sm p-1 text-text-muted transition-colors hover:bg-surface-2 hover:text-text-primary"
              >
                <X size={18} />
              </button>
            </div>

            <div className="min-h-0 flex-1 overflow-y-auto p-5">
              <div className="mb-4 flex h-9 items-center gap-2 rounded-sm border border-transparent bg-surface-inset px-3 focus-within:border-strong">
                <Search size={15} className="shrink-0 text-text-muted" />
                <input
                  value={sectionQuery}
                  onChange={(e) => setSectionQuery(e.target.value)}
                  placeholder="Поиск раздела…"
                  className="w-full bg-transparent text-[12.5px] text-text-primary placeholder:text-text-muted focus:outline-none"
                />
              </div>
              {tree.length === 0 ? (
                <div className="flex flex-col items-center gap-3 py-10">
                  <p className="text-[14px] text-text-secondary">
                    Дерево ещё не загружено — нажмите «Обновить», чтобы подтянуть разделы с RuTracker.
                  </p>
                </div>
              ) : (
                tree
                  .filter((c) => {
                    if (!sectionQuery.trim()) return true
                    const q = sectionQuery.toLowerCase()
                    return (
                      c.category.toLowerCase().includes(q) ||
                      c.forums.some(
                        (f) =>
                          f.title.toLowerCase().includes(q) ||
                          f.subforums.some((s) => s.title.toLowerCase().includes(q)),
                      )
                    )
                  })
                  .map((c) => {
                    const collapsed = collapsedCats[c.category]
                    return (
                      <div key={c.category} className="mb-3">
                        <button
                          type="button"
                          onClick={() => setCollapsedCats((p) => ({ ...p, [c.category]: !p[c.category] }))}
                          className="mb-1 flex w-full items-center gap-1.5 text-left text-[13px] font-semibold uppercase tracking-wide text-text-muted transition-colors hover:text-text-primary"
                        >
                          <ChevronRight size={14} className={`shrink-0 transition-transform ${collapsed ? '' : 'rotate-90'}`} />
                          {c.category}
                        </button>
                        {!collapsed &&
                          c.forums.map((g) => (
                            <div key={g.id} className="mb-1">
                              {g.subforums.length > 0 ? (
                                <div>
                                  <p className="py-1 text-[13px] font-medium text-text-primary">{g.title}</p>
                                  {g.subforums.map((s) => (
                                    <label
                                      key={s.id}
                                      className="ml-4 flex cursor-pointer items-center gap-2 py-1 text-[13px] text-text-secondary"
                                    >
                                      <input
                                        type="checkbox"
                                        checked={draftEnabled ? draftEnabled.has(s.id) : s.enabled}
                                        onChange={() => toggleDraft(s.id)}
                                        className="h-4 w-4 rounded-xs border-border-strong bg-surface-inset accent-primary"
                                      />
                                      {s.title}
                                    </label>
                                  ))}
                                </div>
                              ) : (
                                <label className="flex cursor-pointer items-center gap-2 py-1 text-[13px] text-text-secondary">
                                  <input
                                    type="checkbox"
                                    checked={draftEnabled ? draftEnabled.has(g.id) : g.enabled}
                                    onChange={() => toggleDraft(g.id)}
                                    className="h-4 w-4 rounded-xs border-border-strong bg-surface-inset accent-primary"
                                  />
                                  {g.title}
                                </label>
                              )}
                            </div>
                          ))}
                      </div>
                    )
                  })
              )}
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
