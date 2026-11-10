import { Music2 } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import AlbumCard from './AlbumCard.jsx'

const PAGE = 60 // сколько карточек рендерить за раз
const VIRTUALIZE_FROM = 90 // виртуализировать, только если альбомов заметно больше одной порции

function SkeletonCard() {
  return (
    <div className="rounded-md border border-border-subtle bg-surface-1 p-3 shadow-1">
      <div className="aspect-square w-full animate-pulse rounded-sm bg-surface-2" />
      <div className="mt-3 h-4 w-3/4 animate-pulse rounded-sm bg-surface-2" />
      <div className="mt-2 h-3 w-1/2 animate-pulse rounded-sm bg-surface-2" />
    </div>
  )
}

export default function AlbumGrid({
  albums,
  loading,
  onOpen,
  onPlay,
  onQueue,
  onToggleFavorite,
  isPlayingAlbum,
}) {
  const [visible, setVisible] = useState(PAGE)
  const sentinelRef = useRef(null)

  // Сбрасывать порцию при смене набора (фильтр/вкладка) — иначе после
  // длинного списка отфильтрованный короткий может не показать «догрузку».
  useEffect(() => {
    setVisible(PAGE)
  }, [albums])

  // Виртуализация: догружаем следующую порцию, когда sentinel входит во вьюпорт.
  // Карточки разной высоты (прогресс-бар у скачиваемых) — строгая windowing
  // по строкам дала бы прыгающий скроллбар, поэтому используем infinite scroll.
  useEffect(() => {
    if (albums.length <= VIRTUALIZE_FROM) return undefined
    const el = sentinelRef.current
    if (!el || visible >= albums.length) return undefined
    const io = new IntersectionObserver(
      (entries) => {
        if (entries[0].isIntersecting) {
          setVisible((v) => Math.min(v + PAGE, albums.length))
        }
      },
      { rootMargin: '800px 0px' },
    )
    io.observe(el)
    return () => io.disconnect()
  }, [albums.length, visible])

  if (loading) {
    return (
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(160px,1fr))] md:grid-cols-[repeat(auto-fill,minmax(180px,1fr))] md:gap-5">
        {Array.from({ length: 10 }).map((_, i) => (
          <SkeletonCard key={i} />
        ))}
      </div>
    )
  }

  if (!albums.length) {
    return (
      <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-border-subtle py-12">
        <Music2 size={40} className="text-text-muted" />
        <p className="text-[14px] text-text-secondary">Здесь пока пусто</p>
      </div>
    )
  }

  const slice = albums.length > VIRTUALIZE_FROM ? albums.slice(0, visible) : albums

  return (
    <>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(160px,1fr))] md:grid-cols-[repeat(auto-fill,minmax(180px,1fr))] md:gap-5">
        {slice.map((album) => (
          <AlbumCard
            key={album.id}
            album={album}
            onOpen={onOpen}
            onPlay={onPlay}
            onQueue={onQueue}
            onToggleFavorite={onToggleFavorite}
            isPlaying={isPlayingAlbum(album)}
          />
        ))}
      </div>
      {albums.length > VIRTUALIZE_FROM && visible < albums.length && (
        <div ref={sentinelRef} className="h-px" aria-hidden="true" />
      )}
    </>
  )
}
