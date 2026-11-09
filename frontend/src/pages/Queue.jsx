import { useState } from 'react'
import { Disc3, ListMusic, Play, X } from 'lucide-react'
import { api } from '../api.js'
import { formatTime } from '../utils.js'

function QueueCover({ albumId, lg }) {
  const [failed, setFailed] = useState(false)
  const box = lg ? 'h-14 w-14' : 'h-11 w-11'
  const icon = lg ? 22 : 18
  if (failed || !albumId) {
    return (
      <div className={`${box} flex shrink-0 items-center justify-center rounded-md bg-surface-2`}>
        <Disc3 size={icon} className="text-text-muted" />
      </div>
    )
  }
  return (
    <img
      src={api.coverUrl(albumId)}
      alt=""
      loading="lazy"
      onError={() => setFailed(true)}
      className={`${box} shrink-0 rounded-md object-cover shadow-1`}
    />
  )
}

export default function Queue({ queue, loading, onJump, onRemove }) {
  const list = queue.queue || []
  const currentIdx = list.findIndex((i) => i.current)
  const current = currentIdx >= 0 ? list[currentIdx] : null
  const rest = list.map((item, i) => ({ item, i })).filter(({ i }) => i !== currentIdx)

  return (
    <div className="mx-auto max-w-[900px] px-4 py-8 md:px-6">
      <div className="mb-7 flex items-baseline gap-3">
        <h1 className="font-display text-[30px] font-bold leading-[38px] tracking-tight text-text-primary">
          Очередь
        </h1>
        {list.length > 0 && (
          <span className="font-mono text-[12px] text-text-muted">
            {list.length} {list.length === 1 ? 'трек' : 'треков'}
          </span>
        )}
      </div>

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
          <p className="text-[12.5px] text-text-muted">
            Добавьте альбомы кнопкой «В очередь» в библиотеке
          </p>
        </div>
      ) : (
        <div className="space-y-6">
          {current && (
            <section>
              <h2 className="mb-2 text-[12px] font-semibold uppercase tracking-wider text-text-muted">
                Сейчас играет
              </h2>
              <div className="flex items-center gap-4 rounded-lg border border-primary/30 bg-primary-tint p-3">
                <QueueCover albumId={current.album_id} lg />
                <div className="min-w-0 flex-1">
                  <p className="truncate text-[15px] font-medium text-text-primary">
                    {current.title}
                  </p>
                  <p className="truncate text-[12.5px] text-text-secondary">
                    {current.artist || 'Неизвестный исполнитель'}
                    {current.album ? ` · ${current.album}` : ''}
                  </p>
                </div>
                <span className="shrink-0 font-mono text-[12px] text-text-muted">
                  {current.duration != null ? formatTime(current.duration) : ''}
                </span>
                {onRemove && (
                  <button
                    type="button"
                    onClick={() => onRemove(currentIdx)}
                    aria-label="Убрать из очереди"
                    className="shrink-0 rounded-sm p-1.5 text-text-muted transition-colors hover:bg-surface-2 hover:text-text-primary"
                  >
                    <X size={15} />
                  </button>
                )}
              </div>
            </section>
          )}

          {rest.length > 0 && (
            <section>
              <h2 className="mb-2 text-[12px] font-semibold uppercase tracking-wider text-text-muted">
                Далее
              </h2>
              <ul className="overflow-hidden rounded-md border border-border-subtle bg-surface-1">
                {rest.map(({ item, i }, n) => (
                  <li
                    key={`${item.album_id}-${item.track_index}-${i}`}
                    className={n > 0 ? 'border-t border-border-subtle' : ''}
                  >
                    <div className="group flex items-center gap-3 px-3 py-2.5 transition-colors hover:bg-surface-2">
                      <button
                        type="button"
                        onClick={() => onJump(i)}
                        aria-label={`Играть ${item.title}`}
                        className="flex min-w-0 flex-1 items-center gap-3 text-left"
                      >
                        <span className="relative shrink-0">
                          <QueueCover albumId={item.album_id} />
                          <span className="absolute inset-0 flex items-center justify-center rounded-md bg-black/50 opacity-0 transition-opacity group-hover:opacity-100">
                            <Play size={16} className="ml-0.5 fill-white text-white" />
                          </span>
                        </span>
                        <span className="min-w-0 flex-1">
                          <span className="block truncate text-[14px] text-text-primary">
                            {item.title}
                          </span>
                          <span className="block truncate text-[12px] text-text-muted">
                            {item.artist || 'Неизвестный исполнитель'}
                            {item.album ? ` · ${item.album}` : ''}
                          </span>
                        </span>
                      </button>
                      <span className="shrink-0 font-mono text-[12px] text-text-muted">
                        {item.duration != null ? formatTime(item.duration) : ''}
                      </span>
                      {onRemove && (
                        <button
                          type="button"
                          onClick={() => onRemove(i)}
                          aria-label="Убрать из очереди"
                          className="shrink-0 rounded-sm p-1.5 text-text-muted transition-colors hover:bg-surface-3 hover:text-text-primary md:opacity-0 md:group-hover:opacity-100"
                        >
                          <X size={15} />
                        </button>
                      )}
                    </div>
                  </li>
                ))}
              </ul>
            </section>
          )}
        </div>
      )}
    </div>
  )
}
