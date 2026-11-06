import { Download, Loader2, RefreshCw } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api.js'

const RELEASES = 'https://github.com/druner-ai/oaat/releases/latest/download'
const DOWNLOADS = [
  { os: 'macOS — Apple Silicon', file: 'tune-bridge-aarch64-apple-darwin.tar.gz' },
  { os: 'macOS — Intel', file: 'tune-bridge-x86_64-apple-darwin.tar.gz' },
  { os: 'Linux x86_64 (.deb)', file: 'tune-bridge_latest_amd64.deb' },
  { os: 'Linux arm64 / Raspberry Pi (.deb)', file: 'tune-bridge_latest_arm64.deb' },
  { os: 'Windows (.zip)', file: 'tune-bridge-x86_64-pc-windows-msvc.zip' },
]

const inputCls =
  'h-9 w-full rounded-sm border border-transparent bg-surface-inset px-3 text-[13px] text-text-primary placeholder:text-text-muted focus:border-strong focus:outline-none'

export default function SetupWizard({ missing, onDone, onSkip, notify }) {
  const [step, setStep] = useState(0)
  const [busy, setBusy] = useState(false)
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [qbtUrl, setQbtUrl] = useState('')
  const [qbtUser, setQbtUser] = useState('')
  const [qbtPass, setQbtPass] = useState('')
  const [eps, setEps] = useState([])
  const item = missing[step]

  useEffect(() => {
    if (item !== 'endpoint') return
    let alive = true
    api
      .endpoints()
      .then((d) => alive && setEps(d.endpoints || []))
      .catch(() => {})
    return () => {
      alive = false
    }
  }, [item, step])

  function next() {
    if (step + 1 >= missing.length) onDone()
    else setStep(step + 1)
  }

  async function saveRutracker() {
    setBusy(true)
    try {
      await api.saveSettings({ rutracker_login: login, rutracker_password: password })
      const r = await api.settingsTest()
      if (r.ok) {
        notify('RuTracker: логин работает', 'success')
        next()
      } else {
        notify(r.error || 'Логин не прошёл', 'error')
      }
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function saveQbt() {
    setBusy(true)
    try {
      await api.saveSettings({
        qbt_url: qbtUrl || undefined,
        qbt_user: qbtUser || undefined,
        qbt_password: qbtPass || undefined,
      })
      const s = await api.setupStatus()
      if (!s.missing.includes('qbittorrent')) {
        notify('qBittorrent подключён', 'success')
        next()
      } else {
        notify('qBittorrent всё ещё недоступен — проверьте адрес и креды', 'error')
      }
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function refreshEps() {
    setBusy(true)
    try {
      const d = await api.endpoints()
      setEps(d.endpoints || [])
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setBusy(false)
    }
  }

  async function pickEp(name) {
    try {
      await api.selectEndpoint(name)
      notify(`Активный endpoint: ${name}`, 'success')
    } catch (e) {
      notify(e.message, 'error')
    }
  }

  return (
    <div className="fixed inset-0 z-[60] flex items-center justify-center bg-overlay p-4">
      <div className="max-h-[90vh] w-full max-w-md overflow-y-auto rounded-lg bg-surface-3 p-6 shadow-4">
        <div className="mb-1 flex items-center justify-between gap-3">
          <h2 className="text-[17px] font-semibold text-text-primary">Первый запуск SoundVault</h2>
          <button
            type="button"
            onClick={onSkip}
            className="shrink-0 text-[12px] text-text-muted transition-colors hover:text-text-primary"
          >
            Пропустить
          </button>
        </div>
        <p className="mb-5 text-[12px] text-text-muted">
          Шаг {step + 1} из {missing.length}
        </p>

        {item === 'rutracker' && (
          <div>
            <h3 className="mb-1 text-[14px] font-medium text-text-primary">Источник: RuTracker</h3>
            <p className="mb-4 text-[12px] text-text-muted">
              Логин и пароль нужны, чтобы скачивать раздачи. Проверяются сразу после сохранения.
            </p>
            <label className="mb-1 block text-[12.5px] text-text-secondary">Логин</label>
            <input
              value={login}
              onChange={(e) => setLogin(e.target.value)}
              placeholder="логин RuTracker"
              className={`mb-3 ${inputCls}`}
            />
            <label className="mb-1 block text-[12.5px] text-text-secondary">Пароль</label>
            <input
              type="password"
              value={password}
              onChange={(e) => setPassword(e.target.value)}
              placeholder="пароль"
              autoComplete="new-password"
              className={inputCls}
            />
            <button
              type="button"
              onClick={saveRutracker}
              disabled={busy || !login || !password}
              className="mt-4 flex w-full items-center justify-center gap-2 rounded-sm bg-primary px-4 py-2 text-[13px] font-medium text-on-primary transition-colors hover:bg-primary-hover disabled:opacity-60"
            >
              {busy && <Loader2 size={14} className="animate-spin" />}
              Сохранить и проверить
            </button>
          </div>
        )}

        {item === 'qbittorrent' && (
          <div>
            <h3 className="mb-1 text-[14px] font-medium text-text-primary">qBittorrent недоступен</h3>
            <p className="mb-4 text-[12px] text-text-muted">
              Сервер качает раздачи через qBittorrent. Укажите адрес и креды (пустые поля =
              значения по умолчанию из окружения).
            </p>
            <label className="mb-1 block text-[12.5px] text-text-secondary">Адрес</label>
            <input
              value={qbtUrl}
              onChange={(e) => setQbtUrl(e.target.value)}
              placeholder="http://127.0.0.1:8080"
              className={`mb-3 ${inputCls}`}
            />
            <label className="mb-1 block text-[12.5px] text-text-secondary">Логин</label>
            <input
              value={qbtUser}
              onChange={(e) => setQbtUser(e.target.value)}
              placeholder="admin"
              className={`mb-3 ${inputCls}`}
            />
            <label className="mb-1 block text-[12.5px] text-text-secondary">Пароль</label>
            <input
              type="password"
              value={qbtPass}
              onChange={(e) => setQbtPass(e.target.value)}
              placeholder="пароль"
              autoComplete="new-password"
              className={inputCls}
            />
            <button
              type="button"
              onClick={saveQbt}
              disabled={busy}
              className="mt-4 flex w-full items-center justify-center gap-2 rounded-sm bg-primary px-4 py-2 text-[13px] font-medium text-on-primary transition-colors hover:bg-primary-hover disabled:opacity-60"
            >
              {busy && <Loader2 size={14} className="animate-spin" />}
              Сохранить и подключить
            </button>
          </div>
        )}

        {item === 'endpoint' && (
          <div>
            <h3 className="mb-1 text-[14px] font-medium text-text-primary">
              Эндпоинты воспроизведения
            </h3>
            <p className="mb-4 text-[12px] text-text-muted">
              Скачайте приложение-эндпоинт на устройства, которые будут играть звук, и запустите
              его — устройство появится здесь само (mDNS).
            </p>
            {eps.length > 0 ? (
              <div className="mb-4 space-y-2">
                {eps.map((ep) => (
                  <button
                    key={ep.name}
                    type="button"
                    onClick={() => pickEp(ep.name)}
                    className={`flex w-full items-center gap-2 rounded-sm px-3 py-2 text-left text-[13px] transition-colors ${
                      ep.active
                        ? 'bg-primary/15 text-primary'
                        : 'bg-surface-inset text-text-secondary hover:bg-surface-2'
                    }`}
                  >
                    <span className="truncate">{ep.name}</span>
                    {ep.auto && (
                      <span className="shrink-0 text-[10px] text-text-muted">mDNS</span>
                    )}
                    {ep.active && <span className="ml-auto text-[11px]">активен</span>}
                  </button>
                ))}
              </div>
            ) : (
              <p className="mb-4 text-[12px] text-text-muted">
                Пока ни одного эндпоинта не видно.
              </p>
            )}
            <button
              type="button"
              onClick={refreshEps}
              disabled={busy}
              className="mb-4 flex items-center gap-2 rounded-sm bg-surface-1 px-3 py-1.5 text-[12px] text-text-secondary transition-colors hover:bg-surface-2 disabled:opacity-60"
            >
              {busy ? <Loader2 size={13} className="animate-spin" /> : <RefreshCw size={13} />}
              Обновить список
            </button>
            <h4 className="mb-2 text-[12px] font-semibold uppercase tracking-wide text-text-muted">
              Скачать эндпоинт
            </h4>
            <div className="space-y-1.5">
              {DOWNLOADS.map((d) => (
                <a
                  key={d.file}
                  href={`${RELEASES}/${d.file}`}
                  className="flex items-center gap-2 rounded-sm bg-surface-inset px-3 py-2 text-[12.5px] text-text-secondary transition-colors hover:bg-surface-2"
                >
                  <Download size={13} className="shrink-0" />
                  {d.os}
                </a>
              ))}
            </div>
            <button
              type="button"
              onClick={next}
              className="mt-5 w-full rounded-sm bg-primary px-4 py-2 text-[13px] font-medium text-on-primary transition-colors hover:bg-primary-hover"
            >
              Готово
            </button>
          </div>
        )}
      </div>
    </div>
  )
}
