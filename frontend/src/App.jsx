import { useCallback, useState } from 'react'
import { api } from './api.js'
import { usePolling } from './hooks/usePolling.js'
import AlbumModal from './components/AlbumModal.jsx'
import BottomNav from './components/BottomNav.jsx'
import PlayerBar from './components/PlayerBar.jsx'
import SettingsModal from './components/SettingsModal.jsx'
import Sidebar from './components/Sidebar.jsx'
import Toast from './components/Toast.jsx'
import TopBar from './components/TopBar.jsx'
import Artists from './pages/Artists.jsx'
import Library from './pages/Library.jsx'
import Queue from './pages/Queue.jsx'
import Tracker from './pages/Tracker.jsx'

export default function App() {
  const [view, setView] = useState('library')
  const [albums, setAlbums] = useState([])
  const [albumsLoading, setAlbumsLoading] = useState(true)
  const [player, setPlayer] = useState({ state: 'stop', volume: 100, album_id: null })
  const [queue, setQueue] = useState({ queue: [], pos: -1 })
  const [queueLoading, setQueueLoading] = useState(true)
  const [sleepMinutes, setSleepMinutes] = useState(null)
  const [toasts, setToasts] = useState([])
  const [openAlbumId, setOpenAlbumId] = useState(null)
  const [libraryQuery, setLibraryQuery] = useState('')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)

  const pushToast = useCallback((message, type = 'error') => {
    const id = `${Date.now()}-${Math.random()}`
    setToasts((prev) => [...prev, { id, message, type }])
    window.setTimeout(() => {
      setToasts((prev) => prev.filter((t) => t.id !== id))
    }, 4500)
  }, [])

  const dismissToast = useCallback((id) => {
    setToasts((prev) => prev.filter((t) => t.id !== id))
  }, [])

  const loadAlbums = useCallback(async () => {
    try {
      const data = await api.albums()
      setAlbums(data || [])
    } catch {
      // background poll — surfaced elsewhere
    } finally {
      setAlbumsLoading(false)
    }
  }, [])

  const loadPlayer = useCallback(async () => {
    try {
      setPlayer(await api.player())
    } catch {
      // background poll
    }
  }, [])

  const loadQueue = useCallback(async () => {
    try {
      setQueue(await api.queue())
    } catch {
      // background poll
    } finally {
      setQueueLoading(false)
    }
  }, [])

  usePolling(loadAlbums, 5000)
  usePolling(loadPlayer, 2000)
  usePolling(loadQueue, 5000)

  const handlePlayAlbum = useCallback(
    async (album) => {
      try {
        if (player.album_id === album.id && player.state === 'playing') {
          await api.playerCmd('pause')
        } else {
          await api.playAlbum(album.id)
        }
        loadPlayer()
        loadQueue()
      } catch (e) {
        pushToast(e.message, 'error')
      }
    },
    [player, loadPlayer, loadQueue, pushToast],
  )

  const handlePlayTrack = useCallback(
    async (albumId, index) => {
      try {
        await api.playTrack(albumId, index)
        loadPlayer()
        loadQueue()
      } catch (e) {
        pushToast(e.message, 'error')
      }
    },
    [loadPlayer, loadQueue, pushToast],
  )

  const handleToggleFavorite = useCallback(
    async (albumId) => {
      try {
        const res = await api.toggleFavorite(albumId)
        setAlbums((list) =>
          list.map((a) => (a.id === albumId ? { ...a, favorite: !!res.favorite } : a)),
        )
      } catch (e) {
        pushToast(e.message, 'error')
      }
    },
    [pushToast],
  )

  const handleDeleteAlbum = useCallback(
    async (albumId) => {
      try {
        await api.deleteAlbum(albumId)
        pushToast('Альбом удалён', 'success')
        setOpenAlbumId(null)
        loadAlbums()
      } catch (e) {
        pushToast(e.message, 'error')
      }
    },
    [loadAlbums, pushToast],
  )

  const handleAddToQueue = useCallback(
    async (albumId, index, mode) => {
      try {
        await api.addToQueue(albumId, index, mode)
        pushToast('Добавлено в очередь', 'success')
        loadQueue()
      } catch (e) {
        pushToast(e.message, 'error')
      }
    },
    [loadQueue, pushToast],
  )

  const handleJump = useCallback(
    async (pos) => {
      try {
        await api.jumpQueue(pos)
        loadQueue()
        loadPlayer()
      } catch (e) {
        pushToast(e.message, 'error')
      }
    },
    [loadQueue, loadPlayer, pushToast],
  )

  const handlePlayerCmd = useCallback(
    async (cmd) => {
      try {
        await api.playerCmd(cmd)
        loadPlayer()
      } catch (e) {
        pushToast(e.message, 'error')
      }
    },
    [loadPlayer, pushToast],
  )

  const handleSeek = useCallback(
    async (sec) => {
      try {
        await api.playerSeek(sec)
        loadPlayer()
      } catch (e) {
        pushToast(e.message, 'error')
      }
    },
    [loadPlayer, pushToast],
  )

  const handleVolume = useCallback(
    async (vol) => {
      try {
        const res = await api.playerVolume(vol)
        setPlayer((p) => ({ ...p, volume: res.volume ?? vol }))
      } catch (e) {
        pushToast(e.message, 'error')
      }
    },
    [pushToast],
  )

  const handleSleep = useCallback(
    async (minutes) => {
      try {
        await api.playerSleep(minutes)
        setSleepMinutes(minutes > 0 ? minutes : null)
      } catch (e) {
        pushToast(e.message, 'error')
      }
    },
    [pushToast],
  )

  const isPlayingAlbum = useCallback(
    (album) => player.state === 'playing' && player.album_id === album.id,
    [player],
  )

  const openAlbum = albums.find((a) => a.id === openAlbumId) || null
  const hasTrack = player.state !== 'stop' && player.album_id != null

  const pageProps = {
    albums,
    loading: albumsLoading,
    onOpen: setOpenAlbumId,
    onPlay: handlePlayAlbum,
    onToggleFavorite: (album) => handleToggleFavorite(album.id),
    isPlayingAlbum,
  }

  return (
    <div className="flex h-screen w-full overflow-hidden bg-bg-base text-text-primary">
      <Sidebar
        view={view}
        onNavigate={setView}
        counts={{ albums: albums.length, queue: (queue.queue || []).length }}
        collapsed={sidebarCollapsed}
        onToggle={() => setSidebarCollapsed((v) => !v)}
        onOpenSettings={() => setSettingsOpen(true)}
      />

      <main className="flex min-h-0 min-w-0 flex-1 flex-col">
        <TopBar
          search={libraryQuery}
          onSearch={(q) => {
            setLibraryQuery(q)
            if (view !== 'library') setView('library')
          }}
          onAddTracker={() => setView('tracker')}
          onOpenSettings={() => setSettingsOpen(true)}
        />

        <div
          className={`min-h-0 flex-1 overflow-y-auto bg-bg-main ${
            hasTrack ? 'pb-32' : 'pb-16'
          } md:pb-0`}
        >
          {view === 'library' && (
            <Library query={libraryQuery} onQueryChange={setLibraryQuery} {...pageProps} />
          )}
          {view === 'artists' && <Artists {...pageProps} />}
          {view === 'tracker' && (
            <Tracker notify={pushToast} onAlbumAdded={loadAlbums} />
          )}
          {view === 'queue' && (
            <Queue
              queue={queue}
              player={player}
              loading={queueLoading}
              onJump={handleJump}
            />
          )}
        </div>

        <PlayerBar
          player={player}
          onCmd={handlePlayerCmd}
          onSeek={handleSeek}
          onVolume={handleVolume}
          onSleep={handleSleep}
          sleepMinutes={sleepMinutes}
          onOpenAlbum={(id) => setOpenAlbumId(id)}
          onNotify={pushToast}
        />
      </main>

      <BottomNav view={view} onNavigate={setView} />

      <AlbumModal
        album={openAlbum}
        albumId={openAlbumId}
        open={openAlbumId != null}
        onClose={() => setOpenAlbumId(null)}
        onPlayTrack={(index) => handlePlayTrack(openAlbumId, index)}
        onAddToQueue={(index, mode) => handleAddToQueue(openAlbumId, index, mode)}
        onToggleFavorite={handleToggleFavorite}
        onDelete={handleDeleteAlbum}
        player={player}
        notify={pushToast}
      />

      <SettingsModal
        open={settingsOpen}
        onClose={() => setSettingsOpen(false)}
        notify={pushToast}
      />

      <Toast toasts={toasts} onDismiss={dismissToast} />
    </div>
  )
}
