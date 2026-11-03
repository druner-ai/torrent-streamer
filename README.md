# torrent-streamer

Self-hosted аудио-стример: подаёшь ссылку на торрент-сайт / magnet / название альбома →
получаешь постоянную библиотеку → играешь и управляешь. Устройство играет само (MPD → USB-DAC → система).

## Архитектура
```
ВХОД: ссылка / magnet / название
  → Prowlarr (индексаторы)
  → Парсер метаданных (MusicBrainz/Discogs)
  → Каталог (SQLite, постоянная библиотека)
  → qbittorrent-nox (sequential download) → файлы на диске
  → MPD (bit-perfect) → ALSA → USB-DAC → система
УПРАВЛЕНИЕ: веб-UI, доступ через Tailscale
```

## Компоненты
| Слой | Технология | Где |
|---|---|---|
| Индексация | Prowlarr | Docker (этап 1) |
| Downloader | qbittorrent-nox | хост (sequential, работает за CGNAT) |
| Плеер | MPD | хост (ALSA/USB-DAC) |
| Бэкенд | FastAPI | Docker (этап 1) |
| Веб-UI | — | Docker (этап 1) |

## Почему не TorrServer
TorrServer/MatriX за CGNAT подключает 1–4 пира из 500 и не качает данные (проверено эмпирически).
qbittorrent-nox на том же NUC качает 18 MiB/s (10 сидов). CGNAT не блокирует торренты — блокирует TorrServer.

## Установка
```bash
./scripts/install.sh   # Docker + MPD + qbittorrent-nox + audio group + папки
docker compose up -d
```

## API
Защита опциональна: если задан env `API_KEY`, все эндпоинты `/api/*` (кроме `/api/health`) требуют заголовок `X-API-Key: <API_KEY>`.

| Метод | Эндпоинт | Описание |
|---|---|---|
| POST | `/api/add` | Принимает `{ "link": ... }` (magnet/URL), добавляет торрент и создаёт альбом в каталоге. |
| GET | `/api/albums` | Список альбомов со статусом, прогрессом, количеством треков, исполнителем и обложкой. |
| GET | `/api/albums/{id}` | Альбом по `id` со списком треков. |
| DELETE | `/api/albums/{id}?remove_torrent=` | Удаляет альбом из каталога; `remove_torrent=true` также удаляет торрент из qBittorrent. |
| POST | `/api/albums/{id}/play` | Очищает очередь MPD и запускает все треки альбома. |
| GET | `/api/albums/{id}/files` | Список файлов альбома с готовностью и прогрессом скачивания. |
| GET | `/api/player` | Текущее состояние MPD: state, song, elapsed, duration, volume. |
| POST | `/api/player/{cmd}` | Команда плееру: `play`, `pause`, `next`, `prev`, `stop`, `vol<число>`. |
| GET | `/api/health` | Статус сервиса и MPD; всегда 200, доступен без API-ключа. |

## Этапы
- [x] Этап 0 — MPD + qbittorrent-nox (база, проверено: 18 MiB/s за CGNAT, MPD играет FLAC HTTP-поток)
- [ ] Этап 1 — Prowlarr + бэкенд + веб-UI
- [ ] Этап 2 — закачка целиком для DSD/SACD, интеграция с Navidrome
