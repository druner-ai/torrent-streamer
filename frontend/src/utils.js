export function formatTime(sec) {
  if (sec == null || Number.isNaN(Number(sec))) return '0:00'
  const total = Math.max(0, Math.floor(Number(sec)))
  const h = Math.floor(total / 3600)
  const m = Math.floor((total % 3600) / 60)
  const s = total % 60
  if (h > 0) return `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
  return `${m}:${String(s).padStart(2, '0')}`
}

export function formatBytes(n) {
  const num = Number(n)
  if (!Number.isFinite(num) || num <= 0) return '—'
  const units = ['Б', 'КБ', 'МБ', 'ГБ', 'ТБ']
  const i = Math.min(Math.floor(Math.log(num) / Math.log(1024)), units.length - 1)
  const value = num / 1024 ** i
  return `${value >= 100 || i === 0 ? Math.round(value) : value.toFixed(1)} ${units[i]}`
}

// /api/catalog returns a human-readable string in prod, but the docs also
// document raw byte counts — handle both.
export function formatSize(size) {
  if (size == null || size === '') return '—'
  if (typeof size === 'number') return formatBytes(size)
  return String(size).replace(/\u00a0/g, ' ').trim() || '—'
}

export function statusLabel(status) {
  switch (status) {
    case 'downloading':
      return 'Скачивается'
    case 'complete':
      return 'Готово'
    case 'stalled':
      return 'Нет пиров'
    case 'error':
      return 'Ошибка'
    case 'paused':
      return 'Пауза'
    default:
      return status || '—'
  }
}

export function statusTone(status) {
  switch (status) {
    case 'complete':
      return 'success'
    case 'stalled':
    case 'paused':
      return 'warning'
    case 'error':
      return 'danger'
    case 'downloading':
      return 'info'
    default:
      return 'muted'
  }
}

export function fileStateLabel(file) {
  switch (file.state) {
    case 'ready':
      return 'Готово'
    case 'downloading':
      return `Скачивается ${Math.round((file.progress || 0) * 100)}%`
    case 'stalled':
      return 'Нет пиров'
    case 'error':
      return 'Ошибка'
    case 'queued':
      return 'В очереди'
    default:
      return file.state || '—'
  }
}

export function fileStateTone(state) {
  switch (state) {
    case 'ready':
      return 'success'
    case 'downloading':
    case 'queued':
      return 'info'
    case 'stalled':
      return 'warning'
    case 'error':
      return 'danger'
    default:
      return 'muted'
  }
}

export function seedersTone(seeders) {
  if (seeders >= 10) return 'success'
  if (seeders >= 1) return 'warning'
  return 'danger'
}

export function isHiRes(format) {
  const f = (format || '').toLowerCase()
  return f.includes('24/') || f.includes('dsd') || f.includes('sacd') || f.includes('hi-res')
}

export function formatBadge(format) {
  if (!format) return null
  return isHiRes(format) ? '24/96' : '16/44'
}
