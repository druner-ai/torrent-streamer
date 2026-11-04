import { ListMusic, Play, Volume2 } from 'lucide-react'

export default function Queue({ queue, player, loading, onJump }) {
  const list = queue.queue || []

  return (
    <div className="mx-auto max-w-[900px] px-4 py-8 md:px-6">
      <h1 className="mb-7 font-display text-[30px] font-bold leading-[38px] tracking-tight text-text-primary">
        Очередь
      </h1>

      {loading ? (
        <div className="space-y-1">
          {Array.from({ length: 5 }).map((_, i) => (
            <div key={i} className="h-14 animate-pulse rounded-md bg-surface-1" />
          ))}
        </div>
      ) : list.length === 0 ? (
        <div className="flex flex-col items-center gap-3 rounded-lg border border-border-subtle py-12">
          <ListMusic size={36} className="text-text-muted" />
          <p className="text-[14px] text-text-secondary">Очередь пуста</p>
        </div>
      ) : (
        <ul className="overflow-hidden rounded-md border border-border-subtle">
          {list.map((item, i) => {
            const current = item.current
            return (
              <li key={`${item.album_id}-${item.track_index}-${i}`}>
                <button
                  type="button"
                  onClick={() => onJump(i)}
                  className={`flex w-full items-center gap-4 px-4 py-3 text-left transition-colors hover:bg-surface-2 ${
                    current ? 'bg-primary-tint' : 'bg-surface-1'
                  }`}
                >
                  <span className="w-6 shrink-0 text-center font-mono text-[12px] text-text-muted">
                    {current ? (
                      <Volume2 size={16} className="mx-auto text-primary" />
                    ) : (
                      String(i + 1).padStart(2, '0')
                    )}
                  </span>
                  <span className="min-w-0 flex-1">
                    <span
                      className={`block truncate text-[14px] leading-5 ${
                        current ? 'font-medium text-primary' : 'text-text-primary'
                      }`}
                    >
                      {item.title}
                    </span>
                    <span className="block font-mono text-[11px] text-text-muted">
                      альбом {item.album_id} · трек {item.track_index + 1}
                    </span>
                  </span>
                  {!current && (
                    <span className="flex shrink-0 items-center gap-1 text-text-muted">
                      <Play size={14} className="fill-current" />
                    </span>
                  )}
                </button>
              </li>
            )
          })}
        </ul>
      )}
    </div>
  )
}
