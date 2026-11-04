import { Download, Loader2, Rss, Search } from 'lucide-react'
import { useEffect, useMemo, useState } from 'react'
import { api } from '../api.js'
import { formatSize, seedersTone } from '../utils.js'

const toneText = {
  success: 'text-success',
  warning: 'text-warning',
  danger: 'text-danger',
}

export default function Tracker({ notify, onAlbumAdded }) {
  const [sections, setSections] = useState([])
  const [sectionId, setSectionId] = useState(null)
  const [forums, setForums] = useState([])
  const [forumId, setForumId] = useState(null)
  const [minSeeders, setMinSeeders] = useState(0)
  const [losslessOnly, setLosslessOnly] = useState(false)
  const [qInput, setQInput] = useState('')
  const [q, setQ] = useState('')
  const [items, setItems] = useState([])
  const [count, setCount] = useState(0)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState(null)
  const [downloadingT, setDownloadingT] = useState(null)

  useEffect(() => {
    let alive = true
    api
      .catalogSections()
      .then((data) => {
        if (alive) setSections(data.sections || [])
      })
      .catch((e) => {
        if (alive) notify(e.message, 'error')
      })
    return () => {
      alive = false
    }
  }, [notify])

  useEffect(() => {
    const section = sections.find((s) => s.id === sectionId)
    setForums(section ? section.forums || [] : [])
    setForumId(null)
  }, [sectionId, sections])

  useEffect(() => {
    const id = setTimeout(() => setQ(qInput.trim()), 300)
    return () => clearTimeout(id)
  }, [qInput])

  useEffect(() => {
    let alive = true
    setLoading(true)
    setError(null)
    api
      .catalog({
        forum_id: forumId || undefined,
        min_seeders: minSeeders || undefined,
        lossless: losslessOnly ? true : undefined,
        q: q || undefined,
      })
      .then((data) => {
        if (!alive) return
        setItems(data.items || [])
        setCount(data.count ?? (data.items || []).length)
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
  }, [forumId, minSeeders, losslessOnly, q])

  const activeSection = useMemo(
    () => sections.find((s) => s.id === sectionId),
    [sections, sectionId],
  )

  async function download(item) {
    setDownloadingT(item.t)
    try {
      const res = await api.downloadTorrent(item.t)
      notify(`Добавлено в библиотеку: ${res.name || item.title}`, 'success')
      onAlbumAdded()
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setDownloadingT(null)
    }
  }

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-8 md:px-6">
      <h1 className="mb-7 font-display text-[30px] font-bold leading-[38px] tracking-tight text-text-primary">
        Трекер
      </h1>

      <div className="no-scrollbar -mx-4 mb-3 flex gap-2 overflow-x-auto px-4 pb-1">
        <button
          type="button"
          onClick={() => setSectionId(null)}
          className={`shrink-0 rounded-sm px-3 py-1.5 text-[12.5px] font-medium transition-colors ${
            sectionId === null
              ? 'bg-primary-tint text-primary'
              : 'bg-surface-1 text-text-secondary hover:bg-surface-2'
          }`}
        >
          Все
        </button>
        {sections.map((s) => (
          <button
            key={s.id}
            type="button"
            onClick={() => setSectionId(s.id)}
            className={`shrink-0 rounded-sm px-3 py-1.5 text-[12.5px] font-medium transition-colors ${
              sectionId === s.id
                ? 'bg-primary-tint text-primary'
                : 'bg-surface-1 text-text-secondary hover:bg-surface-2'
            }`}
          >
            {s.title}
          </button>
        ))}
      </div>

      {activeSection && (
        <div className="no-scrollbar -mx-4 mb-3 flex gap-2 overflow-x-auto px-4 pb-1">
          {forums.map((f) => (
            <button
              key={f.id}
              type="button"
              onClick={() => setForumId(f.id)}
              className={`shrink-0 rounded-sm px-3 py-1.5 text-[12px] transition-colors ${
                forumId === f.id
                  ? 'bg-surface-2 text-primary ring-1 ring-border-strong'
                  : 'bg-surface-1 text-text-muted hover:bg-surface-2 hover:text-text-primary'
              }`}
            >
              {f.title}
            </button>
          ))}
        </div>
      )}

      <div className="mb-5 flex flex-wrap items-center gap-3">
        <div className="flex h-9 flex-1 items-center gap-2 rounded-sm border border-transparent bg-surface-inset px-3 focus-within:border-strong md:max-w-[360px]">
          <Search size={16} className="shrink-0 text-text-muted" />
          <input
            value={qInput}
            onChange={(e) => setQInput(e.target.value)}
            placeholder="Поиск по раздачам…"
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

        <span className="font-mono text-[11px] text-text-muted">{count} раздач</span>
      </div>

      {error && (
        <div className="mb-4 rounded-sm bg-danger/10 p-3 text-[14px] text-danger">{error}</div>
      )}

      <div className="overflow-hidden rounded-md border border-border-subtle">
        {loading ? (
          <div className="space-y-1 p-2">
            {Array.from({ length: 8 }).map((_, i) => (
              <div key={i} className="h-14 animate-pulse rounded-md bg-surface-1" />
            ))}
          </div>
        ) : items.length === 0 ? (
          <div className="flex flex-col items-center gap-3 py-12">
            <Rss size={36} className="text-text-muted" />
            <p className="text-[14px] text-text-secondary">Раздач не найдено</p>
          </div>
        ) : (
          <ul className="divide-y divide-border-subtle">
            {items.map((item) => {
              const tone = seedersTone(item.seeders || 0)
              return (
                <li
                  key={item.t}
                  className="flex items-center gap-3 px-3 py-3 transition-colors hover:bg-surface-2 md:px-4"
                >
                  <div className="min-w-0 flex-1">
                    <p className="truncate text-[14px] leading-5 text-text-primary">{item.title}</p>
                    <p className="mt-0.5 flex flex-wrap items-center gap-x-3 gap-y-1 font-mono text-[11px] text-text-muted">
                      <span className={toneText[tone]}>
                        {item.seeders || 0} сид. · {item.leechers || 0} лич.
                      </span>
                      <span>{formatSize(item.size)}</span>
                      <span>{item.year || '—'}</span>
                    </p>
                  </div>

                  {item.lossless === 1 && (
                    <span className="hidden shrink-0 rounded-xs bg-success/10 px-2 py-0.5 font-mono text-[10.5px] font-semibold uppercase tracking-wider text-success sm:inline">
                      Lossless
                    </span>
                  )}
                  {item.lossless === 0 && (
                    <span className="hidden shrink-0 rounded-xs bg-[#262A33] px-2 py-0.5 font-mono text-[10.5px] font-semibold uppercase tracking-wider text-[#A7ADBA] sm:inline">
                      Lossy
                    </span>
                  )}

                  <button
                    type="button"
                    onClick={() => download(item)}
                    disabled={downloadingT === item.t}
                    className="flex h-9 shrink-0 items-center gap-2 rounded-sm bg-primary px-3 text-[13px] font-medium text-on-primary transition-colors hover:bg-primary-hover active:bg-primary-pressed disabled:opacity-60"
                  >
                    {downloadingT === item.t ? (
                      <Loader2 size={15} className="animate-spin" />
                    ) : (
                      <Download size={15} />
                    )}
                    <span className="hidden sm:inline">
                      {downloadingT === item.t ? 'Скачиваем…' : 'Скачать'}
                    </span>
                  </button>
                </li>
              )
            })}
          </ul>
        )}
      </div>
    </div>
  )
}
