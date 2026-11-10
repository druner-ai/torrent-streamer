# SoundVault (torrent-streamer)

Self-hosted музыкальный сервер: витрина раздач RuTracker → скачивание через qBittorrent →
постоянная библиотека → bit-perfect воспроизведение на аудио-эндпоинты в вашей сети.
Веб-интерфейс (PWA) работает с телефона и десктопа.

*English: a self-hosted music server — browse a RuTracker showcase, download via qBittorrent
into a persistent library, and play bit-perfectly to OAAT audio endpoints discovered over mDNS.*

## Как это устроено

```
┌──────────── Сервер (этот репозиторий, Docker) ────────────┐
│  Веб-UI (React, PWA)  ←→  Backend (FastAPI, SQLite)       │
│    · витрина RuTracker (каталог, фильтры, «✓ в библиотеке»)│
│    · библиотека альбомов, плейлисты, история, ReplayGain   │
│    · qBittorrent: автодокачка, приоритизация файлов        │
└──────────────┬────────────────────────────────────────────┘
               │ UDP + FEC, PTP-подобная синхронизация (OAAT)
       ┌───────┴────────┐
       ▼                ▼
  Эндпоинт A        Эндпоинт B        ← любые машины с ЦАП:
  (macOS/Linux/     (Linux/Windows)     macOS, Linux, Windows,
   Windows)                             Raspberry Pi
```

- **Сервер** — каталог, библиотека и плеер-логика. Работает в Docker (хост: Linux/NUC/RPi/NAS,
  позже — нативные сборки).
- **Эндпоинты** — лёгкие нативные приложения (`tune-bridge`, репозиторий `oaat`): принимают
  аудио-поток и выводят его на ЦАП. Мастер-часы — ЦАП (pull-модель), сеть подстраивается
  PTS-серво; бит-перфект при аппаратной громкости.
- **Обнаружение** — эндпоинты анонсируются по mDNS (`_oaat._tcp`) и находятся сервером сами;
  ключи управления передаются в TXT-записях. Ручная настройка не требуется.
- **Загрузка** — qBittorrent на хосте (sequential, отлично работает за CGNAT).

## Быстрый старт

Предварительно: только Docker. Одна команда поднимает весь серверный стек —
backend + qBittorrent + FlareSolverr (отдельно ничего ставить не нужно):

```bash
git clone https://github.com/druner-ai/torrent-streamer.git
cd torrent-streamer
cp .env.example .env      # заполнить QBT_PASS, DOWNLOADS_DIR, FRONTEND_AUTH
docker compose up -d --build
```

Откройте `http://<хост>:8000`. При первом запуске мастер настройки проведёт по шагам:
логин трекера, выбор аудио-эндпоинта.

Переменные окружения — см. `.env.example` (все опциональны).

## Полный стек (qBittorrent + FlareSolverr в контейнерах)

Поднимается той же командой `docker compose up -d` (это дефолт, профиль не нужен):

- **qBittorrent**: WebUI на `127.0.0.1:8080` (loopback — наружу не торчит),
  логин `admin`. Пароль задаётся в `.env` (`QBT_PASS`) и при ПЕРВОМ старте
  выставляется автоматически (init-хук `services/qbittorrent/set-webui-password.sh`
  генерирует PBKDF2-хеш и кладёт в `qBittorrent.conf`) — доставать временный
  пароль из логов не нужно. Позже сменить пароль можно в WebUI qbt → Настройки →
  Web UI (и обновить `QBT_PASS` в `.env`, чтобы backend логинился с ним же).
  Каталог загрузок — `DOWNLOADS_DIR` из `.env`: монтируется в qbt как `/downloads`
  (его дефолтный путь сохранения), а в backend — тем же хост-путём и как `/downloads`,
  поэтому пути из qbt API резолвятся корректно.
- **FlareSolverr** (обход Cloudflare для RuTracker — витрина трекера и скачивание):
  поднимется на `127.0.0.1:8191` (только loopback), с лимитами 1 CPU / 1 ГБ RAM,
  чтобы решение CF-challenge не мешало звуку. Backend уже настроен на него через
  `FLARESOLVERR_URL` по умолчанию.
- Prowlarr (поиск по индексаторам) остаётся внешним и опциональным —
  `PROWLARR_URL`/`PROWLARR_API_KEY` в `.env`.

## Внешний qBittorrent (для существующих хостов)

Если qBittorrent (и/или FlareSolverr) уже работают на хосте вне Docker (напр.
`qbittorrent-nox`, WebUI на 8080) — не поднимайте их контейнерные версии. Создайте
`docker-compose.override.yml`:

```yaml
services:
  qbittorrent:
    profiles: ["disabled"]
  flaresolverr:
    profiles: ["disabled"]
```

Тогда `docker compose up -d` поднимет только backend, а он достучится до внешнего
qbt по `127.0.0.1:8080` (задайте `QBT_PASS` тот же, что у qbt-nox).

## Эндпоинты (звук)

Скачайте `tune-bridge` для своей платформы из релизов
[druner-ai/oaat](https://github.com/druner-ai/oaat/releases) и запустите установщик:

| Платформа | Артефакт | Установка |
|---|---|---|
| macOS (Apple Silicon / Intel) | `.tar.gz` | `install.sh` → LaunchAgent (автозапуск) |
| Linux x86_64 / arm64 | `.deb` или `.tar.gz` | dpkg → systemd-юнит; tar — вручную |
| Windows x64 | `.zip` | `install.ps1` (HKCU autostart) |

Эндпоинт сам объявится в сети по mDNS и появится в «Настройки → Эндпоинты» сервера.
Если роутер блокирует multicast (mDNS не видит эндпоинт) — добавьте его там же вручную
по `host:port` (адрес виден в логе эндпоинта при старте). Обновления — кнопкой OTA в
веб-интерфейсе.

Публичный доступ из интернета (опционально): укажите `TS_AUTHKEY` (ключ Tailscale) в `.env`
и запустите `docker compose --profile public up -d` — Tailscale-sidecar поднимет сервер
в вашем tailnet без проброса портов.

## Возможности

- Витрина RuTracker: каталог 180k+ релизов, дерево разделов, фильтры, «Новое», обложки,
  отметка «уже в библиотеке».
- Библиотека: альбомы/треки в SQLite, фоновая автодокачка, приоритизация нужных файлов,
  гигиена каталога (purge мёртвых раздач).
- Медиапапки: USB-диски и сетевые монтирования (NAS) как дополнительные источники —
  альбомы попадают в общую библиотеку и играют через тот же тракт (см. ниже).
- Плеер: очередь, автопереход, shuffle/repeat, плейлисты, история, ReplayGain,
  Media Session/PWA, хоткеи, переключение эндпоинтов на лету.
- Эксплуатация: HTTP Basic Auth, мастер первого запуска, mDNS-автообнаружение,
  OTA-обновление эндпоинтов, человеческие сообщения об ошибках.

## Медиапапки (USB / сетевые источники)

Помимо торрентов, библиотека умеет читать музыку из обычных папок — USB-диска или
монтирования NAS (SMB/NFS в `/etc/fstab` или autofs на хосте):

1. Пробросьте монтирование в контейнер тем же путём, что на хосте (read-only).
   В `docker-compose.yml` уже есть два слота — `MEDIA_DIR1` (по умолчанию `/mnt/music`)
   и `MEDIA_DIR2` (`/mnt/nas-music`); свои пути задаются в `.env`.
2. В веб-интерфейсе: **Настройки → Медиапапки** — добавьте путь и нажмите «сканировать».
3. Альбом (каталог с аудиофайлами) попадает в общую библиотеку с меткой источника,
   играет и ставится в очередь наравне с торрентными; теги/обложки читаются из файлов.

Сканер понимает FLAC, DSF/DFF, WAV, MP3, M4A/AAC, OGG/Opus, APE, WV, AIFF; служебные
каталоги (`@eaDir`, `#recycle`, …) пропускаются. Повторное сканирование идемпотентно —
обновляет изменившиеся альбомы. Удаление медиапапки из настроек убирает её альбомы
из библиотеки (файлы на диске не трогаются).

## Разработка

```bash
cd services/backend
python -m pytest tests/ -q     # 97+ тестов
ruff check .
```

Backend: FastAPI + SQLite; frontend: React + Vite + Tailwind (собирается внутри
multi-stage Docker-образа). Роадмап — `docs/CLIENT_JOURNEY.md`.

## Лицензия

Apache-2.0 (см. [LICENSE](LICENSE)). Аудио-транспорт — форк
[oaat](https://github.com/druner-ai/oaat) (Apache-2.0).

Проект предназначен для личного использования. Ответственность за соблюдение
законодательства об авторском праве несёт пользователь.
