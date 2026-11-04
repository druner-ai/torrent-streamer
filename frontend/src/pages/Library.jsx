import { Search } from 'lucide-react'
import AlbumGrid from '../components/AlbumGrid.jsx'

export default function Library({
  albums,
  loading,
  query,
  onQueryChange,
  onOpen,
  onPlay,
  onToggleFavorite,
  isPlayingAlbum,
}) {
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

      <AlbumGrid
        albums={filtered}
        loading={loading}
        onOpen={onOpen}
        onPlay={onPlay}
        onToggleFavorite={onToggleFavorite}
        isPlayingAlbum={isPlayingAlbum}
      />
    </div>
  )
}
