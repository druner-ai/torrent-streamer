"""
torrent-streamer backend — связывает qbittorrent (download) + MPD (play) + каталог (SQLite).
Вход: magnet/ссылка → альбом с треками → play в MPD → управление.
"""
import base64
import contextlib
import html
import json
import os
import re
import socket
import sqlite3
import subprocess
import threading
import time
import urllib.parse
from pathlib import Path
from typing import Annotated

import requests
from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import JSONResponse, Response
from fastapi.staticfiles import StaticFiles
from mpd import MPDClient
from mutagen import File as MutagenFile
from pydantic import BaseModel

VERSION = "0.1.0"

# --- конфиг (env с дефолтами) ---
QBT_URL = os.getenv("QBT_URL", "http://127.0.0.1:8080")
QBT_USER = os.getenv("QBT_USER", "admin")
QBT_PASS = os.getenv("QBT_PASS", "adminadmin")
MPD_HOST = os.getenv("MPD_HOST", "127.0.0.1")
MPD_PORT = int(os.getenv("MPD_PORT", "6600"))
OAAT_TARGET = os.getenv("OAAT_TARGET", "192.168.31.215:9740")
TS_CONTROLLER_SOCK = os.getenv("TS_CONTROLLER_SOCK", "/tmp/ts-controller.sock")
TS_CONTROLLER_BIN = os.getenv("TS_CONTROLLER_BIN", "ts-controller")


# ---------- мульти-эндпоинт: имя=кандидаты через запятую, кандидаты через | ----------
# Кандидат: "host:port" (прямой адрес) или "mdns:SUBSTR" (поиск _oaat._tcp по подстроке имени).
# Порядок кандидатов = приоритет: mDNS (LAN, дома) важнее прямого Tailscale-адреса (дорога).
# Пример: OAAT_ENDPOINTS=MacBook=mdns:MacBook|100.75.171.5:9740,NUC=127.0.0.1:9740
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


def _mdns_lookup(substr: str, timeout: float = 3.0) -> str | None:
    """Поиск OAAT-endpoint по mDNS (_oaat._tcp), чьё имя содержит substr → 'ip:port'.

    Endpoint (tune-bridge) сам регистрируется в mDNS: name=«MacBook DAC» port=9740
    service_type=_oaat._tcp. Работает только в LAN (multicast) — вне LAN вернёт None,
    и вызывающий уйдёт на прямой Tailscale-адрес.
    """
    try:
        from zeroconf import ServiceBrowser, ServiceListener, Zeroconf
    except ImportError:
        return None
    try:
        zc = Zeroconf()
    except Exception:  # noqa: BLE001 — Zeroconf кидает разные ошибки (OSError/RuntimeError) на разных системах
        return None
    found: list[str] = []

    class _Listener(ServiceListener):
        def add_service(self, _zc, type_, name):
            if substr.lower() not in name.lower():
                return
            info = _zc.get_service_info(type_, name)
            if info and info.addresses:
                raw = info.addresses[0]
                ip = socket.inet_ntoa(raw) if len(raw) == 4 else socket.inet_ntop(socket.AF_INET6, raw)
                found.append(f"{ip}:{info.port}")

        def remove_service(self, _zc, type_, name):
            return None

        def update_service(self, _zc, type_, name):
            return None

    browser = ServiceBrowser(zc, "_oaat._tcp.local.", _Listener())
    deadline = time.time() + timeout
    while time.time() < deadline and not found:
        time.sleep(0.1)
    browser.cancel()
    zc.close()
    return found[0] if found else None


OAAT_ENDPOINTS = _parse_endpoints(os.getenv("OAAT_ENDPOINTS", ""))
if not OAAT_ENDPOINTS:
    OAAT_ENDPOINTS = {"MacBook": [OAAT_TARGET]}
ACTIVE_EP_FILE = os.getenv("ACTIVE_EP_FILE", "/data/active_endpoint")
OAAT_CONTROL_TOKEN = os.getenv("OAAT_CONTROL_TOKEN", "")
# Виртуальные/ненужные аудиоустройства, которые прячем из списка (подстроки через запятую).
OAAT_IGNORE_DEVICES = [m.strip().lower() for m in os.getenv(
    "OAAT_IGNORE_DEVICES", "microsoft teams audio").split(",") if m.strip()]

# Кэш резолва адресов: name -> (expires_monotonic, "host:port"). mDNS-результат живёт
# TTL секунд, потом пересматривается (это и есть авто-обнаружение при переезде).
_EP_ADDR_CACHE: dict[str, tuple[float, str]] = {}
_EP_ADDR_TTL = 30.0


def _resolve_ep(name: str) -> str | None:
    """Пробегает кандидатов по порядку: первый живой mDNS; иначе первый прямой адрес."""
    cands = OAAT_ENDPOINTS.get(name, [])
    if not cands:
        return None
    direct = None
    for cand in cands:
        if cand.startswith("mdns:"):
            resolved = _mdns_lookup(cand[5:])
            if resolved:
                return resolved
        else:
            if direct is None:
                direct = cand
    return direct


def _ep_addr(name: str) -> str | None:
    """Актуальный 'host:port' для endpoint (с TTL-кэшем)."""
    if name not in OAAT_ENDPOINTS:
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


def _ep_headers() -> dict:
    """Bearer-токен для управляющих POST на OAAT web UI (если задан control_token)."""
    return {"Authorization": f"Bearer {OAAT_CONTROL_TOKEN}"} if OAAT_CONTROL_TOKEN else {}


def _active_ep_name() -> str:
    try:
        name = Path(ACTIVE_EP_FILE).read_text().strip()
        if name in OAAT_ENDPOINTS:
            return name
    except OSError:
        pass
    return next(iter(OAAT_ENDPOINTS))


def _set_active_ep(name: str) -> None:
    try:
        Path(ACTIVE_EP_FILE).write_text(name)
    except OSError:
        pass


# ts-controller — один процесс, держит соединение с АКТИВНЫМ endpoint'ом.
_TS_PROC = None


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
    global _TS_PROC
    _stop_ts()
    if not target:
        return
    try:
        _TS_PROC = subprocess.Popen(
            [TS_CONTROLLER_BIN, "--target", target, "--socket", TS_CONTROLLER_SOCK],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
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
            addr = _resolve_ep(name)  # форс-резолв, минуя TTL-кэш
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

@contextlib.asynccontextmanager
async def lifespan(_app):
    # ts-controller стартует с активным endpoint'ом (persist в /data/active_endpoint);
    # адрес резолвится через mDNS (LAN) с fallback на прямой (Tailscale).
    _start_ts(_ep_addr(_active_ep_name()) or "")
    _watchdog = threading.Thread(target=_ep_watchdog, daemon=True)
    _watchdog.start()
    yield
    _stop_ts()


app = FastAPI(title="torrent-streamer", lifespan=lifespan)

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
_SLEEP_TIMER = None  # таймер сна (threading.Timer или None)

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
    if FRONTEND_AUTH and request.url.path != "/api/health":
        expected = "Basic " + base64.b64encode(FRONTEND_AUTH.encode()).decode()
        if request.headers.get("Authorization") != expected:
            return JSONResponse({"detail": "авторизация требуется"}, status_code=401,
                                headers={"WWW-Authenticate": 'Basic realm="SoundVault"'})
    return await call_next(request)

# ---------- health ----------
@app.get("/api/health")
def health():
    try:
        mpd_cmd(lambda mc: mc.status())
        status, mpd = "ok", "ok"
    except Exception:  # noqa: BLE001 — health всегда отдаёт 200
        status, mpd = "degraded", "error"
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
    for col in ("artist", "cover_url", "release_key"):
        with contextlib.suppress(sqlite3.OperationalError):
            c.execute(f"ALTER TABLE albums ADD COLUMN {col} TEXT")
    c.execute("""CREATE TABLE IF NOT EXISTS tracks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        album_id INTEGER, path TEXT, name TEXT, size INTEGER)""")
    for col in ("duration REAL", "title TEXT", "format TEXT", "year TEXT", "genre TEXT"):
        with contextlib.suppress(sqlite3.OperationalError):
            c.execute(f"ALTER TABLE tracks ADD COLUMN {col}")
    c.execute("""CREATE TABLE IF NOT EXISTS favorites(
        album_id INTEGER PRIMARY KEY, added_at INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS catalog(
        t INTEGER PRIMARY KEY, forum_id INTEGER, title TEXT, size TEXT,
        seeders INTEGER, leechers INTEGER, crawled_at INTEGER)""")
    for col in ("lossless INTEGER", "year TEXT", "artist TEXT", "album TEXT",
                "format TEXT", "bitrate TEXT", "release_key TEXT", "cover_url TEXT",
                "genre TEXT"):
        with contextlib.suppress(sqlite3.OperationalError):
            c.execute(f"ALTER TABLE catalog ADD COLUMN {col}")
    with contextlib.suppress(sqlite3.OperationalError):
        c.execute("ALTER TABLE catalog ADD COLUMN last_post INTEGER")
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


# ---------- MusicBrainz ----------
def _release_key(artist, name):
    """нормализованный ключ релиза: «исполнитель | название» без тегов/года/скобок."""
    key = re.sub(r"[\[\]{}]", " ", name or "")
    key = re.sub(r"\b(19|20)\d{2}\b", " ", key)
    key = re.sub(r"\([^)]*\)", " ", key)
    key = re.sub(r"\s+", " ", key).strip().lower()
    return f"{(artist or '').strip().lower()}|{key}"


def _parse_torrent_title(title: str) -> dict:
    """RuTracker «(жанры) [медиа] Исполнитель - Альбом (год) - год, FLAC (tracks+.cue), lossless»
    → {artist, album, year, format, bitrate, lossless, genre, media}."""
    t = (title or "").replace("<wbr>", "")
    genre = media = None
    rest = t
    m = re.match(r"^\s*\(([^)]*)\)\s*(?:\[([^\]]+)\])?\s*(.*)$", t, re.DOTALL)
    if m and m.group(1) and re.search(r"[a-zа-яё]", m.group(1), re.IGNORECASE):
        genre = m.group(1).strip()
        media = (m.group(2) or "").strip() or None
        rest = m.group(3).strip()
    fmt = bitrate = None
    fm = re.search(r",\s*((?:FLAC|WavPack|WAVPack|APE|ALAC|MP3|AAC|OGG|Opus|WAV|DSD|DSF|DFF|SACD-R?|ISO|DTS|Dolby)[^,]*?)(?:,\s*(?:lossless|lossy))?\s*$",
                   rest, re.IGNORECASE)
    if fm:
        spec = fm.group(1)
        fm2 = re.match(r"(FLAC|WavPack|WAVPack|APE|ALAC|MP3|AAC|OGG|Opus|WAV|DSD|DSF|DFF|SACD|ISO|DTS)", spec, re.IGNORECASE)
        fmt = fm2.group(1) if fm2 else None
        bm = re.search(r"(24\s*/\s*(?:192|96|88\.2|88)|16\s*/\s*44\.1|16\s*/\s*44|DSD\s*(?:64|128|256)|\b(?:320|256|192|128)\s*kbps?)",
                       spec, re.IGNORECASE)
        if bm:
            bitrate = re.sub(r"\s+", "", bm.group(1))
        rest = rest[:fm.start()].rstrip(" ,;")
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
    album = re.sub(r"\s+", " ", a).strip(" -–—") or None
    lossless = 0 if fmt and fmt.upper() in ("MP3", "AAC", "OGG", "OPUS") else (1 if fmt else None)
    return {"artist": artist, "album": album, "year": year, "format": fmt,
            "bitrate": bitrate, "lossless": lossless, "genre": genre, "media": media}


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
                    cover_url = front.get("image")
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
        if meta and meta.get("cover_url"):
            with db() as c:
                c.execute("UPDATE catalog SET cover_url=? WHERE release_key=?",
                          (meta["cover_url"], r["release_key"]))
                c.commit()
            n += 1
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


def _flaresolverr_get(url: str) -> str:
    r = requests.post(f"{FLARESOLVERR_URL}/v1",
                      json={"cmd": "request.get", "url": url, "maxTimeout": 30000},
                      timeout=45)
    r.raise_for_status()
    return r.json().get("solution", {}).get("response", "") or ""


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


def crawl_forum(forum_id: int, pages: int = 3) -> list[dict]:
    rows = []
    for p in range(pages):
        url = f"{RUTRACKER_BASE}/viewforum.php?f={forum_id}&start={p * 50}"
        page = _parse_forum_page(_flaresolverr_get(url))
        if not page:
            break  # пустая/последняя страница
        for r in page:
            r["forum_id"] = forum_id
        rows += page
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


@app.get("/api/settings")
def settings():
    """логин/пароль трекеров (пароль не отдаём, только флаг)."""
    return {"rutracker_login": _rutracker_login(),
            "rutracker_password_set": bool(_rutracker_password())}


@app.post("/api/settings")
def settings_save(req: SettingsReq):
    if req.rutracker_login is not None:
        _set_setting("rutracker_login", req.rutracker_login.strip())
    if req.rutracker_password is not None and req.rutracker_password.strip():
        _set_setting("rutracker_password", req.rutracker_password.strip())
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


@app.get("/api/catalog/groups")
def catalog_groups(forum_id: Annotated[list[int] | None, Query()] = None,
                   min_seeders: int = 0, lossless: bool | None = None, q: str | None = None,
                   genre: str | None = None, sort: str = "seeders"):
    """раздачи, сгруппированные по релизу (release_key) → выбор качества (издания)."""
    sql = "SELECT * FROM catalog WHERE 1=1"
    params: list = []
    if forum_id:
        sql += f" AND forum_id IN ({','.join('?' * len(forum_id))})"
        params += forum_id
    if min_seeders > 0:
        sql += " AND seeders>=?"
        params.append(min_seeders)
    if lossless is not None:
        sql += " AND lossless=?"
        params.append(1 if lossless else 0)
    if genre:
        sql += " AND genre LIKE ?"
        params.append(f"%{genre}%")
    if q:
        sql += " AND (title LIKE ? OR artist LIKE ? OR album LIKE ?)"
        like = f"%{q}%"
        params += [like, like, like]
    with db() as c:
        rows = c.execute(sql, params).fetchall()
    groups: dict[str, dict] = {}
    for r in rows:
        rk = r["release_key"] or _release_key(r["artist"], r["title"])
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
            "seeders": r["seeders"], "leechers": r["leechers"],
            "size": r["size"], "year": r["year"], "last_post": r["last_post"] or 0})
    if sort == "new":
        out = sorted(groups.values(), key=lambda g: -(g["last_post"] or 0))
    elif sort == "year":
        out = sorted(groups.values(),
                     key=lambda g: (g["year"] or "9999",
                                    -max(e["seeders"] for e in g["editions"])))
    elif sort == "name":
        out = sorted(groups.values(), key=lambda g: (g["artist"] or g["album"] or "").lower())
    else:
        out = sorted(groups.values(), key=lambda g: -max(e["seeders"] for e in g["editions"]))
    with db() as c:
        lib = {r["release_key"] for r in c.execute(
            "SELECT DISTINCT release_key FROM albums WHERE release_key IS NOT NULL").fetchall()}
    for g in out:
        g["editions"].sort(key=lambda e: -e["seeders"])
        g["count"] = len(g["editions"])
        g["in_library"] = g["release_key"] in lib
    return {"groups": out, "count": len(out)}


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
                                       lossless,year,artist,album,format,bitrate,release_key,genre,last_post)
                   VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                   ON CONFLICT(t) DO UPDATE SET seeders=excluded.seeders,
                       leechers=excluded.leechers, lossless=excluded.lossless,
                       year=excluded.year, crawled_at=excluded.crawled_at,
                       artist=excluded.artist, album=excluded.album,
                       format=excluded.format, bitrate=excluded.bitrate,
                       release_key=excluded.release_key, genre=excluded.genre,
                       last_post=excluded.last_post""",
                (r["t"], r["forum_id"], r["title"], r["size"], r["seeders"],
                 r["leechers"], now, p["lossless"], p["year"], p["artist"],
                 p["album"], p["format"], p["bitrate"], rk, p["genre"],
                 r["last_post"]),
            )
        c.commit()
    return len(crawled)


def _run_catalog_refresh(forums: list[int], pages: int):
    _REFRESH_STATE.update(running=True, total=len(forums), done=0, crawled=0)
    try:
        for fid in forums:
            crawled = crawl_forum(fid, pages)
            _save_crawled(crawled)
            _REFRESH_STATE["crawled"] += len(crawled)
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


def _rutracker_cookies() -> list[dict]:
    """логин RuTracker через FlareSolverr → cookies (bb_session и др.)."""
    data = ("login_username=" + urllib.parse.quote(_rutracker_login())
            + "&login_password=" + urllib.parse.quote(_rutracker_password())
            + "&login=%E2%F5%EE%E4")
    r = requests.post(f"{FLARESOLVERR_URL}/v1",
                      json={"cmd": "request.post", "url": f"{RUTRACKER_BASE}/login.php",
                            "postData": data, "maxTimeout": 30000},
                      timeout=45)
    r.raise_for_status()
    return r.json().get("solution", {}).get("cookies", [])


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
    path = f"/tmp/rt-{req.t}.torrent"
    with open(path, "wb") as f:
        f.write(r.content)
    qb = QB()
    before = {t.get("hash") for t in qb.info() if t.get("hash")}
    qb.add_file(path)
    imported = _import_torrent(qb, before)
    return {"ok": True, "t": req.t, "bytes": len(r.content), **imported}


# ---------- qbittorrent ----------
class QB:
    def __init__(self):
        self.s = requests.Session()
        self._login()

    def _login(self):
        self.s.post(f"{QBT_URL}/api/v2/auth/login",
                    data={"username": QBT_USER, "password": QBT_PASS})

    def add(self, link: str):
        r = self.s.post(f"{QBT_URL}/api/v2/torrents/add",
                        data={"urls": link, "sequentialDownload": "true",
                              "firstLastPiecePrio": "true"})
        r.raise_for_status()

    def add_file(self, path: str):
        with open(path, "rb") as f:
            r = self.s.post(f"{QBT_URL}/api/v2/torrents/add",
                            data={"sequentialDownload": "true",
                                  "firstLastPiecePrio": "true"},
                            files={"torrents": (os.path.basename(path), f,
                                                "application/x-bittorrent")})
        r.raise_for_status()

    def delete(self, hash: str):
        r = self.s.post(f"{QBT_URL}/api/v2/torrents/delete",
                        data={"hashes": hash, "deleteFiles": "false"})
        r.raise_for_status()

    def info(self):
        r = self.s.get(f"{QBT_URL}/api/v2/torrents/info")
        r.raise_for_status()
        return r.json()

    def files(self, hash_: str):
        r = self.s.get(f"{QBT_URL}/api/v2/torrents/files", params={"hash": hash_})
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
        self.s.post(f"{QBT_URL}/api/v2/torrents/filePrio",
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
        self.s.post(f"{QBT_URL}/api/v2/torrents/filePrio",
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

def _register_album(h: str, name: str, files: list) -> tuple[int, list]:
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
            "INSERT INTO albums(hash,name,status,added_at,artist,cover_url,release_key) VALUES(?,?,?,?,?,?,?)",
            (h, name, "downloading", int(time.time()), artist, cover_url,
             _release_key(artist, name)),
        )
        aid = cur.lastrowid
        for f in audio:
            c.execute("INSERT INTO tracks(album_id,path,name,size) VALUES(?,?,?,?)",
                      (aid, f["name"], Path(f["name"]).name, f["size"]))
        c.commit()
    except sqlite3.IntegrityError:
        aid = c.execute("SELECT id FROM albums WHERE hash=?", (h,)).fetchone()["id"]
    return aid, audio


def _import_torrent(qb: "QB", before: set) -> dict:
    """ждёт метаданные нового торрента (hash не из `before`) и заводит альбом в библиотеку."""
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
    aid, audio = _register_album(h, info["name"], qb.files(h))
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


@app.get("/api/albums")
def albums():
    c = db()
    rows = c.execute("""SELECT a.*, COUNT(t.id) n FROM albums a
                        LEFT JOIN tracks t ON t.album_id=a.id GROUP BY a.id ORDER BY a.id DESC""").fetchall()
    out = []
    qb = QB()
    favs = {row["album_id"] for row in c.execute("SELECT album_id FROM favorites")}
    for r in rows:
        info = qb.find_by_hash(r["hash"])
        status = _torrent_state(info["state"]) if info else (r["status"] or "downloading")
        progress = info["progress"] if info else 0
        year = genre = fmt = None
        for t in c.execute("""SELECT year, genre, format FROM tracks
                              WHERE album_id=? AND (year IS NOT NULL OR genre IS NOT NULL
                                                    OR format IS NOT NULL)
                              LIMIT 1""", (r["id"],)):
            year, genre, fmt = t["year"], t["genre"], t["format"]
        release_key = r["release_key"] or _release_key(r["artist"], r["name"])
        out.append({"id": r["id"], "hash": r["hash"], "name": r["name"],
                    "status": status, "progress": round(progress * 100, 1),
                    "tracks": r["n"], "artist": r["artist"], "cover_url": r["cover_url"],
                    "year": year, "genre": genre, "format": fmt,
                    "release_key": release_key, "favorite": r["id"] in favs})
    return out

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
    if remove_torrent:
        qb = QB()
        qb.delete(a["hash"])
    c.execute("DELETE FROM tracks WHERE album_id=?", (id,))
    c.execute("DELETE FROM albums WHERE id=?", (id,))
    c.commit()
    return {"ok": True, "id": id}

def _current_tracks(aid):
    c = db()
    a = c.execute("SELECT * FROM albums WHERE id=?", (aid,)).fetchone()
    if not a:
        raise HTTPException(404, "альбом не найден")
    tracks = c.execute("SELECT * FROM tracks WHERE album_id=? ORDER BY path", (aid,)).fetchall()
    if not tracks:
        raise HTTPException(400, "в альбоме нет треков")
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
    except (OSError, ValueError) as e:
        return {"ok": False, "error": str(e)}


_DECODE_JOBS: set = set()
_DECODE_LOCK = threading.Lock()


def _wav_path(aid: int, idx: int) -> str:
    return f"/tmp/oaat_stream_{aid}_{idx}.wav"


def _predecode(aid: int, idx: int, abs_path: str):
    """фоновое декодирование FLAC/DSD/MP3 → WAV (lossless; для PCM-кодеков бит-перфект),
    чтобы play/next был мгновенным, а не ждал синхронный ffmpeg."""
    if Path(abs_path).suffix.lower() == ".wav":
        return
    wav = _wav_path(aid, idx)
    if os.path.exists(wav) and os.path.getsize(wav) > 0:
        return
    key = (aid, idx)
    with _DECODE_LOCK:
        if key in _DECODE_JOBS:
            return
        _DECODE_JOBS.add(key)

    def job():
        try:
            tmp = wav + ".part"
            conv = subprocess.run([FFMPEG, "-y", "-i", abs_path, "-f", "wav", tmp],
                                  check=False, capture_output=True)
            if conv.returncode == 0:
                os.replace(tmp, wav)
            else:
                with contextlib.suppress(OSError):
                    os.remove(tmp)
        finally:
            with _DECODE_LOCK:
                _DECODE_JOBS.discard(key)

    threading.Thread(target=job, daemon=True).start()


def _start_track(aid, idx, offset=0.0):
    global _OAAT_ALBUM_ID, _OAAT_TRACK_INDEX, _OAAT_TRACK_NAME, _OAAT_TRACK_TITLE
    global _OAAT_DURATION, _OAAT_ALBUM_NAME, _OAAT_ARTIST, _OAAT_COVER_URL, _OAAT_FORMAT
    tracks, save_path, meta = _current_tracks(aid)
    if not (0 <= idx < len(tracks)):
        raise HTTPException(400, "нет такого трека в альбоме")
    track = tracks[idx]
    # приоритизируем текущий + следующий трек (6), остальные аудио — 1 (докачаются позже),
    # не-аудио — 0: нужный файл качается первым, но весь альбом в итоге докачается.
    # best-effort: не блокирует play.
    with contextlib.suppress(Exception):  # торрент может быть ещё не найден / удалён
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
        stream_path = _wav_path(aid, idx)
        if not (os.path.exists(stream_path) and os.path.getsize(stream_path) > 0):
            # не успели пред-декодировать — декодируем синхронно (fallback)
            conv = subprocess.run(
                [FFMPEG, "-y", "-i", abs_path, "-f", "wav", stream_path],
                check=False, capture_output=True,
            )
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


@app.post("/api/albums/{aid}/play")
def play(aid: int):
    _build_queue(aid, 0)
    return _start_track(aid, 0)


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
    _ts_cmd({"cmd": "volume", "level": level})
    _OAAT_VOLUME = level
    return {"ok": True, "volume": level}


@app.post("/api/player/sleep")
def player_sleep(minutes: int):
    """Таймер сна: остановить через N минут (0 — отменить)."""
    global _SLEEP_TIMER
    if _SLEEP_TIMER:
        _SLEEP_TIMER.cancel()
        _SLEEP_TIMER = None
    if minutes <= 0:
        return {"ok": True, "sleep": None}

    def _do_stop():
        global _SLEEP_TIMER
        _SLEEP_TIMER = None
        with contextlib.suppress(Exception):
            player_cmd("stop")

    _SLEEP_TIMER = threading.Timer(minutes * 60, _do_stop)
    _SLEEP_TIMER.daemon = True
    _SLEEP_TIMER.start()
    return {"ok": True, "sleep": minutes}


@app.get("/api/player")
def player():
    resp = _ts_cmd({"cmd": "status"})
    state = resp.get("state", "stop")
    if state == "stop":
        return {"state": "stop", "album_id": None, "track_index": None, "track": None,
                "elapsed": None, "duration": None, "album": None, "artist": None,
                "cover_url": None, "volume": _OAAT_VOLUME, "format": None}
    return {"state": state, "album_id": _OAAT_ALBUM_ID, "track_index": _OAAT_TRACK_INDEX,
            "track": _OAAT_TRACK_TITLE, "elapsed": resp.get("elapsed"),
            "duration": resp.get("duration") or _OAAT_DURATION,
            "album": _OAAT_ALBUM_NAME, "artist": _OAAT_ARTIST, "cover_url": _OAAT_COVER_URL,
            "volume": _OAAT_VOLUME, "format": _OAAT_FORMAT}


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
                  "playing": d.get("playing", False)}
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
    """Все endpoint'ы + их аудио-устройства + активный."""
    active = _active_ep_name()
    eps = []
    for name in OAAT_ENDPOINTS:
        ep = {"name": name, "control": _ep_addr(name), "active": name == active}
        ep.update(_ep_status(name))
        eps.append(ep)
    return {"active": active, "endpoints": eps}


@app.post("/api/endpoints/select")
def endpoints_select(req: EndpointSelectReq):
    """Переключить активный endpoint (перезапуск ts-controller) и/или устройство на нём."""
    if req.endpoint and req.endpoint not in OAAT_ENDPOINTS:
        raise HTTPException(400, f"неизвестный endpoint: {req.endpoint}")
    if req.endpoint:
        _set_active_ep(req.endpoint)
        _start_ts(_ep_addr(req.endpoint) or "")
    if req.device:
        # Пауза перед сменой устройства: позиция трека сохраняется в ts-controller,
        # а не обнуляется (после переключения — resume с того же места).
        _ts_cmd({"cmd": "pause"})
        try:
            r = requests.post(f"{_ep_web(_active_ep_name())}/api/device",
                              json={"device": req.device}, headers=_ep_headers(), timeout=5)
            r.raise_for_status()
        except requests.RequestException as e:
            return JSONResponse({"detail": f"endpoint недоступен: {e}"}, status_code=502)
    return {"active": _active_ep_name(), "ok": True}


@app.post("/api/player/{cmd}")
def player_cmd(cmd: str):
    global _OAAT_ALBUM_ID, _OAAT_TRACK_INDEX, _OAAT_TRACK_NAME, _OAAT_TRACK_TITLE
    global _OAAT_DURATION, _OAAT_ALBUM_NAME, _OAAT_ARTIST, _OAAT_COVER_URL, _OAAT_FORMAT
    global _QUEUE_POS

    if cmd == "stop":
        _ts_cmd({"cmd": "stop"})
        _OAAT_ALBUM_ID = None
        _OAAT_TRACK_INDEX = None
        _OAAT_TRACK_NAME = None
        _OAAT_TRACK_TITLE = None
        _OAAT_DURATION = None
        _OAAT_ALBUM_NAME = None
        _OAAT_ARTIST = None
        _OAAT_COVER_URL = None
        _OAAT_FORMAT = None
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
            # идём по явной очереди
            npos = _QUEUE_POS + step
            if not (0 <= npos < len(_QUEUE)):
                return player_cmd("stop")  # конец очереди
            _QUEUE_POS = npos
            entry = _QUEUE[npos]
            return _start_track(entry["aid"], entry["idx"])
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
    return {"ok": True, "queue_len": len(_QUEUE), "pos": _QUEUE_POS}


@app.post("/api/queue/jump")
def queue_jump(pos: int):
    global _QUEUE_POS
    if not (0 <= pos < len(_QUEUE)):
        raise HTTPException(400, "нет такой позиции в очереди")
    _QUEUE_POS = pos
    entry = _QUEUE[pos]
    return _start_track(entry["aid"], entry["idx"])


@app.get("/api/queue")
def queue_get():
    out = []
    for i, e in enumerate(_QUEUE):
        title = f"альбом {e['aid']} #{e['idx']}"
        with contextlib.suppress(Exception):
            tracks, _, _ = _current_tracks(e["aid"])
            if 0 <= e["idx"] < len(tracks):
                t = tracks[e["idx"]]
                title = t["title"] or _clean_track_name(t["name"])
        out.append({"album_id": e["aid"], "track_index": e["idx"],
                    "title": title, "current": i == _QUEUE_POS})
    return {"queue": out, "pos": _QUEUE_POS}


# фронт: в Docker (multi-stage) собранный React-билд в dist/, локально/в тестах — старый static/
STATIC_DIR = Path(__file__).parent / "dist"
if not STATIC_DIR.exists():
    STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
