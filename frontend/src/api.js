async function request(path, options = {}) {
  const res = await fetch(path, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })

  if (!res.ok) {
    let detail = `Ошибка ${res.status}`
    try {
      const data = await res.json()
      if (typeof data.detail === 'string' && data.detail) detail = data.detail
      else if (typeof data.error === 'string' && data.error) detail = data.error
    } catch {
      // non-JSON error body — keep the generic message
    }
    const err = new Error(detail)
    err.status = res.status
    throw err
  }

  if (res.status === 204) return null
  return res.json()
}

const post = (path, body) =>
  request(path, { method: 'POST', body: body ? JSON.stringify(body) : undefined })

export const api = {
  albums: () => request('/api/albums'),
  albumFiles: (id) => request(`/api/albums/${id}/files`),
  coverUrl: (id) => `/api/albums/${id}/cover`,
  playAlbum: (id) => post(`/api/albums/${id}/play`),
  playTrack: (id, index) => post(`/api/albums/${id}/tracks/${index}/play`),
  toggleFavorite: (id) => post(`/api/albums/${id}/favorite`),
  deleteAlbum: (id) => request(`/api/albums/${id}`, { method: 'DELETE' }),

  catalog: (params = {}) => {
    const qs = new URLSearchParams()
    const fids = params.forum_id ? [].concat(params.forum_id) : []
    fids.forEach((f) => qs.append('forum_id', f))
    if (params.min_seeders) qs.set('min_seeders', params.min_seeders)
    if (params.lossless != null) qs.set('lossless', params.lossless ? 'true' : 'false')
    if (params.q) qs.set('q', params.q)
    const suffix = qs.toString() ? `?${qs.toString()}` : ''
    return request(`/api/catalog${suffix}`)
  },
  catalogSections: () => request('/api/catalog/sections'),
  catalogGroups: (params = {}) => {
    const qs = new URLSearchParams()
    const fids = params.forum_id ? [].concat(params.forum_id) : []
    fids.forEach((f) => qs.append('forum_id', f))
    if (params.min_seeders) qs.set('min_seeders', params.min_seeders)
    if (params.lossless != null) qs.set('lossless', params.lossless ? 'true' : 'false')
    if (params.q) qs.set('q', params.q)
    if (params.genre) qs.set('genre', params.genre)
    if (params.sort) qs.set('sort', params.sort)
    const suffix = qs.toString() ? `?${qs.toString()}` : ''
    return request(`/api/catalog/groups${suffix}`)
  },
  catalogCovers: () => post('/api/catalog/covers'),
  downloadTorrent: (t) => post('/api/catalog/download', { t }),
  catalogTree: () => request('/api/catalog/tree'),
  saveCatalogTree: (enabled) => post('/api/catalog/tree', { enabled }),
  refreshCatalogTree: () => post('/api/catalog/tree/refresh'),

  settings: () => request('/api/settings'),
  saveSettings: (data) => post('/api/settings', data),

  queue: () => request('/api/queue'),
  addToQueue: (albumId, trackIndex, mode) =>
    post('/api/queue', { album_id: albumId, track_index: trackIndex, mode }),
  jumpQueue: (pos) => post(`/api/queue/jump?pos=${pos}`),

  endpoints: () => request('/api/endpoints'),
  selectEndpoint: (endpoint, device) => post('/api/endpoints/select', { endpoint, device }),

  player: () => request('/api/player'),
  playerCmd: (cmd) => post(`/api/player/${cmd}`),
  playerSeek: (sec) => post('/api/player/seek', { sec }),
  playerVolume: (vol) => post('/api/player/volume', { vol }),
  playerSleep: (minutes) => post('/api/player/sleep', { minutes }),
}
