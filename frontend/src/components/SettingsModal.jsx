import { Loader2, X } from 'lucide-react'
import { useEffect, useState } from 'react'
import { api } from '../api.js'

export default function SettingsModal({ open, onClose, notify }) {
  const [login, setLogin] = useState('')
  const [password, setPassword] = useState('')
  const [passwordSet, setPasswordSet] = useState(false)
  const [loading, setLoading] = useState(false)
  const [saving, setSaving] = useState(false)

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
      })
      .catch((e) => notify(e.message, 'error'))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
  }, [open, notify])

  async function save() {
    setSaving(true)
    try {
      await api.saveSettings({
        rutracker_login: login,
        rutracker_password: password || undefined,
      })
      setPassword('')
      setPasswordSet(true)
      notify('Настройки сохранены', 'success')
      onClose()
    } catch (e) {
      notify(e.message, 'error')
    } finally {
      setSaving(false)
    }
  }

  if (!open) return null

  return (
    <div className="fixed inset-0 z-50 flex items-end justify-center bg-overlay md:items-center" onClick={onClose}>
      <div
        className="w-full max-w-md rounded-t-xl bg-surface-3 p-5 shadow-4 md:rounded-lg"
        onClick={(e) => e.stopPropagation()}
      >
        <div className="mb-4 flex items-center gap-3">
          <h2 className="text-[18px] font-semibold text-text-primary">Настройки</h2>
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
