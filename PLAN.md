# Торрент-стример на NUC (Минск)

Цель: конечное устройство, куда подаёшь ссылку на торрент-сайт / magnet / название альбома →
получаешь постоянную библиотеку альбомов → играешь и управляешь. NUC играет сам (USB-DAC → Marantz → Tannoy).

## Решения (зафиксированы 2026-11-02)
1. Каталог — **постоянная библиотека**.
2. Источники — **шире**: Prowlarr (rutracker, rutor, приватные и т.д.).
3. Плеер — **NUC играет сам** (MPD → ALSA → USB-DAC). DAC — вне скоупа, Андрей решает отдельно.

## Архитектура
```
ВХОД: ссылка / magnet / название
  → Prowlarr (индексаторы)
  → Парсер метаданных (MusicBrainz/Discogs)
  → Каталог (SQLite, постоянная библиотека)
  → qbittorrent-nox (sequential download) → файлы
  → MPD (bit-perfect) → ALSA → USB-DAC → Marantz → Tannoy
УПРАВЛЕНИЕ: веб-UI, с телефона через Tailscale
```

## Компоненты (на NUC)
- Prowlarr (Docker) — индексаторы (этап 1)
- qbittorrent-nox (хост) — downloader, sequential, работает за CGNAT
- MPD (хост) — битперфект-плеер
- Бэкенд (FastAPI, Docker) — клей (этап 1)
- Веб-UI (Docker) — каталог + пульт (этап 1)

## Этапы
- [x] Этап 0 (база): MPD + qbittorrent-nox; проверено «MPD играет HTTP-поток», «NUC качает 18 MiB/s»
- [ ] Этап 1 (каталог): Prowlarr + бэкенд + веб-UI → «ссылка → альбом → play → управление»
- [ ] Этап 2 (библиотека + hi-res): закачка целиком для DSD/SACD, интеграция с Navidrome

## Протоколы (вывод научного ресёрча, Kimi)
- Свой протокол с нуля НЕ нужен. libtorrent/WebTorrent уже умеют on-demand + seek + time-critical pieces.
- Правильный путь — overlay поверх BitTorrent (MAST). qbittorrent sequential + MPD = практический MAST-lite.
- FLAC/DSF на лету — реально. SACD ISO/DST — отдельный подпроект (качаем целиком / sacd_extract).

## Лог
- 2026-11-02: этапы согласованы (постоянная библиотека, Prowlarr, NUC играет сам), старт Этапа 0.
- 2026-11-02 Этап 0: MPD 0.23.14 играет FLAC HTTP-поток ✅. NUC качает: aria2 11 MiB/s, qbittorrent-nox 18 MiB/s ✅.
- 2026-11-02 Этап 0: TorrServer/MatriX НЕ качает за CGNAT (1-4 пира из 500) — заменён на qbittorrent-nox.
- 2026-11-02 Этап 1 (частично): FastAPI backend + веб-UI развёрнуты. magnet→9 треков→каталог ✅. play → «Access denied» (MPD music_directory) — исправлено (music_directory=downloads + относительные пути), НЕ проверено.
- 2026-11-02 ⚠️ SSH к NUC потерян: authorized_keys изменён (ключ vmx4DHq… отклонён, host key тот же A1kk…). Tailscale SSH не включён на NUC, WireGuard wg0 протух. Жду ручного фикса от Андрея.

## Восстановление SSH (когда Андрей у NUC)
```bash
# на NUC, физически или через консоль:
echo 'ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIGTpU4AMcCfeJb0PmKqK0yLjTdZjBLzHTl0clNQKLTPY deploy@vpsA-rotated-20261026' >> /home/nuc/.ssh/authorized_keys
chmod 600 /home/nuc/.ssh/authorized_keys
```
После этого проверить: `ssh nuc 'echo OK'`.

## Что осталось (после восстановления доступа)
1. Логи backend: почему play «Internal Server Error» (docker logs torrent-streamer-backend).
2. Проверить MPD видит downloads (mpc ls / mpc update) и может add файлы.
3. Полный цикл: add magnet → play → веб-UI → управление.
4. `hermes verify` на NUC.

## Решения (2026-11-02, финал)
- Каноничный код — GitHub **druner-ai/torrent-streamer** (private). NUC тянет через `git pull`.
- Платформа — **NUC** (порт на другие платформы — потом).
- Доступ к веб-UI — **локальная сеть** (http://<ip NUC>:8000), Tailscale не нужен.
- **Spotify Connect (опционально, потом):** librespot/raspotify как второй источник (320 kbps, вывод в USB-DAC, конфликт с MPD за ALSA решать взаимоисключением). Issue #9.
