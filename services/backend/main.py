"""
torrent-streamer backend — связывает qbittorrent (download) + MPD (play) + каталог (SQLite).
Вход: magnet/ссылка → альбом с треками → play в MPD → управление.
"""
import contextlib
import json
import os
import re
import socket
import sqlite3
import subprocess
import threading
import time
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
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
FFMPEG = os.getenv("FFMPEG", "ffmpeg")
DB_PATH = os.getenv("DB_PATH", "/data/catalog.db")
API_KEY = os.getenv("API_KEY", "")
PROWLARR_URL = os.getenv("PROWLARR_URL", "http://127.0.0.1:9696")
PROWLARR_API_KEY = os.getenv("PROWLARR_API_KEY", "")
COVER_CACHE_DIR = os.getenv("COVER_CACHE_DIR", "/data/covers")
AUDIO_EXT = {".flac", ".mp3", ".dsf", ".dff", ".wav", ".m4a", ".aiff", ".aif", ".ape", ".ogg", ".opus", ".alac"}
MUSICBRAINZ_UA = "torrent-streamer/0.1 (druner@gmail.com)"

app = FastAPI(title="torrent-streamer")

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


# ---------- MusicBrainz ----------
def _release_key(artist, name):
    """нормализованный ключ релиза: «исполнитель | название» без тегов/года/скобок."""
    key = re.sub(r"\[[^\]]*\]", " ", name or "")
    key = re.sub(r"\b(19|20)\d{2}\b", " ", key)
    key = re.sub(r"\([^)]*\)", " ", key)
    key = re.sub(r"\s+", " ", key).strip().lower()
    return f"{(artist or '').strip().lower()}|{key}"


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
            cr = requests.get(
                f"https://coverartarchive.org/release/{mbid}/front",
                headers={"User-Agent": MUSICBRAINZ_UA},
                timeout=10,
            )
            cr.raise_for_status()
            cover = cr.json()
            images = cover.get("images", [])
            if images:
                cover_url = images[0].get("image")
        return {"artist": artist, "title": title, "cover_url": cover_url}
    except Exception:  # noqa: BLE001 — обогащение не должно ронять добавление
        return None


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

@app.post("/api/add")
def add(req: AddReq):
    qb = QB()
    qb.add(req.link)
    # ждём metainfo (список файлов) до 30с
    info = None
    for _ in range(30):
        time.sleep(1)
        for t in qb.info():
            if t.get("name") and t.get("hash"):
                info = t
                break
        if info:
            break
    if not info:
        raise HTTPException(500, "не удалось получить метаданные торрента")
    h = info["hash"]
    files = qb.files(h)
    audio = [f for f in files if Path(f["name"]).suffix.lower() in AUDIO_EXT]
    if not audio:
        raise HTTPException(400, "в раздаче нет аудиофайлов")
    # альбом
    c = db()
    try:
        try:
            meta = enrich_album(info["name"])
        except Exception:  # noqa: BLE001 — обогащение необязательно
            meta = None
        artist = meta.get("artist") if meta else None
        cover_url = meta.get("cover_url") if meta else None
        cur = c.execute(
            "INSERT INTO albums(hash,name,status,added_at,artist,cover_url,release_key) VALUES(?,?,?,?,?,?,?)",
            (h, info["name"], "downloading", int(time.time()), artist, cover_url,
             _release_key(artist, info["name"])),
        )
        aid = cur.lastrowid
        for f in audio:
            c.execute("INSERT INTO tracks(album_id,path,name,size) VALUES(?,?,?,?)",
                      (aid, f["name"], Path(f["name"]).name, f["size"]))
        c.commit()
    except sqlite3.IntegrityError:
        aid = c.execute("SELECT id FROM albums WHERE hash=?", (h,)).fetchone()["id"]
    # приоритет: качать только аудио, по порядку
    qb.set_priority(h, {f["name"] for f in audio})
    return JSONResponse({"album_id": aid, "hash": h, "name": info["name"], "tracks": len(audio)})

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
    for t in tracks:
        f = files.get(t["path"])
        progress = f["progress"] if f else 0.0
        ready = bool(progress >= 1.0)
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
    meta = {"name": a["name"], "artist": a["artist"], "cover_url": a["cover_url"]}
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


def _start_track(aid, idx, offset=0.0):
    global _OAAT_ALBUM_ID, _OAAT_TRACK_INDEX, _OAAT_TRACK_NAME, _OAAT_TRACK_TITLE
    global _OAAT_DURATION, _OAAT_ALBUM_NAME, _OAAT_ARTIST, _OAAT_COVER_URL, _OAAT_FORMAT
    tracks, save_path, meta = _current_tracks(aid)
    if not (0 <= idx < len(tracks)):
        raise HTTPException(400, "нет такого трека в альбоме")
    track = tracks[idx]
    abs_path = os.path.join(save_path, track["path"])
    # ts-controller стримит только WAV → декодируем остальное (FLAC/DSD/mp3)
    # через ffmpeg в lossless PCM; перемотку делает сам контроллер (без -ss).
    if Path(abs_path).suffix.lower() != ".wav":
        stream_path = f"/tmp/oaat_stream_{aid}.wav"
        conv = subprocess.run(
            [FFMPEG, "-y", "-i", abs_path, "-f", "wav", stream_path],
            check=False, capture_output=True,
        )
        if conv.returncode != 0:
            raise HTTPException(500, f"ffmpeg не смог декодировать: {conv.stderr.decode(errors='replace')[-200:]}")
    else:
        stream_path = abs_path

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


# фронт (путь относительно файла — работает и в Docker, и локально)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
