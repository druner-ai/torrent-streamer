"""
torrent-streamer backend — связывает qbittorrent (download) + MPD (play) + каталог (SQLite).
Вход: magnet/ссылка → альбом с треками → play в MPD → управление.
"""
import contextlib
import os
import sqlite3
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
DB_PATH = os.getenv("DB_PATH", "/data/catalog.db")
AUDIO_EXT = {".flac", ".mp3", ".dsf", ".dff", ".wav", ".m4a", ".aiff", ".aif", ".ape", ".ogg", ".opus", ".alac"}

app = FastAPI(title="torrent-streamer")

# ---------- health ----------
@app.get("/api/health")
def health():
    try:
        mpd_cmd(lambda mc: mc.status())
        status, mpd = "ok", "ok"
    except Exception:  # noqa: BLE001 — health всегда отдаёт 200
        status, mpd = "degraded", "error"
    return {"status": status, "version": VERSION, "mpd": mpd}

# ---------- БД ----------
def db():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    c.execute("""CREATE TABLE IF NOT EXISTS albums(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        hash TEXT UNIQUE, name TEXT, status TEXT, added_at INTEGER)""")
    c.execute("""CREATE TABLE IF NOT EXISTS tracks(
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        album_id INTEGER, path TEXT, name TEXT, size INTEGER)""")
    c.commit()
    return c

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
        cur = c.execute("INSERT INTO albums(hash,name,status,added_at) VALUES(?,?,?,?)",
                  (h, info["name"], "downloading", int(time.time())))
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
                    "tracks": r["n"]})
    return out

@app.get("/api/albums/{aid}")
def album(aid: int):
    c = db()
    a = c.execute("SELECT * FROM albums WHERE id=?", (aid,)).fetchone()
    if not a:
        raise HTTPException(404, "альбом не найден")
    tracks = c.execute("SELECT * FROM tracks WHERE album_id=? ORDER BY path", (aid,)).fetchall()
    return {"id": a["id"], "hash": a["hash"], "name": a["name"], "status": a["status"],
            "tracks": [dict(t) for t in tracks]}

@app.post("/api/albums/{aid}/play")
def play(aid: int):
    c = db()
    a = c.execute("SELECT * FROM albums WHERE id=?", (aid,)).fetchone()
    if not a:
        raise HTTPException(404, "альбом не найден")
    tracks = c.execute("SELECT * FROM tracks WHERE album_id=? ORDER BY path", (aid,)).fetchall()
    qb = QB()
    info = qb.find_by_hash(a["hash"])
    save_dir = info["save_path"] if info else None
    if not save_dir:
        raise HTTPException(500, "не найден путь загрузки")
    # пути файлов = имя торрента + путь файла (относительно music_directory = downloads)
    def add_all(mc):
        mc.clear()
        for t in tracks:
            rel = os.path.join(info["name"], t["path"])
            mc.add(rel)
        mc.play()
    mpd_cmd(add_all)
    return {"ok": True, "tracks": len(tracks)}

@app.get("/api/player")
def player():
    def get(mc):
        st = mc.status()
        cur = mc.currentsong()
        return {"state": st.get("state"), "song": cur.get("file"), "elapsed": st.get("elapsed"),
                "duration": st.get("duration"), "volume": st.get("volume")}
    try:
        return mpd_cmd(get)
    except Exception as e:  # noqa: BLE001 — catch-all на границе API
        return {"state": "error", "error": str(e)}

@app.post("/api/player/{cmd}")
def player_cmd(cmd: str):
    def do(mc):
        if cmd == "play":
            mc.play()
        elif cmd == "pause":
            mc.pause(1)
        elif cmd == "next":
            mc.next()
        elif cmd == "prev":
            mc.previous()
        elif cmd == "stop":
            mc.stop()
        elif cmd.startswith("vol"):
            mc.setvol(int(cmd[3:]))
        else:
            raise HTTPException(400, "неизвестная команда")
    mpd_cmd(do)
    return {"ok": True, "cmd": cmd}

# фронт (путь относительно файла — работает и в Docker, и локально)
STATIC_DIR = Path(__file__).parent / "static"
app.mount("/", StaticFiles(directory=str(STATIC_DIR), html=True), name="static")
