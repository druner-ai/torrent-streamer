# torrent-streamer (SoundVault) — снимок состояния проекта

> Последнее обновление: 2026-11-04. Полная процедурная память — в скилле
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
- **OAAT** — bit-perfect транспорт (renesenses/oaat, наш форк `/home/nuc/oaat` и
  `/Users/tochenyi/oaat`): controller (на NUC) → endpoint (MacBook/NUC).

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
