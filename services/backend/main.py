"""
torrent-streamer backend — связывает qbittorrent (download) + MPD (play) + каталог (SQLite).
Вход: magnet/ссылка → альбом с треками → play в MPD → управление.
"""
import contextlib
import os
import signal
import sqlite3
import subprocess
import time
from pathlib import Path

import requests
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from mpd import MPDClient
from pydantic import BaseModel

VERSION = "0.1.0"

# --- конфиг (env с дефолтами) ---
QBT_URL = os.getenv("QBT_URL", "http://127.0.0.1:8080")
QBT_USER = os.getenv("QBT_USER", "admin")
QBT_PASS = os.getenv("QBT_PASS", "adminadmin")
MPD_HOST = os.getenv("MPD_HOST", "127.0.0.1")
MPD_PORT = int(os.getenv("MPD_PORT", "6600"))
OAAT_TARGET = os.getenv("OAAT_TARGET", "192.168.31.215:9740")
TUNE_BRIDGE = os.getenv("TUNE_BRIDGE", "tune-bridge")
FFMPEG = os.getenv("FFMPEG", "ffmpeg")
DB_PATH = os.getenv("DB_PATH", "/data/catalog.db")
API_KEY = os.getenv("API_KEY", "")
PROWLARR_URL = os.getenv("PROWLARR_URL", "http://127.0.0.1:9696")
PROWLARR_API_KEY = os.getenv("PROWLARR_API_KEY", "")
AUDIO_EXT = {".flac", ".mp3", ".dsf", ".dff", ".wav", ".m4a", ".aiff", ".aif", ".ape", ".ogg", ".opus", ".alac"}
MUSICBRAINZ_UA = "torrent-streamer/0.1 (druner@gmail.com)"

app = FastAPI(title="torrent-streamer")

# ---------- OAAT (tune-bridge) ----------
_OAAT_PROC = None
_OAAT_ALBUM_ID = None
_OAAT_TRACK_INDEX = None
_OAAT_TRACK_NAME = None
_OAAT_PAUSED = False
_OAAT_WAV_PATH = None

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
def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS albums(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hash TEXT UNIQUE, name TEXT, status TEXT, added_at INTEGER,
        artist TEXT, cover_url TEXT)""")
    try:
        c.execute("ALTER TABLE albums ADD COLUMN artist TEXT")
    except sqlite3.OperationalError:
        pass  # колонки уже есть в существующей БД
    try:
        c.execute("ALTER TABLE albums ADD COLUMN cover_url TEXT")
    except sqlite3.OperationalError:
        pass  # колонки уже есть в существующей БД
    c.execute("""CREATE TABLE IF NOT EXISTS tracks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        album_id INTEGER, path TEXT, name TEXT, size INTEGER)""")
    c.commit()
    return c


# ---------- MusicBrainz ----------
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
            "INSERT INTO albums(hash,name,status,added_at,artist,cover_url) VALUES(?,?,?,?,?,?)",
            (h, info["name"], "downloading", int(time.time()), artist, cover_url),
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

@app.get("/api/albums")
def albums():
    c = db()
    rows = c.execute("""SELECT a.*, COUNT(t.id) n FROM albums a
                        LEFT JOIN tracks t ON t.album_id=a.id GROUP BY a.id ORDER BY a.id DESC""").fetchall()
    out = []
    qb = QB()
    for r in rows:
        info = qb.find_by_hash(r["hash"])
        status = info["state"] if info else r["status"]
        progress = info["progress"] if info else 0
        out.append({"id": r["id"], "hash": r["hash"], "name": r["name"],
                    "status": status, "progress": round(progress * 100, 1),
                    "tracks": r["n"], "artist": r["artist"], "cover_url": r["cover_url"]})
    return out

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

@app.get("/api/albums/{aid}/files")
def album_files(aid: int):
    c = db()
    a = c.execute("SELECT * FROM albums WHERE id=?", (aid,)).fetchone()
    if not a:
        raise HTTPException(404, "альбом не найден")
    tracks = c.execute("SELECT * FROM tracks WHERE album_id=? ORDER BY path", (aid,)).fetchall()
    try:
        qb = QB()
        files = {f["name"]: f for f in qb.files(a["hash"])}
    except Exception:  # noqa: BLE001 — торрент не найден / qbt недоступен
        files = {}
    out = []
    for t in tracks:
        f = files.get(t["path"])
        progress = f["progress"] if f else 0.0
        out.append({
            "name": t["name"],
            "path": t["path"],
            "size": t["size"],
            "ready": bool(progress >= 1.0),
            "progress": float(progress),
        })
    return {"id": aid, "files": out}

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
    return tracks, save_path


def _stop_oaat():
    global _OAAT_PROC, _OAAT_WAV_PATH
    if _OAAT_PROC is not None:
        if _OAAT_PROC.poll() is None:
            _OAAT_PROC.terminate()
        _OAAT_PROC = None
    if _OAAT_WAV_PATH:
        with contextlib.suppress(OSError):
            os.remove(_OAAT_WAV_PATH)
        _OAAT_WAV_PATH = None


def _start_track(aid, idx):
    global _OAAT_PROC, _OAAT_ALBUM_ID, _OAAT_TRACK_INDEX, _OAAT_TRACK_NAME, _OAAT_PAUSED, _OAAT_WAV_PATH
    tracks, save_path = _current_tracks(aid)
    if not (0 <= idx < len(tracks)):
        raise HTTPException(400, "нет такого трека в альбоме")
    _stop_oaat()
    track = tracks[idx]
    abs_path = os.path.join(save_path, track["path"])
    # tune-bridge умеет стримить только WAV → декодируем остальное (FLAC/DSD/mp3)
    # через ffmpeg в lossless PCM (bit-perfect, качество сохраняется)
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
    try:
        _OAAT_PROC = subprocess.Popen([
            TUNE_BRIDGE, "controller", "--target", OAAT_TARGET, "--file", stream_path
        ])
    except OSError as e:
        raise HTTPException(500, f"не удалось запустить tune-bridge: {e}") from e
    _OAAT_ALBUM_ID = aid
    _OAAT_TRACK_INDEX = idx
    _OAAT_TRACK_NAME = track["name"]
    _OAAT_PAUSED = False
    _OAAT_WAV_PATH = stream_path if stream_path != abs_path else None
    return {"ok": True, "album_id": aid, "track_index": idx, "file": os.path.basename(abs_path)}


@app.post("/api/albums/{aid}/play")
def play(aid: int):
    return _start_track(aid, 0)


@app.post("/api/albums/{aid}/tracks/{idx}/play")
def play_track(aid: int, idx: int):
    return _start_track(aid, idx)


@app.get("/api/player")
def player():
    if _OAAT_PROC is not None and _OAAT_PROC.poll() is None:
        state = "paused" if _OAAT_PAUSED else "playing"
        return {"state": state, "album_id": _OAAT_ALBUM_ID, "track_index": _OAAT_TRACK_INDEX,
                "track": _OAAT_TRACK_NAME}
    return {"state": "stop", "album_id": None, "track_index": None, "track": None}


@app.post("/api/player/{cmd}")
def player_cmd(cmd: str):
    global _OAAT_PAUSED, _OAAT_ALBUM_ID, _OAAT_TRACK_INDEX

    if cmd == "stop":
        _stop_oaat()
        _OAAT_ALBUM_ID = None
        _OAAT_TRACK_INDEX = None
        _OAAT_TRACK_NAME = None
        _OAAT_PAUSED = False
        return {"ok": True, "cmd": cmd}

    if cmd == "pause":
        if _OAAT_PROC is None or _OAAT_PROC.poll() is not None:
            raise HTTPException(400, "нечего ставить на паузу")
        os.kill(_OAAT_PROC.pid, signal.SIGSTOP)
        _OAAT_PAUSED = True
        return {"ok": True, "cmd": cmd}

    if cmd == "play":  # resume после паузы
        if _OAAT_PROC is None or _OAAT_PROC.poll() is not None:
            raise HTTPException(400, "нечего продолжать")
        os.kill(_OAAT_PROC.pid, signal.SIGCONT)
        _OAAT_PAUSED = False
        return {"ok": True, "cmd": cmd}

    if cmd in ("next", "prev"):
        if _OAAT_ALBUM_ID is None:
            raise HTTPException(400, "сначала запусти альбом")
        idx = _OAAT_TRACK_INDEX if _OAAT_TRACK_INDEX is not None else 0
        nxt = idx + 1 if cmd == "next" else idx - 1
        return _start_track(_OAAT_ALBUM_ID, nxt)

    raise HTTPException(400, f"неизвестная команда: {cmd}")

# фронт (путь относительно файла — работает и в Docker, и локально)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
