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

Предварительно: Docker + qBittorrent-nox на хосте (WebUI на 8080).

```bash
git clone https://github.com/druner-ai/torrent-streamer.git
cd torrent-streamer
cp .env.example .env      # заполнить QBT_PASS, DOWNLOADS_DIR, FRONTEND_AUTH
docker compose up -d --build
```

Откройте `http://<хост>:8000`. При первом запуске мастер настройки проведёт по шагам:
подключение к qBittorrent, логин трекера, выбор аудио-эндпоинта.

Переменные окружения — см. `.env.example` (все опциональны, кроме qBittorrent).

## Эндпоинты (звук)

Скачайте `tune-bridge` для своей платформы из релизов
[druner-ai/oaat](https://github.com/druner-ai/oaat/releases) и запустите установщик:

| Платформа | Артефакт | Установка |
|---|---|---|
| macOS (Apple Silicon / Intel) | `.tar.gz` | `install.sh` → LaunchAgent (автозапуск) |
| Linux x86_64 / arm64 | `.deb` или `.tar.gz` | dpkg → systemd-юнит; tar — вручную |
| Windows x64 | `.zip` | `install.ps1` (HKCU autostart) |

Эндпоинт сам объявится в сети по mDNS и появится в «Настройки → Эндпоинты» сервера.
Обновления — кнопкой OTA в веб-интерфейсе.

Публичный доступ из интернета (опционально): укажите `TS_AUTHKEY` (ключ Tailscale) в `.env`
и запустите `docker compose --profile public up -d` — Tailscale-sidecar поднимет сервер
в вашем tailnet без проброса портов.

## Возможности

- Витрина RuTracker: каталог 180k+ релизов, дерево разделов, фильтры, «Новое», обложки,
  отметка «уже в библиотеке».
- Библиотека: альбомы/треки в SQLite, фоновая автодокачка, приоритизация нужных файлов,
  гигиена каталога (purge мёртвых раздач).
- Плеер: очередь, автопереход, shuffle/repeat, плейлисты, история, ReplayGain,
  Media Session/PWA, хоткеи, переключение эндпоинтов на лету.
- Эксплуатация: HTTP Basic Auth, мастер первого запуска, mDNS-автообнаружение,
  OTA-обновление эндпоинтов, человеческие сообщения об ошибках.

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
