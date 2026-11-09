/* SoundVault SW: network-first с офлайн-фолбэком на кэш.
   /api/ никогда не кэшируем — только живые данные.
   В кэш попадают ТОЛЬКО успешные ответы (res.ok): закэшированный 401
   отдавался бы из офлайн-фолбэка как «мёртвая» страница без промпта пароля. */
const CACHE = 'soundvault-v2'
const SHELL = ['/', '/index.html', '/manifest.webmanifest', '/icon-192.png', '/icon-512.png']

self.addEventListener('install', (e) => {
  e.waitUntil(
    caches
      .open(CACHE)
      .then((c) =>
        Promise.all(
          SHELL.map((u) =>
            fetch(u)
              .then((r) => (r.ok ? c.put(u, r) : undefined))
              .catch(() => {}),
          ),
        ),
      )
      .catch(() => {}),
  )
  self.skipWaiting()
})

self.addEventListener('activate', (e) => {
  e.waitUntil(
    caches
      .keys()
      .then((keys) => Promise.all(keys.filter((k) => k !== CACHE).map((k) => caches.delete(k))))
      .then(() => self.clients.claim()),
  )
})

self.addEventListener('fetch', (e) => {
  const url = new URL(e.request.url)
  if (e.request.method !== 'GET' || url.origin !== self.location.origin) return
  if (url.pathname.startsWith('/api/')) return
  e.respondWith(
    fetch(e.request)
      .then((res) => {
        if (res.ok) {
          const copy = res.clone()
          caches.open(CACHE).then((c) => c.put(e.request, copy)).catch(() => {})
        }
        return res
      })
      .catch(async () => {
        const m = await caches.match(e.request)
        if (m) return m
        if (e.request.mode === 'navigate') {
          const shell = await caches.match('/index.html')
          if (shell) return shell
        }
        return new Response('', { status: 503 })
      }),
  )
})
