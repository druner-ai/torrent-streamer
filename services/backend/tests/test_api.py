"""Базовые тесты backend без реальных MPD/qbittorrent (мокаем QB и mpd_cmd)."""
import os
import sqlite3

# DB_PATH до импорта main: in-memory SQLite, чтобы не трогать реальную БД.
os.environ["DB_PATH"] = ":memory:"

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import Mock, patch

import main
import requests
from fastapi.testclient import TestClient

client = TestClient(main.app)


def test_albums_empty_when_no_albums_and_no_torrents():
    with patch.object(main, "QB") as QB:
        QB.return_value.info.return_value = []
        r = client.get("/api/albums")
        assert r.status_code == 200
        assert r.json() == []


def test_album_404():
    r = client.get("/api/albums/999")
    assert r.status_code == 404


def test_album_files_ready_progress(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")), patch.object(main, "QB") as QB:
        c = main.db()
        cur = c.execute("INSERT INTO albums(hash,name,status,added_at) VALUES(?,?,?,?)",
                        ("hash1", "Album", "downloading", 0))
        aid = cur.lastrowid
        c.execute("INSERT INTO tracks(album_id,path,name,size) VALUES(?,?,?,?)",
                  (aid, "01.flac", "01.flac", 100))
        c.execute("INSERT INTO tracks(album_id,path,name,size) VALUES(?,?,?,?)",
                  (aid, "02.flac", "02.flac", 200))
        c.commit()
        c.close()

        qb = QB.return_value
        qb.files.return_value = [
            {"name": "01.flac", "index": 0, "size": 100, "progress": 1.0, "priority": 6},
            {"name": "02.flac", "index": 1, "size": 200, "progress": 0.5, "priority": 6},
        ]
        r = client.get(f"/api/albums/{aid}/files")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == aid
        assert body["files"] == [
            {"name": "01.flac", "path": "01.flac", "size": 100, "ready": True, "progress": 1.0},
            {"name": "02.flac", "path": "02.flac", "size": 200, "ready": False, "progress": 0.5},
        ]


def test_album_files_404():
    r = client.get("/api/albums/999/files")
    assert r.status_code == 404


def test_health_ok_when_mpd_available():
    with patch.object(main, "mpd_cmd") as mpd_cmd:
        mpd_cmd.return_value = {"state": "stop"}
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_health_degraded_when_mpd_fails():
    with patch.object(main, "mpd_cmd") as mpd_cmd:
        mpd_cmd.side_effect = Exception("mpd unavailable")
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "degraded"


def test_add_rejects_when_no_audio_files():
    with patch.object(main, "QB") as QB:
        qb = QB.return_value
        qb.info.return_value = [{"name": "SomeTorrent", "hash": "abc123"}]
        qb.files.return_value = [{"name": "readme.txt", "index": 0, "size": 10}]
        r = client.post("/api/add", json={"link": "magnet:?xt=urn:btih:abc123"})
        assert r.status_code == 400


def test_add_creates_album_for_audio_files():
    with patch.object(main, "QB") as QB:
        qb = QB.return_value
        qb.info.return_value = [{"name": "Great Album", "hash": "abc123"}]
        qb.files.return_value = [
            {"name": "01.flac", "index": 0, "size": 100},
            {"name": "02.flac", "index": 1, "size": 200},
        ]
        r = client.post("/api/add", json={"link": "magnet:?xt=urn:btih:abc123"})
        assert r.status_code == 200
        body = r.json()
        assert body["tracks"] == 2
        assert body["name"] == "Great Album"


def test_enrich_album_fetches_artist_and_cover_url():
    mb = Mock()
    mb.raise_for_status.return_value = None
    mb.json.return_value = {
        "releases": [
            {
                "id": "mbid-123",
                "title": "Album Title",
                "artist-credit": [{"name": "Artist Name"}],
            }
        ]
    }
    cover = Mock()
    cover.raise_for_status.return_value = None
    cover.json.return_value = {"images": [{"image": "https://example.com/front.jpg"}]}

    with patch("main.requests.get", side_effect=[mb, cover]) as get:
        result = main.enrich_album("Artist Name — Album Title")

    assert result == {
        "artist": "Artist Name",
        "title": "Album Title",
        "cover_url": "https://example.com/front.jpg",
    }
    assert get.call_count == 2


def test_add_does_not_crash_when_enrich_fails(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")), patch.object(main, "QB") as QB, patch(
        "main.requests.get", side_effect=requests.exceptions.ConnectionError("boom")
    ):
        qb = QB.return_value
        qb.info.return_value = [{"name": "Great Album", "hash": "abc123"}]
        qb.files.return_value = [{"name": "01.flac", "index": 0, "size": 100}]
        r = client.post("/api/add", json={"link": "magnet:?xt=urn:btih:abc123"})
        assert r.status_code == 200
        assert r.json()["name"] == "Great Album"

        conn = sqlite3.connect(str(tmp_path / "catalog.db"))
        conn.row_factory = sqlite3.Row
        row = conn.execute("SELECT artist, cover_url FROM albums WHERE hash=?", ("abc123",)).fetchone()
        assert row["artist"] is None
        assert row["cover_url"] is None
        conn.close()


def test_delete_album_removes_album(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")), patch.object(main, "QB") as QB:
        qb = QB.return_value
        qb.info.return_value = [{"name": "Great Album", "hash": "abc123"}]
        qb.files.return_value = [
            {"name": "01.flac", "index": 0, "size": 100},
            {"name": "02.flac", "index": 1, "size": 200},
        ]
        r = client.post("/api/add", json={"link": "magnet:?xt=urn:btih:abc123"})
        assert r.status_code == 200
        album_id = r.json()["album_id"]

        r = client.delete(f"/api/albums/{album_id}")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "id": album_id}

        r = client.get("/api/albums")
        assert r.status_code == 200
        assert album_id not in [a["id"] for a in r.json()]


def test_delete_album_404():
    r = client.delete("/api/albums/999")
    assert r.status_code == 404


def test_api_key_required_for_albums():
    with patch.object(main, "API_KEY", "secret"), patch.object(main, "QB") as QB:
        QB.return_value.info.return_value = []

        r = client.get("/api/albums")
        assert r.status_code == 401
        assert r.json() == {"detail": "неверный API-ключ"}

        r = client.get("/api/albums", headers={"X-API-Key": "secret"})
        assert r.status_code == 200
        assert r.json() == []


def test_health_open_when_api_key_set():
    with patch.object(main, "API_KEY", "secret"), patch.object(main, "mpd_cmd") as mpd_cmd:
        mpd_cmd.return_value = {"state": "stop"}
        r = client.get("/api/health")
        assert r.status_code == 200
        assert r.json()["status"] == "ok"


def test_api_key_disabled_allows_albums_without_header():
    with patch.object(main, "API_KEY", ""), patch.object(main, "QB") as QB:
        QB.return_value.info.return_value = []
        r = client.get("/api/albums")
        assert r.status_code == 200
