#!/bin/sh
set -e
# Persistent OAAT controller: держит соединение с endpoint (MacBook) и стримит WAV.
# Если endpoint пока недоступен — контроллер ретраит подключение сам.
ts-controller --target "${OAAT_TARGET:-192.168.31.215:9740}" --socket /tmp/ts-controller.sock &
exec uvicorn main:app --host 0.0.0.0 --port 8000
