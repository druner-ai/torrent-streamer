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
  playAlbumAt: (id, trackIndex = 0, position = 0) =>
    post(`/api/albums/${id}/play?track_index=${trackIndex}&position=${Math.round(position)}`),
  playTrack: (id, index) => post(`/api/albums/${id}/tracks/${index}/play`),
  toggleFavorite: (id) => post(`/api/albums/${id}/favorite`),
  history: () => request('/api/history'),
  historyRemove: (id) => request(`/api/history/${id}`, { method: 'DELETE' }),
  playlists: () => request('/api/playlists'),
  playlistCreate: (name) => post('/api/playlists', { name }),
  playlistDelete: (id) => request(`/api/playlists/${id}`, { method: 'DELETE' }),
  playlistGet: (id) => request(`/api/playlists/${id}`),
  playlistAdd: (id, albumId, trackIndex = -1) =>
    post(`/api/playlists/${id}/items`, { album_id: albumId, track_index: trackIndex }),
  playlistItemRemove: (pid, itemId) =>
    request(`/api/playlists/${pid}/items/${itemId}`, { method: 'DELETE' }),
  playlistPlay: (id, start = 0) => post(`/api/playlists/${id}/play?start=${start}`),
  deleteAlbum: (id) => request(`/api/albums/${id}`, { method: 'DELETE' }),

  catalogSections: () => request('/api/catalog/sections'),
  catalogGroups: (params = {}) => {
    const qs = new URLSearchParams()
    const fids = params.forum_id ? [].concat(params.forum_id) : []
    fids.forEach((f) => qs.append('forum_id', f))
    if (params.min_seeders) qs.set('min_seeders', params.min_seeders)
    if (params.lossless != null) qs.set('lossless', params.lossless ? 'true' : 'false')
    if (params.q) qs.set('q', params.q)
    if (params.genre) qs.set('genre', params.genre)
    if (params.format) qs.set('format', params.format)
    if (params.source) qs.set('source', params.source)
    if (params.rip) qs.set('rip', params.rip)
    if (params.quality) qs.set('quality', params.quality)
    if (params.sort) qs.set('sort', params.sort)
    if (params.artist) qs.set('artist', params.artist)
    if (params.limit) qs.set('limit', params.limit)
    if (params.offset) qs.set('offset', params.offset)
    const suffix = qs.toString() ? `?${qs.toString()}` : ''
    return request(`/api/catalog/groups${suffix}`)
  },
  catalogArtists: (params = {}) => {
    const qs = new URLSearchParams()
    if (params.q) qs.set('q', params.q)
    if (params.letter) qs.set('letter', params.letter)
    if (params.limit) qs.set('limit', params.limit)
    if (params.offset) qs.set('offset', params.offset)
    const suffix = qs.toString() ? `?${qs.toString()}` : ''
    return request(`/api/catalog/artists${suffix}`)
  },
  catalogCovers: () => post('/api/catalog/covers'),
  coverProxy: (url) => `/api/catalog/cover?url=${encodeURIComponent(url)}`,
  downloadTorrent: (t) => post('/api/catalog/download', { t }),
  catalogTree: () => request('/api/catalog/tree'),
  saveCatalogTree: (enabled) => post('/api/catalog/tree', { enabled }),
  refreshCatalogTree: () => post('/api/catalog/tree/refresh'),
  catalogRefresh: (forums, pages = 1) => post('/api/catalog/refresh', { forums, pages }),
  catalogRefreshStatus: () => request('/api/catalog/refresh/status'),
  catalogPurge: (days = 365) => post(`/api/catalog/purge?days=${days}`),

  settings: () => request('/api/settings'),
  saveSettings: (data) => post('/api/settings', data),
  settingsTest: () => post('/api/settings/test'),
  rutrackerStatus: () => request('/api/rutracker/status'),
  rutrackerResetSession: () => post('/api/rutracker/reset_session'),
  setupStatus: () => request('/api/setup/status'),

  queue: () => request('/api/queue'),
  sources: () => request('/api/sources'),
  addSource: (path) => post('/api/sources', { path }),
  removeSource: (path, deleteAlbums = true) =>
    request(
      `/api/sources?path=${encodeURIComponent(path)}&delete_albums=${deleteAlbums}`,
      { method: 'DELETE' },
    ),
  scanSources: (path) => post('/api/sources/scan', { path: path || '' }),
  addToQueue: (albumId, trackIndex, mode) =>
    post('/api/queue', { album_id: albumId, track_index: trackIndex, mode }),
  removeQueue: (pos) => request(`/api/queue?pos=${pos}`, { method: 'DELETE' }),
  jumpQueue: (pos) => post(`/api/queue/jump?pos=${pos}`),

  endpoints: () => request('/api/endpoints'),
  selectEndpoint: (endpoint, device) => post('/api/endpoints/select', { endpoint, device }),
  regenerateToken: (endpoint) => post('/api/endpoints/regenerate_token', { endpoint }),
  endpointsUpdateCheck: () => request('/api/endpoints/update_check'),
  endpointsUpdate: (endpoint) => post('/api/endpoints/update', { endpoint }),

  player: () => request('/api/player'),
  playerCmd: (cmd) => post(`/api/player/${cmd}`),
  playerRepeat: (mode) => post('/api/player/repeat', { mode }),
  playerShuffle: (on) => post('/api/player/shuffle', { on }),
  playerReplayGain: (on) => post('/api/player/replaygain', { on }),
  playerSeek: (sec) => post(`/api/player/seek?position=${sec}`),
  playerVolume: (vol) => post(`/api/player/volume?level=${vol}`),
  playerSleep: (minutes) => post(`/api/player/sleep?minutes=${minutes}`),
}
