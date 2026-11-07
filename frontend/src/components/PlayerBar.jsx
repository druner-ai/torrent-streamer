import {
  AudioLines,
  ChevronDown,
  Disc3,
  MonitorSpeaker,
  Pause,
  Play,
  Repeat,
  Repeat1,
  Shuffle,
  SkipBack,
  SkipForward,
  Timer,
  Volume2,
  VolumeX,
  X,
} from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api.js'
import { formatTime } from '../utils.js'

const SLEEP_OPTIONS = [
  { label: 'Выкл', value: 0 },
  { label: '15 мин', value: 15 },
  { label: '30 мин', value: 30 },
  { label: '60 мин', value: 60 },
  { label: '90 мин', value: 90 },
]

function coverFor(player) {
  if (player.album_id != null) return api.coverUrl(player.album_id)
  if (player.cover_url && (player.cover_url.startsWith('/') || player.cover_url.startsWith('http')))
    return player.cover_url
  return null
}

function Cover({ player, size }) {
  const [failed, setFailed] = useState(false)
  const src = coverFor(player)

  if (!src || failed) {
    return (
      <div
        className="flex shrink-0 items-center justify-center rounded-sm bg-surface-2"
        style={{ width: size, height: size }}
      >
        <Disc3 size={size * 0.55} className="text-text-muted" />
      </div>
    )
  }

  return (
    <img
      src={src}
      alt={player.album || ''}
      onError={() => setFailed(true)}
      className="shrink-0 rounded-sm object-cover"
      style={{ width: size, height: size }}
    />
  )
}

function SeekSlider({ player, onSeek }) {
  const [seek, setSeek] = useState(0)
  const [seeking, setSeeking] = useState(false)

  useEffect(() => {
    if (!seeking && player.elapsed != null) setSeek(Number(player.elapsed))
  }, [player.elapsed, seeking])

  const duration = Math.max(0, Math.floor(Number(player.duration) || 0))
  const pct = duration > 0 ? Math.min(100, (seek / duration) * 100) : 0

  return (
    <div className="flex items-center gap-2">
      <span className="tnum w-10 text-right font-mono text-[11px] text-text-muted">
        {formatTime(seek)}
      </span>
      <input
        type="range"
        min={0}
        max={duration}
        step={1}
        value={seek}
        disabled={duration === 0}
        aria-label="Перемотка"
        style={{ '--fill-pct': `${pct}%` }}
        onChange={(e) => {
          setSeeking(true)
          setSeek(Number(e.target.value))
        }}
        onPointerUp={(e) => {
          setSeeking(false)
          onSeek(Number(e.target.value))
        }}
        onKeyUp={(e) => {
          setSeeking(false)
          onSeek(Number(e.target.value))
        }}
        className="w-full"
      />
      <span className="tnum w-10 font-mono text-[11px] text-text-muted">
        {formatTime(duration)}
      </span>
    </div>
  )
}

function VolumeSlider({ player, onVolume }) {
  const [vol, setVol] = useState(100)
  const [dragging, setDragging] = useState(false)
  const [lastVol, setLastVol] = useState(100)

  useEffect(() => {
    if (!dragging && player.volume != null) setVol(Number(player.volume))
  }, [player.volume, dragging])

  const muted = vol === 0
  const Icon = muted ? VolumeX : Volume2

  return (
    <div className="flex items-center gap-2">
      <button
        type="button"
        onClick={() => {
          // unmute — на прежний уровень, а не всегда на 100
          const next = muted ? lastVol || 100 : 0
          if (!muted) setLastVol(vol)
          setVol(next)
          onVolume(next)
        }}
        className="text-text-muted transition-colors hover:text-text-primary"
        aria-label={muted ? 'Включить звук' : 'Выключить звук'}
      >
        <Icon size={18} />
      </button>
      <input
        type="range"
        min={0}
        max={100}
        step={1}
        value={vol}
        aria-label="Громкость"
        style={{ '--fill-pct': `${vol}%` }}
        onChange={(e) => {
          setDragging(true)
          const v = Number(e.target.value)
          setVol(v)
          if (v > 0) setLastVol(v)
        }}
        onPointerUp={(e) => {
          setDragging(false)
          onVolume(Number(e.target.value))
        }}
        onKeyUp={(e) => {
          setDragging(false)
          onVolume(Number(e.target.value))
        }}
        className="w-24"
      />
    </div>
  )
}

function SleepMenu({ sleepMinutes, onSleep }) {
  const [open, setOpen] = useState(false)

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        title="Таймер сна"
        className={`flex items-center gap-1.5 rounded-sm px-2 py-1 font-mono text-[12px] transition-colors ${
          sleepMinutes
            ? 'bg-warning/10 text-warning'
            : 'text-text-muted hover:bg-surface-2 hover:text-text-primary'
        }`}
      >
        <Timer size={18} />
        {sleepMinutes ? `${sleepMinutes} мин` : 'Сон'}
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute bottom-full right-0 z-20 mb-2 w-32 overflow-hidden rounded-lg border border-border-subtle bg-surface-3 shadow-3">
            {SLEEP_OPTIONS.map((opt) => (
              <button
                key={opt.value}
                type="button"
                onClick={() => {
                  onSleep(opt.value)
                  setOpen(false)
                }}
                className={`block w-full px-3 py-2 text-left text-[13px] transition-colors hover:bg-surface-2 ${
                  sleepMinutes === opt.value ? 'text-primary' : 'text-text-secondary'
                }`}
              >
                {opt.label}
              </button>
            ))}
          </div>
        </>
      )}
    </div>
  )
}

function EndpointSelect({ onNotify }) {
  const [open, setOpen] = useState(false)
  const [eps, setEps] = useState([])
  const [active, setActive] = useState(null)

  async function load() {
    try {
      const d = await api.endpoints()
      setEps(d.endpoints || [])
      setActive(d.active || null)
    } catch {
      /* endpoint недоступен — оставляем пусто */
    }
  }

  useEffect(() => {
    load()
  }, [])

  async function pickEndpoint(name) {
    try {
      await api.selectEndpoint(name, undefined)
      setActive(name)
      onNotify?.(`Endpoint: ${name}`, 'success')
      load()
    } catch (e) {
      onNotify?.(e.message, 'error')
    }
  }

  async function pickDevice(epName, device) {
    setOpen(false)
    try {
      await api.selectEndpoint(epName, device)
      onNotify?.(`${epName} → ${device}`, 'success')
      load()
    } catch (e) {
      onNotify?.(e.message, 'error')
    }
  }

  const activeEp = eps.find((e) => e.active) || eps[0]
  const label = activeEp ? activeEp.current_device || activeEp.name : 'Выход'

  return (
    <div className="relative">
      <button
        type="button"
        onClick={() => {
          load()
          setOpen((v) => !v)
        }}
        title={`Аудиовыход: ${label}`}
        className="flex items-center gap-1.5 rounded-sm px-2 py-1 text-[12px] text-text-muted transition-colors hover:bg-surface-2 hover:text-text-primary"
      >
        <MonitorSpeaker size={18} />
        <span className="max-w-[120px] truncate">{label}</span>
      </button>

      {open && (
        <>
          <div className="fixed inset-0 z-10" onClick={() => setOpen(false)} />
          <div className="absolute bottom-full right-0 z-20 mb-2 max-h-80 w-64 overflow-y-auto rounded-lg border border-border-subtle bg-surface-3 shadow-3">
            {eps.length === 0 ? (
              <div className="px-3 py-2 text-[12px] text-text-muted">Нет эндпоинтов</div>
            ) : (
              eps.map((ep) => (
                <div key={ep.name}>
                  <button
                    type="button"
                    onClick={() => pickEndpoint(ep.name)}
                    className={`flex w-full items-center justify-between px-3 py-1.5 text-left text-[12px] font-semibold uppercase tracking-wider transition-colors hover:bg-surface-2 ${
                      ep.active ? 'text-primary' : 'text-text-secondary'
                    }`}
                  >
                    <span className="flex min-w-0 items-center gap-1">
                      <span className="truncate">{ep.name}</span>
                      {ep.auto && (
                        <span className="shrink-0 text-[9px] font-normal normal-case text-text-muted">
                          auto
                        </span>
                      )}
                    </span>
                    {ep.error ? (
                      <span className="text-[10px] font-normal normal-case text-danger">{ep.error}</span>
                    ) : (
                      ep.active && <span className="text-primary">●</span>
                    )}
                  </button>
                  {(ep.available_devices || []).length === 0 && !ep.error && (
                    <div className="px-5 py-1 text-[12px] text-text-muted">нет устройств</div>
                  )}
                  {(ep.available_devices || []).map((dev) => (
                    <button
                      key={dev}
                      type="button"
                      onClick={() => pickDevice(ep.name, dev)}
                      className={`block w-full truncate px-5 py-1.5 text-left text-[13px] transition-colors hover:bg-surface-2 ${
                        ep.active && dev === ep.current_device ? 'text-primary' : 'text-text-secondary'
                      }`}
                    >
                      {dev}
                      {ep.active && dev === ep.current_device ? ' ✓' : ''}
                    </button>
                  ))}
                </div>
              ))
            )}
          </div>
        </>
      )}
    </div>
  )
}

function Controls({ player, onCmd, onShuffle, onRepeat }) {
  const stopped = player.state === 'stop'
  const playing = player.state === 'playing'
  const shuffle = !!player.shuffle
  const repeat = player.repeat || 'off'
  const RepeatIcon = repeat === 'one' ? Repeat1 : Repeat

  return (
    <div className="flex items-center justify-center gap-3">
      <button
        type="button"
        onClick={onShuffle}
        aria-label="Перемешивание"
        title={shuffle ? 'Перемешивание: вкл' : 'Перемешивание: выкл'}
        className={`transition-colors ${
          shuffle ? 'text-primary' : 'text-text-muted hover:text-text-primary'
        }`}
      >
        <Shuffle size={16} />
      </button>
      <button
        type="button"
        disabled={stopped}
        onClick={() => onCmd('prev')}
        aria-label="Предыдущий трек"
        className="text-text-secondary transition-colors hover:text-text-primary disabled:text-text-disabled"
      >
        <SkipBack size={20} className="fill-current" />
      </button>
      <button
        type="button"
        disabled={stopped}
        onClick={() => onCmd(playing ? 'pause' : 'play')}
        aria-label={playing ? 'Пауза' : 'Играть'}
        className="flex h-10 w-10 items-center justify-center rounded-full bg-primary text-on-primary transition-all hover:scale-105 hover:bg-primary-hover active:bg-primary-pressed disabled:bg-surface-2 disabled:text-text-disabled disabled:hover:scale-100"
      >
        {playing ? (
          <Pause size={18} className="fill-current" />
        ) : (
          <Play size={18} className="ml-0.5 fill-current" />
        )}
      </button>
      <button
        type="button"
        disabled={stopped}
        onClick={() => onCmd('next')}
        aria-label="Следующий трек"
        className="text-text-secondary transition-colors hover:text-text-primary disabled:text-text-disabled"
      >
        <SkipForward size={20} className="fill-current" />
      </button>
      <button
        type="button"
        onClick={onRepeat}
        aria-label="Повтор"
        title={
          repeat === 'all' ? 'Повтор очереди' : repeat === 'one' ? 'Повтор трека' : 'Без повтора'
        }
        className={`transition-colors ${
          repeat === 'off' ? 'text-text-muted hover:text-text-primary' : 'text-primary'
        }`}
      >
        <RepeatIcon size={16} />
      </button>
    </div>
  )
}

function ReplayGainToggle({ active, onToggle }) {
  return (
    <button
      type="button"
      onClick={onToggle}
      aria-label="Выравнивание громкости"
      title={
        active
          ? 'ReplayGain: вкл (громкость выровнена к −18 LUFS)'
          : 'ReplayGain: выкл (нажмите, чтобы выравнивать громкость)'
      }
      className={`flex items-center gap-1.5 rounded-sm px-2 py-1 text-[12px] transition-colors ${
        active
          ? 'bg-primary/10 text-primary'
          : 'text-text-muted hover:bg-surface-2 hover:text-text-primary'
      }`}
    >
      <AudioLines size={18} />
    </button>
  )
}

export default function PlayerBar({
  player,
  onCmd,
  onSeek,
  onVolume,
  onSleep,
  sleepMinutes,
  onOpenAlbum,
  onNotify,
  onShuffle,
  onRepeat,
  onReplayGain,
}) {
  const [mobileOpen, setMobileOpen] = useState(false)
  const hasTrack = player.state !== 'stop' && player.album_id != null

  return (
    <>
      {/* Desktop full bar */}
      <div className="hidden h-20 shrink-0 items-center gap-4 border-t border-border-subtle bg-surface-1 px-4 shadow-player md:flex">
        <button
          type="button"
          onClick={() => hasTrack && onOpenAlbum(player.album_id)}
          className="flex min-w-0 max-w-[220px] items-center gap-3 text-left"
        >
          <Cover player={player} size={48} />
          <span className="min-w-0">
            <span className="block truncate text-[14px] font-medium text-text-primary">
              {player.track || 'Ничего не играет'}
            </span>
            <span className="block truncate text-[12px] text-text-secondary">
              {player.artist || player.album || 'Выберите трек'}
            </span>
          </span>
        </button>

        <div className="ml-auto flex w-[260px] shrink-0 justify-center">
          <Controls player={player} onCmd={onCmd} onShuffle={onShuffle} onRepeat={onRepeat} />
        </div>

        <div className="flex flex-1 items-center gap-4">
          <SeekSlider player={player} onSeek={onSeek} />
        </div>

        <VolumeSlider player={player} onVolume={onVolume} />
        <ReplayGainToggle active={!!player.replaygain} onToggle={onReplayGain} />
        <EndpointSelect onNotify={onNotify} />
        <SleepMenu sleepMinutes={sleepMinutes} onSleep={onSleep} />
      </div>

      {/* Mobile mini player */}
      {hasTrack && (
        <div
          role="button"
          tabIndex={0}
          onClick={() => setMobileOpen(true)}
          onKeyDown={(e) => {
            if (e.key === 'Enter' || e.key === ' ') setMobileOpen(true)
          }}
          className="fixed inset-x-0 bottom-14 z-30 flex h-14 cursor-pointer items-center gap-3 border-t border-border-subtle bg-surface-1 px-3 shadow-player md:hidden"
        >
          <div className="absolute inset-x-0 top-0 h-0.5 bg-surface-inset">
            <div
              className="h-full bg-primary"
              style={{
                width: `${player.duration > 0 ? Math.min(100, ((player.elapsed || 0) / player.duration) * 100) : 0}%`,
              }}
            />
          </div>
          <Cover player={player} size={40} />
          <span className="min-w-0 flex-1 text-left">
            <span className="block truncate text-[14px] font-medium text-text-primary">
              {player.track || '—'}
            </span>
            <span className="block truncate font-mono text-[10.5px] text-text-muted">
              {formatTime(player.elapsed || 0)} / {formatTime(player.duration || 0)}
            </span>
          </span>
          <button
            type="button"
            aria-label={player.state === 'playing' ? 'Пауза' : 'Играть'}
            onClick={(e) => {
              e.stopPropagation()
              onCmd(player.state === 'playing' ? 'pause' : 'play')
            }}
            className="flex h-10 w-10 items-center justify-center rounded-full text-primary"
          >
            {player.state === 'playing' ? (
              <Pause size={20} className="fill-current" />
            ) : (
              <Play size={20} className="ml-0.5 fill-current" />
            )}
          </button>
          <button
            type="button"
            aria-label="Следующий трек"
            onClick={(e) => {
              e.stopPropagation()
              onCmd('next')
            }}
            className="flex h-10 w-10 items-center justify-center text-text-secondary"
          >
            <SkipForward size={20} className="fill-current" />
          </button>
        </div>
      )}

      {/* Mobile expanded sheet */}
      {mobileOpen && (
        <div className="animate-fade-in fixed inset-0 z-50 flex flex-col bg-bg-base p-6 md:hidden">
          <div className="flex items-center justify-between">
            <span className="font-mono text-[11px] font-semibold uppercase tracking-[0.07em] text-text-muted">
              Сейчас играет
            </span>
            <button
              type="button"
              onClick={() => setMobileOpen(false)}
              aria-label="Свернуть"
              className="flex h-8 w-8 items-center justify-center rounded-full bg-surface-2 text-text-secondary"
            >
              <ChevronDown size={20} />
            </button>
          </div>

          <div className="flex flex-1 flex-col items-center justify-center gap-6">
            <Cover player={player} size={280} />
            <div className="w-full text-center">
              <h2 className="truncate text-[24px] font-bold text-text-primary">
                {player.track || '—'}
              </h2>
              <p className="truncate text-[16px] text-text-secondary">
                {player.artist || player.album || '—'}
              </p>
            </div>
            <div className="w-full">
              <SeekSlider player={player} onSeek={onSeek} />
            </div>
            <Controls player={player} onCmd={onCmd} onShuffle={onShuffle} onRepeat={onRepeat} />
            <div className="flex w-full items-center justify-between">
              <VolumeSlider player={player} onVolume={onVolume} />
              <ReplayGainToggle active={!!player.replaygain} onToggle={onReplayGain} />
              <EndpointSelect onNotify={onNotify} />
              <SleepMenu sleepMinutes={sleepMinutes} onSleep={onSleep} />
            </div>
          </div>
        </div>
      )}
    </>
  )
}
