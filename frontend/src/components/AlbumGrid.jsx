import { Music2 } from 'lucide-react'
import AlbumCard from './AlbumCard.jsx'

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
  onToggleFavorite,
  isPlayingAlbum,
}) {
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

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-[repeat(auto-fill,minmax(160px,1fr))] md:grid-cols-[repeat(auto-fill,minmax(180px,1fr))] md:gap-5">
      {albums.map((album) => (
        <AlbumCard
          key={album.id}
          album={album}
          onOpen={onOpen}
          onPlay={onPlay}
          onToggleFavorite={onToggleFavorite}
          isPlaying={isPlayingAlbum(album)}
        />
      ))}
    </div>
  )
}
