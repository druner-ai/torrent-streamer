#!/bin/sh
# linuxserver custom-cont-init.d: при ПЕРВОМ старте выставить WebUI-пароль qBittorrent
# из QBT_PASS (вместо случайного временного пароля, который печатается в логи).
#
# Идемпотентен: маркер-файл не даёт перезаписать пароль, который пользователь
# позже сменил через WebUI qbt (иначе каждый рестарт сбрасывал бы его к QBT_PASS).
#
# Формат пароля qBittorrent: PBKDF2-HMAC-SHA512, 100k итераций, 64-байт ключ,
# 16-байт соль → строка @ByteArray(salt_b64:hash_b64) в qBittorrent.conf.

CONF="/config/qBittorrent/qBittorrent.conf"
MARK="/config/.sv_qbt_pass_set"

[ -z "${QBT_PASS:-}" ] && exit 0   # пароль не задан — оставить дефолтное поведение
[ -f "$MARK" ] && exit 0           # уже выставляли — не трогать (пользователь мог сменить)

mkdir -p "$(dirname "$CONF")"

python3 - "$QBT_PASS" "$CONF" <<'PY'
import sys, hashlib, base64, os

pw = sys.argv[1].encode("utf-8")
conf = sys.argv[2]
salt = os.urandom(16)
dk = hashlib.pbkdf2_hmac("sha512", pw, salt, 100_000, dklen=64)
val = "@ByteArray(%s:%s)" % (
    base64.b64encode(salt).decode(),
    base64.b64encode(dk).decode(),
)

# В ini-конфиге qbt ключ содержит обратный слэш: WebUI\Password_PBKDF2
key = r"WebUI\Password_PBKDF2"
lines = []
if os.path.exists(conf):
    with open(conf, "r", encoding="utf-8", errors="replace") as f:
        lines = f.read().splitlines()

# убрать старые строки пароля (в т.ч. случайный временный от linuxserver)
lines = [ln for ln in lines if not ln.startswith(key + "=")]
lines.append("%s=%s" % (key, val))

# секция [Preferences] должна присутствовать
if not any(ln.strip() == "[Preferences]" for ln in lines):
    lines.insert(0, "[Preferences]")

with open(conf, "w", encoding="utf-8") as f:
    f.write("\n".join(lines) + "\n")
PY

touch "$MARK"
