"""
torrent-streamer backend — связывает qbittorrent (download) + MPD (play) + каталог (SQLite).
Вход: magnet/ссылка → альбом с треками → play в MPD → управление.
"""
import base64
import contextlib
import hashlib
import html
import io
import json
import os
import random
import re
import socket
import sqlite3
import subprocess
import tarfile
import threading
import time
import urllib.parse
import zipfile
from pathlib import Path
from typing import Annotated

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from mpd import MPDClient
from mutagen import File as MutagenFile
from pydantic import BaseModel

VERSION = "0.9.0"

# --- конфиг (env с дефолтами) ---
QBT_URL = os.getenv("QBT_URL", "http://127.0.0.1:8080")
QBT_USER = os.getenv("QBT_USER", "admin")
QBT_PASS = os.getenv("QBT_PASS", "")
MPD_HOST = os.getenv("MPD_HOST", "127.0.0.1")
MPD_PORT = int(os.getenv("MPD_PORT", "6600"))
OAAT_TARGET = os.getenv("OAAT_TARGET", "")
TS_CONTROLLER_SOCK = os.getenv("TS_CONTROLLER_SOCK", "/tmp/ts-controller.sock")
TS_CONTROLLER_BIN = os.getenv("TS_CONTROLLER_BIN", "ts-controller")
TS_CONTROLLER_LOG = os.getenv("TS_CONTROLLER_LOG", "/tmp/ts-controller.log")


# ---------- мульти-эндпоинт: имя=кандидаты через запятую, кандидаты через | ----------
# Кандидат: "host:port" (прямой адрес) или "mdns:SUBSTR" (поиск _oaat._tcp по подстроке имени).
# Порядок кандидатов = приоритет: mDNS (LAN, дома) важнее прямого Tailscale-адреса (дорога).
# Пример: OAAT_ENDPOINTS=Desk=mdns:Desk|192.0.2.7:9740,Server=127.0.0.1:9740
def _parse_endpoints(raw: str) -> dict:
    eps = {}
    for part in raw.split(","):
        part = part.strip()
        if "=" in part:
            name, addr = part.split("=", 1)
            name, addr = name.strip(), addr.strip()
            if name and addr:
                eps[name] = [c.strip() for c in addr.split("|") if c.strip()]
    return eps


def _mdns_browse(timeout: float = 2.5) -> tuple[dict[str, str], dict[str, str]]:
    """Обзор _oaat._tcp: ({имя: 'ip:port'}, {имя: token из mDNS TXT})."""
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        return {}, {}
    try:
        zc = Zeroconf()
    except Exception:  # noqa: BLE001 — Zeroconf кидает разные ошибки (OSError/RuntimeError) на разных системах
        return {}, {}
    found: dict[str, str] = {}
    tokens: dict[str, str] = {}

    class _Listener(ServiceListener):
        def add_service(self, _zc, type_, name):
            info = _zc.get_service_info(type_, name)
            if info and info.addresses:
                raw = info.addresses[0]
                ip = socket.inet_ntoa(raw) if len(raw) == 4 else socket.inet_ntop(socket.AF_INET6, raw)
                # «MacBook DAC._oaat._tcp.local.» → «MacBook DAC»
                disp = name.split("._oaat._tcp")[0]
                found[disp] = f"{ip}:{info.port}"
                tok = (info.properties or {}).get(b"token")
                if tok:
                    tokens[disp] = tok.decode(errors="replace")

        def remove_service(self, _zc, type_, name):
            return None

        def update_service(self, _zc, type_, name):
            return None

    browser = ServiceBrowser(zc, "_oaat._tcp.local.", _Listener())
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.1)
        if found:
            time.sleep(0.4)  # дать остальным endpoint'ам успеть ответить
            break
    browser.cancel()
    zc.close()
    return found, tokens


# Кэш mDNS-обнаружения: {name: 'ip:port'} + момент снимка. Пустой результат НЕ
# перезаписывает непустой кэш сразу (моргания mDNS не должны ронять список);
# чистим только когда никого не видно 3 TTL подряд.
_MDNS_SERVICES: dict[str, str] = {}
_MDNS_TOKENS: dict[str, str] = {}
_MDNS_TS = 0.0
_MDNS_TTL = 30.0
_MDNS_LOCK = threading.Lock()


def _mdns_services(force: bool = False) -> dict[str, str]:
    """Все OAAT-endpoint'ы из mDNS (кэш TTL 30с) — включая не настроенные в .env."""
    global _MDNS_TS, _MDNS_SERVICES, _MDNS_TOKENS
    now = time.monotonic()
    with _MDNS_LOCK:
        if not force and now - _MDNS_TS < _MDNS_TTL:
            return dict(_MDNS_SERVICES)
    services, tokens = _mdns_browse()
    with _MDNS_LOCK:
        if services:
            _MDNS_SERVICES = services
            _MDNS_TOKENS = tokens
        elif now - _MDNS_TS >= _MDNS_TTL * 3:
            _MDNS_SERVICES = {}
            _MDNS_TOKENS = {}
        _MDNS_TS = now
    return dict(_MDNS_SERVICES)


def _mdns_token(name: str) -> str | None:
    """control_token конкретного endpoint'а: mDNS TXT (авто-токен) > настройка в БД."""
    if name in _mdns_services():
        tok = _MDNS_TOKENS.get(name)
        if tok:
            return tok
    return _get_setting(f"ep_token:{name}") or None


def _mdns_lookup(substr: str, timeout: float = 3.0, force: bool = False) -> str | None:
    """Поиск OAAT-endpoint по mDNS (_oaat._tcp), чьё имя содержит substr → 'ip:port'.

    Endpoint (tune-bridge) сам регистрируется в mDNS: name=«MacBook DAC» port=9740
    service_type=_oaat._tcp. Работает только в LAN (multicast) — вне LAN вернёт None,
    и вызывающий уйдёт на прямой Tailscale-адрес.
    """
    for name, addr in _mdns_services(force=force).items():
        if substr.lower() in name.lower():
            return addr
    return None


OAAT_ENDPOINTS = _parse_endpoints(os.getenv("OAAT_ENDPOINTS", ""))
if not OAAT_ENDPOINTS and OAAT_TARGET:
    # legacy: один прямой адрес → единственный эндпоинт; иначе полагаемся на mDNS-автообнаружение
    OAAT_ENDPOINTS = {"Endpoint": [OAAT_TARGET]}


def _manual_endpoints() -> dict:
    """Эндпоинты, добавленные вручную через UI (настройка manual_endpoints: {имя: 'host:port'})."""
    raw = _get_setting("manual_endpoints")
    if not raw:
        return {}
    try:
        d = json.loads(raw)
    except ValueError:
        return {}
    return {k: v for k, v in d.items() if isinstance(k, str) and isinstance(v, str) and v}


def _configured_endpoints() -> dict:
    """Все настроенные эндпоинты: .env (приоритет при коллизии имён) + добавленные вручную."""
    merged = {k: [v] for k, v in _manual_endpoints().items()}
    merged.update(OAAT_ENDPOINTS)
    return merged
ACTIVE_EP_FILE = os.getenv("ACTIVE_EP_FILE", "/data/active_endpoint")
OAAT_CONTROL_TOKEN = os.getenv("OAAT_CONTROL_TOKEN", "")
# Виртуальные/ненужные аудиоустройства, которые прячем из списка (подстроки через запятую).
OAAT_IGNORE_DEVICES = [m.strip().lower() for m in os.getenv(
    "OAAT_IGNORE_DEVICES", "microsoft teams audio").split(",") if m.strip()]

# Кэш резолва адресов: name -> (expires_monotonic, "host:port"). mDNS-результат живёт
# TTL секунд, потом пересматривается (это и есть авто-обнаружение при переезде).
_EP_ADDR_CACHE: dict[str, tuple[float, str]] = {}
_EP_ADDR_TTL = 30.0


def _resolve_ep(name: str, force: bool = False) -> str | None:
    """Пробегает кандидатов по порядку: первый живой mDNS; иначе первый прямой адрес.
    Для endpoint'ов без записи в .env — авто-обнаружение по точному mDNS-имени."""
    cands = _configured_endpoints().get(name, [])
    direct = None
    for cand in cands:
        if cand.startswith("mdns:"):
            resolved = _mdns_lookup(cand[5:], force=force)
            if resolved:
                return resolved
        else:
            if direct is None:
                direct = cand
    if direct:
        return direct
    if not cands:
        return _mdns_services(force=force).get(name)
    return None


def _ep_addr(name: str | None) -> str | None:
    """Актуальный 'host:port' для endpoint (с TTL-кэшем)."""
    if name is None:
        return None
    now = time.monotonic()
    hit = _EP_ADDR_CACHE.get(name)
    if hit and hit[0] > now:
        return hit[1]
    addr = _resolve_ep(name)
    if addr:
        _EP_ADDR_CACHE[name] = (now + _EP_ADDR_TTL, addr)
    return addr


def _ep_web(name: str) -> str:
    """Web UI OAAT-эндпоинта (control 9740 + 1) — список/смена аудио-устройства."""
    return "http://" + (_ep_addr(name) or "").rsplit(":", 1)[0] + ":9741"


def _ep_headers(name: str | None = None) -> dict:
    """Bearer для управляющих POST на OAAT web UI.

    Приоритет токена: mDNS TXT (авто-сгенерированный endpoint'ом) →
    настройка `ep_token:<name>` в БД → глобальный OAAT_CONTROL_TOKEN из env.
    """
    ep = name or _active_ep_name()
    token = (_mdns_token(ep) if ep else None) or OAAT_CONTROL_TOKEN
    return {"Authorization": f"Bearer {token}"} if token else {}


def _active_ep_name() -> str | None:
    try:
        name = Path(ACTIVE_EP_FILE).read_text().strip()
        # валидно: настроенный (.env/вручную), либо обнаруженный по mDNS, либо недавно резолвленный
        if (name in _configured_endpoints() or name in _mdns_services()
                or name in _EP_ADDR_CACHE):
            return name
    except OSError:
        pass
    cfg = _configured_endpoints()
    if cfg:
        return next(iter(cfg))
    discovered = _mdns_services()
    if discovered:
        return next(iter(discovered))
    return None


def _set_active_ep(name: str) -> None:
    try:
        Path(ACTIVE_EP_FILE).write_text(name)
    except OSError:
        pass


# ts-controller — один процесс, держит соединение с АКТИВНЫМ endpoint'ом.
_TS_PROC = None
_TS_LOGF = None  # append-лог ts-controller (переживает рестарты контроллера)


def _stop_ts() -> None:
    global _TS_PROC
    if _TS_PROC and _TS_PROC.poll() is None:
        _TS_PROC.terminate()
        try:
            _TS_PROC.wait(timeout=3)
        except subprocess.TimeoutExpired:
            _TS_PROC.kill()
    _TS_PROC = None


def _start_ts(target: str) -> None:
    global _TS_PROC, _TS_LOGF
    _stop_ts()
    if not target:
        return
    try:
        # лог контроллера — в файл (был DEVNULL: падения/паники невозможно diagnose)
        with contextlib.suppress(OSError):
            if _TS_LOGF is None or _TS_LOGF.closed:
                # опрос статуса даёт ~2 строки/с — разросшийся лог усекаем на старте
                mode = ("wb" if os.path.exists(TS_CONTROLLER_LOG)
                        and os.path.getsize(TS_CONTROLLER_LOG) > 5_000_000 else "ab")
                _TS_LOGF = open(TS_CONTROLLER_LOG, mode)  # noqa: SIM115 — живет весь процесс
        _TS_PROC = subprocess.Popen(
            [TS_CONTROLLER_BIN, "--target", target, "--socket", TS_CONTROLLER_SOCK],
            stdout=_TS_LOGF if _TS_LOGF and not _TS_LOGF.closed else subprocess.DEVNULL,
            stderr=subprocess.STDOUT,
            env={**os.environ, "RUST_LOG": os.getenv("TS_RUST_LOG", "info")},
        )
    except OSError as e:
        # нет бинаря/права — деградируем: приложение живёт, аудио недоступно.
        _TS_PROC = None
        print(f"[ts-controller] не удалось запустить ({e}); аудио недоступно", flush=True)


def _ep_watchdog() -> None:
    """Фоновый пересмотр адреса активного endpoint'а (mDNS появился/пропал = переезд).

    Переключает ts-controller на новый адрес, только если два цикла подряд видит
    один и тот же НОВЫЙ адрес (дебаунс против «мигания» mDNS на нестабильной сети).
    """
    last = _ep_addr(_active_ep_name())
    pending = None
    while True:
        time.sleep(30)
        with contextlib.suppress(Exception):
            name = _active_ep_name()
            if name is None:
                pending = None
                continue
            addr = _resolve_ep(name, force=True)  # форс-резолв, минуя TTL-кэш
            # контроллер умер (crash/kill) — перезапускаем на текущем адресе,
            # иначе плеер навсегда останется в «stop» до рестарта бэкенда
            if _TS_PROC is not None and _TS_PROC.poll() is not None:
                if addr:
                    _start_ts(addr)
                    last = addr
                pending = None
                continue
            if not addr or addr == last:
                pending = None
                continue
            if addr == pending:
                _EP_ADDR_CACHE[name] = (time.monotonic() + _EP_ADDR_TTL, addr)
                _start_ts(addr)
                last = addr
                pending = None
            else:
                pending = addr
FFMPEG = os.getenv("FFMPEG", "ffmpeg")
FFPROBE = os.getenv("FFPROBE", "ffprobe")


def _wav_codec_for(src: str) -> str:
    """WAV-кодек, сохраняющий глубину источника: 16→s16le, 24→s24le, 32/float→s32le.
    Без явного -c:a WAV-мульчер ffmpeg по умолчанию pcm_s16le — 24-битные
    источники усекались до 16 бит (бит-перфект ломался)."""
    try:
        out = subprocess.run(
            [FFPROBE, "-v", "error", "-select_streams", "a:0",
             "-show_entries", "stream=bits_per_raw_sample,bits_per_sample",
             "-of", "csv=p=0", src],
            check=False, capture_output=True, text=True, timeout=30,
        ).stdout
        for p in out.strip().split(","):
            try:
                b = int(p)
            except ValueError:
                continue
            if b == 24:
                return "pcm_s24le"
            if b >= 32:
                return "pcm_s32le"
            if b == 16:
                return "pcm_s16le"
    except Exception:  # noqa: BLE001, S110 — probe не удался → консервативный s16
        pass
    return "pcm_s16le"
DB_PATH = os.getenv("DB_PATH", "/data/catalog.db")
API_KEY = os.getenv("API_KEY", "")
PROWLARR_URL = os.getenv("PROWLARR_URL", "http://127.0.0.1:9696")
PROWLARR_API_KEY = os.getenv("PROWLARR_API_KEY", "")
FLARESOLVERR_URL = os.getenv("FLARESOLVERR_URL", "http://127.0.0.1:8191")
RUTRACKER_LOGIN = os.getenv("RUTRACKER_LOGIN", "")
RUTRACKER_PASSWORD = os.getenv("RUTRACKER_PASSWORD", "")
COVER_CACHE_DIR = os.getenv("COVER_CACHE_DIR", "/data/covers")
AUDIO_EXT = {".flac", ".mp3", ".dsf", ".dff", ".wav", ".m4a", ".aiff", ".aif", ".ape", ".ogg", ".opus", ".alac"}
MUSICBRAINZ_UA = "torrent-streamer/0.1 (druner@gmail.com)"

# ---------- mDNS-анонс сервера (_soundvault._tcp): автообнаружение клиентами в LAN ----------
_mdns_state: list = []  # [(Zeroconf, ServiceInfo)]


def _lan_ip() -> str:
    import socket
    try:
        s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        try:
            s.connect(("8.8.8.8", 80))
            return s.getsockname()[0]
        finally:
            s.close()
    except OSError:
        return "127.0.0.1"


def _mdns_register() -> None:
    """Анонсирует сервер как _soundvault._tcp, чтобы клиенты в LAN находили его без настроек."""
    if os.getenv("MDNS_REGISTER", "1") != "1" or _mdns_state:
        return
    try:
        import socket

        from zeroconf import ServiceInfo, Zeroconf
        name = os.getenv("MDNS_NAME", "SoundVault")
        port = int(os.getenv("MDNS_PORT", "8000"))
        ip = _lan_ip()
        info = ServiceInfo(
            "_soundvault._tcp.local.",
            f"{name}._soundvault._tcp.local.",
            addresses=[socket.inet_aton(ip)],
            port=port,
            properties={"version": VERSION, "path": "/", "host": socket.gethostname()},
            server=f"{socket.gethostname()}.local.",
        )
        zc = Zeroconf()
        # cooperating_responders: не падать NonUniqueNameException, если в сети
        # ещё жив анонс предыдущего инстанса (контейнер без graceful unregister)
        zc.register_service(info, cooperating_responders=True)
        _mdns_state.append((zc, info))
        print(f"[mdns] сервер {name} анонсируется на {ip}:{port}", flush=True)
    except Exception as e:  # noqa: BLE001 — анонс не должен ронять старт
        print(f"[mdns] анонс сервера не удался: {type(e).__name__}: {e}", flush=True)


def _mdns_unregister() -> None:
    while _mdns_state:
        zc, info = _mdns_state.pop()
        try:
            zc.unregister_service(info)
            zc.close()
        except Exception as e:  # noqa: BLE001
            print(f"[mdns] снятие анонса: {e}", flush=True)


@contextlib.asynccontextmanager
async def lifespan(_app):
    # ts-controller стартует с активным endpoint'ом (persist в /data/active_endpoint);
    # адрес резолвится через mDNS (LAN) с fallback на прямой (Tailscale).
    _start_ts(_ep_addr(_active_ep_name()) or "")
    _watchdog = threading.Thread(target=_ep_watchdog, daemon=True)
    _watchdog.start()
    # очередь/режимы переживают рестарт; монитор автоперехода ловит конец трека
    _restore_player_state()
    _monitor = threading.Thread(target=_player_monitor, daemon=True)
    _monitor.start()
    # обложки каталога: одноразовая миграция URL в тамбнейлы + фоновое обогащение
    try:
        n = _migrate_cover_thumbs()
        if n:
            print(f"[covers] мигрировано в тамбнейлы: {n}", flush=True)
    except Exception as e:  # noqa: BLE001 — миграция не должна ронять старт
        print(f"[covers] миграция тамбнейлов: {e}", flush=True)
    _cover_thread = threading.Thread(target=_cover_worker, daemon=True)
    _cover_thread.start()
    # бэкфилл тегов: возобновить после рестарта, если остались неразмеченные треки
    _backfill_kick()
    # сервер сам виден в LAN: _soundvault._tcp для клиентов/мобильных приложений.
    # zeroconf блокирующий — из asyncio-loop зовём только из отдельного потока.
    threading.Thread(target=_mdns_register, daemon=True).start()
    yield
    _t = threading.Thread(target=_mdns_unregister, daemon=True)
    _t.start()
    _t.join(timeout=5)
    _COVER_STOP.set()
    _stop_ts()


app = FastAPI(title="torrent-streamer", lifespan=lifespan)
# JSON каталога ~200KB — gzip режет его в 5-8 раз (мобильный доступ через relay)
app.add_middleware(GZipMiddleware, minimum_size=1024)

# ---------- OAAT (ts-controller, persistent) ----------
_OAAT_ALBUM_ID = None
_OAAT_TRACK_INDEX = None
_OAAT_TRACK_NAME = None
_OAAT_TRACK_TITLE = None
_OAAT_DURATION = None
_OAAT_ALBUM_NAME = None
_OAAT_ARTIST = None
_OAAT_COVER_URL = None
_OAAT_VOLUME = 100
_OAAT_FORMAT = None
_QUEUE = []          # очередь воспроизведения: [{"aid": int, "idx": int}, ...]
_QUEUE_POS = -1      # позиция текущего трека в очереди; -1 = очереди нет
_REPEAT = "off"      # off | all | one — режим повтора очереди
_SHUFFLE = False     # перемешанный порядок очереди
_DEVICE_LOST_NAME = None  # P1.7: имя выходного устройства, пропажа которого авто-паузит
_DEVICE_BASELINE = None  # P1.7: (endpoint, device) — device, ранее виденный в available_devices
_SLEEP_TIMER = None  # таймер сна (threading.Timer или None)
_SLEEP_MINUTES = None  # установленное значение таймера — отдаётся в GET /api/player

# ---------- защита API ключом (опционально) ----------
@app.middleware("http")
async def api_key_middleware(request, call_next):
    path = request.url.path
    if (
        API_KEY
        and path.startswith("/api/")
        and path != "/api/health"
        and request.headers.get("X-API-Key") != API_KEY
    ):
        return JSONResponse({"detail": "неверный API-ключ"}, status_code=401)
    return await call_next(request)


# ---------- Basic Auth для публичного доступа (Cloudflare Tunnel) ----------
FRONTEND_AUTH = os.getenv("FRONTEND_AUTH", "")  # "user:pass" — пусто = выключено


@app.middleware("http")
async def basic_auth_middleware(request, call_next):
    try:
        auth = _get_setting("frontend_auth") or FRONTEND_AUTH
    except Exception:  # noqa: BLE001 — БД недоступна → только env-креды
        auth = FRONTEND_AUTH
    if auth and request.url.path != "/api/health":
        expected = "Basic " + base64.b64encode(auth.encode()).decode()
        if request.headers.get("Authorization") != expected:
            return JSONResponse({"detail": "авторизация требуется"}, status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="SoundVault"'})
    return await call_next(request)

# ---------- health ----------
@app.get("/api/health")
def health():
    # статус — по ядру (БД); mpd/qbt — опциональные компоненты, не роняют health
    try:
        db().execute("SELECT 1").fetchone()
        status = "ok"
    except Exception:  # noqa: BLE001 — health всегда отдаёт 200
        status = "error"
    try:
        mpd_cmd(lambda mc: mc.status())
        mpd = "ok"
    except Exception:  # noqa: BLE001
        mpd = "unavailable"
    return {"status": status, "version": VERSION, "mpd": mpd}


@app.get("/api/search")
def search(q: str, limit: int = 10):
    if not PROWLARR_API_KEY:
        raise HTTPException(503, "Prowlarr не настроен: задай PROWLARR_API_KEY")
    return {"results": prowlarr_search(q, limit)}


# ---------- БД ----------
_schema_lock = threading.Lock()
_schema_path = None


def _connect():
    if DB_PATH == ":memory:":
        return sqlite3.connect(":memory:", timeout=30)
    return sqlite3.connect(DB_PATH, timeout=30)


def _schema(c):
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("""CREATE TABLE IF NOT EXISTS albums(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hash TEXT UNIQUE, name TEXT, status TEXT, added_at INTEGER,
        artist TEXT, cover_url TEXT)""")
    for col in ("artist", "cover_url", "release_key", "reg_date"):
        with contextlib.suppress(sqlite3.OperationalError):
            c.execute(f"ALTER TABLE albums ADD COLUMN {col} TEXT")
    with contextlib.suppress(sqlite3.OperationalError):
        c.execute("ALTER TABLE albums ADD COLUMN catalog_t INTEGER")
    c.execute("""CREATE TABLE IF NOT EXISTS tracks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        album_id INTEGER, path TEXT, name TEXT, size INTEGER)""")
    for col in ("duration REAL", "title TEXT", "format TEXT", "year TEXT", "genre TEXT",
                "gain_db REAL", "tagged INTEGER DEFAULT 0"):
        with contextlib.suppress(sqlite3.OperationalError):
            c.execute(f"ALTER TABLE tracks ADD COLUMN {col}")
    c.execute("""CREATE TABLE IF NOT EXISTS favorites(
        album_id INTEGER PRIMARY KEY, added_at INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS history(
        album_id INTEGER PRIMARY KEY, track_index INTEGER, position REAL, ts INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS playlists(
        id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT, created_at INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS playlist_items(
        id INTEGER PRIMARY KEY AUTOINCREMENT, playlist_id INTEGER,
        album_id INTEGER, track_index INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS catalog(
        t INTEGER PRIMARY KEY, forum_id INTEGER, title TEXT, size TEXT,
        seeders INTEGER, leechers INTEGER, crawled_at INTEGER)""")
    for col in ("lossless INTEGER", "year TEXT", "artist TEXT", "album TEXT",
                "format TEXT", "bitrate TEXT", "release_key TEXT", "cover_url TEXT",
                "genre TEXT", "source TEXT", "rip TEXT", "quality TEXT", "reg_date TEXT"):
        with contextlib.suppress(sqlite3.OperationalError):
            c.execute(f"ALTER TABLE catalog ADD COLUMN {col}")
    with contextlib.suppress(sqlite3.OperationalError):
        c.execute("ALTER TABLE catalog ADD COLUMN last_post INTEGER")
    # индексы: каталог растёт до 100k+ строк, фильтры/сортировки без них деградируют
    for idx, col in (("idx_cat_forum", "forum_id"), ("idx_cat_rk", "release_key"),
                     ("idx_cat_lp", "last_post"), ("idx_cat_seed", "seeders"),
                     ("idx_cat_artist", "artist")):
        c.execute(f"CREATE INDEX IF NOT EXISTS {idx} ON catalog({col})")
    # вкладки «Треки»: join/пагинация по tracks без индекса деградируют на 100k+
    c.execute("CREATE INDEX IF NOT EXISTS idx_tracks_album ON tracks(album_id)")
    c.execute("""CREATE TABLE IF NOT EXISTS settings(
        key TEXT PRIMARY KEY, value TEXT)""")
    c.execute("""CREATE TABLE IF NOT EXISTS forums(
        forum_id INTEGER PRIMARY KEY, title TEXT, parent_id INTEGER DEFAULT 0,
        category TEXT, enabled INTEGER DEFAULT 0)""")
    c.commit()


def db():
    global _schema_path
    c = _connect()
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA busy_timeout=30000")
    if DB_PATH == ":memory:":
        # каждая in-memory БД отдельна → схема на каждый вызов
        _schema(c)
    elif _schema_path != DB_PATH:
        with _schema_lock:
            if _schema_path != DB_PATH:
                _schema(c)
                _schema_path = DB_PATH
    return c


# ---------- настройки (settings-таблица, fallback на env) ----------
def _get_setting(key: str) -> str:
    with db() as c:
        row = c.execute("SELECT value FROM settings WHERE key=?", (key,)).fetchone()
        return row["value"] if row else ""


def _set_setting(key: str, value: str):
    with db() as c:
        c.execute("INSERT INTO settings(key,value) VALUES(?,?) "
                  "ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))
        c.commit()


def _rutracker_login() -> str:
    return _get_setting("rutracker_login") or RUTRACKER_LOGIN


def _rutracker_password() -> str:
    return _get_setting("rutracker_password") or RUTRACKER_PASSWORD


def _qbt_url() -> str:
    return _get_setting("qbt_url") or QBT_URL


def _qbt_user() -> str:
    return _get_setting("qbt_user") or QBT_USER


def _qbt_pass() -> str:
    return _get_setting("qbt_password") or QBT_PASS


# ---------- MusicBrainz ----------
def _release_key(artist, name):
    """нормализованный ключ релиза: «исполнитель | название» без тегов/года/скобок."""
    key = re.sub(r"[\[\]{}]", " ", name or "")
    key = re.sub(r"\b(19|20)\d{2}\b", " ", key)
    key = re.sub(r"\([^)]*\)", " ", key)
    key = re.sub(r"\s+", " ", key).strip().lower()
    return f"{(artist or '').strip().lower()}|{key}"


# форматный/технический шум в названиях раздач — не участвует в fuzzy-матчинге
_KEY_NOISE = re.compile(
    r"\b(flac|ape|mp3|alac|wav|wavpack|dsd|dsf|dff|ogg|opus|aac|m4a|lossless|lossy|"
    r"cue|log|tracks|image|cd|web|sacd|vinyl|dvd|hires|hi-res|tr24|kbps|of|deluxe|"
    r"edition|remaster(ed)?|reissue|anniversary)\b", re.IGNORECASE)


def _key_tokens(rk: str | None) -> frozenset:
    """множество значимых токенов release_key для fuzzy-сверки каталог ↔ библиотека.

    Ключи albums (из полного имени торрента) и catalog (из парсера) никогда не
    совпадают дословно («the who - 1975 - the who by numbers» vs «the who|the who
    by numbers»), поэтому сверяем множества токенов с очисткой от форматного шума."""
    if not rk:
        return frozenset()
    txt = rk.replace("|", " ")
    txt = re.sub(r"[\[\]{}()]", " ", txt)
    txt = _KEY_NOISE.sub(" ", txt)
    toks = {t for t in re.split(r"[^0-9a-zа-яё]+", txt.lower()) if len(t) > 1}
    return frozenset(toks)


def _keys_match(a: frozenset, b: frozenset) -> bool:
    """fuzzy-совпадение релизов: одно множество токенов содержит другое (мин. 2 токена)."""
    if len(a) < 2 or len(b) < 2:
        return False
    return a <= b or b <= a


# канонические имена кодеков (в названиях регистр разнобойный: WavPack/WAVPack, Ape/APE)
_FMT_NORM = {"FLAC": "FLAC", "WAVPACK": "WavPack", "APE": "APE", "ALAC": "ALAC",
             "MP3": "MP3", "AAC": "AAC", "OGG": "OGG", "OPUS": "Opus", "WAV": "WAV",
             "DSD": "DSD", "DSF": "DSF", "DFF": "DFF", "SACD": "SACD", "ISO": "ISO",
             "DTS": "DTS"}


def _quality(fmt: str | None, bitrate: str | None) -> str | None:
    """Категория качества записи: dsd | hires | cd | lossy."""
    f = (fmt or "").upper()
    b = (bitrate or "").upper()
    if f in ("DSD", "DSF", "DFF", "SACD", "SACD-R", "ISO"):
        return "dsd"
    if f in ("MP3", "AAC", "OGG", "OPUS"):
        return "lossy"
    if f:  # lossless: FLAC/APE/WavPack/ALAC/WAV
        if b.startswith("DSD"):
            return "dsd"
        if b.startswith("24") or any(x in b for x in ("96", "192", "176", "88")):
            return "hires"
        return "cd"
    return None


def _parse_torrent_title(title: str) -> dict:
    """RuTracker «(жанры) [медиа] Исполнитель - Альбом (год) - год, FLAC (tracks+.cue), lossless»
    → {artist, album, year, format, bitrate, lossless, genre, source, rip, quality}."""
    t = (title or "").replace("<wbr>", "")
    genre = media = None
    # префиксные [теги] в самом начале: [TR24] [OF] [CD] [WEB] [SACD-R] [Vinyl] ...
    mpre = re.match(r"^\s*((?:\[[^\]]+\]\s*)+)", t)
    if mpre:
        media = " ".join(re.findall(r"\[([^\]]+)\]", mpre.group(1)))
        t = t[mpre.end():].lstrip()
    # (жанры) в начале, после них может быть ещё [медиа]
    rest = t
    m = re.match(r"^\s*\(([^)]*)\)\s*(\[[^\]]+\])?\s*(.*)$", t, re.DOTALL)
    if m and m.group(1) and re.search(r"[a-zа-яё]", m.group(1), re.IGNORECASE):
        genre = m.group(1).strip()
        if not media and m.group(2):
            media = m.group(2).strip("[] ").strip()
        rest = m.group(3).strip()
    fmt = bitrate = None
    fm = re.search(r",\s*((?:FLAC|WavPack|WAVPack|APE|ALAC|MP3|AAC|OGG|Opus|WAV|DSD|DSF|DFF|SACD-R?|ISO|DTS|Dolby)\b.*)$",
                   rest, re.IGNORECASE)
    if fm:
        spec = fm.group(1)
        fm2 = re.match(r"(FLAC|WavPack|WAVPack|APE|ALAC|MP3|AAC|OGG|Opus|WAV|DSD|DSF|DFF|SACD|ISO|DTS)", spec, re.IGNORECASE)
        fmt = _FMT_NORM.get(fm2.group(1).upper()) if fm2 else None
        bm = re.search(r"(24\s*/?\s*(?:192|96|88\.2|88|176\.4|176)|16\s*/?\s*44\.1|16\s*/?\s*44|DSD\s*(?:64|128|256)|\b(?:320|256|192|128)\s*kbps?)",
                       spec, re.IGNORECASE)
        if bm:
            bitrate = re.sub(r"\s+", "", bm.group(1))
        rest = rest[:fm.start()].rstrip(" ,;")
    # источник/носитель — из [медиа] + маркеры в тексте
    source = None
    med = (media or "").upper()
    if "SACD" in med:
        source = "SACD"
    elif "DVD" in med:
        source = "DVD-A"
    elif "BLU" in med:
        source = "Blu-ray"
    elif "VINYL" in med:
        source = "Vinyl"
    elif "CD" in med:
        source = "CD"
    elif "WEB" in med:
        source = "WEB"
    elif "TR24" in med:
        source = "WEB"  # TR24 = цифровой Hi-Res релиз
    tl = t.lower()
    if not source:
        if "digital release" in tl or "web release" in tl:
            source = "WEB"
        elif "vinyl" in tl:
            source = "Vinyl"
        elif "sacd" in tl:
            source = "SACD"
        elif " cd" in tl or tl.startswith("cd ") or "[cd" in tl:
            source = "CD"
    # тип раздачи: image (цельный образ) vs tracks (по трекам)
    rip = None
    if re.search(r"\(image", t, re.IGNORECASE):
        rip = "image"
    elif re.search(r"\(tracks", t, re.IGNORECASE) or "по-треково" in tl:
        rip = "tracks"
    artist = album = None
    am = re.split(r"\s+[-–—]\s+", rest, maxsplit=1)
    if len(am) == 2:
        artist = am[0].strip()
        album = am[1].strip()
    elif rest.strip():
        album = rest.strip()
        if re.match(r"^VA\b|^Various", rest.strip(), re.IGNORECASE):
            artist = "Various Artists"
    year = None
    if album:
        ym = re.search(r"\b(19|20)\d{2}\b", album)
        if ym:
            year = ym.group(0)
    artist = re.sub(r"^\s*[\[\(\{]\s*(?:19|20)\d{2}\s*[\]\)\}]\s*", "", artist or "").strip()
    a = album or ""
    a = re.sub(r"[\[\(\{]\s*(?:19|20)\d{2}(?:[\s\-–—]*(?:19|20)\d{2})*\s*[\]\)\}]", " ", a)
    a = re.sub(r"\s*,\s*\d+\s*CD.*$", "", a, flags=re.IGNORECASE)
    a = re.sub(r"\s*\(\d+\s*CD[^)]*\)\s*$", "", a, flags=re.IGNORECASE)
    a = re.sub(r"\s*[-–—]\s*(?:19|20)\d{2}\s*$", "", a)
    # форматный хвост (« - APE», « - .ape+.cue/ lossless») — не часть названия:
    # он ломал группировку изданий одного альбома и поиск обложек
    a = re.sub(r"\s*[-–—/]\s*\.?\s*(?:ape|flac|mp3|alac|wavpack|wv|dsd|dsf|ogg|aac|opus)"
               r"(?:\s*\+\s*\.?\s*cue)?/?\s*(?:tracks|image)?/?\s*(?:lossless|lossy)?\s*$",
               "", a, flags=re.IGNORECASE)
    album = re.sub(r"\s+", " ", a).strip(" -–—") or None
    lossless = 0 if fmt and fmt.upper() in ("MP3", "AAC", "OGG", "OPUS") else (1 if fmt else None)
    quality = _quality(fmt, bitrate)
    if "TR24" in med and quality in (None, "cd"):
        quality = "hires"
    if source == "SACD" and quality is None:
        quality = "dsd"
    return {"artist": artist, "album": album, "year": year, "format": fmt,
            "bitrate": bitrate, "lossless": lossless, "genre": genre,
            "source": source, "rip": rip, "quality": quality}


def _thumb_url(url: str | None, size: int = 500) -> str | None:
    """CAA-ссылка на полноразмерный JPEG → тамбнейл (идемпотентно: -500.jpg остаётся -500.jpg).

    Полноразмерные обложки CAA — 300-500 КБ и 2 редиректа на archive.org (~4 с из РФ/РБ);
    тамбнейл 500px — ~50 КБ, в сетке карточек ~150px этого хватает с запасом на retina."""
    if not url:
        return None
    m = re.match(r"^(https?://coverartarchive\.org/release/[^/]+/\d+)(?:-\d+)?\.jpg$", url)
    return f"{m.group(1)}-{size}.jpg" if m else url


def enrich_album(name: str) -> dict | None:
    try:
        r = requests.get(
            "https://musicbrainz.org/ws/2/release/",
            params={"query": name, "fmt": "json", "limit": 1},
            headers={"User-Agent": MUSICBRAINZ_UA},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        releases = data.get("releases", [])
        if not releases:
            return None
        rel = releases[0]
        artist_credit = rel.get("artist-credit", [])
        artist = None
        if artist_credit:
            artist = artist_credit[0].get("name")
        mbid = rel.get("id")
        title = rel.get("title")
        cover_url = None
        if mbid:
            try:
                # /front отдаёт 307-редирект на JPEG (не JSON) — юзаем JSON-эндпоинт /
                cr = requests.get(
                    f"https://coverartarchive.org/release/{mbid}/",
                    headers={"User-Agent": MUSICBRAINZ_UA},
                    timeout=10,
                )
                cr.raise_for_status()
                images = cr.json().get("images", [])
                front = next((i for i in images if i.get("front")),
                             images[0] if images else None)
                if front:
                    cover_url = _thumb_url(front.get("image"))
            except Exception:  # noqa: BLE001 — нет обложки/таймаут → None
                cover_url = None
        return {"artist": artist, "title": title, "cover_url": cover_url}
    except Exception:  # noqa: BLE001 — обогащение не должно ронять добавление
        return None


def _enrich_covers(limit: int = 300) -> int:
    """лениво тянет обложки для релизов каталога (MusicBrainz+CoverArtArchive), кэширует в catalog."""
    with db() as c:
        rows = c.execute(
            "SELECT release_key, artist, album FROM catalog "
            "WHERE artist IS NOT NULL AND album IS NOT NULL AND cover_url IS NULL "
            "GROUP BY release_key LIMIT ?", (limit,)).fetchall()
    n = 0
    for r in rows:
        q = " ".join(x for x in (r["artist"], r["album"]) if x)
        meta = enrich_album(q)
        time.sleep(1.05)  # MusicBrainz rate limit — 1 req/s
        if meta and meta.get("cover_url"):
            with db() as c:
                c.execute("UPDATE catalog SET cover_url=? WHERE release_key=?",
                          (_thumb_url(meta["cover_url"]), r["release_key"]))
                c.commit()
            n += 1
    return n


def _migrate_cover_thumbs() -> int:
    """одноразово: полноразмерные CAA-URL в БД → тамбнейлы -500.jpg."""
    n = 0
    with db() as c:
        for table in ("catalog", "albums"):
            rows = c.execute(
                f"SELECT rowid AS rid, cover_url FROM {table} "
                "WHERE cover_url LIKE '%coverartarchive.org%'").fetchall()
            for r in rows:
                t = _thumb_url(r["cover_url"])
                if t and t != r["cover_url"]:
                    c.execute(f"UPDATE {table} SET cover_url=? WHERE rowid=?",
                              (t, r["rid"]))
                    n += 1
        c.commit()
    return n


# ---------- Prowlarr ----------
def prowlarr_search(query: str, limit: int = 10) -> list[dict]:
    try:
        r = requests.get(
            f"{PROWLARR_URL}/api/v1/search",
            params={"query": query, "type": "search", "limit": limit},
            headers={"X-Api-Key": PROWLARR_API_KEY},
            timeout=10,
        )
        r.raise_for_status()
        data = r.json()
        results = []
        for release in data:
            magnet = release.get("magnetUrl") or release.get("downloadUrl")
            if not magnet and str(release.get("guid", "")).startswith("magnet:"):
                magnet = release.get("guid")
            results.append({
                "title": release.get("title"),
                "guid": release.get("guid"),
                "indexer": release.get("indexer"),
                "size": release.get("size"),
                "seeders": release.get("seeders"),
                "magnet": magnet,
            })
        return results
    except Exception:  # noqa: BLE001 — поиск не должен ронять API
        return []


# ---------- RuTracker каталог (витрина трекера) ----------
RUTRACKER_BASE = "https://rutracker.org/forum"
# cf_clearance привязан к UA FlareSolverr — прямой запрос dl.php обязан слать ТОТ ЖЕ UA
RUTRACKER_UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/152.0.0.0 Safari/537.36")
# жанровые разделы → подфорумы RuTracker (lossless + lossy)
FORUM_CATALOG = {
    "jazz": {"title": "Джаз (зарубежный)", "forums": [
        (2277, "Early Jazz, Swing"), (2278, "Bop"), (2279, "Mainstream, Cool"),
        (2280, "Jazz Fusion"), (2281, "World Fusion, Ethnic"), (2282, "Avant-Garde"),
        (2353, "Modern Creative"), (2284, "Smooth, Jazz-Pop"), (2285, "Vocal Jazz"),
        (2283, "Funk, Soul, R&B"), (2286, "Сборники"), (2287, "lossy")]},
    "blues": {"title": "Блюз (зарубежный)", "forums": [
        (2292, "Blues-rock"), (2290, "Roots, Pre-War"), (2289, "Сборники"), (2288, "lossy")]},
    "classical": {"title": "Классика", "forums": [
        (794, "Опера"), (556, "Вокальная"), (2307, "Хоровая"), (557, "Оркестровая"),
        (2308, "Концерт с оркестром"), (558, "Камерная"), (793, "Сольная инструментальная"),
        (560, "Собрания сочинений"), (969, "Classical Crossover")]},
    "rock": {"title": "Rock (зарубежный)", "forums": [
        (1702, "Classic/Hard"), (1704, "Progressive/Art"), (1706, "Folk-Rock"),
        (2329, "AOR"), (1708, "Pop/Soft"), (1710, "Instrumental Guitar"),
        (1712, "Rockabilly"), (731, "Сборники"), (1714, "Восточноазиатский")]},
    "ru_jazz": {"title": "Джаз и блюз (отечественный)", "forums": [
        (2297, "Джаз lossless"), (2296, "Блюз lossless")]},
}
JAZZ_FORUMS = [f for f, _ in FORUM_CATALOG["jazz"]["forums"]]


class CatalogRefreshReq(BaseModel):
    forums: list[int] = JAZZ_FORUMS
    pages: int = 3


class CatalogDownloadReq(BaseModel):
    t: int


def _rt_session() -> str:
    """имя FlareSolverr-сессии (cf_clearance привязана к ней); ротация = сброс обхода CF."""
    return _get_setting("rt_session") or "soundvault"


def _flaresolverr_get(url: str) -> str:
    """страница через FlareSolverr; один retry с удлинённым таймаутом — решение
    CF-challenge иногда не укладывается в 30с (контейнер ограничен 1 CPU).

    session: cf_clearance переиспользуется между запросами — challenge решается
    один раз на TTL, а не на каждой странице (иначе ~2.5 мин/страницу)."""
    last = None
    for attempt, (mt, to) in enumerate(((30000, 45), (55000, 75))):
        try:
            r = requests.post(f"{FLARESOLVERR_URL}/v1",
                              json={"cmd": "request.get", "url": url, "maxTimeout": mt,
                                    "session": _rt_session(), "session_ttl": 1800},
                              timeout=to)
            r.raise_for_status()
            return r.json().get("solution", {}).get("response", "") or ""
        except requests.RequestException as e:
            last = e
            if attempt == 0:
                time.sleep(3)
    raise last or RuntimeError("FlareSolverr недоступен")


def _guess_lossless(title: str) -> int | None:
    t = (title or "").lower()
    if any(k in t for k in ("lossless", "flac", "dsd", "sacd", "ape", "wavpack", "24/", "24-96")):
        return 1
    if any(k in t for k in ("mp3", "lossy", "ogg", "aac", "m4a", "320")):
        return 0
    return None


def _parse_forum_page(page: str) -> list[dict]:
    rows = []
    for tr in re.split(r"<tr", page)[1:]:
        if "dl-stub" not in tr:
            continue
        m = re.search(r'id="tt-(\d+)"[^>]*>(.*?)</a>', tr)
        if m:
            tid, title_raw = int(m.group(1)), m.group(2)
        else:  # fallback: id темы в data-topic_id, название в ссылке tt-text
            d = re.search(r'data-topic_id="(\d+)"', tr)
            t = re.search(r'class="[^"]*tt-text[^"]*"[^>]*>(.*?)</a>', tr)
            if not (d and t):
                continue
            tid, title_raw = int(d.group(1)), t.group(1)
        s = re.search(r'class="seedmed"[^>]*><b>(\d+)</b>', tr)
        l = re.search(r'class="leechmed"[^>]*><b>(\d+)</b>', tr)
        sz = re.search(r'class="[^"]*dl-stub[^"]*">([^<]+)</a>', tr)
        title = re.sub(r"\s+", " ", html.unescape(title_raw)).strip()
        ym = re.search(r"\b(19|20)\d{2}\b", title)
        lp = re.search(r'vf-col-last-post[^>]*>\s*<p>([^<]+)</p>', tr)
        last_post = 0
        if lp:
            try:
                last_post = int(time.mktime(time.strptime(lp.group(1).strip(),
                                                          "%Y-%m-%d %H:%M")))
            except ValueError:
                last_post = 0
        rows.append({
            "t": tid,
            "title": title,
            "seeders": int(s.group(1)) if s else 0,
            "leechers": int(l.group(1)) if l else 0,
            "size": (sz.group(1) or "").replace("&nbsp;", " ").strip() if sz else "",
            "lossless": _guess_lossless(title),
            "year": ym.group(0) if ym else None,
            "last_post": last_post,
        })
    return rows


_MAX_CRAWL_PAGES = 200  # cap для pages=0 (авто-глубина): 200×50 = 10 000 тем на форум


def crawl_forum(forum_id: int, pages: int = 3) -> list[dict]:
    """краулит подфорум; pages=0 → авто-глубина по пагинации RuTracker (cap _MAX_CRAWL_PAGES)."""
    first_url = f"{RUTRACKER_BASE}/viewforum.php?f={forum_id}"
    first_html = _flaresolverr_get(first_url)
    rows = [dict(r, forum_id=forum_id) for r in _parse_forum_page(first_html)]
    if pages <= 0:
        starts = [int(x) for x in re.findall(r"start=(\d+)", first_html)]
        pages = min((max(starts) // 50 + 1) if starts else 1, _MAX_CRAWL_PAGES)
    for p in range(1, pages):
        url = f"{RUTRACKER_BASE}/viewforum.php?f={forum_id}&start={p * 50}"
        page = _parse_forum_page(_flaresolverr_get(url))
        if not page:  # разовый сбой FlareSolverr не должен обрывать краулинг
            page = _parse_forum_page(_flaresolverr_get(url))
        if not page:
            break  # два пустых ответа подряд = конец списка (или трекер лежит)
        rows += [dict(r, forum_id=forum_id) for r in page]
    return rows


@app.get("/api/catalog/sections")
def catalog_sections():
    return {"sections": [
        {"id": k, "title": v["title"],
         "forums": [{"id": f, "title": t} for f, t in v["forums"]]}
        for k, v in FORUM_CATALOG.items()]}


class SettingsReq(BaseModel):
    rutracker_login: str | None = None
    rutracker_password: str | None = None
    qbt_url: str | None = None
    qbt_user: str | None = None
    qbt_password: str | None = None
    frontend_auth: str | None = None


@app.get("/api/settings")
def settings():
    """логин/пароль трекеров и qBittorrent (пароли не отдаём, только флаги)."""
    return {"rutracker_login": _rutracker_login(),
            "rutracker_password_set": bool(_rutracker_password()),
            "qbt_url": _qbt_url(),
            "qbt_user": _qbt_user(),
            "qbt_password_set": bool(_qbt_pass()),
            "frontend_auth_set": bool(_get_setting("frontend_auth") or FRONTEND_AUTH)}


@app.post("/api/settings")
def settings_save(req: SettingsReq):
    if req.rutracker_login is not None:
        _set_setting("rutracker_login", req.rutracker_login.strip())
    if req.rutracker_password is not None and req.rutracker_password.strip():
        _set_setting("rutracker_password", req.rutracker_password.strip())
    if req.qbt_url is not None and req.qbt_url.strip():
        _set_setting("qbt_url", req.qbt_url.strip())
    if req.qbt_user is not None and req.qbt_user.strip():
        _set_setting("qbt_user", req.qbt_user.strip())
    if req.qbt_password is not None and req.qbt_password.strip():
        _set_setting("qbt_password", req.qbt_password.strip())
    if req.frontend_auth is not None:
        v = req.frontend_auth.strip()
        if v and ":" not in v:
            raise HTTPException(400, "формат кредов админки: user:pass")
        _set_setting("frontend_auth", v)
    return {"ok": True}


@app.post("/api/settings/test")
def settings_test():
    """проверяет логин/пароль RuTracker (через FlareSolverr login.php)."""
    if not (_rutracker_login() and _rutracker_password()):
        return {"ok": False, "error": "не задан логин/пароль"}
    try:
        cookies = _rutracker_cookies()
    except requests.RequestException as e:
        return {"ok": False, "error": f"логин не прошёл: {e}"}
    has_session = any(c.get("name") == "bb_session" for c in cookies)
    return {"ok": has_session,
            "error": "" if has_session else "логин не прошёл (нет сессии bb_session)"}


@app.get("/api/setup/status")
def setup_status():
    """Мастер первого запуска: какие шаги ещё не настроены."""
    missing = []
    if not (_rutracker_login() and _rutracker_password()):
        missing.append("rutracker")
    if not _configured_endpoints() and not _mdns_services():
        missing.append("endpoint")
    try:
        QB().info()
    except Exception:  # noqa: BLE001 — qbt недоступен/не авторизован → шаг не готов
        missing.append("qbittorrent")
    return {"configured": not missing, "missing": missing}


def _split_genres(genre: str | None) -> list[str]:
    """«Hard Bop, Post-Bop / Fusion» → ['Hard Bop', 'Post-Bop', 'Fusion'] (без дублей)."""
    if not genre:
        return []
    seen: set[str] = set()
    out: list[str] = []
    for p in re.split(r"[,/|]", genre):
        p = p.strip()
        k = p.lower()
        if p and k not in seen:
            seen.add(k)
            out.append(p)
    return out


def _groups_where(forum_id, min_seeders, lossless, q, genre, format, source, rip,
                  quality, artist, with_genre: bool = True) -> tuple[str, list]:
    """общий WHERE-конструктор для catalog_groups (SQL-фильтры вместо Python)."""
    sql = " FROM catalog WHERE 1=1"
    params: list = []
    if forum_id:
        sql += f" AND forum_id IN ({','.join('?' * len(forum_id))})"
        params += list(forum_id)
    if min_seeders > 0:
        sql += " AND seeders>=?"
        params.append(min_seeders)
    if lossless is not None:
        sql += " AND lossless=?"
        params.append(1 if lossless else 0)
    if format:
        sql += " AND format=?"
        params.append(format)
    if source:
        sql += " AND source=?"
        params.append(source)
    if rip:
        sql += " AND rip=?"
        params.append(rip)
    if quality:
        sql += " AND quality=?"
        params.append(quality)
    if artist:
        sql += " AND artist=?"
        params.append(artist)
    if q:
        sql += " AND (title LIKE ? OR artist LIKE ? OR album LIKE ?)"
        like = f"%{q}%"
        params += [like, like, like]
    if genre and with_genre:
        sql += " AND genre LIKE ? COLLATE NOCASE"
        params.append(f"%{genre}%")
    return sql, params


# кэш ответов groups: каталог меняется редко, а страница дёргает API на каждый чип
_GROUPS_CACHE: dict = {}
_GROUPS_CACHE_TTL = 60.0
_GROUPS_CACHE_MAX = 32


@app.get("/api/catalog/groups")
def catalog_groups(forum_id: Annotated[list[int] | None, Query()] = None,
                   min_seeders: int = 0, lossless: bool | None = None, q: str | None = None,
                   genre: str | None = None, format: str | None = None,
                   source: str | None = None, rip: str | None = None,
                   quality: str | None = None, sort: str = "seeders",
                   artist: str | None = None, limit: int = 0, offset: int = 0):
    """раздачи, сгруппированные по релизу (release_key) → выбор качества (издания).

    Группировка/сортировка/пагинация — в SQLite (каталог 100k+ строк); в Python
    собираются только издания групп текущей страницы. Ответ кэшируется на 60с
    (сбрасывается при сохранении нового краула).
    """
    cache_key = (tuple(forum_id or ()), min_seeders, lossless, q, genre, format,
                 source, rip, quality, artist, sort, limit, offset)
    hit = _GROUPS_CACHE.get(cache_key)
    if hit and time.time() - hit[0] < _GROUPS_CACHE_TTL:
        return hit[1]

    args = (forum_id, min_seeders, lossless, q, genre, format, source, rip,
            quality, artist)
    where, params = _groups_where(*args)
    # facets считаются БЕЗ genre-фильтра, чтобы чипы жанров не схлопывались
    fwhere, fparams = _groups_where(*args, with_genre=False)
    order = {
        "new": "max_lp DESC",
        "reg": "max_reg DESC",  # NULL (неизвестные даты) в SQLite DESC уходят в конец
        "year": "min_year ASC, max_seed DESC",
        "name": "sort_name COLLATE NOCASE ASC",
    }.get(sort, "max_seed DESC")
    lim = limit if limit > 0 else -1
    with db() as c:
        total = c.execute(
            f"SELECT COUNT(DISTINCT release_key) AS n{where}", params).fetchone()["n"]
        page = c.execute(
            "SELECT release_key, MAX(seeders) AS max_seed,"
            " MAX(COALESCE(last_post,0)) AS max_lp, MAX(reg_date) AS max_reg,"
            " MIN(CASE WHEN year IS NULL OR year='' THEN '9999' ELSE year END) AS min_year,"
            " MIN(COALESCE(NULLIF(artist,''), NULLIF(album,''), '')) AS sort_name"
            f"{where} GROUP BY release_key ORDER BY {order} LIMIT ? OFFSET ?",
            [*params, lim, offset]).fetchall()
        keys = [r["release_key"] for r in page]
        rows = []
        if keys:
            ph = ",".join("?" * len(keys))
            rows = c.execute(
                f"SELECT *{where} AND release_key IN ({ph})", [*params, *keys]).fetchall()
        fmt_rows = c.execute(
            f"SELECT format AS k, COUNT(*) AS n{fwhere}"
            " AND format IS NOT NULL AND format!='' GROUP BY format ORDER BY n DESC",
            fparams).fetchall()
        src_rows = c.execute(
            f"SELECT source AS k, COUNT(*) AS n{fwhere}"
            " AND source IS NOT NULL AND source!='' GROUP BY source ORDER BY n DESC",
            fparams).fetchall()
        genre_rows = c.execute(
            f"SELECT genre AS k, COUNT(*) AS n{fwhere}"
            " AND genre IS NOT NULL AND genre!='' GROUP BY genre", fparams).fetchall()
        lib_rows = c.execute("SELECT release_key, catalog_t FROM albums").fetchall()

    genres_ct: dict[str, int] = {}
    for r in genre_rows:
        for g in _split_genres(r["k"]):
            genres_ct[g] = genres_ct.get(g, 0) + r["n"]

    groups: dict = {}
    for r in rows:
        rk = r["release_key"]
        g = groups.setdefault(rk, {
            "release_key": rk, "artist": r["artist"], "album": r["album"],
            "year": r["year"], "cover_url": r["cover_url"], "genre": r["genre"],
            "last_post": 0, "editions": []})
        if r["cover_url"] and not g["cover_url"]:
            g["cover_url"] = r["cover_url"]
        if r["genre"] and not g["genre"]:
            g["genre"] = r["genre"]
        g["last_post"] = max(g["last_post"], r["last_post"] or 0)
        g["editions"].append({
            "t": r["t"], "title": r["title"], "format": r["format"],
            "bitrate": r["bitrate"], "lossless": r["lossless"],
            "quality": r["quality"], "source": r["source"], "rip": r["rip"],
            "seeders": r["seeders"], "leechers": r["leechers"],
            "size": r["size"], "year": r["year"], "last_post": r["last_post"] or 0,
            "reg_date": r["reg_date"]})
    out = [groups[k] for k in keys if k in groups]  # порядок страницы из SQL

    lib_exact = {r["release_key"] for r in lib_rows if r["release_key"]}
    lib_t = {r["catalog_t"] for r in lib_rows if r["catalog_t"] is not None}
    lib_keys = [k for k in (_key_tokens(r["release_key"]) for r in lib_rows) if len(k) >= 2]
    for g in out:
        g["editions"].sort(key=lambda e: -e["seeders"])
        g["count"] = len(g["editions"])
        # «в библиотеке»: точная связь раздачи (catalog_t) → дословный ключ → fuzzy-токены
        gk = _key_tokens(g["release_key"])
        g["in_library"] = (
            g["release_key"] in lib_exact
            or any(e["t"] in lib_t for e in g["editions"])
            or any(_keys_match(gk, lk) for lk in lib_keys))
    facets = {
        "genres": [{"name": k, "count": v} for k, v in
                   sorted(genres_ct.items(), key=lambda x: -x[1])[:30]],
        "formats": [{"name": r["k"], "count": r["n"]} for r in fmt_rows],
        "sources": [{"name": r["k"], "count": r["n"]} for r in src_rows],
    }
    result = {"groups": out, "count": total, "total": total,
              "offset": offset, "facets": facets}
    if len(_GROUPS_CACHE) >= _GROUPS_CACHE_MAX:
        _GROUPS_CACHE.pop(next(iter(_GROUPS_CACHE)))
    _GROUPS_CACHE[cache_key] = (time.time(), result)
    return result


@app.get("/api/catalog/artists")
def catalog_artists(q: str | None = None, letter: str | None = None,
                    limit: int = 100, offset: int = 0):
    """агрегат исполнителей каталога для режима «По исполнителю»."""
    sql = ("SELECT artist, COUNT(DISTINCT release_key) AS albums, COUNT(*) AS torrents, "
           "MAX(seeders) AS seeders, COUNT(*) OVER() AS total "
           "FROM catalog WHERE artist IS NOT NULL AND artist != ''")
    params: list = []
    if q:
        sql += " AND artist LIKE ?"
        params.append(f"%{q}%")
    if letter:
        sql += " AND lower(substr(artist,1,1))=?"
        params.append(letter.lower())
    sql += " GROUP BY artist ORDER BY artist COLLATE NOCASE LIMIT ? OFFSET ?"
    params += [limit, offset]
    with db() as c:
        rows = [dict(r) for r in c.execute(sql, params).fetchall()]
    total = rows[0]["total"] if rows else 0
    for r in rows:
        r.pop("total", None)
    return {"artists": rows, "total": total, "offset": offset}


# ---------- фоновое обновление каталога ----------
_REFRESH_STATE = {"running": False, "total": 0, "done": 0, "crawled": 0}


def _save_crawled(crawled: list[dict]) -> int:
    if not crawled:
        return 0
    now = int(time.time())
    with db() as c:
        for r in crawled:
            p = _parse_torrent_title(r["title"])
            rk = _release_key(p["artist"], p["album"])
            c.execute(
                """INSERT INTO catalog(t,forum_id,title,size,seeders,leechers,crawled_at,
                                       lossless,year,artist,album,format,bitrate,release_key,
                                       genre,source,rip,quality,last_post)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(t) DO UPDATE SET seeders=excluded.seeders,
                       leechers=excluded.leechers, lossless=excluded.lossless,
                       year=excluded.year,
                       artist=excluded.artist, album=excluded.album,
                       format=excluded.format, bitrate=excluded.bitrate,
                       release_key=excluded.release_key, genre=excluded.genre,
                       source=excluded.source, rip=excluded.rip, quality=excluded.quality,
                       last_post=excluded.last_post""",
                (r["t"], r["forum_id"], r["title"], r["size"], r["seeders"],
                 r["leechers"], now, p["lossless"], p["year"], p["artist"],
                 p["album"], p["format"], p["bitrate"], rk, p["genre"],
                 p["source"], p["rip"], p["quality"], r["last_post"]),
            )
        c.commit()
    _GROUPS_CACHE.clear()  # каталог изменился — кэш групп недействителен
    return len(crawled)


def _run_catalog_refresh(forums: list[int], pages: int):
    _REFRESH_STATE.update(running=True, total=len(forums), done=0, crawled=0)
    try:
        for fid in forums:
            try:
                crawled = crawl_forum(fid, pages)
                _save_crawled(crawled)
                _REFRESH_STATE["crawled"] += len(crawled)
            except Exception as e:  # noqa: BLE001 — сбой форума не рвёт остальные
                print(f"[catalog] f={fid}: {e}", flush=True)
            _REFRESH_STATE["done"] += 1
        # обложки для релизов без cover_url (ограниченно, чтобы не вешать MusicBrainz)
        _enrich_covers(50)
    finally:
        _REFRESH_STATE["running"] = False


@app.post("/api/catalog/refresh")
def catalog_refresh(req: CatalogRefreshReq):
    if _REFRESH_STATE["running"]:
        return {"ok": False, "error": "обновление каталога уже идёт"}
    threading.Thread(target=_run_catalog_refresh,
                     args=(req.forums, req.pages), daemon=True).start()
    return {"ok": True, "started": True, "total": len(req.forums)}


@app.get("/api/catalog/refresh/status")
def catalog_refresh_status():
    return _REFRESH_STATE


@app.get("/api/rutracker/status")
def rutracker_status():
    """P2.12: здоровье обхода RuTracker — FlareSolverr, CF-сессия, краулинг."""
    fs: dict = {"up": False}
    try:
        # health FlareSolverr — GET / (не /v1, там только POST)
        r = requests.get(FLARESOLVERR_URL.rstrip("/") + "/", timeout=5)
        r.raise_for_status()
        d = r.json()
        fs = {"up": True, "version": d.get("version")}
    except (requests.RequestException, ValueError):
        pass
    return {
        "flaresolverr": fs,
        "session": _rt_session(),
        "login_set": bool(_rutracker_login()),
        "password_set": bool(_rutracker_password()),
        "refresh": dict(_REFRESH_STATE),
    }


@app.post("/api/rutracker/reset_session")
def rutracker_reset_session():
    """Ротация FlareSolverr-сессии: следующий запрос пойдёт с чистым профилем
    браузера и решит CF-challenge заново (лечит залипшую cf_clearance)."""
    name = f"soundvault-{int(time.time())}"
    _set_setting("rt_session", name)
    return {"ok": True, "session": name}


@app.post("/api/catalog/purge")
def catalog_purge(days: int = 365):
    """удаляет мёртвые раздачи: 0 сидов и последний пост старше N дней."""
    cutoff = int(time.time()) - days * 86400
    with db() as c:
        cur = c.execute("DELETE FROM catalog WHERE seeders=0 AND last_post>0 AND last_post<?",
                        (cutoff,))
        c.commit()
        return {"ok": True, "removed": cur.rowcount}


@app.post("/api/catalog/covers")
def catalog_covers(limit: int = 300):
    """фоново/по запросу тянет обложки для релизов каталога без cover_url."""
    return {"ok": True, "covers": _enrich_covers(limit)}


_COVER_HOSTS = ("coverartarchive.org", "archive.org")


@app.get("/api/catalog/cover")
def catalog_cover_proxy(url: str):
    """прокси обложек каталога с кэшем на диске.

    Браузер ходит только на backend (LAN/Tailscale — миллисекунды); медленный
    archive.org оплачивается один раз на сервере и кэшируется навсегда.
    Заодно снимает mixed-content для http://-URL из старых записей."""
    p = urllib.parse.urlparse(url)
    host = (p.hostname or "").lower()
    if p.scheme not in ("http", "https") or not any(
            host == h or host.endswith("." + h) for h in _COVER_HOSTS):
        raise HTTPException(400, "недопустимый источник обложки")
    cache_dir = Path(COVER_CACHE_DIR)
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache_file = cache_dir / f"cat_{hashlib.sha1(url.encode()).hexdigest()[:24]}.jpg"
    if cache_file.exists():
        return FileResponse(cache_file, media_type="image/jpeg",
                            headers={"Cache-Control": "public, max-age=604800"})
    try:
        r = requests.get(url, headers={"User-Agent": MUSICBRAINZ_UA},
                         timeout=20, allow_redirects=True)
        r.raise_for_status()
        data = r.content
    except requests.RequestException:
        raise HTTPException(502, "источник обложки недоступен") from None
    if not data or data[:64].lstrip().startswith(b"<"):
        raise HTTPException(404, "обложка не найдена")
    tmp = cache_file.with_suffix(".part")
    tmp.write_bytes(data)
    os.replace(tmp, cache_file)  # атомарно: частичный файл не кэшируется
    return FileResponse(cache_file, media_type="image/jpeg",
                        headers={"Cache-Control": "public, max-age=604800"})


_COVER_STOP = threading.Event()


def _cover_worker():
    """фоновое обогащение обложек каталога: пачка за пачкой, MB rate limit 1 req/s."""
    while not _COVER_STOP.wait(120):  # первый заход через 2 мин после старта
        try:
            with db() as c:
                left = c.execute(
                    "SELECT COUNT(*) AS n FROM (SELECT DISTINCT release_key FROM catalog "
                    "WHERE artist IS NOT NULL AND album IS NOT NULL "
                    "AND cover_url IS NULL)").fetchone()["n"]
            if not left:
                continue
            got = _enrich_covers(25)
            print(f"[covers] +{got} обложек, осталось ~{left - got}", flush=True)
        except Exception as e:  # noqa: BLE001 — воркер не должен умирать
            print(f"[covers] ошибка воркера: {e}", flush=True)


def _rutracker_cookies() -> list[dict]:
    """логин RuTracker через FlareSolverr → cookies (bb_session и др.).

    Та же сессия, что у _flaresolverr_get: cf_clearance переиспользуется и
    challenge при логине обычно не решается вовсе. Лестница таймаутов как у
    GET: 30с часто не хватает (решение challenge занимает 33-37с)."""
    data = ("login_username=" + urllib.parse.quote(_rutracker_login())
            + "&login_password=" + urllib.parse.quote(_rutracker_password())
            + "&login=%E2%F5%EE%E4")
    last: Exception | None = None
    for mt, to in ((30000, 45), (55000, 75)):
        try:
            r = requests.post(f"{FLARESOLVERR_URL}/v1",
                              json={"cmd": "request.post", "url": f"{RUTRACKER_BASE}/login.php",
                                    "postData": data, "maxTimeout": mt,
                                    "session": _rt_session(), "session_ttl": 1800},
                              timeout=to)
            r.raise_for_status()
            return r.json().get("solution", {}).get("cookies", [])
        except Exception as e:  # noqa: BLE001 — повторить с большим таймаутом
            last = e
    raise RuntimeError(f"RuTracker-логин через FlareSolverr не удался: {last}") from last


def _torrent_creation_date(data: bytes) -> str | None:
    """дата создания .torrent (bencode «creation date») → YYYY-MM-DD (UTC)."""
    m = re.search(rb"13:creation datei(\d{9,11})e", data)
    if not m:
        return None
    with contextlib.suppress(Exception):
        return time.strftime("%Y-%m-%d", time.gmtime(int(m.group(1))))
    return None


@app.post("/api/catalog/download")
def catalog_download(req: CatalogDownloadReq):
    if not (_rutracker_login() and _rutracker_password()):
        raise HTTPException(400, "не задан логин RuTracker (настройки или RUTRACKER_LOGIN/RUTRACKER_PASSWORD)")
    jar = {c["name"]: c["value"] for c in _rutracker_cookies() if c.get("name")}
    r = requests.get(f"{RUTRACKER_BASE}/dl.php?t={req.t}", cookies=jar, timeout=30,
                     headers={"Referer": f"{RUTRACKER_BASE}/viewtopic.php?t={req.t}",
                              "User-Agent": RUTRACKER_UA})
    r.raise_for_status()
    if r.content[:200].lstrip().startswith(b"<"):
        raise HTTPException(502, "не удалось скачать .torrent (нужна авторизация)")
    # дата регистрации раздачи живёт в самом .torrent (bencode «creation date») —
    # на странице листинга её нет, а тут она достаётся бесплатно
    reg = _torrent_creation_date(r.content)
    if reg:
        with contextlib.suppress(Exception), db() as c:
            c.execute("UPDATE catalog SET reg_date=? WHERE t=?", (reg, req.t))
            c.commit()
    path = f"/tmp/rt-{req.t}.torrent"
    with open(path, "wb") as f:
        f.write(r.content)
    qb = QB()
    before = {t.get("hash") for t in qb.info() if t.get("hash")}
    qb.add_file(path)
    imported = _import_torrent(qb, before, catalog_t=req.t)
    if reg and imported.get("album_id") is not None:
        with contextlib.suppress(Exception), db() as c:
            c.execute("UPDATE albums SET reg_date=? WHERE id=?", (reg, imported["album_id"]))
            c.commit()
    return {"ok": True, "t": req.t, "bytes": len(r.content), "reg_date": reg, **imported}


# ---------- qbittorrent ----------
class QB:
    def __init__(self):
        self.s = requests.Session()
        self._login()

    def _login(self):
        try:
            self.s.post(f"{_qbt_url()}/api/v2/auth/login",
                        data={"username": _qbt_user(), "password": _qbt_pass()},
                        timeout=5)
        except requests.RequestException as e:
            raise HTTPException(
                503,
                f"qBittorrent недоступен ({_qbt_url()}) — проверьте, что он запущен, "
                "или укажите подключение в Настройках",
            ) from e

    def add(self, link: str):
        r = self.s.post(f"{_qbt_url()}/api/v2/torrents/add",
                        data={"urls": link, "sequentialDownload": "true",
                              "firstLastPiecePrio": "true"})
        r.raise_for_status()

    def add_file(self, path: str):
        with open(path, "rb") as f:
            r = self.s.post(f"{_qbt_url()}/api/v2/torrents/add",
                            data={"sequentialDownload": "true",
                                  "firstLastPiecePrio": "true"},
                            files={"torrents": (os.path.basename(path), f,
                                                "application/x-bittorrent")})
        r.raise_for_status()

    def delete(self, hash: str):
        r = self.s.post(f"{_qbt_url()}/api/v2/torrents/delete",
                        data={"hashes": hash, "deleteFiles": "false"})
        r.raise_for_status()

    def info(self):
        r = self.s.get(f"{_qbt_url()}/api/v2/torrents/info")
        r.raise_for_status()
        return r.json()

    def files(self, hash_: str):
        r = self.s.get(f"{_qbt_url()}/api/v2/torrents/files", params={"hash": hash_})
        r.raise_for_status()
        return r.json()

    def find_by_hash(self, hash_: str):
        for t in self.info():
            if t["hash"] == hash_:
                return t
        return None

    def set_priority(self, hash_: str, audio_paths, enable=True):
        # приоритизировать аудио-файлы, остальные — не качать
        files = self.files(hash_)
        prios = {}
        for f in files:
            if f["name"] in audio_paths:
                prios[f["index"]] = 6 if enable else 0  # 6 = high priority
            else:
                prios[f["index"]] = 0  # don't download non-audio
        self.s.post(f"{_qbt_url()}/api/v2/torrents/filePrio",
                    data={"hash": hash_, "priority": "|".join(str(prios.get(f["index"], 0)) for f in files)})

    def prioritize_window(self, hash_: str, window: set, audio_names: set):
        # окно (текущий+следующий трек) — 6, остальные аудио — 1 (докачаются позже),
        # не-аудио (cue/jpg/log) — 0. Приоритет высокий = качается первым при sequential.
        files = self.files(hash_)
        prios = {}
        for f in files:
            if f["name"] in window:
                prios[f["index"]] = 6
            elif f["name"] in audio_names:
                prios[f["index"]] = 1
            else:
                prios[f["index"]] = 0
        self.s.post(f"{_qbt_url()}/api/v2/torrents/filePrio",
                    data={"hash": hash_, "priority": "|".join(str(prios.get(f["index"], 0)) for f in files)})

# ---------- MPD ----------
def mpd_cmd(fn):
    c = MPDClient()
    c.timeout = 10
    c.connect(MPD_HOST, MPD_PORT)
    try:
        return fn(c)
    finally:
        with contextlib.suppress(Exception):
            c.close()
        with contextlib.suppress(Exception):
            c.disconnect()

# ---------- API ----------
class AddReq(BaseModel):
    link: str

def _register_album(h: str, name: str, files: list, catalog_t: int | None = None) -> tuple[int, list]:
    """создаёт запись альбома + треки в библиотеке; возвращает (album_id, audio_files)."""
    audio = [f for f in files if Path(f["name"]).suffix.lower() in AUDIO_EXT]
    if not audio:
        raise HTTPException(400, "в раздаче нет аудиофайлов")
    c = db()
    try:
        try:
            meta = enrich_album(name)
        except Exception:  # noqa: BLE001 — обогащение необязательно
            meta = None
        artist = meta.get("artist") if meta else None
        cover_url = meta.get("cover_url") if meta else None
        cur = c.execute(
            "INSERT INTO albums(hash,name,status,added_at,artist,cover_url,release_key,catalog_t) "
            "VALUES(?,?,?,?,?,?,?,?)",
            (h, name, "downloading", int(time.time()), artist, cover_url,
             _release_key(artist, name), catalog_t),
        )
        aid = cur.lastrowid
        for f in audio:
            c.execute("INSERT INTO tracks(album_id,path,name,size) VALUES(?,?,?,?)",
                      (aid, f["name"], Path(f["name"]).name, f["size"]))
        c.commit()
    except sqlite3.IntegrityError:
        aid = c.execute("SELECT id FROM albums WHERE hash=?", (h,)).fetchone()["id"]
    return aid, audio


def _import_torrent(qb: "QB", before: set, catalog_t: int | None = None) -> dict:
    """ждёт метаданные нового торрента (hash не из `before`) и заводит альбом в библиотеке."""
    info = None
    for _ in range(60):
        time.sleep(1)
        for t in qb.info():
            if t.get("name") and t.get("hash") and t["hash"] not in before:
                info = t
                break
        if info:
            break
    if not info:
        raise HTTPException(500, "не удалось получить метаданные торрента")
    h = info["hash"]
    aid, audio = _register_album(h, info["name"], qb.files(h), catalog_t=catalog_t)
    qb.set_priority(h, {f["name"] for f in audio})
    return {"album_id": aid, "hash": h, "name": info["name"], "tracks": len(audio)}


@app.post("/api/add")
def add(req: AddReq):
    qb = QB()
    before = {t.get("hash") for t in qb.info() if t.get("hash")}
    qb.add(req.link)
    return JSONResponse(_import_torrent(qb, before))


@app.post("/api/catalog/reimport")
def catalog_reimport():
    """заводит в библиотеку все торренты qbittorrent, которых ещё нет в albums."""
    qb = QB()
    with db() as c:
        existing = {r["hash"] for r in c.execute("SELECT hash FROM albums").fetchall()}
    n = 0
    for t in qb.info():
        if not (t.get("hash") and t.get("name")) or t["hash"] in existing:
            continue
        try:
            _aid, audio = _register_album(t["hash"], t["name"], qb.files(t["hash"]))
            qb.set_priority(t["hash"], {f["name"] for f in audio})
            n += 1
        except HTTPException:
            continue  # нет аудиофайлов — пропускаем
    return {"imported": n}


# ---------- дерево трекера (полный список форумов RuTracker) ----------
DEFAULT_ENABLED = {f for v in FORUM_CATALOG.values() for f, _ in v["forums"]}
# категории, где группы имеют жанровые подфорумы (краулим вторым уровнем)
MUSIC_CATEGORIES = {"Музыка", "Джазовая и Блюзовая музыка", "Рок-музыка",
                    "Электронная музыка", "Популярная музыка",
                    "Hi-Res форматы, оцифровки"}


def _parse_group_subforums(page: str) -> list[tuple[int, str]]:
    """подфорумы группы со страницы viewforum.php?f=N (h4.forumlink → viewforum)."""
    out = []
    for fid, t in re.findall(r'<h4 class="forumlink"><a href="viewforum\.php\?f=(\d+)">([^<]+)</a>', page):
        fid = int(fid)
        t = html.unescape(re.sub(r"\s+", " ", t)).strip()
        if t and (fid, t) not in out:
            out.append((fid, t))
    return out


def _parse_index_tree(page: str) -> list[dict]:
    """дерево index.php RuTracker: категория → форум-группа → подфорумы."""
    tree = []
    for cat_block in re.findall(r'<div id="c-\d+" class="category">(.*?)</div><!--/category-->',
                                page, re.DOTALL):
        h3 = re.search(r"<h3[^>]*>(.*?)</h3>", cat_block, re.DOTALL)
        cat_name = (html.unescape(re.sub(r"\s+", " ",
                    re.sub(r"<[^>]+>", "", h3.group(1)))).strip() if h3 else "?")
        forums = []
        for fid_s, body in re.findall(r'<tr id="f-(\d+)">(.*?)</tr>', cat_block, re.DOTALL):
            h4 = re.search(r"<h4[^>]*>\s*<a[^>]*>([^<]*)</a>", body)
            ftitle = html.unescape(re.sub(r"\s+", " ", h4.group(1))).strip() if h4 else f"f={fid_s}"
            sf = re.search(r'<p class="subforums">(.*?)</p>', body, re.DOTALL)
            subs = re.findall(r'viewforum\.php\?f=(\d+)"[^>]*>([^<]+)<', sf.group(1)) if sf else []
            forums.append({
                "id": int(fid_s), "title": ftitle,
                "subforums": [{"id": int(s), "title": html.unescape(re.sub(r"\s+", " ", t)).strip()}
                              for s, t in subs]})
        tree.append({"category": cat_name, "forums": forums})
    return tree


class TreeSaveReq(BaseModel):
    enabled: list[int] = []


@app.post("/api/catalog/tree/refresh")
def catalog_tree_refresh():
    """краулит index.php + страницы музыкальных групп → полное дерево форумов."""
    html = _flaresolverr_get(f"{RUTRACKER_BASE}/index.php")
    tree = _parse_index_tree(html)
    if not tree:
        raise HTTPException(502, "не удалось разобрать дерево форумов")
    with db() as c:
        empty = c.execute("SELECT COUNT(*) AS n FROM forums").fetchone()["n"] == 0
        for cat in tree:
            for f in cat["forums"]:
                c.execute("""INSERT INTO forums(forum_id,title,parent_id,category,enabled)
                             VALUES(?,?,?,?,?)
                             ON CONFLICT(forum_id) DO UPDATE SET title=excluded.title,
                                parent_id=excluded.parent_id, category=excluded.category""",
                          (f["id"], f["title"], 0, cat["category"],
                           1 if (empty and f["id"] in DEFAULT_ENABLED) else 0))
        c.commit()
    # второй уровень: жанровые подфорумы музыкальных групп (viewforum.php?f=группа)
    n_groups = 0
    for cat in tree:
        if cat["category"] not in MUSIC_CATEGORIES:
            continue
        for f in cat["forums"]:
            n_groups += 1
            try:
                sub_page = _flaresolverr_get(f"{RUTRACKER_BASE}/viewforum.php?f={f['id']}")
            except requests.RequestException:
                continue
            subs = _parse_group_subforums(sub_page)
            with db() as c:
                for sid, stitle in subs:
                    c.execute("""INSERT INTO forums(forum_id,title,parent_id,category,enabled)
                                 VALUES(?,?,?,?,?)
                                 ON CONFLICT(forum_id) DO UPDATE SET title=excluded.title,
                                    parent_id=excluded.parent_id, category=excluded.category""",
                              (sid, stitle, f["id"], cat["category"],
                               1 if (empty and sid in DEFAULT_ENABLED) else 0))
                c.commit()
    return {"ok": True, "categories": len(tree), "groups": n_groups}


@app.get("/api/catalog/tree")
def catalog_tree():
    with db() as c:
        rows = c.execute("SELECT * FROM forums ORDER BY rowid").fetchall()
    cats: dict[str, list] = {}
    groups: dict[int, dict] = {}
    for r in rows:
        if r["parent_id"] == 0:
            g = {"id": r["forum_id"], "title": r["title"],
                 "enabled": bool(r["enabled"]), "subforums": []}
            groups[r["forum_id"]] = g
            cats.setdefault(r["category"], []).append(g)
        elif r["parent_id"] in groups:
            groups[r["parent_id"]]["subforums"].append(
                {"id": r["forum_id"], "title": r["title"], "enabled": bool(r["enabled"])})
    return {"categories": [{"category": k, "forums": v} for k, v in cats.items()]}


@app.post("/api/catalog/tree")
def catalog_tree_save(req: TreeSaveReq):
    ids = set(req.enabled)
    with db() as c:
        c.execute("UPDATE forums SET enabled=0")
        for fid in ids:
            c.execute("UPDATE forums SET enabled=1 WHERE forum_id=?", (fid,))
        c.commit()
        crawled = {r["forum_id"] for r in c.execute("SELECT DISTINCT forum_id FROM catalog").fetchall()}
    # авто-краулинг новых включённых разделов (которых ещё нет в каталоге)
    new_forums = [fid for fid in ids if fid not in crawled]
    queued = 0
    if new_forums and not _REFRESH_STATE["running"]:
        threading.Thread(target=_run_catalog_refresh,
                         args=(new_forums, 1), daemon=True).start()
        queued = len(new_forums)
    return {"ok": True, "enabled": len(ids), "queued": queued}


def _torrent_state(state: str) -> str:
    """qbittorrent state → читаемый статус альбома."""
    if state in ("uploading", "forcedUP", "stalledUP", "queuedUP",
                 "checkingUP", "pausedUP"):
        return "complete"          # всё скачано (раздаёт/готово)
    if state == "stalledDL":
        return "stalled"           # качается, но нет пиров
    if state == "error":
        return "error"
    if state in ("pausedDL", "queuedDL"):
        return "paused"
    return "downloading"           # downloading/forcedDL/metaDL/allocating/checkingDL


def _file_state(progress: float, torrent_state: str) -> str:
    """per-file статус: 7 состояний из ресёрча."""
    if progress >= 1.0:
        return "ready"
    if torrent_state == "error":
        return "error"
    if torrent_state == "stalledDL":
        return "stalled"           # нет пиров
    if progress > 0:
        return "downloading"
    return "queued"


def _source_label(hash_) -> str | None:
    """Метка источника локального альбома: basename медиапапки, в которой он лежит."""
    d = _local_dir(hash_)
    best = None
    for root in _media_dirs():
        rr = root.rstrip("/")
        if (d == rr or d.startswith(rr + "/")) and (best is None or len(rr) > len(best)):
            best = rr
    return os.path.basename(best) if best else None


@app.get("/api/albums")
def albums():
    c = db()
    rows = c.execute("""SELECT a.*, COUNT(t.id) n FROM albums a
                        LEFT JOIN tracks t ON t.album_id=a.id GROUP BY a.id ORDER BY a.id DESC""").fetchall()
    out = []
    try:
        qb = QB()
        # ОДИН запрос всего списка торрентов вместо find_by_hash на каждый альбом
        by_hash = {t["hash"]: t for t in qb.info()}
    except (HTTPException, requests.RequestException):
        by_hash = None  # qbt недоступен — библиотека отдаётся без live-прогресса
    favs = {row["album_id"] for row in c.execute("SELECT album_id FROM favorites")}
    # год/жанр/формат первого размеченного трека — одним проходом вместо N запросов
    meta: dict = {}
    for m in c.execute("""SELECT album_id, year, genre, format FROM tracks
                          WHERE year IS NOT NULL OR genre IS NOT NULL
                             OR format IS NOT NULL
                          ORDER BY album_id, id"""):
        if m["album_id"] not in meta:
            meta[m["album_id"]] = (m["year"], m["genre"], m["format"])

    for r in rows:
        local = _is_local(r["hash"])
        info = None if local or by_hash is None else by_hash.get(r["hash"])
        if local:
            available = _dir_available(_local_dir(r["hash"]))
            status = "complete" if available else "missing"
            progress = 100.0 if available else 0.0
        else:
            status = _torrent_state(info["state"]) if info else (r["status"] or "downloading")
            progress = round((info["progress"] if info else 0) * 100, 1)
        year, genre, fmt = meta.get(r["id"], (None, None, None))
        release_key = r["release_key"] or _release_key(r["artist"], r["name"])
        out.append({"id": r["id"], "hash": r["hash"], "name": r["name"],
                    "status": status, "progress": progress,
                    "tracks": r["n"], "artist": r["artist"], "cover_url": r["cover_url"],
                    "year": year, "genre": genre, "format": fmt,
                    "release_key": release_key, "favorite": r["id"] in favs,
                    "origin": "folder" if local else "torrent",
                    "source_label": _source_label(r["hash"]) if local else None})
    return out

# ---------- библиотека: все треки (вкладка «Треки») ----------
@app.get("/api/library/tracks")
def library_tracks(q: str = "", genre: str = "", year: str = "", format: str = "",
                   source: str = "", fav: int = 0, sort: str = "artist",
                   offset: int = 0, limit: int = 100):
    """Плоский список треков библиотеки: фильтры/пагинация в SQL (100k+ строк).

    track_index считается по ORDER BY path внутри альбома — тот же порядок,
    что в _current_tracks/album_files, поэтому пригоден для play_track.
    """
    c = db()
    limit = max(1, min(limit, 500))
    offset = max(0, offset)
    where: list[str] = []
    args: list = []
    if q:
        like = f"%{q}%"
        where.append("""(COALESCE(NULLIF(t.title,''), t.name) LIKE ? COLLATE NOCASE
                         OR a.name LIKE ? COLLATE NOCASE OR a.artist LIKE ? COLLATE NOCASE)""")
        args += [like, like, like]
    if genre:
        where.append("t.genre = ?")
        args.append(genre)
    if year:
        where.append("substr(t.year,1,4) = ?")
        args.append(year[:4])
    if format:
        where.append("t.format = ?")
        args.append(format)
    if source == "folder":
        where.append("a.hash LIKE 'local:%'")
    elif source == "torrent":
        where.append("a.hash NOT LIKE 'local:%'")
    join_fav = "JOIN favorites f ON f.album_id = t.album_id" if fav else ""
    w = ("WHERE " + " AND ".join(where)) if where else ""
    order = {
        "artist": "a.artist COLLATE NOCASE, a.name COLLATE NOCASE, t.path",
        "album": "a.name COLLATE NOCASE, t.path",
        "title": "title COLLATE NOCASE",
        "recent": "a.added_at DESC, t.path",
    }.get(sort, "a.artist COLLATE NOCASE, a.name COLLATE NOCASE, t.path")
    frm = f"FROM tracks t JOIN albums a ON a.id = t.album_id {join_fav} {w}"
    total = c.execute(f"SELECT COUNT(*) n {frm}", args).fetchone()["n"]
    rows = c.execute(
        f"""SELECT t.album_id, t.duration, t.path,
                   COALESCE(NULLIF(t.title,''), t.name) AS title,
                   t.format AS fmt, t.year AS t_year, t.genre AS t_genre,
                   a.name AS album, a.artist AS artist,
                   (SELECT COUNT(*) FROM tracks t2
                    WHERE t2.album_id = t.album_id AND t2.path < t.path) AS idx
            {frm} ORDER BY {order} LIMIT ? OFFSET ?""",
        [*args, limit, offset]).fetchall()

    def _facets(col: str, top: int) -> list[dict]:
        return [{"v": r["v"], "n": r["n"]} for r in c.execute(
            f"""SELECT {col} v, COUNT(*) n FROM tracks
                WHERE {col} IS NOT NULL AND {col} != ''
                GROUP BY {col} ORDER BY n DESC LIMIT ?""", (top,))]

    return {"total": total, "offset": offset, "limit": limit,
            "tracks": [{"album_id": r["album_id"], "track_index": r["idx"],
                        "title": r["title"], "album": r["album"], "artist": r["artist"],
                        "duration": r["duration"], "format": r["fmt"],
                        "year": r["t_year"], "genre": r["t_genre"]} for r in rows],
            "facets": {"genres": _facets("genre", 30), "formats": _facets("format", 12),
                       "years": [{"v": r["v"], "n": r["n"]} for r in c.execute(
                           """SELECT substr(year,1,4) v, COUNT(*) n FROM tracks
                              WHERE year IS NOT NULL AND year != ''
                              GROUP BY v ORDER BY v DESC LIMIT 40""")]}}


@app.post("/api/albums/{aid}/favorite")
def toggle_favorite(aid: int):
    c = db()
    row = c.execute("SELECT album_id FROM favorites WHERE album_id=?", (aid,)).fetchone()
    if row:
        c.execute("DELETE FROM favorites WHERE album_id=?", (aid,))
        c.commit()
        return {"ok": True, "favorite": False}
    c.execute("INSERT INTO favorites(album_id, added_at) VALUES(?, ?)", (aid, int(time.time())))
    c.commit()
    return {"ok": True, "favorite": True}

@app.get("/api/albums/{aid}")
def album(aid: int):
    c = db()
    a = c.execute("SELECT * FROM albums WHERE id=?", (aid,)).fetchone()
    if not a:
        raise HTTPException(404, "альбом не найден")
    tracks = c.execute("SELECT * FROM tracks WHERE album_id=? ORDER BY path", (aid,)).fetchall()
    return {"id": a["id"], "hash": a["hash"], "name": a["name"], "status": a["status"],
            "artist": a["artist"], "cover_url": a["cover_url"],
            "tracks": [dict(t) for t in tracks]}

def _clean_track_name(name):
    """«01. Look Up.flac» → «Look Up»; «01 - Look Up.flac» → «Look Up»."""
    stem = Path(name).stem
    cleaned = re.sub(r"^\s*\d+\s*[.\-_)]\s*", "", stem)
    return cleaned.strip() or stem


@app.get("/api/albums/{aid}/files")
def album_files(aid: int):
    c = db()
    a = c.execute("SELECT * FROM albums WHERE id=?", (aid,)).fetchone()
    if not a:
        raise HTTPException(404, "альбом не найден")
    tracks = c.execute("SELECT * FROM tracks WHERE album_id=? ORDER BY path", (aid,)).fetchall()
    save_path = None
    torrent_state = None
    local = _is_local(a["hash"])
    if local:
        save_path = _local_dir(a["hash"])
        files = {}
    else:
        try:
            qb = QB()
            files = {f["name"]: f for f in qb.files(a["hash"])}
            info = qb.find_by_hash(a["hash"])
            save_path = info.get("save_path") if info else None
            torrent_state = info.get("state") if info else None
        except Exception:  # noqa: BLE001 — торрент не найден / qbt недоступен
            files = {}
    out = []
    for idx, t in enumerate(tracks):
        f = files.get(t["path"])
        if local:
            abs_local = os.path.join(save_path or "", t["path"])
            progress = 1.0 if os.path.exists(abs_local) else 0.0
        else:
            progress = f["progress"] if f else 0.0
        ready = bool(progress >= 1.0)
        if ready and save_path:
            abs_path = os.path.join(save_path, t["path"])
            # пред-декодируем следующий за текущим играющим треком (мгновенный next)
            if _OAAT_ALBUM_ID == aid and idx == (_OAAT_TRACK_INDEX or 0) + 1:
                _predecode(aid, idx, abs_path)
        title = t["title"]
        duration = t["duration"]
        fmt = t["format"]
        year = t["year"]
        genre = t["genre"]
        # лениво читаем метаданные из скачанного файла и кэшируем
        if ready and save_path and (title is None or duration is None or fmt is None
                                    or year is None or genre is None or not a["artist"]):
            abs_path = os.path.join(save_path, t["path"])
            dur, tl, art, _, fmeta = _read_meta(abs_path)
            if dur is not None:
                duration = dur
            if tl:
                title = tl
            if art and not a["artist"]:
                c.execute("UPDATE albums SET artist=? WHERE id=?", (art, aid))
                a = c.execute("SELECT * FROM albums WHERE id=?", (aid,)).fetchone()
            if fmeta:
                fmt = fmeta
            yr, gn = _read_year_genre(abs_path)
            if yr is not None:
                year = yr
            if gn is not None:
                genre = gn
            c.execute("UPDATE tracks SET duration=?, title=?, format=?, year=?, genre=? WHERE id=?",
                      (duration, title, fmt, year, genre, t["id"]))
        out.append({
            "name": t["name"],
            "title": title or _clean_track_name(t["name"]),
            "path": t["path"],
            "size": t["size"],
            "ready": ready,
            "state": _file_state(progress, torrent_state),
            "progress": float(progress),
            "duration": duration,
            "format": fmt,
            "year": year,
            "genre": genre,
        })
    c.commit()
    return {"id": aid, "name": a["name"], "artist": a["artist"], "files": out}


@app.get("/api/albums/{aid}/cover")
def album_cover(aid: int):
    c = db()
    a = c.execute("SELECT * FROM albums WHERE id=?", (aid,)).fetchone()
    if not a:
        raise HTTPException(404, "альбом не найден")
    cache_dir = Path(COVER_CACHE_DIR)
    cache_dir.mkdir(exist_ok=True)
    cache_file = cache_dir / f"{aid}.jpg"
    if cache_file.exists():
        return Response(content=cache_file.read_bytes(), media_type="image/jpeg")
    data = mime = None
    try:
        if _is_local(a["hash"]):
            save_path = _local_dir(a["hash"])
        else:
            qb = QB()
            info = qb.find_by_hash(a["hash"])
            save_path = info.get("save_path") if info else None
        tracks = c.execute("SELECT * FROM tracks WHERE album_id=? ORDER BY path", (aid,)).fetchall()
        # 1) embedded обложка из первого читаемого трека
        for t in tracks:
            if save_path:
                abs_path = os.path.join(save_path, t["path"])
                if os.path.exists(abs_path):
                    got = _read_cover(abs_path)
                    if got:
                        data, mime = got
                        break
        # 2) cover.jpg/folder.jpg рядом с треками
        if not data and save_path and tracks:
            dirs = {os.path.dirname(os.path.join(save_path, tracks[0]["path"])), save_path}
            for d in dirs:
                for name in ("cover.jpg", "folder.jpg", "front.jpg", "cover.png", "folder.png"):
                    p = os.path.join(d, name)
                    if os.path.exists(p):
                        data = Path(p).read_bytes()
                        mime = "image/png" if name.endswith(".png") else "image/jpeg"
                        break
                if data:
                    break
    except Exception:  # noqa: BLE001, S110 — обложка не критична
        pass
    if not data:
        raise HTTPException(404, "обложка не найдена")
    cache_file.write_bytes(data)
    return Response(content=data, media_type=mime or "image/jpeg")


@app.delete("/api/albums/{id}")
def delete_album(id: int, remove_torrent: bool = False):
    c = db()
    a = c.execute("SELECT * FROM albums WHERE id=?", (id,)).fetchone()
    if not a:
        raise HTTPException(404, "альбом не найден")
    if remove_torrent and not _is_local(a["hash"]):
        qb = QB()
        qb.delete(a["hash"])
    c.execute("DELETE FROM tracks WHERE album_id=?", (id,))
    c.execute("DELETE FROM albums WHERE id=?", (id,))
    c.commit()
    return {"ok": True, "id": id}

_LOCAL_PREFIX = "local:"


def _is_local(hash_) -> bool:
    """Локальный альбом (медиапапка USB/NAS) хранится с hash='local:<путь к папке>'."""
    return str(hash_).startswith(_LOCAL_PREFIX)


def _local_dir(hash_) -> str:
    return str(hash_)[len(_LOCAL_PREFIX):]


# ---------- медиапапки (USB / сетевые источники) ----------
AUDIO_EXTS = {".flac", ".dsf", ".dff", ".wav", ".mp3", ".m4a", ".aac", ".ogg",
              ".opus", ".ape", ".wv", ".aiff", ".aif", ".mp4", ".m4b"}
_SKIP_DIRS = {"@eadir", "#recycle", "@tmp", "lost+found", ".spotlight-v100",
              ".trashes", ".fseventsd", ".synologyworkingdir", ".appledouble",
              "system volume information", "$recycle.bin"}


def _media_dirs() -> list:
    """Список корней медиапапок из настроек (JSON-массив абсолютных путей)."""
    raw = _get_setting("media_dirs")
    if not raw:
        return []
    try:
        dirs = json.loads(raw)
    except ValueError:
        return []
    return [d for d in dirs if isinstance(d, str) and d.startswith("/")]


_SCAN_STATE = {"running": False, "path": None, "scanned": 0,
               "added": 0, "updated": 0, "errors": 0}
_SCAN_LOCK = threading.Lock()

# Кэш доступности корней медиапапок (TTL): /api/albums на каждый запрос делает
# os.path.isdir() на SMB-корне, а при активном бэкфилле (чтение тегов по SMB)
# этот stat блокируется на секунды — API деградирует. Доступность корня меняется
# редко (размонтировали NAS), поэтому кэш на 30с снимает SMB с горячего пути.
_MEDIA_ROOT_OK: dict = {}  # path -> (bool, ts)


def _dir_available(d: str) -> bool:
    def _check(rr: str) -> bool:
        now = time.time()
        hit = _MEDIA_ROOT_OK.get(rr)
        if hit and now - hit[1] < 30:
            return hit[0]
        ok = os.path.isdir(rr)
        _MEDIA_ROOT_OK[rr] = (ok, now)
        return ok
    for root in _media_dirs():
        rr = root.rstrip("/")
        if d == rr or d.startswith(rr + "/"):
            return _check(rr)
    return _check(d)


def _parse_folder_album(dirname: str):
    """'Basia - Time And Tide 1987(LP)' → ('Basia', 'Time And Tide 1987(LP)').

    Левая часть не берётся как артист, если это номер/мусор ('02 - Song')."""
    m = re.match(r"^(.+?)\s*[-–—]\s*(.+)$", dirname)
    if m:
        left = m.group(1).strip()
        if not re.fullmatch(r"[\d\W_]+", left) and len(left) > 1:
            return left, m.group(2).strip()
    return None, dirname


def _upsert_local_album(c, album_dir: str, audio) -> bool:
    """Создать/обновить локальный альбом. Возвращает True, если создан новый."""
    h = _LOCAL_PREFIX + album_dir
    dirname = os.path.basename(album_dir)
    artist, name = _parse_folder_album(dirname)
    audio.sort(key=lambda e: e.name)
    row = c.execute("SELECT id, artist FROM albums WHERE hash=?", (h,)).fetchone()
    created = row is None
    # артист: теги приоритетнее имени папки (папка — только fallback).
    # Читаем, если создаём альбом или существующий лежит без артиста.
    if created or (row is not None and not row["artist"]):
        try:
            _, _, tag_artist, _, _ = _read_meta(audio[0].path)
            if tag_artist:
                artist = tag_artist
        except Exception:  # noqa: BLE001, S110 — теги не критичны
            pass
    if created:
        cur = c.execute(
            "INSERT INTO albums(hash,name,artist,status,added_at) VALUES(?,?,?,?,?)",
            (h, name, artist, "complete", int(time.time())))
        aid = cur.lastrowid
    else:
        aid = row["id"]
        if artist and not row["artist"]:
            c.execute("UPDATE albums SET artist=? WHERE id=?", (artist, aid))
    existing = {t["path"]: t["size"] for t in
                c.execute("SELECT path,size FROM tracks WHERE album_id=?", (aid,))}
    names = set()
    for e in audio:
        names.add(e.name)
        try:
            size = e.stat().st_size
        except OSError:
            size = 0
        if e.name in existing and existing[e.name] == size:
            continue
        if e.name in existing:
            c.execute("UPDATE tracks SET size=? WHERE album_id=? AND path=?",
                      (size, aid, e.name))
        else:
            c.execute("INSERT INTO tracks(album_id,path,name,size) VALUES(?,?,?,?)",
                      (aid, e.name, e.name, size))
    for gone in set(existing) - names:
        c.execute("DELETE FROM tracks WHERE album_id=? AND path=?", (aid, gone))
    c.commit()
    return created


def _scan_root(root: str) -> None:
    """Обход медиапапки: альбом = каталог, в котором лежат аудиофайлы."""
    root = os.path.abspath(root)
    c = db()
    stack = [(root, 0)]
    scanned = 0
    while stack:
        if not _SCAN_STATE["running"]:
            return  # отменён
        d, lvl = stack.pop()
        try:
            with os.scandir(d) as it:
                entries = list(it)
        except OSError:
            _SCAN_STATE["errors"] += 1
            continue
        scanned += 1
        audio = []
        for e in entries:
            try:
                if e.is_dir(follow_symlinks=False):
                    if (e.name.lower() not in _SKIP_DIRS
                            and not e.name.startswith(".") and lvl < 6):
                        stack.append((e.path, lvl + 1))
                elif e.is_file(follow_symlinks=False) and \
                        os.path.splitext(e.name)[1].lower() in AUDIO_EXTS:
                    audio.append(e)
            except OSError:
                continue
        if audio:
            try:
                if _upsert_local_album(c, d, audio):
                    _SCAN_STATE["added"] += 1
                else:
                    _SCAN_STATE["updated"] += 1
            except Exception as e:  # noqa: BLE001 — битая папка не роняет скан
                print(f"[scan] {d}: {e}", flush=True)
                _SCAN_STATE["errors"] += 1
        if scanned % 25 == 0:
            _SCAN_STATE["scanned"] = scanned


def _scan_worker(targets: list) -> None:
    try:
        for root in targets:
            if not _SCAN_STATE["running"]:
                break
            _SCAN_STATE["path"] = root
            _scan_root(root)
    finally:
        with _SCAN_LOCK:
            _SCAN_STATE.update(running=False, path=None)
        _backfill_kick()  # после скана — автозаполнение тегов новых треков (фон)


# ---------- бэкфилл тегов треков (названия/длительности/жанры по всей библиотеке) ----------
_BACKFILL_STATE = {"running": False, "total": 0, "done": 0, "updated": 0,
                   "errors": 0, "album": None}
_BACKFILL_LOCK = threading.Lock()
# Пауза между треками (сек): бэкфилл читает теги с медленных монтирований
# (SMB/USB) и без троттлинга доводит load до десятков на 8 ядрах — API
# (в т.ч. /api/albums, который фронт поллит каждые 5с) деградирует до 10с+.
# 0.1с НЕ хватает: сам чтение тега по SMB — многосекундный блокирующий I/O,
# поэтому пауза должна давать API окно. 1.0с + последовательные чтения
# оставляют API ~половину времени SMB-линка.
_BACKFILL_DELAY = 1.0


def _read_tags_full(path):
    """Все теги одним открытием файла (дешевле _read_meta+_read_year_genre по сети).

    Поддерживает Vorbis (FLAC) и ID3 (MP3/DSF): ключи обоих семейств.
    """
    try:
        f = MutagenFile(path)
        if f is None:
            return {}
        out = {}
        if f.info is not None:
            with contextlib.suppress(Exception):
                out["duration"] = float(f.info.length)
            bits = getattr(f.info, "bits_per_sample", None)
            sr = getattr(f.info, "sample_rate", None)
            if bits and sr:
                out["format"] = f"{bits}/{sr // 1000}"
            else:
                br = getattr(f.info, "bitrate", None)
                if br:
                    out["format"] = f"{int(br // 1000)} kbps"
        tags = getattr(f, "tags", None)
        if tags:
            def pick(*keys):
                for k in keys:
                    v = tags.get(k)
                    if not v:
                        continue
                    if isinstance(v, (list, tuple)):
                        v = v[0]
                    text = getattr(v, "text", None)
                    if isinstance(text, (list, tuple)):
                        text = text[0] if text else None
                    s = str(text if text is not None else v).strip()
                    if s:
                        return s
                return None
            for dst, keys in (("title", ("title", "TIT2")),
                              ("artist", ("artist", "TPE1")),
                              ("genre", ("genre", "TCON"))):
                v = pick(*keys)
                if v:
                    out[dst] = v
            y = pick("date", "year", "originaldate", "TDRC", "TYER")
            if y:
                out["year"] = y[:4]
        return out
    except Exception:  # noqa: BLE001 — метаданные не критичны
        return {}


def _backfill_kick() -> bool:
    """Стартует воркер, если есть треки без тегов и он не идёт. Возвращает started."""
    with _BACKFILL_LOCK:
        if _BACKFILL_STATE["running"]:
            return False
        total = db().execute(
            "SELECT COUNT(*) FROM tracks WHERE tagged IS NULL OR tagged=0").fetchone()[0]
        if not total:
            return False
        _BACKFILL_STATE.update(running=True, total=total, done=0, updated=0,
                               errors=0, album=None)
    threading.Thread(target=_backfill_worker, daemon=True).start()
    return True


def _backfill_worker() -> None:
    try:
        c = db()
        albums = c.execute(
            """SELECT DISTINCT a.id, a.hash, a.name, a.artist FROM albums a
               JOIN tracks t ON t.album_id = a.id
               WHERE t.tagged IS NULL OR t.tagged=0 ORDER BY a.id""").fetchall()
        qb = None
        for a in albums:
            if not _BACKFILL_STATE["running"]:
                return
            _BACKFILL_STATE["album"] = (a["name"] or "")[:60]
            if _is_local(a["hash"]):
                save_path = _local_dir(a["hash"])
            else:
                save_path = None
                try:
                    if qb is None:
                        qb = QB()
                    info = qb.find_by_hash(a["hash"])
                    save_path = info.get("save_path") if info else None
                except Exception:  # noqa: BLE001 — qbt недоступен: torrent-альбомы пропускаем
                    qb = None
            tracks = c.execute(
                """SELECT id, path, title, duration, format, year, genre FROM tracks
                   WHERE album_id=? AND (tagged IS NULL OR tagged=0) ORDER BY path""",
                (a["id"],)).fetchall()
            if not save_path:
                # путь не резолвится (qbt down / торрент удалён) — НЕ помечаем, повторим позже
                _BACKFILL_STATE["done"] += len(tracks)
                _BACKFILL_STATE["errors"] += 1
                continue
            artist_tag = None
            for i, t in enumerate(tracks):
                if not _BACKFILL_STATE["running"]:
                    c.commit()
                    return
                tags = _read_tags_full(os.path.join(save_path, t["path"]))
                artist_tag = artist_tag or tags.get("artist")
                c.execute(
                    "UPDATE tracks SET title=?, duration=?, format=?, year=?, genre=?,"
                    " tagged=1 WHERE id=?",
                    (tags.get("title") or t["title"],
                     tags.get("duration", t["duration"]),
                     tags.get("format") or t["format"],
                     tags.get("year") or t["year"],
                     tags.get("genre") or t["genre"], t["id"]))
                if tags:
                    _BACKFILL_STATE["updated"] += 1
                _BACKFILL_STATE["done"] += 1
                time.sleep(_BACKFILL_DELAY)  # троттлинг: не дать I/O-голод API
                if i % 50 == 49:
                    c.commit()
            if artist_tag and not a["artist"]:
                c.execute("UPDATE albums SET artist=? WHERE id=?", (artist_tag, a["id"]))
            c.commit()
    except Exception as e:  # noqa: BLE001 — воркер не должен ронять процесс
        _BACKFILL_STATE["errors"] += 1
        print(f"backfill: {e}")
    finally:
        with _BACKFILL_LOCK:
            _BACKFILL_STATE.update(running=False, album=None)


@app.get("/api/library/backfill")
def backfill_status():
    return dict(_BACKFILL_STATE)


@app.post("/api/library/backfill")
def backfill_start():
    with _BACKFILL_LOCK:
        if _BACKFILL_STATE["running"]:
            raise HTTPException(409, "бэкфилл уже идёт")
    started = _backfill_kick()
    return {"ok": True, "started": started,
            **({"reason": "нет треков без тегов"} if not started else {})}


@app.delete("/api/library/backfill")
def backfill_stop():
    _BACKFILL_STATE["running"] = False
    return {"ok": True}


class SourceReq(BaseModel):
    path: str = ""


@app.get("/api/sources")
def sources_get():
    c = db()
    out = []
    for d in _media_dirs():
        like = _LOCAL_PREFIX + d.rstrip("/") + "/%"
        n = c.execute("SELECT COUNT(*) FROM albums WHERE hash LIKE ?", (like,)).fetchone()[0]
        out.append({"path": d, "exists": os.path.isdir(d), "albums": n})
    return {"sources": out, "scan": dict(_SCAN_STATE)}


@app.post("/api/sources")
def sources_add(req: SourceReq):
    p = os.path.abspath(req.path or "")
    if not p.startswith("/") or p == "/":
        raise HTTPException(400, "нужен абсолютный путь к папке")
    if not os.path.isdir(p):
        raise HTTPException(400, f"папка недоступна: {p} (проверьте монтирование)")
    dirs = _media_dirs()
    if p not in dirs:
        dirs.append(p)
        _set_setting("media_dirs", json.dumps(dirs))
    return {"ok": True, "sources": dirs}


@app.delete("/api/sources")
def sources_remove(path: str, delete_albums: bool = True):
    p = os.path.abspath(path)
    dirs = _media_dirs()
    if p not in dirs:
        raise HTTPException(404, "такой медиапапки нет в списке")
    dirs.remove(p)
    _set_setting("media_dirs", json.dumps(dirs))
    _MEDIA_ROOT_OK.pop(p, None)  # сброс кэша доступности удалённого корня
    removed = 0
    if delete_albums:
        c = db()
        like = _LOCAL_PREFIX + p.rstrip("/") + "/%"
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM albums WHERE hash LIKE ?", (like,))]
        for aid in ids:
            c.execute("DELETE FROM tracks WHERE album_id=?", (aid,))
        c.execute("DELETE FROM albums WHERE hash LIKE ?", (like,))
        c.commit()
        removed = len(ids)
    return {"ok": True, "sources": dirs, "albums_removed": removed}


@app.post("/api/sources/scan")
def sources_scan(req: SourceReq | None = None):
    with _SCAN_LOCK:
        if _SCAN_STATE["running"]:
            raise HTTPException(409, "сканирование уже идёт")
        targets = [os.path.abspath(req.path)] if req and req.path else _media_dirs()
        if not targets:
            raise HTTPException(400, "нет медиапапок для сканирования")
        missing = [t for t in targets if not os.path.isdir(t)]
        if missing:
            raise HTTPException(400, f"недоступно: {', '.join(missing)}")
        _SCAN_STATE.update(running=True, path=targets[0], scanned=0,
                           added=0, updated=0, errors=0)
    threading.Thread(target=_scan_worker, args=(targets,), daemon=True).start()
    return {"ok": True, "targets": targets}


def _current_tracks(aid):
    c = db()
    a = c.execute("SELECT * FROM albums WHERE id=?", (aid,)).fetchone()
    if not a:
        raise HTTPException(404, "альбом не найден")
    tracks = c.execute("SELECT * FROM tracks WHERE album_id=? ORDER BY path", (aid,)).fetchall()
    if not tracks:
        raise HTTPException(400, "в альбоме нет треков")
    if _is_local(a["hash"]):
        save_path = _local_dir(a["hash"])
        if not os.path.isdir(save_path):
            raise HTTPException(500, f"медиапапка недоступна: {save_path}")
        meta = {"name": a["name"], "artist": a["artist"], "cover_url": a["cover_url"],
                "hash": a["hash"]}
        return tracks, save_path, meta
    qb = QB()
    info = qb.find_by_hash(a["hash"])
    save_path = info["save_path"] if info else None
    if not save_path:
        raise HTTPException(500, "не найден путь загрузки")
    meta = {"name": a["name"], "artist": a["artist"], "cover_url": a["cover_url"],
            "hash": a["hash"]}
    return tracks, save_path, meta


def _read_meta(path):
    """duration, title/artist/album, format-строка из тегов (fallback — None)."""
    try:
        f = MutagenFile(path)
        if f is None:
            return None, None, None, None, None
        duration = float(f.info.length) if f.info is not None else None
        title = artist = album = None
        tags = getattr(f, "tags", None)
        if tags:
            title = (tags.get("title") or [None])[0]
            artist = (tags.get("artist") or [None])[0]
            album = (tags.get("album") or [None])[0]
        # формат: для lossless — битность/частота («24/96»), иначе битрейт
        fmt = None
        if f.info is not None:
            bits = getattr(f.info, "bits_per_sample", None)
            sr = getattr(f.info, "sample_rate", None)
            if bits and sr:
                fmt = f"{bits}/{sr // 1000}"
            else:
                br = getattr(f.info, "bitrate", None)
                if br:
                    fmt = f"{int(br // 1000)} kbps"
        return duration, title, artist, album, fmt
    except Exception:  # noqa: BLE001 — метаданные не критичны
        return None, None, None, None, None


def _read_year_genre(path):
    """(year, genre) из тегов → (str|None, str|None)."""
    try:
        f = MutagenFile(path)
        if f is None:
            return None, None
        tags = getattr(f, "tags", None)
        year = genre = None
        if tags:
            for k in ("date", "year", "originaldate"):
                v = tags.get(k)
                if v:
                    year = str(v[0])[:4]  # «2026-03-01» → «2026»
                    break
            genre = (tags.get("genre") or [None])[0]
        return year, genre
    except Exception:  # noqa: BLE001 — метаданные не критичны
        return None, None


def _read_cover(path):
    """embedded обложка из тегов → (bytes, mime) или None."""
    try:
        f = MutagenFile(path)
        if f is None:
            return None
        pics = getattr(f, "pictures", None)
        if pics and pics[0].data:
            mime = getattr(pics[0], "mime", None) or "image/jpeg"
            return pics[0].data, mime
        tags = getattr(f, "tags", None)
        if tags:
            for key in ("APIC:", "covr"):
                if key in tags:
                    frame = tags[key]
                    if isinstance(frame, list) and frame and getattr(frame[0], "data", None):
                        return frame[0].data, getattr(frame[0], "mime", None) or "image/jpeg"
    except Exception:  # noqa: BLE001 — обложка не критична
        return None
    return None


def _ts_cmd(payload: dict) -> dict:
    """Отправить JSON-команду в ts-controller и получить ответ."""
    try:
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as s:
            s.settimeout(5)
            s.connect(TS_CONTROLLER_SOCK)
            s.sendall((json.dumps(payload) + "\n").encode())
            buf = b""
            while b"\n" not in buf:
                chunk = s.recv(4096)
                if not chunk:
                    break
                buf += chunk
            if not buf:
                return {"ok": False, "error": "контроллер не ответил"}
            return json.loads(buf.decode().strip())
    except FileNotFoundError:
        # ts-controller создаёт сокет только ПОСЛЕ подключения к endpoint'у:
        # спящий/выключенный endpoint (напр. закрытый MacBook) = сокета нет.
        return {"ok": False,
                "error": f"нет связи с endpoint «{_active_ep_name()}» — устройство спит "
                         "или не в сети; разбудите его или переключите endpoint в настройках"}
    except ConnectionRefusedError:
        return {"ok": False, "error": "контроллер перезапускается — повторите через пару секунд"}
    except TimeoutError:
        return {"ok": False, "error": "контроллер не ответил вовремя (5с)"}
    except (OSError, ValueError) as e:
        return {"ok": False, "error": str(e)}


_DECODE_JOBS: set = set()
_DECODE_LOCK = threading.Lock()


def _wav_path(aid: int, idx: int, rg: bool = False) -> str:
    return f"/tmp/oaat_stream_{aid}_{idx}{'_rg' if rg else ''}.wav"


# ---------- ReplayGain (EBU R128): выравнивание громкости между релизами ----------
_RG_TARGET_LUFS = -18.0  # компромисс между Spotify (-14) и EBU R128 (-23)
_RG_CLAMP_DB = 12.0


def _rg_enabled() -> bool:
    with contextlib.suppress(Exception):
        return _get_setting("replaygain") == "1"
    return False


def _track_gain_db(aid: int, idx: int) -> float | None:
    with contextlib.suppress(Exception), db() as c:
        row = c.execute("SELECT gain_db FROM tracks WHERE album_id=? ORDER BY path "
                        "LIMIT 1 OFFSET ?", (aid, idx)).fetchone()
        if row and row["gain_db"] is not None:
            return float(row["gain_db"])
    return None


def _measure_gain(abs_path: str) -> float | None:
    """интегрированная громкость трека (EBU R128) → gain к целевому уровню, dB."""
    try:
        p = subprocess.run([FFMPEG, "-i", abs_path, "-map", "a:0",
                            "-af", "ebur128=framelog=quiet", "-f", "null", "-"],
                           check=False, capture_output=True, timeout=600)
        m = re.search(rb"I:\s*(-?\d+(?:\.\d+)?)\s*LUFS", p.stderr)
        if not m or p.returncode != 0:
            return None
        loudness = float(m.group(1))
        if loudness < -69.5:  # тише порога измерения R128 — считаем тишиной
            return None
        gain = _RG_TARGET_LUFS - loudness
        return max(-_RG_CLAMP_DB, min(_RG_CLAMP_DB, gain))
    except (subprocess.TimeoutExpired, OSError):
        return None


def _measure_and_store_gain(aid: int, idx: int, abs_path: str) -> float | None:
    gain = _measure_gain(abs_path)
    if gain is not None:
        with contextlib.suppress(Exception), db() as c:
            c.execute("UPDATE tracks SET gain_db=? WHERE album_id=? AND rowid="
                      "(SELECT rowid FROM tracks WHERE album_id=? ORDER BY path "
                      "LIMIT 1 OFFSET ?)", (gain, aid, aid, idx))
            c.commit()
    return gain


def _measure_all_gains():
    """фоновый замер громкости всех треков библиотеки (запуск при включении ReplayGain)."""
    with db() as c:
        rows = c.execute(
            "SELECT tr.id, tr.path, a.hash FROM tracks tr "
            "JOIN albums a ON a.id=tr.album_id WHERE tr.gain_db IS NULL").fetchall()
    save_paths: dict = {}  # hash → save_path (кэш на альбом)
    n = 0
    for r in rows:
        h = r["hash"]
        if h not in save_paths:
            sp = None
            with contextlib.suppress(Exception):
                info = QB().find_by_hash(h)
                sp = info.get("save_path") if info else None
            save_paths[h] = sp or ""
        if not save_paths[h]:
            continue
        abs_path = os.path.join(save_paths[h], r["path"])
        if not os.path.exists(abs_path):
            continue
        gain = _measure_gain(abs_path)
        if gain is None:
            continue
        with contextlib.suppress(Exception), db() as c:
            c.execute("UPDATE tracks SET gain_db=? WHERE id=?", (gain, r["id"]))
            c.commit()
        n += 1
    print(f"[replaygain] замерено треков: {n}", flush=True)


def _predecode(aid: int, idx: int, abs_path: str):
    """фоновое декодирование FLAC/DSD/MP3 → WAV (lossless; для PCM-кодеков бит-перфект),
    чтобы play/next был мгновенным, а не ждал синхронный ffmpeg.
    При включённом ReplayGain и известном gain — декодирует сразу rg-вариант."""
    if Path(abs_path).suffix.lower() == ".wav":
        return
    rg_on = _rg_enabled()
    gain = _track_gain_db(aid, idx) if rg_on else None
    want_rg = bool(rg_on and gain is not None)
    wav = _wav_path(aid, idx, rg=want_rg)
    if os.path.exists(wav) and os.path.getsize(wav) > 0:
        return
    key = (aid, idx, want_rg)
    with _DECODE_LOCK:
        if key in _DECODE_JOBS:
            return
        _DECODE_JOBS.add(key)

    def job():
        try:
            g = gain
            out = wav
            if rg_on and g is None:
                g = _measure_and_store_gain(aid, idx, abs_path)
                if g is not None:
                    out = _wav_path(aid, idx, rg=True)
            tmp = out + ".part"
            cmd = [FFMPEG, "-y", "-i", abs_path]
            if rg_on and g is not None:
                cmd += ["-af", f"volume={g:.2f}dB", "-c:a", "pcm_s32le"]  # RG → s32 для headroom
            else:
                cmd += ["-c:a", _wav_codec_for(abs_path)]  # сохраняем глубину источника
            cmd += ["-f", "wav", tmp]
            conv = subprocess.run(cmd, check=False, capture_output=True)
            if conv.returncode == 0:
                os.replace(tmp, out)
            else:
                with contextlib.suppress(OSError):
                    os.remove(tmp)
        finally:
            with _DECODE_LOCK:
                _DECODE_JOBS.discard(key)

    threading.Thread(target=job, daemon=True).start()


def _wait_predecode(aid: int, idx: int, timeout: float = 120.0) -> None:
    """Дождаться окончания фонового декодирования (aid, idx), если оно идёт.
    Без этого синхронный fallback запускает второй ffmpeg на тот же файл, а
    фоновый os.replace подменяет WAV прямо во время стриминга контроллером."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        with _DECODE_LOCK:
            if (aid, idx) not in _DECODE_JOBS:
                return
        time.sleep(0.25)


def _start_track(aid, idx, offset=0.0):
    global _OAAT_ALBUM_ID, _OAAT_TRACK_INDEX, _OAAT_TRACK_NAME, _OAAT_TRACK_TITLE
    global _OAAT_DURATION, _OAAT_ALBUM_NAME, _OAAT_ARTIST, _OAAT_COVER_URL, _OAAT_FORMAT
    tracks, save_path, meta = _current_tracks(aid)
    if not (0 <= idx < len(tracks)):
        raise HTTPException(400, "нет такого трека в альбоме")
    track = tracks[idx]
    # приоритизируем текущий + следующий трек (6), остальные аудио — 1 (докачаются позже),
    # не-аудио — 0: нужный файл качается первым, но весь альбом в итоге докачается.
    # best-effort: не блокирует play. Для локальных альбомов (медиапапки) не нужно.
    with contextlib.suppress(Exception):  # торрент может быть ещё не найден / удалён
        if not _is_local(meta["hash"]):
            audio_names = {t["path"] for t in tracks}
            window = {tracks[i]["path"] for i in (idx, idx + 1) if 0 <= i < len(tracks)}
            if window:
                QB().prioritize_window(meta["hash"], window, audio_names)
    abs_path = os.path.join(save_path, track["path"])
    # не даём ffmpeg упасть на недокачанном файле — явная ошибка вместо «no packets»
    if track["size"] and os.path.exists(abs_path) and os.path.getsize(abs_path) < track["size"]:
        raise HTTPException(409, "трек ещё скачивается — дождитесь готовности")
    # ts-controller стримит только WAV → декодируем остальное (FLAC/DSD/mp3)
    # через ffmpeg в lossless PCM; перемотку делает сам контроллер (без -ss).
    if Path(abs_path).suffix.lower() != ".wav":
        rg_on = _rg_enabled()
        gain = _track_gain_db(aid, idx) if rg_on else None
        use_rg = gain is not None
        stream_path = _wav_path(aid, idx, rg=use_rg)
        if not (os.path.exists(stream_path) and os.path.getsize(stream_path) > 0):
            # этот трек уже может декодироваться в фоне — дожидаемся, а не
            # запускаем второй ffmpeg (двойная нагрузка + подмена WAV на лету)
            _wait_predecode(aid, idx)
            # фоновая job могла замерить gain и записать rg-вариант — подхватываем
            if rg_on and not use_rg:
                g2 = _track_gain_db(aid, idx)
                rg_path = _wav_path(aid, idx, rg=True)
                if g2 is not None and os.path.exists(rg_path) and os.path.getsize(rg_path) > 0:
                    stream_path, use_rg, gain = rg_path, True, g2
        if not (os.path.exists(stream_path) and os.path.getsize(stream_path) > 0):
            # не успели пред-декодировать — декодируем синхронно (fallback)
            cmd = [FFMPEG, "-y", "-i", abs_path]
            if use_rg and gain is not None:
                cmd += ["-af", f"volume={gain:.2f}dB", "-c:a", "pcm_s32le"]
            else:
                cmd += ["-c:a", _wav_codec_for(abs_path)]
            cmd += ["-f", "wav", stream_path]
            conv = subprocess.run(cmd, check=False, capture_output=True)
            if conv.returncode != 0:
                with contextlib.suppress(OSError):
                    os.remove(stream_path)
                raise HTTPException(500, f"ffmpeg не смог декодировать: {conv.stderr.decode(errors='replace')[-200:]}")
    else:
        stream_path = abs_path
    # пред-декодируем следующий трек в фоне (мгновенный next)
    if idx + 1 < len(tracks):
        _predecode(aid, idx + 1, os.path.join(save_path, tracks[idx + 1]["path"]))

    duration, title, artist, album, fmt = _read_meta(abs_path)
    resp = _ts_cmd({
        "cmd": "play",
        "path": stream_path,
        "title": title or track["name"],
        "artist": artist or meta["artist"],
        "album": album or meta["name"],
    })
    if not resp.get("ok"):
        raise HTTPException(500, f"контроллер: {resp.get('error', 'нет ответа')}")
    if offset > 0:
        _ts_cmd({"cmd": "seek", "sec": offset})

    _OAAT_ALBUM_ID = aid
    _OAAT_TRACK_INDEX = idx
    _OAAT_TRACK_NAME = track["name"]
    _OAAT_TRACK_TITLE = title or track["name"]
    _OAAT_DURATION = duration or resp.get("duration")
    _OAAT_ALBUM_NAME = album or meta["name"]
    _OAAT_ARTIST = artist or meta["artist"]
    _OAAT_COVER_URL = meta["cover_url"]
    _OAAT_FORMAT = fmt
    return {"ok": True, "album_id": aid, "track_index": idx, "file": os.path.basename(abs_path)}


def _build_queue(aid: int, start_idx: int):
    """Заменить очередь альбомом, начиная с start_idx."""
    global _QUEUE, _QUEUE_POS
    tracks, _, _ = _current_tracks(aid)
    _QUEUE = [{"aid": aid, "idx": i} for i in range(start_idx, len(tracks))]
    _QUEUE_POS = 0
    if _SHUFFLE and len(_QUEUE) > 1:
        _apply_shuffle()
    _persist_player_state()


# ---------- состояние плеера: персистенция, repeat/shuffle, автопереход ----------
def _persist_player_state():
    """сохраняет очередь/позицию/режимы — переживают рестарт backend."""
    with contextlib.suppress(Exception):
        _set_setting("player_state", json.dumps({
            "queue": _QUEUE, "pos": _QUEUE_POS,
            "repeat": _REPEAT, "shuffle": _SHUFFLE}))


def _restore_player_state():
    global _QUEUE, _QUEUE_POS, _REPEAT, _SHUFFLE
    with contextlib.suppress(Exception):
        raw = _get_setting("player_state")
        if not raw:
            return
        d = json.loads(raw)
        _QUEUE = [{"aid": int(e["aid"]), "idx": int(e["idx"])}
                  for e in d.get("queue", []) if isinstance(e, dict)]
        _QUEUE_POS = int(d.get("pos", -1))
        _REPEAT = d.get("repeat") if d.get("repeat") in ("off", "all", "one") else "off"
        _SHUFFLE = bool(d.get("shuffle", False))


def _apply_shuffle():
    """перемешивает очередь, оставляя текущий трек первым (честный порядок виден в UI)."""
    global _QUEUE_POS
    if not _QUEUE:
        return
    if 0 <= _QUEUE_POS < len(_QUEUE):
        cur = _QUEUE.pop(_QUEUE_POS)
        random.shuffle(_QUEUE)
        _QUEUE.insert(0, cur)
        _QUEUE_POS = 0
    else:
        random.shuffle(_QUEUE)


def _auto_advance():
    """трек закончился естественно — что дальше по режимам repeat/shuffle."""
    global _QUEUE_POS
    if not _QUEUE or not (0 <= _QUEUE_POS < len(_QUEUE)):
        return
    if _REPEAT == "one":
        entry = _QUEUE[_QUEUE_POS]
        _start_track(entry["aid"], entry["idx"])
        _hist_upsert(entry["aid"], entry["idx"], 0)
        return
    npos = _QUEUE_POS + 1
    if npos >= len(_QUEUE):
        if _REPEAT != "all":
            return  # очередь кончилась, repeat выкл — остаёмся в stop
        npos = 0
        if _SHUFFLE:
            _apply_shuffle()  # новый круг — новая раскладка
    entry = _QUEUE[npos]
    _start_track(entry["aid"], entry["idx"])
    _QUEUE_POS = npos
    _hist_upsert(entry["aid"], entry["idx"], 0)
    _persist_player_state()


def _hist_upsert(aid, idx, pos):
    """точка возобновления альбома (история прослушивания / «продолжить»)."""
    try:
        with db() as c:
            c.execute(
                "INSERT OR REPLACE INTO history(album_id, track_index, position, ts)"
                " VALUES(?,?,?,?)",
                (int(aid), int(idx), float(pos or 0), int(time.time())))
            c.commit()
    except Exception as e:  # noqa: BLE001 — история не должна ронять монитор
        print(f"[history] upsert сбой: {e}", flush=True)


def _device_watch(state: str):
    """P1.7: пропало выходное устройство (снял наушники / отключил DAC) → авто-пауза.

    Устройство вернулось → переселект на нём (endpoint мог отвалиться в default)
    и resume с той же позиции. Вызывается из _player_monitor раз в ~3с.

    Важен baseline: на Linux-эндпоинтах current_device — метка драйвера
    («alsa-direct»), которой никогда нет в available_devices (там ALSA-карты).
    Пропажей считаем только устройство, которое РАНЬШЕ наблюдалось в списке
    этого же endpoint'а (семантика macOS/CoreAudio)."""
    global _DEVICE_LOST_NAME, _DEVICE_BASELINE
    if state == "stop" or (state == "playing" and _DEVICE_LOST_NAME):
        # stop — сессия кончилась; playing при потерянном — пользователь возобновил
        # вручную, не мешаем
        _DEVICE_LOST_NAME = None
        if state == "stop":
            _DEVICE_BASELINE = None
        return
    ep = _active_ep_name()
    if ep is None:
        return
    if _DEVICE_BASELINE and _DEVICE_BASELINE[0] != ep:
        _DEVICE_BASELINE = None  # сменился endpoint — старый baseline недействителен
    st = _ep_status(ep)
    cur = st.get("current_device") or ""
    avail = st.get("available_devices") or []
    if not cur or not avail:
        return  # endpoint недоступен / старая версия / не отдаёт список — не мешаем
    if _DEVICE_LOST_NAME is None:
        if state != "playing":
            return
        if cur in avail:
            _DEVICE_BASELINE = (ep, cur)
            return
        # cur не в списке: потеря — только если baseline этого endpoint'а исчез
        if _DEVICE_BASELINE and _DEVICE_BASELINE[1] not in avail:
            player_cmd("pause")
            _DEVICE_LOST_NAME = _DEVICE_BASELINE[1]
            print(f"[player] устройство '{_DEVICE_LOST_NAME}' пропало — авто-пауза",
                  flush=True)
    elif state == "paused" and _DEVICE_LOST_NAME in avail:
        try:
            if st.get("current_device") != _DEVICE_LOST_NAME:
                requests.post(f"{_ep_web(ep)}/api/device",
                              json={"device": _DEVICE_LOST_NAME},
                              headers=_ep_headers(ep), timeout=5)
                time.sleep(0.5)
            player_cmd("resume")
            print(f"[player] устройство '{_DEVICE_LOST_NAME}' вернулось — resume",
                  flush=True)
        except requests.RequestException as e:
            print(f"[player] device resume не удался: {e}", flush=True)
            return
        _DEVICE_LOST_NAME = None


def _player_monitor():
    """ловит естественный конец трека (playing→stop при живой очереди) и идёт дальше.

    Пользовательский stop отличаем по очищенной очереди: player_cmd('stop')
    сбрасывает _QUEUE до того, как контроллер замолкает."""
    was_playing = False
    tick = 0
    while True:
        time.sleep(1.0)
        resp = _ts_cmd({"cmd": "status"})
        state = resp.get("state", "stop") if isinstance(resp, dict) else "stop"
        tick += 1
        if tick % 3 == 0:
            try:
                _device_watch(state)
            except Exception as e:  # noqa: BLE001 — сбой watch не роняет монитор
                print(f"[player] device_watch сбой: {e}", flush=True)
        if (tick % 10 == 0 and state == "playing" and _OAAT_ALBUM_ID is not None
                and isinstance(resp, dict) and resp.get("elapsed") is not None):
            _hist_upsert(_OAAT_ALBUM_ID, _OAAT_TRACK_INDEX or 0, resp["elapsed"])
        if was_playing and state == "stop" and _QUEUE and 0 <= _QUEUE_POS < len(_QUEUE):
            try:
                _auto_advance()
            except Exception as e:  # noqa: BLE001 — сбой старта не роняет монитор
                print(f"[player] автопереход не удался: {e}", flush=True)
                # контроллер мог умереть в момент конца трека — watchdog его
                # поднимет; даём один повторный шанс через несколько секунд
                time.sleep(6)
                if _QUEUE and 0 <= _QUEUE_POS < len(_QUEUE):
                    try:
                        _auto_advance()
                        print("[player] автопереход удался со второй попытки", flush=True)
                    except Exception as e2:  # noqa: BLE001
                        print(f"[player] автопереход (retry) не удался: {e2}", flush=True)
        was_playing = state == "playing"


@app.post("/api/albums/{aid}/play")
def play(aid: int, track_index: int = 0, position: float = 0):
    _build_queue(aid, track_index)
    return _start_track(aid, track_index, offset=max(position, 0.0))


@app.get("/api/history")
def history_list():
    """«Продолжить слушать»: последние альбомы с точкой возобновления."""
    with db() as c:
        rows = c.execute(
            "SELECT h.album_id, h.track_index, h.position, h.ts, a.name, a.artist"
            " FROM history h JOIN albums a ON a.id = h.album_id"
            " ORDER BY h.ts DESC LIMIT 30").fetchall()
    return {"history": [dict(r) for r in rows]}


@app.delete("/api/history/{aid}")
def history_remove(aid: int):
    with db() as c:
        c.execute("DELETE FROM history WHERE album_id=?", (aid,))
        c.commit()
    return {"ok": True}


# ---------- плейлисты (P2.11): ручные, трек = album_id + track_index ----------
class PlaylistReq(BaseModel):
    name: str


class PlaylistItemReq(BaseModel):
    album_id: int
    track_index: int = -1  # -1 → весь альбом


@app.get("/api/playlists")
def playlists_list():
    with db() as c:
        rows = c.execute(
            "SELECT p.id, p.name, p.created_at, COUNT(i.id) AS count"
            " FROM playlists p LEFT JOIN playlist_items i ON i.playlist_id=p.id"
            " GROUP BY p.id ORDER BY p.created_at").fetchall()
    return {"playlists": [dict(r) for r in rows]}


@app.post("/api/playlists")
def playlists_create(req: PlaylistReq):
    name = (req.name or "").strip()
    if not name:
        raise HTTPException(400, "пустое название")
    with db() as c:
        cur = c.execute("INSERT INTO playlists(name, created_at) VALUES(?,?)",
                        (name, int(time.time())))
        c.commit()
        pid = cur.lastrowid
    return {"ok": True, "id": pid, "name": name}


@app.delete("/api/playlists/{pid}")
def playlists_delete(pid: int):
    with db() as c:
        c.execute("DELETE FROM playlists WHERE id=?", (pid,))
        c.execute("DELETE FROM playlist_items WHERE playlist_id=?", (pid,))
        c.commit()
    return {"ok": True}


@app.get("/api/playlists/{pid}")
def playlist_get(pid: int):
    with db() as c:
        p = c.execute("SELECT * FROM playlists WHERE id=?", (pid,)).fetchone()
        if not p:
            raise HTTPException(404, "плейлист не найден")
        rows = c.execute(
            "SELECT i.id, i.album_id, i.track_index, a.name AS album, a.artist"
            " FROM playlist_items i JOIN albums a ON a.id=i.album_id"
            " WHERE i.playlist_id=? ORDER BY i.id", (pid,)).fetchall()
        items = [dict(r) for r in rows]
        # имена треков по альбому (ORDER BY path — как в _current_tracks);
        # correlated OFFSET в SQLite не видит внешние колонки, поэтому в Python
        names: dict[int, list] = {}
        for aid in {i["album_id"] for i in items}:
            names[aid] = [t["name"] for t in c.execute(
                "SELECT name FROM tracks WHERE album_id=? ORDER BY path", (aid,))]
        for i in items:
            tl = names.get(i["album_id"], [])
            idx = i["track_index"] or 0
            i["track"] = tl[idx] if 0 <= idx < len(tl) else None
    return {"id": pid, "name": p["name"], "items": items}


@app.post("/api/playlists/{pid}/items")
def playlist_add(pid: int, req: PlaylistItemReq):
    with db() as c:
        if not c.execute("SELECT 1 FROM playlists WHERE id=?", (pid,)).fetchone():
            raise HTTPException(404, "плейлист не найден")
        if req.track_index >= 0:
            idxs = [req.track_index]
        else:
            n = c.execute("SELECT COUNT(*) AS n FROM tracks WHERE album_id=?",
                          (req.album_id,)).fetchone()["n"]
            if not n:
                raise HTTPException(400, "в альбоме нет треков")
            idxs = list(range(n))
        for i in idxs:
            c.execute("INSERT INTO playlist_items(playlist_id, album_id, track_index)"
                      " VALUES(?,?,?)", (pid, req.album_id, i))
        c.commit()
    return {"ok": True, "added": len(idxs)}


@app.delete("/api/playlists/{pid}/items/{item_id}")
def playlist_item_remove(pid: int, item_id: int):
    with db() as c:
        c.execute("DELETE FROM playlist_items WHERE id=? AND playlist_id=?",
                  (item_id, pid))
        c.commit()
    return {"ok": True}


@app.post("/api/playlists/{pid}/play")
def playlist_play(pid: int, start: int = 0):
    """Очередь = треки плейлиста; автопереход/repeat/shuffle работают как обычно."""
    global _QUEUE, _QUEUE_POS
    with db() as c:
        rows = c.execute(
            "SELECT album_id, track_index FROM playlist_items WHERE playlist_id=?"
            " ORDER BY id", (pid,)).fetchall()
    if not rows:
        raise HTTPException(400, "плейлист пуст")
    _QUEUE = [{"aid": r["album_id"], "idx": r["track_index"]} for r in rows]
    if _SHUFFLE and len(_QUEUE) > 1:
        _apply_shuffle()
        _QUEUE_POS = 0
    else:
        _QUEUE_POS = max(0, min(start, len(_QUEUE) - 1))
    _persist_player_state()
    entry = _QUEUE[_QUEUE_POS]
    return _start_track(entry["aid"], entry["idx"])


@app.post("/api/albums/{aid}/tracks/{idx}/play")
def play_track(aid: int, idx: int):
    _build_queue(aid, idx)
    return _start_track(aid, idx)


@app.post("/api/player/seek")
def player_seek(position: float):
    if _OAAT_ALBUM_ID is None or _OAAT_TRACK_INDEX is None:
        raise HTTPException(400, "сначала запусти трек")
    position = max(position, 0)
    resp = _ts_cmd({"cmd": "seek", "sec": position})
    if not resp.get("ok"):
        raise HTTPException(500, f"контроллер: {resp.get('error', 'нет ответа')}")
    return {"ok": True, "elapsed": position}


@app.post("/api/player/volume")
def player_volume(level: int):
    global _OAAT_VOLUME
    level = max(0, min(100, level))
    resp = _ts_cmd({"cmd": "volume", "level": level})
    if not resp.get("ok"):
        raise HTTPException(500, f"контроллер: {resp.get('error', 'нет ответа')}")
    _OAAT_VOLUME = level
    return {"ok": True, "volume": level}


@app.post("/api/player/sleep")
def player_sleep(minutes: int):
    """Таймер сна: остановить через N минут (0 — отменить)."""
    global _SLEEP_TIMER, _SLEEP_MINUTES
    if _SLEEP_TIMER:
        _SLEEP_TIMER.cancel()
        _SLEEP_TIMER = None
    if minutes <= 0:
        _SLEEP_MINUTES = None
        return {"ok": True, "sleep": None}

    def _do_stop():
        global _SLEEP_TIMER, _SLEEP_MINUTES
        _SLEEP_TIMER = None
        _SLEEP_MINUTES = None
        with contextlib.suppress(Exception):
            player_cmd("stop")

    _SLEEP_TIMER = threading.Timer(minutes * 60, _do_stop)
    _SLEEP_TIMER.daemon = True
    _SLEEP_TIMER.start()
    _SLEEP_MINUTES = minutes
    return {"ok": True, "sleep": minutes}


@app.get("/api/player")
def player():
    resp = _ts_cmd({"cmd": "status"})
    state = resp.get("state", "stop")
    if state == "stop":
        return {"state": "stop", "album_id": None, "track_index": None, "track": None,
                "elapsed": None, "duration": None, "album": None, "artist": None,
                "cover_url": None, "volume": _OAAT_VOLUME, "format": None,
                "sleep_minutes": _SLEEP_MINUTES,
                "repeat": _REPEAT, "shuffle": _SHUFFLE,
                "replaygain": _rg_enabled(),
                "device_lost": _DEVICE_LOST_NAME,
                "queue_pos": _QUEUE_POS, "queue_len": len(_QUEUE)}
    return {"state": state, "album_id": _OAAT_ALBUM_ID, "track_index": _OAAT_TRACK_INDEX,
            "track": _OAAT_TRACK_TITLE, "elapsed": resp.get("elapsed"),
            "duration": resp.get("duration") or _OAAT_DURATION,
            "album": _OAAT_ALBUM_NAME, "artist": _OAAT_ARTIST, "cover_url": _OAAT_COVER_URL,
            "volume": _OAAT_VOLUME, "format": _OAAT_FORMAT,
            "sleep_minutes": _SLEEP_MINUTES,
            "repeat": _REPEAT, "shuffle": _SHUFFLE,
            "replaygain": _rg_enabled(),
            "device_lost": _DEVICE_LOST_NAME,
            "queue_pos": _QUEUE_POS, "queue_len": len(_QUEUE)}


class RepeatReq(BaseModel):
    mode: str  # off | all | one


@app.post("/api/player/repeat")
def player_repeat(req: RepeatReq):
    global _REPEAT
    if req.mode not in ("off", "all", "one"):
        raise HTTPException(400, "режим: off | all | one")
    _REPEAT = req.mode
    _persist_player_state()
    return {"ok": True, "repeat": _REPEAT}


class ShuffleReq(BaseModel):
    on: bool


@app.post("/api/player/shuffle")
def player_shuffle(req: ShuffleReq):
    global _SHUFFLE
    _SHUFFLE = req.on
    if _SHUFFLE:
        _apply_shuffle()
    _persist_player_state()
    return {"ok": True, "shuffle": _SHUFFLE}


class ReplayGainReq(BaseModel):
    on: bool


@app.post("/api/player/replaygain")
def player_replaygain(req: ReplayGainReq):
    """вкл/выкл выравнивание громкости; при включении — фоновый замер всей библиотеки."""
    _set_setting("replaygain", "1" if req.on else "0")
    if req.on:
        threading.Thread(target=_measure_all_gains, daemon=True).start()
    return {"ok": True, "replaygain": req.on}


class EndpointSelectReq(BaseModel):
    endpoint: str | None = None
    device: str | None = None


def _ep_status(name: str) -> dict:
    """Статус конкретного endpoint'а (устройства + играет ли)."""
    web = _ep_web(name)
    try:
        r = requests.get(f"{web}/api/status", timeout=3)
        r.raise_for_status()
        d = r.json()
        result = {"current_device": d.get("current_device"),
                  "connected": d.get("connected", False),
                  "playing": d.get("playing", False),
                  "version": d.get("version"),
                  "os": d.get("os"), "arch": d.get("arch")}
    except requests.RequestException:
        return {"available_devices": [], "current_device": None,
                "connected": False, "playing": False, "error": "недоступен"}
    # Живой список устройств (/api/devices перечисляет на лету). На старых
    # endpoint'ах без этого эндпоинта — fallback на /api/status available_devices.
    try:
        rd = requests.get(f"{web}/api/devices", timeout=3)
        rd.raise_for_status()
        devices = rd.json().get("devices", [])
    except requests.RequestException:
        devices = d.get("available_devices", [])
    # прячем виртуальные/ненужные устройства (напр. Microsoft Teams Audio)
    result["available_devices"] = [
        dev for dev in devices
        if not any(marker in dev.lower() for marker in OAAT_IGNORE_DEVICES)
    ]
    return result


@app.get("/api/endpoints")
def endpoints():
    """Все endpoint'ы (.env + добавленные вручную + авто-обнаруженные mDNS) + устройства + активный."""
    active = _active_ep_name()
    cfg = _configured_endpoints()
    manual = _manual_endpoints()
    discovered = _mdns_services()
    names = list(cfg) + [n for n in discovered if n not in cfg]
    eps = []
    for name in names:
        ep = {"name": name, "control": _ep_addr(name), "active": name == active,
              "auto": name not in cfg, "manual": name in manual and name not in OAAT_ENDPOINTS}
        ep.update(_ep_status(name))
        eps.append(ep)
    return {"active": active, "endpoints": eps}


class ManualEpReq(BaseModel):
    name: str
    address: str = ""


@app.post("/api/endpoints/manual")
def endpoints_manual_add(req: ManualEpReq):
    """Добавить endpoint вручную (имя + host:port) — для сетей, где mDNS не работает."""
    name = req.name.strip()
    addr = req.address.strip()
    if not name or name in OAAT_ENDPOINTS:
        raise HTTPException(400, "пустое имя или конфликт с endpoint'ом из .env")
    m = re.match(r"^[^:\s]+:\d{1,5}$", addr)
    if not m or not (0 < int(addr.rsplit(":", 1)[1]) < 65536):
        raise HTTPException(400, "адрес в формате host:port (например 192.168.1.50:9740)")
    eps = _manual_endpoints()
    eps[name] = addr
    _set_setting("manual_endpoints", json.dumps(eps))
    return {"ok": True, "manual": eps}


@app.delete("/api/endpoints/manual")
def endpoints_manual_remove(name: str):
    eps = _manual_endpoints()
    if name not in eps:
        raise HTTPException(404, "такого endpoint'а нет среди добавленных вручную")
    del eps[name]
    _set_setting("manual_endpoints", json.dumps(eps))
    if _active_ep_name() == name:
        _set_active_ep("")  # активный сбрасывается — watchdog/select выберет доступный
    return {"ok": True, "manual": eps}


@app.post("/api/endpoints/select")
def endpoints_select(req: EndpointSelectReq):
    """Переключить активный endpoint (перезапуск ts-controller) и/или устройство на нём."""
    if (req.endpoint and req.endpoint not in _configured_endpoints()
            and req.endpoint not in _mdns_services()):
        raise HTTPException(400, f"неизвестный endpoint: {req.endpoint}")
    if req.endpoint:
        _set_active_ep(req.endpoint)
        _start_ts(_ep_addr(req.endpoint) or "")
    if req.device:
        ep_name = _active_ep_name()
        if ep_name is None:
            raise HTTPException(400, "нет доступного endpoint'а")
        # Пауза перед сменой устройства: позиция трека сохраняется в ts-controller,
        # а не обнуляется (после переключения — resume с того же места).
        _ts_cmd({"cmd": "pause"})
        try:
            r = requests.post(f"{_ep_web(ep_name)}/api/device",
                              json={"device": req.device}, headers=_ep_headers(ep_name), timeout=5)
            r.raise_for_status()
        except requests.RequestException as e:
            return JSONResponse({"detail": f"endpoint недоступен: {e}"}, status_code=502)
    return {"active": _active_ep_name(), "ok": True}


@app.post("/api/endpoints/regenerate_token")
def endpoints_regenerate_token(req: EndpointSelectReq):
    """Регенерация control_token на endpoint'е.

    Endpoint удаляет state-файл токена и выходит — супервизор (systemd/
    LaunchAgent) перезапускает его, генерируется новый токен и публикуется
    в mDNS TXT; backend подхватывает его форс-обзором через несколько секунд.
    Доступно только для авто-сгенерированных токенов (иначе endpoint отдаёт 409).
    """
    if (req.endpoint and req.endpoint not in _configured_endpoints()
            and req.endpoint not in _mdns_services()):
        raise HTTPException(400, f"неизвестный endpoint: {req.endpoint}")
    name = req.endpoint or _active_ep_name()
    if name is None:
        raise HTTPException(400, "нет доступного endpoint'а")
    if not _ep_addr(name):
        raise HTTPException(502, "endpoint недоступен")
    try:
        r = requests.post(f"{_ep_web(name)}/api/token/regenerate",
                          json={}, headers=_ep_headers(name), timeout=5)
        if r.status_code == 409:
            raise HTTPException(
                409, "токен endpoint'а задан конфигом — регенерация через API недоступна")
        r.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(502, f"endpoint недоступен: {e}") from e

    def _refresh():
        # endpoint перезапустится и перерегистрируется в mDNS с новым токеном
        time.sleep(8)
        with contextlib.suppress(Exception):
            _mdns_services(force=True)
            _EP_ADDR_CACHE.pop(name, None)

    threading.Thread(target=_refresh, daemon=True).start()
    return {"ok": True, "restart": True}


# ---------- P2.10 OTA-обновление эндпоинтов ----------
_GITHUB_REPO = os.getenv("OAAT_REPO", "druner-ai/oaat")
_UPDATE_TARGETS = {
    ("macos", "aarch64"): ("aarch64-apple-darwin", "tar.gz"),
    ("macos", "x86_64"): ("x86_64-apple-darwin", "tar.gz"),
    ("linux", "x86_64"): ("x86_64-unknown-linux-gnu", "tar.gz"),
    ("linux", "aarch64"): ("aarch64-unknown-linux-gnu", "tar.gz"),
    ("windows", "x86_64"): ("x86_64-pc-windows-msvc", "zip"),
}


def _github_headers() -> dict:
    tok = _get_setting("github_token") or os.getenv("GITHUB_TOKEN", "")
    h = {"Accept": "application/vnd.github+json", "X-GitHub-Api-Version": "2022-11-28"}
    if tok:
        h["Authorization"] = f"Bearer {tok}"
    return h


@app.get("/api/endpoints/update_check")
def endpoints_update_check():
    """Последний релиз oaat-форка (для кнопки «Обновить» в настройках)."""
    try:
        r = requests.get(
            f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest",
            headers=_github_headers(), timeout=15)
        r.raise_for_status()
        d = r.json()
        return {"ok": True, "latest": d.get("tag_name"),
                "published": d.get("published_at")}
    except (requests.RequestException, ValueError) as e:
        raise HTTPException(502, f"GitHub недоступен: {e}") from e


@app.post("/api/endpoints/update")
def endpoints_update(req: EndpointSelectReq):
    """P2.10 OTA: скачать tune-bridge под платформу endpoint'а из последнего
    релиза и передать ему (POST /api/update, JSON+base64). Endpoint меняет
    бинарь и выходит — супервизор (systemd/LaunchAgent) перезапускает новую версию."""
    name = req.endpoint or _active_ep_name()
    if name is None:
        raise HTTPException(400, "нет доступного endpoint'а")
    st = _ep_status(name)
    osname, arch = st.get("os"), st.get("arch")
    if not osname or not arch:
        raise HTTPException(
            409, "endpoint старой версии (в статусе нет os/arch) — обновите один раз вручную")
    tgt = _UPDATE_TARGETS.get((osname, arch))
    if not tgt:
        raise HTTPException(400, f"нет сборки для платформы {osname}/{arch}")
    triple, ext = tgt
    asset = f"tune-bridge-{triple}.{ext}"
    # Шорткат github.com/.../releases/latest/download/<asset> с некоторых сетей
    # (Минск) отдаёт 404 без редиректа — идём через API: releases/latest →
    # assets/{id} c Accept: application/octet-stream.
    try:
        r = requests.get(
            f"https://api.github.com/repos/{_GITHUB_REPO}/releases/latest",
            headers=_github_headers(), timeout=30)
        r.raise_for_status()
        assets = r.json().get("assets", [])
        aid = next((a["id"] for a in assets if a.get("name") == asset), None)
        if aid is None:
            raise HTTPException(502, f"в релизе нет ассета {asset}")
        r = requests.get(
            f"https://api.github.com/repos/{_GITHUB_REPO}/releases/assets/{aid}",
            headers={**_github_headers(), "Accept": "application/octet-stream"},
            timeout=300, allow_redirects=True)
        r.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(502, f"скачивание релиза не удалось: {e}") from e
    bin_name = "tune-bridge.exe" if ext == "zip" else "tune-bridge"
    bio = io.BytesIO(r.content)
    try:
        if ext == "zip":
            with zipfile.ZipFile(bio) as z:
                payload = z.read(bin_name)
        else:
            with tarfile.open(fileobj=bio, mode="r:gz") as tf:
                f = tf.extractfile(bin_name)
                if f is None:
                    raise KeyError(bin_name)
                payload = f.read()
    except (tarfile.TarError, zipfile.BadZipFile, KeyError) as e:
        raise HTTPException(502, f"в архиве нет {bin_name}: {e}") from e
    try:
        resp = requests.post(f"{_ep_web(name)}/api/update",
                             json={"b64": base64.b64encode(payload).decode()},
                             headers=_ep_headers(name), timeout=120)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise HTTPException(502, f"endpoint не принял обновление: {e}") from e
    return {"ok": True, "endpoint": name, "asset": asset, "size": len(payload)}


@app.post("/api/player/{cmd}")
def player_cmd(cmd: str):
    global _OAAT_ALBUM_ID, _OAAT_TRACK_INDEX, _OAAT_TRACK_NAME, _OAAT_TRACK_TITLE
    global _OAAT_DURATION, _OAAT_ALBUM_NAME, _OAAT_ARTIST, _OAAT_COVER_URL, _OAAT_FORMAT
    global _QUEUE, _QUEUE_POS

    if cmd == "stop":
        _ts_cmd({"cmd": "stop"})
        # stop сбрасывает очередь — иначе UI показывает «призрачную» очередь,
        # а next неожиданно продолжает с неё
        _QUEUE = []
        _QUEUE_POS = -1
        _OAAT_ALBUM_ID = None
        _OAAT_TRACK_INDEX = None
        _OAAT_TRACK_NAME = None
        _OAAT_TRACK_TITLE = None
        _OAAT_DURATION = None
        _OAAT_ALBUM_NAME = None
        _OAAT_ARTIST = None
        _OAAT_COVER_URL = None
        _OAAT_FORMAT = None
        _persist_player_state()
        return {"ok": True, "cmd": cmd}

    if cmd == "pause":
        resp = _ts_cmd({"cmd": "pause"})
        if not resp.get("ok"):
            raise HTTPException(400, "нечего ставить на паузу")
        return {"ok": True, "cmd": cmd}

    if cmd == "play":  # resume после паузы
        resp = _ts_cmd({"cmd": "resume"})
        if not resp.get("ok"):
            raise HTTPException(400, "нечего продолжать")
        return {"ok": True, "cmd": cmd}

    if cmd in ("next", "prev"):
        step = 1 if cmd == "next" else -1
        if _QUEUE_POS >= 0 and _QUEUE:
            # идём по явной очереди; repeat=all заворачивает с краёв
            npos = _QUEUE_POS + step
            if not (0 <= npos < len(_QUEUE)):
                if _REPEAT == "all":
                    npos = 0 if npos >= len(_QUEUE) else len(_QUEUE) - 1
                else:
                    return player_cmd("stop")  # конец очереди
            entry = _QUEUE[npos]
            # позицию двигаем ТОЛЬКО после успешного старта — иначе при ошибке
            # (трек ещё качается / ffmpeg упал) очередь разъезжается с плеером
            result = _start_track(entry["aid"], entry["idx"])
            _QUEUE_POS = npos
            _persist_player_state()
            return result
        # фолбэк: соседний трек текущего альбома
        if _OAAT_ALBUM_ID is None:
            raise HTTPException(400, "сначала запусти альбом")
        idx = _OAAT_TRACK_INDEX if _OAAT_TRACK_INDEX is not None else 0
        return _start_track(_OAAT_ALBUM_ID, idx + step)

    raise HTTPException(400, f"неизвестная команда: {cmd}")


class QueueReq(BaseModel):
    album_id: int
    track_index: int
    mode: str = "end"  # "now" (заменить и играть) | "next" | "end"


@app.post("/api/queue")
def queue_add(req: QueueReq):
    if req.mode == "now":
        return play_track(req.album_id, req.track_index)
    if req.mode == "next":
        _QUEUE.insert(_QUEUE_POS + 1, {"aid": req.album_id, "idx": req.track_index})
    else:  # "end"
        _QUEUE.append({"aid": req.album_id, "idx": req.track_index})
    # плеер простаивал — стартуем с первого трека, иначе добавка «осиротеет»
    # (next не работает, пока ничего не играет); сбой старта добавление не отменяет
    if _QUEUE_POS < 0 and _OAAT_ALBUM_ID is None:
        with contextlib.suppress(HTTPException):
            queue_jump(0)
    _persist_player_state()
    return {"ok": True, "queue_len": len(_QUEUE), "pos": _QUEUE_POS}


@app.post("/api/queue/jump")
def queue_jump(pos: int):
    global _QUEUE_POS
    if not (0 <= pos < len(_QUEUE)):
        raise HTTPException(400, "нет такой позиции в очереди")
    entry = _QUEUE[pos]
    # как в next/prev: позицию — только после успешного старта
    result = _start_track(entry["aid"], entry["idx"])
    _QUEUE_POS = pos
    _persist_player_state()
    return result


@app.get("/api/queue")
def queue_get():
    out = []
    for i, e in enumerate(_QUEUE):
        item = {"album_id": e["aid"], "track_index": e["idx"], "current": i == _QUEUE_POS,
                "title": f"альбом {e['aid']} #{e['idx']}",
                "artist": None, "album": None, "duration": None}
        with contextlib.suppress(Exception):
            con = db()
            row = con.execute("SELECT artist, name FROM albums WHERE id=?",
                              (e["aid"],)).fetchone()
            if row:
                item["artist"] = row["artist"]
                item["album"] = row["name"]
            # тот же порядок, что в _current_tracks (ORDER BY path), но без qbt —
            # очередь опрашивается часто, логин в qbt на каждый элемент избыточен
            t = con.execute(
                "SELECT title, name, duration FROM tracks WHERE album_id=? "
                "ORDER BY path LIMIT 1 OFFSET ?", (e["aid"], e["idx"])).fetchone()
            if t:
                item["title"] = t["title"] or _clean_track_name(t["name"])
                item["duration"] = t["duration"]
        out.append(item)
    return {"queue": out, "pos": _QUEUE_POS}


@app.delete("/api/queue")
def queue_remove(pos: int):
    """Убрать позицию из очереди. Текущий трек продолжает играть; если удалён
    он — курсор очереди встаёт на следующий (или -1 при пустой очереди)."""
    global _QUEUE_POS
    if not (0 <= pos < len(_QUEUE)):
        raise HTTPException(400, "нет такой позиции в очереди")
    _QUEUE.pop(pos)
    if pos < _QUEUE_POS:
        _QUEUE_POS -= 1
    elif pos == _QUEUE_POS:
        _QUEUE_POS = min(pos, len(_QUEUE) - 1)
    _persist_player_state()
    return {"ok": True, "queue_len": len(_QUEUE), "pos": _QUEUE_POS}


# фронт: в Docker (multi-stage) собранный React-билд в dist/, локально/в тестах — старый static/
STATIC_DIR = Path(__file__).parent / "dist"
if not STATIC_DIR.exists():
    STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
