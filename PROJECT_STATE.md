# torrent-streamer (SoundVault) — снимок состояния проекта

> Последнее обновление: 2026-11-10. Полная процедурная память — в скилле
> `torrent-streamer` (operations) и `oaat-integration` (dev). Этот файл — компактный
> справочник текущего состояния для быстрой ориентации.

## Что это
Витрина реальных раздач RuTracker → «Скачать» (qbittorrent) → автодокачка в библиотеку
(SQLite) → play → звук на endpoint'е по OAAT. React-фронт SoundVault, публичный доступ
по Basic Auth.

## Поток
```
Каталог RuTracker (краулинг подфорумов через FlareSolverr)
  → GET /api/catalog/groups (раздачи, сгруппированы по релизу, выбор издания)
  → POST /api/catalog/download {t}  (.torrent → qbittorrent → автодокачка)
  → библиотека (albums/tracks, status complete/downloading)
  → POST /api/albums/{id}/tracks/{idx}/play
  → ffmpeg (FLAC→WAV temp) → ts-controller (Unix socket) → OAAT → endpoint
```

## Компоненты (где что живёт)
- **NUC** (Минск, Ubuntu): qbittorrent-nox (downloader), Prowlarr :9696 (поиск),
  FlareSolverr :8191 (Cloudflare-обход для RuTracker), MPD, backend FastAPI
  (Docker, network_mode host, :8000), OAAT endpoint NUC (:9740 control / :9741 web UI,
  systemd `oaat-endpoint-nuc`).
- **MacBook** (endpoint звука): `tune-bridge endpoint` (CoreAudio), :9740/:9741,
  Tailscale `100.75.171.5`. Аудиоустройства: AirPods Pro, Microsoft Teams Audio,
  EDIFIER D32 (внешний), «Динамики MacBook Air» (только при открытой крышке).
- **VPS B** (Амстердам): Caddy reverse-proxy `music.tochenyi.ru` → NUC по Tailscale.
- **OAAT** — bit-perfect транспорт. Канон форка: **github.com/druner-ai/oaat**
  (private, теги `v0.2.8-ts.N` → GitHub Release: macOS arm64/x86_64, Linux
  amd64/arm64 tar.gz+`.deb`, Windows zip; установщики с автозапуском). Рабочие
  копии: `/home/nuc/oaat` (сборка/верификация) и `/home/deploy/oaat` (git);
  MacBook `/Users/tochenyi/oaat`. controller (ts-controller на NUC) → endpoint
  (MacBook/NUC).

## Мульти-эндпоинт (реализовано 2026-11-04)
- Env: `OAAT_ENDPOINTS="MacBook=100.75.171.5:9740,NUC=127.0.0.1:9740"`.
- ts-controller запускает **backend lifespan** (не entrypoint.sh), один процесс держит
  соединение с активным endpoint'ом; активный persist в `/data/active_endpoint`.
- `GET /api/endpoints` — все endpoint'ы + их live-список аудиоустройств;
  `POST /api/endpoints/select {endpoint?, device?}` — переключение endpoint'а
  (перезапуск ts-controller) и/или устройства (Bearer на OAAT web UI).

## Патч OAAT (живой выбор устройств)
- `GET /api/devices` (web_ui.rs) — live-перечисление `list_devices()` (иначе список
  кэшируется при старте endpoint и устаревает).
- device_switch обработчик (main.rs) — live-reconfigure стрима при переключении.
- `control_token` в `[web_ui]` endpoint.toml обязателен для POST с другого хоста:
  без него управляющие POST принимаются только с loopback (403).

## Онбординг и «поставил и забыл» (2026-11-06/07)
- **mDNS-автообнаружение**: новые endpoint'ы появляются в `/api/endpoints`
  (`auto:true`) БЕЗ записи в `OAAT_ENDPOINTS`; кэш обзора TTL 30с (sticky при
  морганиях); zero-config старт. В сети Андрея multicast режет Keenetic —
  фактически Tailscale-fallback.
- **Мастер первого запуска** (`SetupWizard.jsx` + `GET /api/setup/status`):
  RuTracker-логин с проверкой → qBittorrent → экран endpoint'ов со ссылками на
  скачивание (releases druner-ai/oaat). Пропуск — sessionStorage.
- **Runtime-настройки без рестарта**: qbt_url/qbt_user/qbt_password,
  frontend_auth (Basic Auth админки), rutracker_* — в таблице settings
  (env = fallback); читаются на каждый запрос.
- **Per-endpoint control_token**: endpoint ≥ ts.2 генерирует токен сам
  (state-файл `<config>.token`) и публикует авто-токен в mDNS TXT; backend
  приоритет: TXT > setting `ep_token:<name>` > env `OAAT_CONTROL_TOKEN`.
  Регенерация: кнопка в настройках → endpoint exit → супервизор рестарт
  (409 для config-токена).
- **Публичный доступ**: compose-профиль `public` = tailscale-sidecar в netns
  backend'а (TS_AUTHKEY в .env); не включать рядом с host-tailscaled.
- **Автозапуск endpoint'а из установщика**: .deb postinst (systemd
  enable+start), macOS install.sh+LaunchAgent, Windows install.ps1 (HKCU Run).

## Адреса / доступ
- NUC: Tailscale `100.104.232.67`, LAN `192.168.31.204`.
- MacBook: Tailscale `100.75.171.5`, user `tochenyi` (SSH-ключ на NUC `/tmp/hermes_mb_key`).
- Публичный URL: `https://music.tochenyi.ru` (Basic Auth, `andrei:<пароль>`).
- SSH на NUC: `ssh nuc` (ProxyCommand SOCKS5 `127.0.0.1:1080`, ключ `~/.ssh/id_ed25519`).

## Секреты (НЕ в git — только в .env на NUC / файлах)
`QBT_PASS`, `RUTRACKER_LOGIN`/`RUTRACKER_PASSWORD`, `FRONTEND_AUTH` (Basic Auth),
`OAAT_CONTROL_TOKEN`, `PROWLARR_API_KEY`. Значения в чат не выводить — [REDACTED].

## Команды
- Тесты: `ruff check services/backend/` + `services/backend/.venv/bin/python -m pytest -q
  services/backend/tests/ -p no:warnings` (34 passed).
- Фронт: `cd frontend && npm run build`.
- Деплой NUC: `rsync -az --exclude data --exclude .env --exclude __pycache__
  --exclude .venv --exclude .git --exclude .ruff_cache --exclude .pytest_cache
  --exclude node_modules -e 'ssh ...' ./ nuc@100.104.232.67:/home/nuc/torrent-streamer/`
  затем `docker compose up -d --build backend`.
- OAAT пересборка: `cargo build -p oaat-cli --release` на NUC (linux/AlsaDirect) и
  MacBook (CoreAudio, файлы scp NUC→MacBook); endpoint перезапустить с release-бинарём.
- Push без прокси: `NO_PROXY="github.com,$NO_PROXY" ALL_PROXY= HTTP_PROXY= HTTPS_PROXY=
  git push origin main`.

## Известные нюансы (быстрые ответы)
- **Встроенные динамики MacBook не в списке** → крышка закрыта (кламшелл), macOS их
  прячет. Открыть крышку → появятся (список живой).
- **Звук «в пустоту»** → endpoint держит устаревшее устройство (наушники отключились).
  Теперь чинится выбором в UI (патч выше), а не перезапуском endpoint.
- **UDP clock-sync рвётся** на нестабильном uplink MacBook (моб. хотспот/relay) →
  residual latency ~2с. На стабильном WiFi/LAN ок.
- **Cloudflare Tunnel с NUC невозможен** (SNI-блок `api.trycloudflare.com`) → reverse-proxy
  через VPS B.

## Стратегия дистрибуции и обновлений (план 2026-11-10)
Цель — «устанавливается одним пакетом/командой, обновляется с моего сервера», два
горизонта доставки, ОДИН канал обновлений.

- **Два пути установки** (одинаковое приложение внутри):
  1. *Self-host (compose)*: `./install.sh && docker compose up -d` — для своего железа
     (NUC/RPi/NAS), человек сам ставит Docker.
  2. *Образ (Balena/Daphile-стиль)*: флеш-образ предустановленного стека (тот же
     docker + compose + контейнеры) — «флеш и грузись», для флота готовых боксов.
- **Единый канал обновлений**: образ приложения `ghcr.io/druner-ai/soundvault` (+
  `:latest`/`:beta`/`:X.Y.Z`). Обновление в обоих случаях = `docker compose pull &&
  up -d` нового тега с **registry-сервера Андрея** (ghcr). Установка — двумя
  способами, обновление — одним. Self-update-канал закладывается в Фазу 3.
- **План рефакторинга (Фаза 1, поэтапно)**:
  1.1 код: timeout на все `QB`-запросы (fix «qbt завис → backend встал») — beta.15;
  1.2 авто-пароль qbt из env (init-хук, PBKDF2) — beta.16;
  1.3 full-профиль в дефолт (qbt+FlareSolverr в стек) + MPD вычищен (не в тракте) — beta.17;
  1.4 `network_mode: host` → bridge — **ОТМЕНЕНО (2026-11-10)**: host-сеть необходима для звука
      (endpoint'ы по loopback `127.0.0.1:9740` и mDNS-мультикасту не видны из bridge), а Balena
      host-networking поддерживает (`network_mode: host` в balena docker-compose). Пункт снят.
- **Фаза 2** — self-host «один пакет»: `install.sh` = только подготовка хоста (готово в 1.3),
  README — один путь, RuTracker-витрина — опциональная надстройка (не шаг 1 мастера).
- **Фаза 3** — флеш-образ/флот: Balena (`Dockerfile.template` + `balena.yml`, `network_mode: host`,
  `resin-data`, порт через `INGRESS_PORT`) + self-update-канал на registry Андрея.
- **Промо-сайт** (отдельная задача, до выхода «одного пакета»): лендинг с описанием
  фич SoundVault, пошаговой процедурой установки (self-host + образ) и ссылками на
  необходимые файлы (готовый образ, `.env`-шаблон, `install.sh`, endpoint-установщики
  `tune-bridge` для macOS/Linux/Windows). Размещение — на VPS B под своим доменом
  (напр. `soundvault.tochenyi.ru`), рядом с `music.tochenyi.ru`/`beta`.

## Фаза 4 — облачные источники: Яндекс.Диск по публичной ссылке (план 2026-11-10)

**Мотивация (product):** массовый продукт — пользователь вставляет публичную ссылку
на папку Яндекс.Диска → альбомы появляются в библиотеке и играют. Ноль OAuth-токенов,
ноль монтирований (rclone), работает на любом хосте (в т.ч. Balena). Отклонённый
альтернативный путь A1 (rclone mount приватного Диска) требует OAuth + «сохранить папки
на Диск» — трение, неприемлемое для «поставил и забыл».

**Ключевой инсайт (архитектура):** `ts-controller` читает только локальный WAV
(`{"cmd":"play","path":stream_path}`), а backend уже фоново декодирует FLAC/DSD/MP3 → WAV
через ffmpeg (`_predecode`/`_wav_path`). Значит облачный слой сводится к тому, что
**источником для ffmpeg становится URL вместо локального файла** — звуковой тракт
(ts-controller, OAAT, эндпоинты) НЕ трогаем вообще.

**Модель данных:**
- `albums.hash` = `cloud:<public_key>:<облачный путь к папке>` (новый префикс, рядом с
  `local:` — см. `_is_local`/`_local_dir`).
- `tracks.path` для облачных = путь в облаке (относительно `public_key`), не локальный.
- Хранилище облачных источников: `public_key` + label + путь-внутри (таблица settings или
  отдельная `cloud_sources`). Сами ссылки — публичные, секретов нет.

**Механика воспроизведения облачного трека:**
1. download-href через публичный API `cloud-api.yandex.net/v1/disk/public/resources/download
   ?public_key=...&path=...` → `href` на `downloader.disk.yandex.ru` (живёт часы; кэш в
   памяти с TTL, при 403/410 — перегенерация).
2. ffmpeg читает **URL** (не файл) и декодирует в локальный WAV temp — тот же
   `_predecode`/`_start_track`, где `abs_path` может быть URL. `ffmpeg`/`ffprobe` умеют
   http-источник «из коробки».
3. `ts-controller` получает готовый локальный WAV — без изменений.

**Затрагиваемые функции** (расширение на URL, не переписывание):
`_start_track`, `_predecode`, `_read_meta` (ffprobe), `_read_cover` (обложка из трека/папки),
`_measure_and_store_gain` (ReplayGain), новый сканер `_scan_cloud_source` (листинг через
публичный API, альбом = подпапка с аудио).

**Вежливость Яндекса (переносится из `yadisk-music-sync`):** пауза 0.2с между листингами,
0.6с между скачиваниями, retry 429/5xx с экспоненциальным backoff, браузерный UA. Для
стриминга одного файла rate-limit не критичен, но сканирование 10k+ файлов — да.

**Этапы (каждый = коммит + beta + проверка):**
- A2.1 — дизайн + модель данных (этот документ) + префикс `cloud:` и схема хранения источников.
- A2.2 — сканирование: источник по `public_key`, листинг → albums/tracks, с паузами/retry.
- A2.3 — воспроизведение: download-href + ffmpeg по URL + обложки/ReplayGain на лету.
- A2.4 — UI: вкладка «Источники» — вставить ссылку; облачные альбомы в библиотеке.
