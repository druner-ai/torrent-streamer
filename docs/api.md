# Torrent-streamer — Backend API (для фронтенда)

Все эндпоинты живут под `/api/*`, бэкенд FastAPI на `:8000`. Фронт (React+Vite) в dev проксирует `/api` на `:8000`, в прод бэк отдаёт собранный `dist/` как статику.

## Библиотека (скачанное)

### GET /api/albums
Список альбомов (упорядочен по id DESC):
```json
[
  {
    "id": 3, "hash": "9a37...", "name": "Ray Bryant Trio - ...",
    "status": "downloading|complete|stalled|error|paused",
    "progress": 14.2,              // проценты 0-100
    "tracks": 1, "artist": "Ray Bryant Trio", "cover_url": null,
    "year": "1998", "genre": "Jazz", "format": "16/44",
    "release_key": "ray bryant trio|ray's tribute...",
    "favorite": false
  }
]
```

### GET /api/albums/{aid}
```json
{ "id": 3, "hash": "...", "name": "...", "status": "downloading",
  "artist": "...", "cover_url": null,
  "tracks": [ { "id": 24, "album_id": 3, "path": "...", "name": "...",
                "size": 368933058, "duration": 3754.9, "title": "...",
                "format": "16/44", "year": "1998", "genre": "" } ] }
```

### GET /api/albums/{aid}/files  ← главный для модалки альбома
```json
{
  "id": 3, "name": "...", "artist": "...",
  "files": [
    { "name": "01.flac", "title": "Look Up", "path": "rel/path.flac",
      "size": 12345, "ready": true, "state": "ready|downloading|stalled|error|queued",
      "progress": 1.0, "duration": 220.5, "format": "16/44",
      "year": "1998", "genre": "Jazz" }
  ]
}
```
`state`/`ready`: готов играть только когда `ready === true` (state `ready`). Иначе показывать статус (`качается N%`, `нет пиров`, `ошибка`, `в очереди`).

### GET /api/albums/{aid}/cover  → image (jpeg/png), при отсутствии вернёт 404/плейсхолдер

### POST /api/albums/{aid}/play   → запустить альбом (первый готовый трек)
### POST /api/albums/{aid}/tracks/{idx}/play → запустить конкретный трек
Ответ: `{ "ok": true, "album_id": 3, "track_index": 0, "file": "01.flac" }`
Ошибки: 409 «трек ещё скачивается» / 400 / 500.

### POST /api/albums/{aid}/favorite → `{ "ok": true, "favorite": true|false }` (toggle)

### DELETE /api/albums/{id} → удалить альбом

## Трекер (витрина раздач)

### GET /api/catalog?forum_id=&min_seeders=&lossless=&q=
```json
{ "items": [
    { "t": 5299349, "forum_id": 2277, "title": "Buddy Johnson - 1942-1947",
      "size": 158000000, "seeders": 19, "leechers": 3,
      "lossless": 1, "year": "1998", "crawled_at": 1234567890 }
  ],
  "count": 49 }
```
`lossless` — 0/1 (bool в запросе).

### GET /api/catalog/sections
```json
{ "sections": [
    { "id": "jazz", "title": "Джаз",
      "forums": [ { "id": 2277, "title": "Early Jazz Swing" }, ... ] },
    ...
] }
```
Секции: jazz, blues, classical, rock, отеч. Каждая секция = список подфорумов `(id, title)`.

### POST /api/catalog/refresh  { "forums": [2277], "pages": 1 } → `{ "ok": true, "count": 49 }`
Перекраулит разделы трекера (фоновая операция, может занять секунды).

### POST /api/catalog/download  { "t": 5299349 } → скачать раздачу
```json
{ "ok": true, "t": 5299349, "bytes": 123,
  "album_id": 4, "hash": "...", "name": "...", "tracks": 12 }
```
Сразу заводит альбом в библиотеку (status `downloading`). Ошибка 500/403 если торрент недоступен.

### POST /api/catalog/reimport → `{ "imported": N }` (подтянуть качающееся в библиотеку)

### POST /api/add  { "link": "magnet|url" } → как catalog/download, но по ссылке

### GET /api/search?q=  → `{ "results": [...] }` (Prowlarr; может 503 если не настроен)

## Плеер

### GET /api/player
```json
{ "state": "playing|paused|stop", "album_id": 3, "track_index": 0,
  "track": "Look Up", "elapsed": 12.3, "duration": 220.5,
  "album": "...", "artist": "...", "cover_url": null,
  "volume": 100, "format": "16/44" }
```
`state === "stop"` → `album_id/track/elapsed/...` = null.

### POST /api/player/{cmd}  cmd ∈ {stop, pause, play, next, prev}
`play` = resume после паузы. `next`/`prev` идут по очереди (или соседний трек альбома).

### POST /api/player/seek  { "sec": 60 }
### POST /api/player/volume  { "vol": 80 }   (0-100)
### POST /api/player/sleep  { "minutes": 30 }  → таймер сна (0 = выкл)

## Очередь

### GET /api/queue
```json
{ "queue": [ { "album_id": 3, "track_index": 0, "title": "Look Up", "current": false } ],
  "pos": -1 }
```

### POST /api/queue  { "album_id": 3, "track_index": 0, "mode": "now|next|end" }
`now` — играть сразу, `next` — вставить следующей, `end` — в конец.
Ответ: `{ "ok": true, "queue_len": 1, "pos": 0 }`

### POST /api/queue/jump?pos=2   → перейти на позицию в очереди

## Health

### GET /api/health → `{ "status": "ok|degraded", "version": "...", "mpd": "ok|error" }`
