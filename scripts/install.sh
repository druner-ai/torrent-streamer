#!/usr/bin/env bash
# torrent-streamer — bootstrap на чистый Linux-хост (Ubuntu/Debian).
# Идемпотентен: безопасно запускать повторно.
set -euo pipefail

echo "==> [1/5] Docker"
if ! command -v docker >/dev/null 2>&1; then
    curl -fsSL https://get.docker.com | sh
    sudo usermod -aG docker "$USER"
    echo "    (перелогинься или выполни: newgrp docker)"
fi
docker --version

echo "==> [2/5] MPD + mpc"
sudo apt-get update -qq
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq mpd mpc

echo "==> [3/5] downloader: qbittorrent-nox"
sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -qq qbittorrent-nox aria2

echo "==> [4/5] audio group + конфиг MPD"
sudo usermod -aG audio "$USER"
sudo cp services/mpd/mpd.conf /etc/mpd.conf
sudo systemctl enable --now mpd || sudo service mpd start || true

echo "==> [5/5] папки данных"
mkdir -p data/qbt data/torrents

echo ""
echo "Готово. Подними сервисы:  docker compose up -d"
echo "qbittorrent WebUI:        http://<host>:8080"
echo "Проверка MPD:             mpc status"
