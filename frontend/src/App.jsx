import { useCallback, useEffect, useRef, useState } from 'react'
import { api } from './api.js'
import { usePolling } from './hooks/usePolling.js'
import AlbumModal from './components/AlbumModal.jsx'
import BottomNav from './components/BottomNav.jsx'
import PlayerBar from './components/PlayerBar.jsx'
import SettingsModal from './components/SettingsModal.jsx'
import SetupWizard from './components/SetupWizard.jsx'
import Sidebar from './components/Sidebar.jsx'
import Toast from './components/Toast.jsx'
import TopBar from './components/TopBar.jsx'
import Artists from './pages/Artists.jsx'
import Library from './pages/Library.jsx'
import Queue from './pages/Queue.jsx'
import Tracker from './pages/Tracker.jsx'

// Короткий тихий WAV (моно 8 кГц, 0.5 с тишины) — зациклен, пока играет трек:
// без играющего media-элемента Android/Chrome не поднимает Media Session
// (нет управления в шторе и на локскрине). Звук — цифровая тишина, безвреден.
function makeSilentWav(seconds = 0.5) {
  const rate = 8000
  const n = Math.floor(rate * seconds)
  const buf = new ArrayBuffer(44 + n)
  const v = new DataView(buf)
  const ws = (off, s) => {
    for (let i = 0; i < s.length; i++) v.setUint8(off + i, s.charCodeAt(i))
  }
  ws(0, 'RIFF')
  v.setUint32(4, 36 + n, true)
  ws(8, 'WAVEfmt ')
  v.setUint32(16, 16, true)
  v.setUint16(20, 1, true) // PCM
  v.setUint16(22, 1, true) // mono
  v.setUint32(24, rate, true)
  v.setUint32(28, rate, true)
  v.setUint16(32, 1, true)
  v.setUint16(34, 8, true) // 8 бит
  ws(36, 'data')
  v.setUint32(40, n, true)
  for (let i = 0; i < n; i++) v.setUint8(44 + i, 128) // цифровая тишина
  const bytes = new Uint8Array(buf)
  let bin = ''
  for (let i = 0; i < bytes.length; i++) bin += String.fromCharCode(bytes[i])
  return `data:audio/wav;base64,${btoa(bin)}`
}
const SILENT_WAV = makeSilentWav()

export default function App() {
  const [view, setView] = useState('library')
  const [albums, setAlbums] = useState([])
  const [albumsLoading, setAlbumsLoading] = useState(true)
  const [history, setHistory] = useState([])
  const [player, setPlayer] = useState({ state: 'stop', volume: 100, album_id: null })
  const [queue, setQueue] = useState({ queue: [], pos: -1 })
  const [queueLoading, setQueueLoading] = useState(true)
  const [sleepMinutes, setSleepMinutes] = useState(null)
  const [toasts, setToasts] = useState([])
  const [openAlbumId, setOpenAlbumId] = useState(null)
  const [libraryQuery, setLibraryQuery] = useState('')
  const [sidebarCollapsed, setSidebarCollapsed] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  // null = мастер не показываем; массив missing = показать шаг за шагом
  const [setupMissing, setSetupMissing] = useState(null)
  const [authRequired, setAuthRequired] = useState(false)

  useEffect(() => {
    if (sessionStorage.getItem('sv_setup_skipped')) return
    api
      .setupStatus()
      .then((s) => {
        if (!s.configured) setSetupMissing(s.missing)
      })
      .catch(() => {})
  }, [])

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
      const p = await api.player()
      setPlayer(p)
      setSleepMinutes(p.sleep_minutes ?? null) // сервер — источник правды о таймере
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

  const loadHistory = useCallback(async () => {
    try {
      const d = await api.history()
      setHistory(d.history || [])
    } catch {
      // background poll
    }
  }, [])

  usePolling(loadAlbums, 5000)
  usePolling(loadPlayer, 2000)
  usePolling(loadQueue, 5000)
  usePolling(loadHistory, 10000)

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

  const handleRemoveQueue = useCallback(
    async (pos) => {
      try {
        await api.removeQueue(pos)
        loadQueue()
      } catch (e) {
        pushToast(e.message, 'error')
      }
    },
    [loadQueue, pushToast],
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

  const handleCycleRepeat = useCallback(async () => {
    const modes = ['off', 'all', 'one']
    const nextMode = modes[(modes.indexOf(player.repeat || 'off') + 1) % modes.length]
    try {
      await api.playerRepeat(nextMode)
      loadPlayer()
    } catch (e) {
      pushToast(e.message, 'error')
    }
  }, [player.repeat, loadPlayer, pushToast])

  const handleToggleShuffle = useCallback(async () => {
    try {
      const res = await api.playerShuffle(!player.shuffle)
      if (res.shuffle) pushToast('Очередь перемешана', 'success')
      loadPlayer()
      loadQueue()
    } catch (e) {
      pushToast(e.message, 'error')
    }
  }, [player.shuffle, loadPlayer, loadQueue, pushToast])

  const handleToggleReplayGain = useCallback(async () => {
    const on = !player.replaygain
    try {
      await api.playerReplayGain(on)
      pushToast(
        on
          ? 'ReplayGain включён — громкость выравнивается (замер библиотеки идёт в фоне)'
          : 'ReplayGain выключен',
        'success',
      )
      loadPlayer()
    } catch (e) {
      pushToast(e.message, 'error')
    }
  }, [player.replaygain, loadPlayer, pushToast])

  // --- Media Session: управление в шторе/локскрине и с кнопок Bluetooth-гарнитуры ---
  const silentRef = useRef(null)
  useEffect(() => {
    const el = silentRef.current
    if (!el) return
    if (player.state === 'playing') el.play().catch(() => {})
    else el.pause()
  }, [player.state])

  useEffect(() => {
    if (!('mediaSession' in navigator)) return
    const ms = navigator.mediaSession
    ms.metadata = player.track
      ? new MediaMetadata({
          title: player.track,
          artist: player.artist || '',
          album: player.album || '',
          artwork: player.album_id != null
            ? [{
                src: new URL(api.coverUrl(player.album_id), window.location.origin).href,
                sizes: '500x500',
                type: 'image/jpeg',
              }]
            : [],
        })
      : null
    ms.playbackState =
      player.state === 'playing' ? 'playing' : player.state === 'paused' ? 'paused' : 'none'
    const set = (action, fn) => {
      try {
        ms.setActionHandler(action, fn)
      } catch {
        /* действие не поддерживается браузером */
      }
    }
    set('play', () => handlePlayerCmd('play'))
    set('pause', () => handlePlayerCmd('pause'))
    set('previoustrack', () => handlePlayerCmd('prev'))
    set('nexttrack', () => handlePlayerCmd('next'))
    set('seekto', (d) => {
      if (d.seekTime != null) handleSeek(d.seekTime)
    })
    const dur = Number(player.duration) || 0
    if (dur > 0) {
      try {
        ms.setPositionState({
          duration: dur,
          playbackRate: 1,
          position: Math.min(dur, Math.max(0, Number(player.elapsed) || 0)),
        })
      } catch {
        /* positionState опционален */
      }
    }
  }, [player, handlePlayerCmd, handleSeek])

  // --- горячие клавиши: пробел, стрелки (перемотка ±10 с / громкость ±5) ---
  useEffect(() => {
    const onKey = (e) => {
      const t = e.target
      if (
        t &&
        (t.tagName === 'INPUT' ||
          t.tagName === 'TEXTAREA' ||
          t.tagName === 'SELECT' ||
          t.isContentEditable)
      )
        return
      if (e.metaKey || e.ctrlKey || e.altKey) return
      const dur = Number(player.duration) || 0
      const pos = Number(player.elapsed) || 0
      const vol = Number(player.volume ?? 100)
      switch (e.key) {
        case ' ':
        case 'Spacebar':
          if (t && t.tagName === 'BUTTON') return // не конфликтуют с фокусом кнопки
          if (player.state === 'stop') return
          e.preventDefault()
          handlePlayerCmd(player.state === 'playing' ? 'pause' : 'play')
          break
        case 'ArrowRight':
          if (dur > 0) {
            e.preventDefault()
            handleSeek(Math.min(dur - 1, pos + 10))
          }
          break
        case 'ArrowLeft':
          if (dur > 0) {
            e.preventDefault()
            handleSeek(Math.max(0, pos - 10))
          }
          break
        case 'ArrowUp':
          e.preventDefault()
          handleVolume(Math.min(100, vol + 5))
          break
        case 'ArrowDown':
          e.preventDefault()
          handleVolume(Math.max(0, vol - 5))
          break
        default:
          break
      }
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [player, handlePlayerCmd, handleSeek, handleVolume])

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
    onQueue: (album) => handleAddToQueue(album.id, 0, 'end'),
    onToggleFavorite: (album) => handleToggleFavorite(album.id),
    isPlayingAlbum,
  }

  useEffect(() => {
    const onUnauth = () => setAuthRequired(true)
    window.addEventListener('sv:unauthorized', onUnauth)
    return () => window.removeEventListener('sv:unauthorized', onUnauth)
  }, [])

  return (
    <div className="flex h-screen w-full overflow-hidden bg-bg-base text-text-primary">
      {authRequired && (
        <div className="fixed inset-0 z-[100] flex items-center justify-center bg-overlay p-6">
          <div className="max-w-sm rounded-lg border border-border-subtle bg-surface-2 p-6 text-center shadow-4">
            <p className="mb-2 text-[15px] font-semibold text-text-primary">Требуется вход</p>
            <p className="mb-5 text-[13px] leading-relaxed text-text-secondary">
              Сервер не принял авторизацию — библиотека не загрузилась. Нажмите «Войти» и
              введите логин и пароль в окне браузера.
            </p>
            <button
              type="button"
              onClick={() => window.location.reload()}
              className="h-10 w-full rounded-md bg-primary text-[13.5px] font-semibold text-on-primary transition-colors hover:bg-primary-hover"
            >
              Войти
            </button>
          </div>
        </div>
      )}
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
            <Library
              query={libraryQuery}
              onQueryChange={setLibraryQuery}
              {...pageProps}
              history={history}
              notify={pushToast}
              onPlayPlaylist={async (pid, start) => {
                try {
                  await api.playlistPlay(pid, start)
                  loadPlayer()
                } catch (e) {
                  pushToast(e.message, 'error')
                }
              }}
              onResume={async (aid, idx, pos) => {
                try {
                  await api.playAlbumAt(aid, idx, pos)
                  loadPlayer()
                } catch (e) {
                  pushToast(e.message, 'error')
                }
              }}
              onHistoryRemove={async (aid) => {
                try {
                  await api.historyRemove(aid)
                  loadHistory()
                } catch (e) {
                  pushToast(e.message, 'error')
                }
              }}
              onPlayTrack={handlePlayTrack}
            />
          )}
          {view === 'artists' && <Artists {...pageProps} />}
          {view === 'tracker' && (
            <Tracker notify={pushToast} onAlbumAdded={loadAlbums} />
          )}
          {view === 'queue' && (
            <Queue
              queue={queue}
              loading={queueLoading}
              onJump={handleJump}
              onRemove={handleRemoveQueue}
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
          onShuffle={handleToggleShuffle}
          onRepeat={handleCycleRepeat}
          onReplayGain={handleToggleReplayGain}
        />
      </main>

      {/* тихий зацикленный WAV активирует Media Session на мобильных */}
      <audio ref={silentRef} src={SILENT_WAV} loop preload="auto" className="hidden" />

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

      {setupMissing && (
        <SetupWizard
          missing={setupMissing}
          notify={pushToast}
          onSkip={() => {
            sessionStorage.setItem('sv_setup_skipped', '1')
            setSetupMissing(null)
          }}
          onDone={() => {
            sessionStorage.setItem('sv_setup_skipped', '1')
            setSetupMissing(null)
            pushToast('Первичная настройка завершена', 'success')
          }}
        />
      )}

      <Toast toasts={toasts} onDismiss={dismissToast} />
    </div>
  )
}
