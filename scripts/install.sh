#!/usr/bin/env bash
# torrent-streamer — bootstrap на чистый Linux-хост (Ubuntu/Debian).
# Идемпотентен: безопасно запускать повторно.
set -euo pipefail

echo "==> [1/3] Docker"
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    echo "    (перелогинься или выполни: newgrp docker)"
fi
docker --version

echo "==> [2/3] каталоги данных"
mkdir -p data/qbt data/torrents

echo "==> [3/3] готово"
echo ""
echo "Сервисы:  docker compose up -d --build"
echo "Весь стек (backend + qBittorrent + FlareSolverr) поднимется одной командой."
echo "qBittorrent WebUI: http://<host>:8080 (логин admin, пароль из QBT_PASS в .env)."
