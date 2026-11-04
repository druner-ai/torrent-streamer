import { useMemo } from 'react'
import AlbumGrid from '../components/AlbumGrid.jsx'

export default function Artists({
  albums,
  loading,
  onOpen,
  onPlay,
  onToggleFavorite,
  isPlayingAlbum,
}) {
  const groups = useMemo(() => {
    const map = new Map()
    for (const album of albums) {
      const key = (album.artist || 'Неизвестный исполнитель').trim() || 'Неизвестный исполнитель'
      if (!map.has(key)) map.set(key, [])
      map.get(key).push(album)
    }
    return [...map.entries()]
      .sort((a, b) => a[0].localeCompare(b[0], 'ru'))
      .map(([artist, list]) => ({
        artist,
        albums: list.sort((x, y) => String(y.year || '').localeCompare(String(x.year || ''))),
      }))
  }, [albums])

  return (
    <div className="mx-auto max-w-[1400px] px-4 py-8 md:px-6">
      <h1 className="mb-7 font-display text-[30px] font-bold leading-[38px] tracking-tight text-text-primary">
        Исполнители
      </h1>

      {loading && !albums.length ? (
        <AlbumGrid
          albums={[]}
          loading
          onOpen={onOpen}
          onPlay={onPlay}
          onToggleFavorite={onToggleFavorite}
          isPlayingAlbum={isPlayingAlbum}
        />
      ) : groups.length === 0 ? (
        <div className="flex flex-col items-center justify-center gap-3 rounded-lg border border-border-subtle py-12">
          <p className="text-[14px] text-text-secondary">В библиотеке пока нет альбомов</p>
        </div>
      ) : (
        <div className="space-y-10">
          {groups.map(({ artist, albums: list }) => (
            <section key={artist}>
              <div className="mb-7 flex items-center gap-4">
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
                onToggleFavorite={onToggleFavorite}
                isPlayingAlbum={isPlayingAlbum}
              />
            </section>
          ))}
        </div>
      )}
    </div>
  )
}
