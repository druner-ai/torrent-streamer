#!/bin/sh
set -e
# ts-controller теперь запускается самим backend (main.py lifespan) —
# чтобы переключать endpoint (MacBook/NUC/...) на лету.
exec uvicorn main:app --host 0.0.0.0 --port 8000
