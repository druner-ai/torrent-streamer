import { Loader2, RefreshCw, Trash2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function SettingsModal({ open, onClose, notify }) {
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [passwordSet, setPasswordSet] = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)
  const [testing, setTesting] = useState(false)
  const [qbtUrl, setQbtUrl] = useState('')
  const [qbtUser, setQbtUser] = useState('')
  const [qbtPass, setQbtPass] = useState('')
  const [qbtPassSet, setQbtPassSet] = useState(false)
  const [adminAuth, setAdminAuth] = useState('')
  const [adminAuthSet, setAdminAuthSet] = useState(false)
  const [endpointList, setEndpointList] = useState([])
  const [regenBusy, setRegenBusy] = useState(null)
  const [rtStatus, setRtStatus] = useState(null)
  const [rtBusy, setRtBusy] = useState(false)
  const [updLatest, setUpdLatest] = useState(null)
  const [updBusy, setUpdBusy] = useState(null)
  const [actBusy, setActBusy] = useState(null)
  const [sources, setSources] = useState([])
  const [scanState, setScanState] = useState(null)
  const [backfill, setBackfill] = useState(null)
  const [newDir, setNewDir] = useState('')
  const [newCloudUrl, setNewCloudUrl] = useState('')
  const [srcBusy, setSrcBusy] = useState(false)
  const [epName, setEpName] = useState('')
  const [epAddr, setEpAddr] = useState('')
  const [epBusy, setEpBusy] = useState(null)
  const [srvVersion, setSrvVersion] = useState(null)

  useEffect(() => {
    if (!open) return
    api.health().then((d) => setSrvVersion(d.version)).catch(() => {})
  }, [open])

  const addManualEp = async () => {
    const name = epName.trim()
    const address = epAddr.trim()
    if (!name || !address) return
    setEpBusy('add')
    try {
      await api.addManualEndpoint(name, address)
      setEpName('')
      setEpAddr('')
      const d = await api.endpoints()
      setEndpointList(d.endpoints || [])
      notify?.('Эндпоинт добавлен', 'success')
    } catch (e) {
      notify?.(e.message, 'error')
    }
    setEpBusy(null)
  }

  const removeManualEp = async (name) => {
    setEpBusy(name)
    try {
      await api.removeManualEndpoint(name)
      const d = await api.endpoints()
      setEndpointList(d.endpoints || [])
      notify?.('Эндпоинт удалён', 'success')
    } catch (e) {
      notify?.(e.message, 'error')
    }
    setEpBusy(null)
  }

  const refreshSources = () => {
    api
      .sources()
      .then((d) => {
        setSources(d.sources || [])
        setScanState(d.scan || null)
      })
      .catch(() => {})
    api
      .backfillStatus()
      .then(setBackfill)
      .catch(() => {})
  }

  useEffect(() => {
    if (!open) return
    refreshSources()
    const t = setInterval(refreshSources, 2500)
    return () => clearInterval(t)
  }, [open])

  const addSrc = async () => {
    const p = newDir.trim()
    if (!p) return
    setSrcBusy(true)
    try {
      await api.addSource(p)
      setNewDir('')
      notify?.('Медиапапка добавлена', 'success')
    } catch (e) {
      notify?.(e.message, 'error')
    }
    setSrcBusy(false)
    refreshSources()
  }

  const scanSrc = async (path) => {
    try {
      await api.scanSources(path)
    } catch (e) {
      notify?.(e.message, 'error')
    }
    refreshSources()
  }

  const delSrc = async (path) => {
    setSrcBusy(true)
    try {
      const r = await api.removeSource(path, true)
      notify?.(`Медиапапка убрана, альбомов удалено: ${r.albums_removed}`, 'success')
    } catch (e) {
      notify?.(e.message, 'error')
    }
    setSrcBusy(false)
    refreshSources()
  }

  const addCloudSrc = async () => {
    const u = newCloudUrl.trim()
    if (!u) return
    setSrcBusy(true)
    try {
      await api.addCloudSource(u)
      setNewCloudUrl('')
      notify?.('Облачный источник добавлен', 'success')
    } catch (e) {
      notify?.(e.message, 'error')
    }
    setSrcBusy(false)
    refreshSources()
  }

  const scanCloud = async (key) => {
    try {
      await api.scanCloudSource(key)
    } catch (e) {
      notify?.(e.message, 'error')
    }
    refreshSources()
  }

  const delCloud = async (key) => {
    setSrcBusy(true)
    try {
      const r = await api.removeCloudSource(key)
      notify?.(`Облачный источник убран, альбомов удалено: ${r.albums_removed}`, 'success')
    } catch (e) {
      notify?.(e.message, 'error')
    }
    setSrcBusy(false)
    refreshSources()
  }

  const startBackfill = async () => {
    try {
      const r = await api.backfillStart()
      notify?.(r.started ? 'Заполнение тегов запущено' : 'Все треки уже размечены', 'success')
    } catch (e) {
      notify?.(e.message, 'error')
    }
    refreshSources()
  }

  const stopBackfill = async () => {
    try {
      await api.backfillStop()
      notify?.('Остановлено — прогресс сохранён, можно продолжить позже', 'success')
    } catch (e) {
      notify?.(e.message, 'error')
    }
    refreshSources()
  }

  useEffect(() => {
    if (!open) return
    let alive = true
    setLoading(true)
    api
      .settings()
      .then((d) => {
        if (!alive) return
        setLogin(d.rutracker_login || '')
        setPasswordSet(d.rutracker_password_set)
        setQbtUrl(d.qbt_url || '')
        setQbtUser(d.qbt_user || '')
        setQbtPassSet(d.qbt_password_set)
        setAdminAuthSet(d.frontend_auth_set)
      })
      .catch((e) => notify(e.message, 'error'))
      .finally(() => alive && setLoading(false))
    api
      .endpoints()
      .then((d) => alive && setEndpointList(d.endpoints || []))
      .catch(() => {})
    api
      .rutrackerStatus()
      .then((d) => alive && setRtStatus(d))
      .catch(() => {})
    api
      .endpointsUpdateCheck()
      .then((d) => alive && setUpdLatest(d.latest))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [open, notify])

  async function regenToken(name) {
    setRegenBusy(name)
    try {
      await api.regenerateToken(name)
      notify('Токен регенерирован; endpoint перезапускается', 'success')
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setRegenBusy(null)
    }
  }

  async function activateEp(name) {
    setActBusy(name)
    try {
      await api.selectEndpoint(name, undefined)
      const d = await api.endpoints()
      setEndpointList(d.endpoints || [])
      notify(`Звук переключён на «${name}»`, 'success')
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setActBusy(null)
    }
  }

  async function save() {
    setSaving(true)
    try {
      await api.saveSettings({
        rutracker_login: login,
        rutracker_password: password || undefined,
        qbt_url: qbtUrl || undefined,
        qbt_user: qbtUser || undefined,
        qbt_password: qbtPass || undefined,
        frontend_auth: adminAuth || undefined,
      })
      setPassword('')
      setQbtPass('')
      setAdminAuth('')
      setPasswordSet(true)
      notify('Настройки сохранены', 'success')
      onClose()
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setSaving(false)
    }
  }

  async function testLogin() {
    setTesting(true)
    try {
      const r = await api.settingsTest()
      if (r.ok) notify('Логин RuTracker работает', 'success')
      else notify(r.error || 'Логин не прошёл', 'error')
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setTesting(false)
    }
  }

  async function resetSession() {
    setRtBusy(true)
    try {
      const d = await api.rutrackerResetSession()
      setRtStatus((s) => (s ? { ...s, session: d.session } : s))
      notify('CF-сессия сброшена — следующий запрос решит challenge заново', 'success')
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setRtBusy(false)
    }
  }

  async function updateEp(name) {
    setUpdBusy(name)
    try {
      const d = await api.endpointsUpdate(name)
      notify(
        `Endpoint «${name}» обновляется (${d.asset}, ${(d.size / 1e6).toFixed(1)} МБ) — перезапуск…`,
        'success',
      )
      const eps = await api.endpoints()
      setEndpointList(eps.endpoints || [])
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setUpdBusy(null)
    }
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-overlay md:items-center" onClick={onClose}>
      <div
        className="max-h-[92dvh] w-full max-w-md overflow-y-auto overscroll-contain rounded-t-xl bg-surface-3 p-5 shadow-4 md:rounded-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center gap-3">
          <h2 className="text-[18px] font-semibold text-text-primary">
            Настройки
            {srvVersion && (
              <span className="ml-2 font-mono text-[11px] font-normal text-text-muted">
                v{srvVersion}
              </span>
            )}
          </h2>
          <button
            type="button"
            onClick={onClose}
            className="ml-auto rounded-sm p-1 text-text-muted transition-colors hover:bg-surface-2 hover:text-text-primary"
          >
            <X size={18} />
          </button>
        </div>

        {loading ? (
          <div className="flex justify-center py-10">
            <Loader2 size={22} className="animate-spin text-text-muted" />
          </div>
        ) : (
          <div className="space-y-4">
            <div>
              <h3 className="mb-3 text-[12px] font-semibold uppercase tracking-wide text-text-muted">
                Трекеры
              </h3>
              <label className="mb-1 block text-[12.5px] text-text-secondary">
                Логин RuTracker
              </label>
              <input
                value={login}
                onChange={(e) => setLogin(e.target.value)}
                placeholder="логин"
                className="mb-3 h-9 w-full rounded-sm border border-transparent bg-surface-inset px-3 text-[13px] text-text-primary placeholder:text-text-muted focus:border-strong focus:outline-none"
              />
              <label className="mb-1 block text-[12.5px] text-text-secondary">
                Пароль RuTracker {passwordSet ? '(задан)' : ''}
              </label>
              <input
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                placeholder={passwordSet ? '•••••••• (оставьте пустым, чтобы не менять)' : 'пароль'}
                autoComplete="new-password"
                className="h-9 w-full rounded-sm border border-transparent bg-surface-inset px-3 text-[13px] text-text-primary placeholder:text-text-muted focus:border-strong focus:outline-none"
              />
              <button
                type="button"
                onClick={testLogin}
                disabled={testing}
                className="mt-3 flex items-center gap-2 rounded-sm bg-surface-1 px-3 py-1.5 text-[12px] text-text-secondary transition-colors hover:bg-surface-2 disabled:opacity-60"
              >
                {testing && <Loader2 size={13} className="animate-spin" />}
                Проверить логин
              </button>
              {rtStatus && (
                <div className="mt-4 rounded-sm border border-border bg-surface-1 p-3 text-[12px] text-text-secondary">
                  <div className="mb-1 flex items-center gap-2">
                    <span className={rtStatus.flaresolverr.up ? 'text-success' : 'text-danger'}>
                      ●
                    </span>
                    FlareSolverr:{' '}
                    {rtStatus.flaresolverr.up
                      ? `работает${rtStatus.flaresolverr.version ? ` v${rtStatus.flaresolverr.version}` : ''}`
                      : 'недоступен'}
                  </div>
                  <div className="mb-1">
                    CF-сессия: <span className="font-mono">{rtStatus.session}</span>
                  </div>
                  <div className="mb-1">
                    {rtStatus.refresh && rtStatus.refresh.running
                      ? `Краулинг: ${rtStatus.refresh.done}/${rtStatus.refresh.total} разделов, строк ${rtStatus.refresh.crawled}`
                      : 'Краулинг: не запущен'}
                  </div>
                  <button
                    type="button"
                    onClick={resetSession}
                    disabled={rtBusy}
                    className="mt-1 flex items-center gap-2 rounded-sm bg-surface-2 px-3 py-1.5 text-[12px] text-text-secondary transition-colors hover:bg-surface-3 disabled:opacity-60"
                  >
                    {rtBusy && <Loader2 size={13} className="animate-spin" />}
                    Сбросить CF-сессию
                  </button>
                </div>
              )}
            </div>

            <div>
              <h3 className="mb-3 text-[12px] font-semibold uppercase tracking-wide text-text-muted">
                qBittorrent
              </h3>
              <label className="mb-1 block text-[12.5px] text-text-secondary">Адрес</label>
              <input
                value={qbtUrl}
                onChange={(e) => setQbtUrl(e.target.value)}
                placeholder="http://127.0.0.1:8080"
                className="mb-3 h-9 w-full rounded-sm border border-transparent bg-surface-inset px-3 text-[13px] text-text-primary placeholder:text-text-muted focus:border-strong focus:outline-none"
              />
              <label className="mb-1 block text-[12.5px] text-text-secondary">Логин</label>
              <input
                value={qbtUser}
                onChange={(e) => setQbtUser(e.target.value)}
                placeholder="admin"
                className="mb-3 h-9 w-full rounded-sm border border-transparent bg-surface-inset px-3 text-[13px] text-text-primary placeholder:text-text-muted focus:border-strong focus:outline-none"
              />
              <label className="mb-1 block text-[12.5px] text-text-secondary">
                Пароль {qbtPassSet ? '(задан)' : ''}
              </label>
              <input
                type="password"
                value={qbtPass}
                onChange={(e) => setQbtPass(e.target.value)}
                placeholder={qbtPassSet ? '•••••••• (оставьте пустым, чтобы не менять)' : 'пароль'}
                autoComplete="new-password"
                className="h-9 w-full rounded-sm border border-transparent bg-surface-inset px-3 text-[13px] text-text-primary placeholder:text-text-muted focus:border-strong focus:outline-none"
              />
            </div>

            <div>
              <h3 className="mb-3 text-[12px] font-semibold uppercase tracking-wide text-text-muted">
                Облако (Яндекс.Диск)
              </h3>
              {sources.filter((s) => s.kind === 'cloud').map((s) => (
                <div
                  key={s.key}
                  className="mb-2 flex items-center gap-2 rounded-sm bg-surface-inset px-3 py-2"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-mono text-[12px] text-text-primary">
                      {s.label || s.key}
                    </span>
                    <span className="block text-[11px] text-text-muted">
                      {s.albums} альбомов
                      {scanState?.running && scanState.path === `cloud:${s.label || s.key}`
                        ? ` · сканирование: ${scanState.scanned} папок, +${scanState.added}`
                        : ''}
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={() => scanCloud(s.key)}
                    disabled={scanState?.running || srcBusy}
                    className="rounded-sm p-1.5 text-text-muted transition-colors hover:bg-surface-2 hover:text-text-primary disabled:opacity-40"
                    aria-label="Сканировать"
                    title="Сканировать"
                  >
                    <RefreshCw
                      size={14}
                      className={
                        scanState?.running && scanState.path === `cloud:${s.label || s.key}`
                          ? 'animate-spin' : ''
                      }
                    />
                  </button>
                  <button
                    type="button"
                    onClick={() => delCloud(s.key)}
                    disabled={srcBusy}
                    className="rounded-sm p-1.5 text-text-muted transition-colors hover:bg-surface-2 hover:text-red-400"
                    aria-label="Удалить облачный источник"
                    title="Убрать источник (его альбомы удалятся из библиотеки)"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
              <div className="mb-4 flex gap-2">
                <input
                  value={newCloudUrl}
                  onChange={(e) => setNewCloudUrl(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && addCloudSrc()}
                  placeholder="https://disk.yandex.ru/d/… — публичная ссылка"
                  className="h-9 min-w-0 flex-1 rounded-sm border border-transparent bg-surface-inset px-3 text-[13px] text-text-primary placeholder:text-text-muted focus:border-strong focus:outline-none"
                />
                <button
                  type="button"
                  onClick={addCloudSrc}
                  disabled={srcBusy || !newCloudUrl.trim()}
                  className="h-9 shrink-0 rounded-sm bg-surface-2 px-3 text-[13px] text-text-primary transition-colors hover:bg-surface-3 disabled:opacity-40"
                >
                  Добавить
                </button>
              </div>
            </div>

            <div>
              <h3 className="mb-3 text-[12px] font-semibold uppercase tracking-wide text-text-muted">
                Медиапапки (USB / сетевые)
              </h3>
              {sources.filter((s) => s.kind !== 'cloud').map((s) => (
                <div
                  key={s.path}
                  className="mb-2 flex items-center gap-2 rounded-sm bg-surface-inset px-3 py-2"
                >
                  <span className="min-w-0 flex-1">
                    <span className="block truncate font-mono text-[12px] text-text-primary">
                      {s.path}
                    </span>
                    <span className="block text-[11px] text-text-muted">
                      {s.exists ? `${s.albums} альбомов` : 'недоступна'}
                      {scanState?.running && scanState.path === s.path
                        ? ` · сканирование: ${scanState.scanned} папок, +${scanState.added}`
                        : ''}
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={() => scanSrc(s.path)}
                    disabled={!s.exists || scanState?.running || srcBusy}
                    className="rounded-sm p-1.5 text-text-muted transition-colors hover:bg-surface-2 hover:text-text-primary disabled:opacity-40"
                    aria-label="Сканировать"
                    title="Сканировать"
                  >
                    <RefreshCw
                      size={14}
                      className={
                        scanState?.running && scanState.path === s.path ? 'animate-spin' : ''
                      }
                    />
                  </button>
                  <button
                    type="button"
                    onClick={() => delSrc(s.path)}
                    disabled={srcBusy}
                    className="rounded-sm p-1.5 text-text-muted transition-colors hover:bg-surface-2 hover:text-red-400"
                    aria-label="Удалить медиапапку"
                    title="Убрать источник (его альбомы удалятся из библиотеки)"
                  >
                    <Trash2 size={14} />
                  </button>
                </div>
              ))}
              <div className="flex gap-2">
                <input
                  value={newDir}
                  onChange={(e) => setNewDir(e.target.value)}
                  onKeyDown={(e) => e.key === 'Enter' && addSrc()}
                  placeholder="/mnt/… — путь на сервере (USB / NAS)"
                  className="h-9 min-w-0 flex-1 rounded-sm border border-transparent bg-surface-inset px-3 text-[13px] text-text-primary placeholder:text-text-muted focus:border-strong focus:outline-none"
                />
                <button
                  type="button"
                  onClick={addSrc}
                  disabled={srcBusy || !newDir.trim()}
                  className="h-9 shrink-0 rounded-sm bg-surface-2 px-3 text-[13px] text-text-primary transition-colors hover:bg-surface-3 disabled:opacity-40"
                >
                  Добавить
                </button>
              </div>
              <p className="mt-1 text-[11px] text-text-muted">
                Альбомы из медиапапок попадают в общую библиотеку и играют через тот же тракт.
              </p>
            </div>

            <div>
              <h3 className="mb-3 text-[12px] font-semibold uppercase tracking-wide text-text-muted">
                Теги треков
              </h3>
              <div className="rounded-sm bg-surface-inset px-3 py-2.5">
                <p className="text-[11px] leading-relaxed text-text-muted">
                  Названия, длительности, жанры и годы читаются из аудиофайлов и заполняют
                  всю библиотеку (вкладка «Треки», фильтры). Автоматически запускается после
                  сканирования медиапапок; можно запустить вручную.
                </p>
                {backfill?.running ? (
                  <div className="mt-2.5 flex items-center gap-3">
                    <Loader2 size={14} className="shrink-0 animate-spin text-primary" />
                    <span className="min-w-0 flex-1 truncate text-[12px] text-text-secondary">
                      {backfill.done} / {backfill.total} треков
                      {backfill.album ? ` · ${backfill.album}` : ''}
                    </span>
                    <button
                      type="button"
                      onClick={stopBackfill}
                      className="shrink-0 rounded-sm bg-surface-2 px-3 py-1.5 text-[12.5px] text-text-primary transition-colors hover:bg-surface-3"
                    >
                      Остановить
                    </button>
                  </div>
                ) : (
                  <div className="mt-2.5 flex items-center gap-3">
                    <button
                      type="button"
                      onClick={startBackfill}
                      disabled={srcBusy}
                      className="shrink-0 rounded-sm bg-surface-2 px-3 py-1.5 text-[12.5px] text-text-primary transition-colors hover:bg-surface-3 disabled:opacity-40"
                    >
                      Заполнить теги
                    </button>
                    {backfill && backfill.done > 0 && (
                      <span className="text-[11px] text-text-muted">
                        прошлый прогон: обновлено {backfill.updated} из {backfill.total}
                        {backfill.errors ? `, альбомов пропущено: ${backfill.errors}` : ''}
                      </span>
                    )}
                  </div>
                )}
              </div>
            </div>

            <div>
              <h3 className="mb-3 text-[12px] font-semibold uppercase tracking-wide text-text-muted">
                Доступ (Basic Auth)
              </h3>
              <label className="mb-1 block text-[12.5px] text-text-secondary">
                Креды user:pass {adminAuthSet ? '(заданы)' : '(выключен)'}
              </label>
              <input
                type="password"
                value={adminAuth}
                onChange={(e) => setAdminAuth(e.target.value)}
                placeholder={adminAuthSet ? '•••••••• (оставьте пустым, чтобы не менять)' : 'user:pass'}
                autoComplete="new-password"
                className="h-9 w-full rounded-sm border border-transparent bg-surface-inset px-3 text-[13px] text-text-primary placeholder:text-text-muted focus:border-strong focus:outline-none"
              />
              <p className="mt-1 text-[11px] text-text-muted">
                После сохранения браузер запросит новые креды.
              </p>
            </div>

            <div>
              <h3 className="mb-3 text-[12px] font-semibold uppercase tracking-wide text-text-muted">
                Эндпоинты{updLatest ? ` · доступна ${updLatest}` : ''}
              </h3>
                {endpointList.map((ep) => (
                  <div key={ep.name} className="mb-2 flex items-center gap-2">
                    <button
                      type="button"
                      onClick={() => !ep.active && activateEp(ep.name)}
                      disabled={actBusy != null}
                      title={ep.active ? 'Активный endpoint' : 'Сделать активным — звук пойдёт на него'}
                      className="flex min-w-0 flex-1 items-center gap-2 text-left disabled:opacity-60"
                    >
                      <span
                        className={`h-2 w-2 shrink-0 rounded-full ${
                          ep.connected ? 'bg-primary' : 'bg-text-muted/40'
                        }`}
                      />
                      <span className="min-w-0 flex-1 text-[13px] text-text-secondary">
                        <span className="block truncate">
                          {ep.name}
                          {ep.manual ? ' (вручную)' : ep.auto ? ' (mDNS)' : ''}
                          {ep.active ? ' · активен' : ''}
                        </span>
                        {ep.version && (
                          <span className="block font-mono text-[10.5px] text-text-muted">
                            {ep.os}/{ep.arch} · {ep.version}
                          </span>
                        )}
                      </span>
                      {actBusy === ep.name && <Loader2 size={12} className="shrink-0 animate-spin" />}
                    </button>
                    <button
                      type="button"
                      onClick={() => updateEp(ep.name)}
                      disabled={updBusy != null || !ep.connected}
                      title={
                        ep.connected
                          ? 'Обновить до последней версии (OTA)'
                          : 'Endpoint недоступен'
                      }
                      className="flex shrink-0 items-center gap-1.5 rounded-sm bg-primary/10 px-2.5 py-1.5 text-[11.5px] font-medium text-primary transition-colors hover:bg-primary/20 disabled:opacity-50"
                    >
                      {updBusy === ep.name && <Loader2 size={12} className="animate-spin" />}
                      Обновить
                    </button>
                    <button
                      type="button"
                      onClick={() => regenToken(ep.name)}
                      disabled={regenBusy != null}
                      className="flex shrink-0 items-center gap-1.5 rounded-sm bg-surface-1 px-2.5 py-1.5 text-[11.5px] text-text-secondary transition-colors hover:bg-surface-2 disabled:opacity-60"
                    >
                      {regenBusy === ep.name && <Loader2 size={12} className="animate-spin" />}
                      Регенерировать токен
                    </button>
                    {ep.manual && (
                      <button
                        type="button"
                        onClick={() => removeManualEp(ep.name)}
                        disabled={epBusy != null}
                        title="Удалить endpoint"
                        className="shrink-0 rounded-sm p-1.5 text-text-muted transition-colors hover:bg-surface-2 hover:text-red-400 disabled:opacity-60"
                      >
                        {epBusy === ep.name ? (
                          <Loader2 size={14} className="animate-spin" />
                        ) : (
                          <Trash2 size={14} />
                        )}
                      </button>
                    )}
                  </div>
                ))}
                <div className="mt-2 flex gap-2">
                  <input
                    value={epName}
                    onChange={(e) => setEpName(e.target.value)}
                    placeholder="Имя"
                    className="h-9 w-28 shrink-0 rounded-sm border border-transparent bg-surface-inset px-3 text-[13px] text-text-primary placeholder:text-text-muted focus:border-strong focus:outline-none"
                  />
                  <input
                    value={epAddr}
                    onChange={(e) => setEpAddr(e.target.value)}
                    onKeyDown={(e) => e.key === 'Enter' && addManualEp()}
                    placeholder="host:port (192.168.1.50:9740)"
                    className="h-9 min-w-0 flex-1 rounded-sm border border-transparent bg-surface-inset px-3 font-mono text-[12.5px] text-text-primary placeholder:text-text-muted focus:border-strong focus:outline-none"
                  />
                  <button
                    type="button"
                    onClick={addManualEp}
                    disabled={epBusy != null || !epName.trim() || !epAddr.trim()}
                    className="h-9 shrink-0 rounded-sm bg-surface-2 px-3 text-[13px] text-text-primary transition-colors hover:bg-surface-3 disabled:opacity-40"
                  >
                    {epBusy === 'add' && <Loader2 size={12} className="mr-1 inline animate-spin" />}
                    Добавить
                  </button>
                </div>
                <p className="mt-1 text-[11px] text-text-muted">
                  Тап по названию — сделать endpoint активным (звук пойдёт на него).
                  Эндпоинты находятся автоматически по mDNS; если роутер режет multicast —
                  добавьте вручную по IP (адрес endpoint'а виден в его логе при старте).
                </p>
              </div>
          </div>
        )}

        <div className="mt-5 flex justify-end gap-3">
          <button
            type="button"
            onClick={onClose}
            className="rounded-sm px-4 py-2 text-[13px] text-text-secondary transition-colors hover:bg-surface-2"
          >
            Отмена
          </button>
          <button
            type="button"
            onClick={save}
            disabled={saving}
            className="flex items-center gap-2 rounded-sm bg-primary px-4 py-2 text-[13px] font-medium text-on-primary transition-colors hover:bg-primary-hover disabled:opacity-60"
          >
            {saving && <Loader2 size={14} className="animate-spin" />}
            Сохранить
          </button>
        </div>
      </div>
    </div>
  )
}
