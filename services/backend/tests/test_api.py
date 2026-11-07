"""Базовые тесты backend без реальных MPD/qbittorrent (мокаем QB и mpd_cmd)."""
import base64
import os
import sqlite3
import subprocess

# DB_PATH до импорта main: in-memory SQLite, чтобы не трогать реальную БД.
os.environ["DB_PATH"] = ":memory:"

import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from unittest.mock import Mock, patch

import main
import pytest
import requests
from fastapi import HTTPException
from fastapi.testclient import TestClient

client = TestClient(main.app)


@pytest.fixture(autouse=True)
def _clear_groups_cache():
    """кэш /api/catalog/groups не должен протекать между тестами (разные tmp БД)."""
    main._GROUPS_CACHE.clear()
    main._DEVICE_LOST_NAME = None
    yield
    main._GROUPS_CACHE.clear()
    main._DEVICE_LOST_NAME = None


def test_albums_empty_when_no_albums_and_no_torrents():
    with patch.object(main, "QB") as QB:
        QB.return_value.info.return_value = []
        r = client.get("/api/albums")
        assert r.status_code == 200
        assert r.json() == []


def test_parse_torrent_title():
    p = main._parse_torrent_title(
        "(Bop) Duke Ellington - The Far East Suite [1967] - 2011, FLAC (tracks+.cue), lossless")
    assert p["artist"] == "Duke Ellington"
    assert p["album"] == "The Far East Suite"
    assert p["format"] == "FLAC"
    assert p["lossless"] == 1
    assert p["year"] == "1967"
    assert "Bop" in p["genre"]

    p = main._parse_torrent_title(
        "(Swing) Johnny Hodges & Wild Bill Davis - Blue Pyramid {1965} - 2000, MP3 320 kbps, lossy")
    assert p["artist"] == "Johnny Hodges & Wild Bill Davis"
    assert p["format"] == "MP3"
    assert p["lossless"] == 0
    assert p["bitrate"] == "320kbps"

    p = main._parse_torrent_title("(Gypsy) Stephane Grappelli & Stuff Smith — Violins No End — 1996, FLAC (image+.cue), lossless")
    assert p["artist"] == "Stephane Grappelli & Stuff Smith"
    assert p["album"] == "Violins No End"
    assert p["format"] == "FLAC"


def test_release_key_groups_editions():
    a = main._release_key("Stuff Smith", "Swingin' Stuff")
    b = main._release_key("Stuff Smith", "Swingin' Stuff (2005)")
    assert a == b == "stuff smith|swingin' stuff"


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
        qb.find_by_hash.return_value = {"save_path": "/downloads"}
        r = client.get(f"/api/albums/{aid}/files")
        assert r.status_code == 200
        body = r.json()
        assert body["id"] == aid
        assert body["files"] == [
            {"name": "01.flac", "title": "01", "path": "01.flac", "size": 100,
             "ready": True, "progress": 1.0, "duration": None, "format": None,
             "state": "ready", "year": None, "genre": None},
            {"name": "02.flac", "title": "02", "path": "02.flac", "size": 200,
             "ready": False, "progress": 0.5, "duration": None, "format": None,
             "state": "downloading", "year": None, "genre": None},
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
        qb.info.side_effect = [[], [{"name": "SomeTorrent", "hash": "abc123"}]]
        qb.files.return_value = [{"name": "readme.txt", "index": 0, "size": 10}]
        r = client.post("/api/add", json={"link": "magnet:?xt=urn:btih:abc123"})
        assert r.status_code == 400


def test_add_creates_album_for_audio_files():
    with patch.object(main, "QB") as QB:
        qb = QB.return_value
        qb.info.side_effect = [[], [{"name": "Great Album", "hash": "abc123"}]]
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
        qb.info.side_effect = [[], [{"name": "Great Album", "hash": "abc123"}]]
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
        qb.info.side_effect = [[], [{"name": "Great Album", "hash": "abc123"}]]
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


def test_search_returns_prowlarr_results():
    fake = Mock()
    fake.raise_for_status.return_value = None
    fake.json.return_value = [
        {
            "title": "Album A",
            "guid": "guid-a",
            "indexer": "Indexer A",
            "size": 100,
            "seeders": 5,
            "magnetUrl": "magnet:?xt=urn:btih:a",
        },
        {
            "title": "Album B",
            "guid": "https://example.com/a.torrent",
            "indexer": "Indexer B",
            "size": 200,
            "seeders": 8,
            "downloadUrl": "https://example.com/a.torrent",
        },
    ]

    with patch.object(main, "PROWLARR_API_KEY", "test-key"), patch.object(
        main.requests, "get", return_value=fake
    ) as get:
        r = client.get("/api/search", params={"q": "album"})

    assert r.status_code == 200
    assert r.json() == {
        "results": [
            {
                "title": "Album A",
                "guid": "guid-a",
                "indexer": "Indexer A",
                "size": 100,
                "seeders": 5,
                "magnet": "magnet:?xt=urn:btih:a",
            },
            {
                "title": "Album B",
                "guid": "https://example.com/a.torrent",
                "indexer": "Indexer B",
                "size": 200,
                "seeders": 8,
                "magnet": "https://example.com/a.torrent",
            },
        ]
    }
    assert get.call_args.kwargs["params"] == {"query": "album", "type": "search", "limit": 10}
    assert get.call_args.kwargs["headers"] == {"X-Api-Key": "test-key"}


def test_search_503_when_prowlarr_not_configured():
    with patch.object(main, "PROWLARR_API_KEY", ""):
        r = client.get("/api/search", params={"q": "album"})
    assert r.status_code == 503
    assert r.json()["detail"] == "Prowlarr не настроен: задай PROWLARR_API_KEY"


def _reset_oaat_state():
    main._OAAT_ALBUM_ID = None
    main._OAAT_TRACK_INDEX = None
    main._OAAT_TRACK_NAME = None
    main._OAAT_TRACK_TITLE = None
    main._OAAT_DURATION = None
    main._OAAT_ALBUM_NAME = None
    main._OAAT_ARTIST = None
    main._OAAT_COVER_URL = None
    main._OAAT_VOLUME = 100
    main._OAAT_FORMAT = None
    main._QUEUE = []
    main._QUEUE_POS = -1
    if main._SLEEP_TIMER:
        main._SLEEP_TIMER.cancel()
        main._SLEEP_TIMER = None
    main._SLEEP_MINUTES = None


def _seed_album_with_tracks(main, db_path):
    c = main.db()
    cur = c.execute("INSERT INTO albums(hash,name,status,added_at) VALUES(?,?,?,?)",
                    ("hash1", "Album", "downloading", 0))
    aid = cur.lastrowid
    c.execute("INSERT INTO tracks(album_id,path,name,size) VALUES(?,?,?,?)",
              (aid, "01.wav", "01.wav", 100))
    c.execute("INSERT INTO tracks(album_id,path,name,size) VALUES(?,?,?,?)",
              (aid, "02.wav", "02.wav", 200))
    c.commit()
    c.close()
    return aid


def test_play_streams_first_track(tmp_path):
    _reset_oaat_state()
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")), patch.object(
        main, "QB"
    ) as QB, patch.object(main, "_ts_cmd") as ts:
        aid = _seed_album_with_tracks(main, str(tmp_path / "catalog.db"))
        qb = QB.return_value
        qb.find_by_hash.return_value = {"save_path": "/downloads", "name": "Album"}
        ts.return_value = {"ok": True, "state": "playing", "duration": 100.0}

        r = client.post(f"/api/albums/{aid}/play")

        assert r.status_code == 200
        assert r.json() == {"ok": True, "album_id": aid, "track_index": 0, "file": "01.wav"}
        ts.assert_called_once_with({"cmd": "play", "path": "/downloads/01.wav",
                                    "title": "01.wav", "artist": None, "album": "Album"})
        assert main._OAAT_ALBUM_ID == aid
    _reset_oaat_state()


def test_play_track_by_index(tmp_path):
    _reset_oaat_state()
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")), patch.object(
        main, "QB"
    ) as QB, patch.object(main, "_ts_cmd") as ts:
        aid = _seed_album_with_tracks(main, str(tmp_path / "catalog.db"))
        qb = QB.return_value
        qb.find_by_hash.return_value = {"save_path": "/downloads", "name": "Album"}
        ts.return_value = {"ok": True, "state": "playing", "duration": 100.0}

        r = client.post(f"/api/albums/{aid}/tracks/1/play")

        assert r.status_code == 200
        assert r.json()["track_index"] == 1
        assert ts.call_args.args[0]["path"] == "/downloads/02.wav"
    _reset_oaat_state()


def test_play_flac_converts_via_ffmpeg(tmp_path):
    _reset_oaat_state()
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")), patch.object(
        main, "QB"
    ) as QB, patch.object(main, "FFMPEG", "ffmpeg"), patch.object(
        main.subprocess, "run"
    ) as run, patch.object(main, "_ts_cmd") as ts:
        c = main.db()
        cur = c.execute("INSERT INTO albums(hash,name,status,added_at) VALUES(?,?,?,?)",
                        ("hash1", "Album", "downloading", 0))
        aid = cur.lastrowid
        c.execute("INSERT INTO tracks(album_id,path,name,size) VALUES(?,?,?,?)",
                  (aid, "01.flac", "01.flac", 100))
        c.commit()
        c.close()

        qb = QB.return_value
        qb.find_by_hash.return_value = {"save_path": "/downloads", "name": "Album"}
        run.return_value.returncode = 0
        ts.return_value = {"ok": True, "state": "playing", "duration": 100.0}

        r = client.post(f"/api/albums/{aid}/play")

        assert r.status_code == 200
        assert r.json()["file"] == "01.flac"
        run.assert_called_once_with(
            ["ffmpeg", "-y", "-i", "/downloads/01.flac", "-f", "wav",
             f"/tmp/oaat_stream_{aid}_0.wav"], check=False, capture_output=True)
        assert ts.call_args.args[0]["path"] == f"/tmp/oaat_stream_{aid}_0.wav"
    _reset_oaat_state()


def test_stop_resets_state(tmp_path):
    _reset_oaat_state()
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")), patch.object(
        main, "QB"
    ) as QB, patch.object(main, "_ts_cmd") as ts:
        aid = _seed_album_with_tracks(main, str(tmp_path / "catalog.db"))
        qb = QB.return_value
        qb.find_by_hash.return_value = {"save_path": "/downloads", "name": "Album"}
        ts.return_value = {"ok": True, "state": "playing", "duration": 100.0}
        client.post(f"/api/albums/{aid}/play")

        r = client.post("/api/player/stop")

        assert r.status_code == 200
        assert r.json() == {"ok": True, "cmd": "stop"}
        assert ts.call_args.args[0] == {"cmd": "stop"}
        assert main._OAAT_ALBUM_ID is None
    _reset_oaat_state()


def test_pause_resume_seek_volume(tmp_path):
    _reset_oaat_state()
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")), patch.object(
        main, "QB"
    ) as QB, patch.object(main, "_ts_cmd") as ts:
        aid = _seed_album_with_tracks(main, str(tmp_path / "catalog.db"))
        qb = QB.return_value
        qb.find_by_hash.return_value = {"save_path": "/downloads", "name": "Album"}
        ts.return_value = {"ok": True, "state": "playing", "duration": 100.0}
        client.post(f"/api/albums/{aid}/play")

        r = client.post("/api/player/pause")
        assert r.status_code == 200
        assert ts.call_args.args[0] == {"cmd": "pause"}

        r = client.post("/api/player/play")
        assert r.status_code == 200
        assert ts.call_args.args[0] == {"cmd": "resume"}

        r = client.post("/api/player/seek?position=60")
        assert r.status_code == 200
        assert ts.call_args.args[0] == {"cmd": "seek", "sec": 60.0}

        r = client.post("/api/player/volume?level=75")
        assert r.status_code == 200
        assert ts.call_args.args[0] == {"cmd": "volume", "level": 75}
    _reset_oaat_state()


def test_pause_without_track_400(tmp_path):
    _reset_oaat_state()
    with patch.object(main, "_ts_cmd") as ts:
        ts.return_value = {"ok": False, "error": "нет трека"}
        r = client.post("/api/player/pause")
        assert r.status_code == 400
    _reset_oaat_state()


def test_next_prev_switches_track(tmp_path):
    _reset_oaat_state()
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")), patch.object(
        main, "QB"
    ) as QB, patch.object(main, "_ts_cmd") as ts:
        aid = _seed_album_with_tracks(main, str(tmp_path / "catalog.db"))  # 01.wav, 02.wav
        qb = QB.return_value
        qb.find_by_hash.return_value = {"save_path": "/downloads", "name": "Album"}
        ts.return_value = {"ok": True, "state": "playing", "duration": 100.0}
        client.post(f"/api/albums/{aid}/play")  # track 0 = 01.wav

        r = client.post("/api/player/next")
        assert r.status_code == 200
        assert r.json()["track_index"] == 1
        assert ts.call_args.args[0]["path"] == "/downloads/02.wav"

        r = client.post("/api/player/prev")
        assert r.status_code == 200
        assert r.json()["track_index"] == 0
        assert ts.call_args.args[0]["path"] == "/downloads/01.wav"
    _reset_oaat_state()


def test_player_state_from_status(tmp_path):
    _reset_oaat_state()
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")), patch.object(
        main, "QB"
    ) as QB, patch.object(main, "_ts_cmd") as ts:
        aid = _seed_album_with_tracks(main, str(tmp_path / "catalog.db"))
        qb = QB.return_value
        qb.find_by_hash.return_value = {"save_path": "/downloads", "name": "Album"}
        ts.side_effect = [
            {"ok": True, "state": "playing", "duration": 100.0},  # play
            {"state": "playing", "elapsed": 3.5, "duration": 100.0},  # status
        ]
        client.post(f"/api/albums/{aid}/play")

        r = client.get("/api/player")
        assert r.status_code == 200
        body = r.json()
        assert body["state"] == "playing"
        assert body["album_id"] == aid
        assert body["track_index"] == 0
        assert body["elapsed"] == 3.5
        assert body["duration"] == 100.0

        ts.side_effect = [{"state": "stop"}]
        r = client.get("/api/player")
        body = r.json()
        assert body["state"] == "stop"
        assert body["album_id"] is None
        assert body["track"] is None
    _reset_oaat_state()


def test_player_rejects_unknown_commands():
    _reset_oaat_state()
    for cmd in ["vol", "foo"]:
        r = client.post(f"/api/player/{cmd}")
        assert r.status_code == 400
    _reset_oaat_state()


def test_album_cover_embedded(tmp_path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    (dl / "01.wav").write_bytes(b"dummy")
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")), patch.object(
        main, "QB"
    ) as QB, patch.object(main, "COVER_CACHE_DIR", str(tmp_path / "covers")), patch.object(
        main, "_read_cover", return_value=(b"\xff\xd8\xff\xe0coverdata", "image/jpeg")
    ):
        aid = _seed_album_with_tracks(main, str(tmp_path / "catalog.db"))
        qb = QB.return_value
        qb.find_by_hash.return_value = {"save_path": str(dl), "name": "Album"}

        r = client.get(f"/api/albums/{aid}/cover")

        assert r.status_code == 200
        assert r.headers["content-type"] == "image/jpeg"
        assert r.content == b"\xff\xd8\xff\xe0coverdata"


def test_album_cover_404_when_no_cover(tmp_path):
    dl = tmp_path / "downloads"
    dl.mkdir()
    (dl / "01.wav").write_bytes(b"dummy")
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")), patch.object(
        main, "QB"
    ) as QB, patch.object(main, "COVER_CACHE_DIR", str(tmp_path / "covers")), patch.object(
        main, "_read_cover", return_value=None
    ):
        aid = _seed_album_with_tracks(main, str(tmp_path / "catalog.db"))
        qb = QB.return_value
        qb.find_by_hash.return_value = {"save_path": str(dl), "name": "Album"}

        r = client.get(f"/api/albums/{aid}/cover")

        assert r.status_code == 404


def test_player_volume_persists():
    _reset_oaat_state()
    with patch.object(main, "_ts_cmd") as ts:
        ts.return_value = {"ok": True, "volume": 30}
        r = client.post("/api/player/volume?level=30")
        assert r.status_code == 200
        assert r.json() == {"ok": True, "volume": 30}
        assert main._OAAT_VOLUME == 30
    _reset_oaat_state()


def test_queue_add_and_get():
    _reset_oaat_state()
    with patch.object(main, "_start_track", return_value={"ok": True}):
        r = client.post("/api/queue", json={"album_id": 1, "track_index": 2, "mode": "end"})
        assert r.status_code == 200
        assert r.json()["queue_len"] == 1
        # плеер простаивал — добавление автостартовало первый трек
        assert r.json()["pos"] == 0

        r = client.post("/api/queue", json={"album_id": 1, "track_index": 5, "mode": "next"})
        assert r.json()["queue_len"] == 2

        r = client.get("/api/queue")
        assert r.status_code == 200
        body = r.json()
        assert len(body["queue"]) == 2
        assert body["queue"][0]["track_index"] == 2  # автостарт сделал его текущим
        assert body["queue"][0]["current"] is True
        assert body["queue"][1]["track_index"] == 5  # mode=next вставил следом за текущим
    _reset_oaat_state()


def test_queue_add_idle_autostart_failure_keeps_track():
    _reset_oaat_state()
    with patch.object(main, "_start_track",
                      side_effect=HTTPException(409, "трек ещё скачивается")):
        r = client.post("/api/queue", json={"album_id": 1, "track_index": 0, "mode": "end"})
        assert r.status_code == 200      # добавление не отклоняется
        assert r.json()["queue_len"] == 1
        assert r.json()["pos"] == -1     # старт не удался — трек остался в очереди
    _reset_oaat_state()


def test_stop_clears_queue():
    _reset_oaat_state()
    with patch.object(main, "_start_track", return_value={"ok": True}), \
         patch.object(main, "_ts_cmd", return_value={"ok": True}):
        client.post("/api/queue", json={"album_id": 1, "track_index": 0, "mode": "end"})
        r = client.post("/api/player/stop")
        assert r.status_code == 200
        body = client.get("/api/queue").json()
        assert body["queue"] == []
        assert body["pos"] == -1
    _reset_oaat_state()


def test_player_volume_controller_error():
    _reset_oaat_state()
    with patch.object(main, "_ts_cmd", return_value={"ok": False, "error": "контроллер down"}):
        r = client.post("/api/player/volume?level=50")
        assert r.status_code == 500
    _reset_oaat_state()


def test_sleep_minutes_exposed_via_player():
    _reset_oaat_state()
    with patch.object(main, "_ts_cmd", return_value={"state": "stop"}):
        r = client.post("/api/player/sleep?minutes=30")
        assert r.json()["sleep"] == 30
        assert client.get("/api/player").json()["sleep_minutes"] == 30
        client.post("/api/player/sleep?minutes=0")
        assert client.get("/api/player").json()["sleep_minutes"] is None
    _reset_oaat_state()


def test_favorite_toggle(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")):
        r = client.post("/api/albums/1/favorite")
        assert r.status_code == 200
        assert r.json()["favorite"] is True
        r = client.post("/api/albums/1/favorite")
        assert r.json()["favorite"] is False


def test_player_sleep_off():
    r = client.post("/api/player/sleep?minutes=0")
    assert r.status_code == 200
    assert r.json() == {"ok": True, "sleep": None}


# ---------- трекер: фильтры, дерево, настройки, «Новое» ----------

def test_parse_index_tree():
    page = (
        '<div id="c-31" class="category">'
        '<h3 class="cat_title"><a href="index.php?c=31">Джазовая и Блюзовая музыка</a></h3>'
        '<table><tr id="f-2267"><td class="row1">'
        '<h4 class="forumlink"><a href="viewforum.php?f=2267">Зарубежный джаз</a></h4>'
        '<p class="subforums">'
        '<span class="sf_title">•<a href="viewforum.php?f=2277">Bop</a></span>'
        '<span class="sf_title">•<a href="viewforum.php?f=2278">Early Jazz</a></span>'
        '</p></td></tr>'
        '<tr id="f-2269"><td class="row1">'
        '<h4 class="forumlink"><a href="viewforum.php?f=2269">Отечественный джаз</a></h4>'
        '</td></tr></table></div><!--/category-->'
    )
    tree = main._parse_index_tree(page)
    assert len(tree) == 1
    cat = tree[0]
    assert cat["category"] == "Джазовая и Блюзовая музыка"
    assert len(cat["forums"]) == 2
    jazz = cat["forums"][0]
    assert jazz["id"] == 2267
    assert [s["id"] for s in jazz["subforums"]] == [2277, 2278]
    # группа без подфорумов
    assert cat["forums"][1]["id"] == 2269
    assert cat["forums"][1]["subforums"] == []


def test_parse_group_subforums():
    page = (
        '<h4 class="forumlink"><a href="viewforum.php?f=2277">Early Jazz, Swing (lossless)</a></h4>'
        '<h4 class="forumlink"><a href="viewforum.php?f=2278">Bop (lossless)</a></h4>'
        '<a href="viewforum.php?f=2267">Зарубежный джаз</a>'  # хлебная крошка — не h4
    )
    subs = main._parse_group_subforums(page)
    assert subs == [(2277, "Early Jazz, Swing (lossless)"), (2278, "Bop (lossless)")]


def test_parse_forum_page_last_post():
    page = (
        '<tr id="tr-1" data-topic_id="1"><td>'
        '<a id="tt-1" class="torTopic tt-text">(Bop) Duke - Album (1967) FLAC</a>'
        '<span class="seedmed"><b>8</b></span>'
        '<span class="leechmed"><b>5</b></span>'
        '<a class="dl-stub">1.2&nbsp;GB</a>'
        '<td class="vf-col-last-post"><p>2026-02-23 19:02</p></td>'
        '</tr>'
    )
    rows = main._parse_forum_page(page)
    assert len(rows) == 1
    assert rows[0]["t"] == 1
    assert rows[0]["last_post"] > 0


def test_catalog_multi_forum_filter(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")):
        with main.db() as c:
            for t, fid, album in ((100, 2277, "A"), (101, 2278, "B"), (102, 9999, "C")):
                c.execute("""INSERT INTO catalog(t,forum_id,title,size,seeders,leechers,crawled_at,
                              lossless,artist,album,release_key)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                          (t, fid, f"{album} - X (2020)", "1GB", 1, 0, 0, 1, "X", album, album.lower()))
            c.commit()
        r = client.get("/api/catalog/groups?forum_id=2277&forum_id=2278")
        assert r.status_code == 200
        albums = {g["album"] for g in r.json()["groups"]}
        assert albums == {"A", "B"}


def test_catalog_groups_sort_new(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")):
        with main.db() as c:
            # два релиза: у первого last_post старее, у второго — свежее
            c.execute("""INSERT INTO catalog(t,forum_id,title,size,seeders,leechers,crawled_at,
                          lossless,artist,album,release_key,last_post)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (1, 2277, "X - Old (2000) FLAC", "1GB", 1, 0, 0, 1, "X", "Old", "x|old", 1000))
            c.execute("""INSERT INTO catalog(t,forum_id,title,size,seeders,leechers,crawled_at,
                          lossless,artist,album,release_key,last_post)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (2, 2277, "Y - New (2024) FLAC", "1GB", 1, 0, 0, 1, "Y", "New", "y|new", 9000))
            c.commit()
        r = client.get("/api/catalog/groups?sort=new")
        assert r.status_code == 200
        groups = r.json()["groups"]
        assert [g["album"] for g in groups] == ["New", "Old"]


def test_torrent_creation_date():
    data = b"d4:infod4:name3:fooe13:creation datei1700000000ee"
    assert main._torrent_creation_date(data) == "2023-11-14"  # 1700000000 UTC
    assert main._torrent_creation_date(b"d4:name3:fooe") is None


def test_catalog_groups_reg_date_sort_and_field(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")):
        with main.db() as c:
            c.execute("""INSERT INTO catalog(t,forum_id,title,size,seeders,leechers,crawled_at,
                          lossless,artist,album,release_key,reg_date)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (1, 2277, "X - Old (2000) FLAC", "1GB", 5, 0, 0, 1, "X", "Old",
                       "x|old", "2010-05-01"))
            c.execute("""INSERT INTO catalog(t,forum_id,title,size,seeders,leechers,crawled_at,
                          lossless,artist,album,release_key,reg_date)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?,?)""",
                      (2, 2277, "Y - New (2024) FLAC", "1GB", 1, 0, 0, 1, "Y", "New",
                       "y|new", "2024-01-15"))
            c.execute("""INSERT INTO catalog(t,forum_id,title,size,seeders,leechers,crawled_at,
                          lossless,artist,album,release_key)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                      (3, 2277, "Z - Unknown (2020) FLAC", "1GB", 9, 0, 0, 1, "Z", "Unknown",
                       "z|unk"))
            c.commit()
        r = client.get("/api/catalog/groups?sort=reg")
        assert r.status_code == 200
        groups = r.json()["groups"]
        # известные даты — по убыванию, неизвестные — в конце
        assert [g["album"] for g in groups] == ["New", "Old", "Unknown"]
        by_t = {e["t"]: e for g in groups for e in g["editions"]}
        assert by_t[1]["reg_date"] == "2010-05-01"
        assert by_t[3]["reg_date"] is None


def test_settings_save_get(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")):
        r = client.get("/api/settings")
        assert r.status_code == 200
        assert r.json()["rutracker_password_set"] is False

        r = client.post("/api/settings", json={"rutracker_login": "user1",
                                               "rutracker_password": "s3cret"})
        assert r.status_code == 200

        r = client.get("/api/settings")
        body = r.json()
        assert body["rutracker_login"] == "user1"
        assert body["rutracker_password_set"] is True
        assert "s3cret" not in str(body)  # пароль наружу не отдаём


def test_settings_qbt_and_frontend_auth(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")):
        r = client.post("/api/settings", json={"qbt_url": "http://qb:8080",
                                               "qbt_user": "admin2",
                                               "qbt_password": "qp@ss",
                                               "frontend_auth": "andrei:pw123"})
        assert r.status_code == 200
        # после сохранения frontend_auth middleware требует Basic — проверяем с кредами
        creds = "Basic " + base64.b64encode(b"andrei:pw123").decode()
        body = client.get("/api/settings", headers={"Authorization": creds}).json()
        assert body["qbt_url"] == "http://qb:8080"
        assert body["qbt_user"] == "admin2"
        assert body["qbt_password_set"] is True
        assert body["frontend_auth_set"] is True
        assert "qp@ss" not in str(body) and "pw123" not in str(body)
        # QB-хелперы предпочитают settings перед env
        assert main._qbt_url() == "http://qb:8080"
        assert main._qbt_user() == "admin2"
        assert main._qbt_pass() == "qp@ss"


def test_settings_frontend_auth_bad_format(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")):
        r = client.post("/api/settings", json={"frontend_auth": "no-colon"})
        assert r.status_code == 400


def test_setup_status_reports_missing(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")), \
            patch.object(main, "RUTRACKER_LOGIN", ""), \
            patch.object(main, "RUTRACKER_PASSWORD", ""), \
            patch.object(main, "OAAT_ENDPOINTS", {}), \
            patch.object(main, "_mdns_services", return_value={}), \
            patch.object(main, "QB", side_effect=OSError("down")):
        body = client.get("/api/setup/status").json()
        assert body["configured"] is False
        assert set(body["missing"]) == {"rutracker", "endpoint", "qbittorrent"}


def test_setup_status_configured(tmp_path):
    class _QB:
        def info(self):
            return []

    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")), \
            patch.object(main, "RUTRACKER_LOGIN", "u"), \
            patch.object(main, "RUTRACKER_PASSWORD", "p"), \
            patch.object(main, "OAAT_ENDPOINTS", {"NUC": ["127.0.0.1:9740"]}), \
            patch.object(main, "QB", _QB):
        body = client.get("/api/setup/status").json()
        assert body == {"configured": True, "missing": []}


def test_catalog_tree_save_get(tmp_path):
    # авто-краулинг новых разделов запускает фоновый поток — мокаем, чтобы тест
    # не ходил живьём в FlareSolverr/MusicBrainz (утечка потока + warning)
    with patch.object(main, "_run_catalog_refresh"):
        with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")):
            with main.db() as c:
                c.execute("INSERT INTO forums(forum_id,title,parent_id,category,enabled)"
                          " VALUES(2267,'Джаз',0,'Джаз',0)")
                c.execute("INSERT INTO forums(forum_id,title,parent_id,category,enabled)"
                          " VALUES(2277,'Bop',2267,'Джаз',0)")
                c.commit()
            r = client.post("/api/catalog/tree", json={"enabled": [2277]})
            assert r.status_code == 200
            r = client.get("/api/catalog/tree")
        cats = r.json()["categories"]
        jazz = cats[0]["forums"][0]
        assert jazz["enabled"] is False          # группа-контейнер
        assert jazz["subforums"][0]["enabled"] is True  # выбран подфорум


# --- mDNS-обнаружение endpoint (multi-candidate) ---

def test_parse_endpoints_multiple_candidates():
    eps = main._parse_endpoints(
        "MacBook=mdns:MacBook|100.75.171.5:9740,NUC=127.0.0.1:9740")
    assert eps["MacBook"] == ["mdns:MacBook", "100.75.171.5:9740"]
    assert eps["NUC"] == ["127.0.0.1:9740"]


def test_resolve_ep_falls_back_to_direct_when_mdns_empty():
    with patch.object(main, "OAAT_ENDPOINTS",
                      {"MacBook": ["mdns:MacBook", "100.75.171.5:9740"]}), \
            patch.object(main, "_mdns_lookup", return_value=None):
        assert main._resolve_ep("MacBook") == "100.75.171.5:9740"


def test_resolve_ep_prefers_mdns_over_direct():
    with patch.object(main, "OAAT_ENDPOINTS",
                      {"MacBook": ["mdns:MacBook", "100.75.171.5:9740"]}), \
            patch.object(main, "_mdns_lookup", return_value="192.168.31.215:9740"):
        assert main._resolve_ep("MacBook") == "192.168.31.215:9740"


def test_ep_addr_caches_mdns_result():
    with patch.object(main, "OAAT_ENDPOINTS",
                      {"MacBook": ["mdns:MacBook", "100.75.171.5:9740"]}), \
            patch.object(main, "_mdns_lookup",
                         return_value="192.168.31.215:9740") as mdns:
        main._EP_ADDR_CACHE.clear()
        a = main._ep_addr("MacBook")
        b = main._ep_addr("MacBook")  # второй вызов из TTL-кэша, mDNS не зовётся
        assert a == b == "192.168.31.215:9740"
        assert mdns.call_count == 1


# --- P0.2: mDNS-автообнаружение новых endpoint'ов (без записи в .env) ---

def test_mdns_services_caches_browse():
    with patch.object(main, "_mdns_browse",
                      return_value=({"Pi DAC": "192.168.31.50:9740"},
                                    {"Pi DAC": "txt-token"})) as browse:
        main._MDNS_SERVICES = {}
        main._MDNS_TOKENS = {}
        main._MDNS_TS = 0.0
        a = main._mdns_services()
        b = main._mdns_services()  # в пределах TTL — второй browse не зовётся
        assert a == b == {"Pi DAC": "192.168.31.50:9740"}
        assert browse.call_count == 1
        assert main._mdns_token("Pi DAC") == "txt-token"
        # пустой browse НЕ стирает кэш сразу (защита от морганий mDNS):
        # TTL истёк, но 3*TTL ещё нет
        browse.return_value = ({}, {})
        main._MDNS_TS = main.time.monotonic() - main._MDNS_TTL - 1
        assert main._mdns_services() == {"Pi DAC": "192.168.31.50:9740"}
    main._MDNS_SERVICES = {}
    main._MDNS_TOKENS = {}
    main._MDNS_TS = 0.0


def test_resolve_ep_discovered_without_env():
    with patch.object(main, "OAAT_ENDPOINTS", {}), \
            patch.object(main, "_mdns_services",
                         return_value={"Pi DAC": "192.168.31.50:9740"}):
        assert main._resolve_ep("Pi DAC") == "192.168.31.50:9740"
        assert main._resolve_ep("Неизвестный") is None


def test_endpoints_includes_discovered():
    main._EP_ADDR_CACHE.clear()
    with patch.object(main, "OAAT_ENDPOINTS", {"NUC": ["127.0.0.1:9740"]}), \
            patch.object(main, "_mdns_services",
                         return_value={"Pi DAC": "192.168.31.50:9740"}), \
            patch.object(main, "_ep_status", return_value={"available_devices": [],
                                                           "current_device": None,
                                                           "connected": False,
                                                           "playing": False}):
        body = client.get("/api/endpoints").json()
        by_name = {e["name"]: e for e in body["endpoints"]}
        assert "NUC" in by_name and "Pi DAC" in by_name
        assert by_name["NUC"]["auto"] is False
        assert by_name["Pi DAC"]["auto"] is True
        assert by_name["Pi DAC"]["control"] == "192.168.31.50:9740"
    main._EP_ADDR_CACHE.clear()


def test_select_discovered_endpoint(tmp_path):
    main._EP_ADDR_CACHE.clear()
    with patch.object(main, "OAAT_ENDPOINTS", {"NUC": ["127.0.0.1:9740"]}), \
            patch.object(main, "_mdns_services",
                         return_value={"Pi DAC": "192.168.31.50:9740"}), \
            patch.object(main, "ACTIVE_EP_FILE", str(tmp_path / "active_ep")), \
            patch.object(main, "_start_ts") as start_ts:
        r = client.post("/api/endpoints/select", json={"endpoint": "Pi DAC"})
        assert r.status_code == 200
        assert r.json()["active"] == "Pi DAC"
        start_ts.assert_called_once_with("192.168.31.50:9740")
        assert (tmp_path / "active_ep").read_text() == "Pi DAC"
    main._EP_ADDR_CACHE.clear()


def test_mdns_token_falls_back_to_setting(tmp_path):
    with patch.object(main, "_mdns_services",
                      return_value={"Pi DAC": "192.168.31.50:9740"}), \
            patch.object(main, "_MDNS_TOKENS", {}), \
            patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")):
        assert main._mdns_token("Pi DAC") is None
        main._set_setting("ep_token:Pi DAC", "db-token")
        assert main._mdns_token("Pi DAC") == "db-token"


def test_ep_headers_prefers_per_endpoint_token():
    with patch.object(main, "_mdns_token", return_value="tok-mdns"), \
            patch.object(main, "OAAT_CONTROL_TOKEN", "tok-env"):
        assert main._ep_headers("Pi DAC")["Authorization"].endswith("tok-mdns")
    with patch.object(main, "_mdns_token", return_value=None), \
            patch.object(main, "OAAT_CONTROL_TOKEN", "tok-env"):
        assert main._ep_headers("Pi DAC")["Authorization"].endswith("tok-env")
    with patch.object(main, "_mdns_token", return_value=None), \
            patch.object(main, "OAAT_CONTROL_TOKEN", ""):
        assert main._ep_headers("Pi DAC") == {}


def test_regenerate_token_proxy():
    resp = Mock(status_code=200)
    with patch.object(main, "_mdns_services",
                      return_value={"Pi DAC": "192.168.31.50:9740"}), \
            patch.object(main, "OAAT_ENDPOINTS", {}), \
            patch.object(main, "_ep_addr", return_value="192.168.31.50:9740"), \
            patch.object(main, "_ep_web", return_value="http://192.168.31.50:9741"), \
            patch.object(main, "_ep_headers", return_value={}), \
            patch.object(main.threading, "Thread"), \
            patch.object(main.requests, "post", return_value=resp) as rp:
        r = client.post("/api/endpoints/regenerate_token", json={"endpoint": "Pi DAC"})
        assert r.status_code == 200 and r.json()["ok"] is True
        assert rp.call_args[0][0] == "http://192.168.31.50:9741/api/token/regenerate"


def test_regenerate_token_conflict_when_config_token():
    resp = Mock(status_code=409)
    with patch.object(main, "_mdns_services",
                      return_value={"Pi DAC": "192.168.31.50:9740"}), \
            patch.object(main, "OAAT_ENDPOINTS", {}), \
            patch.object(main, "_ep_addr", return_value="192.168.31.50:9740"), \
            patch.object(main, "_ep_web", return_value="http://192.168.31.50:9741"), \
            patch.object(main, "_ep_headers", return_value={}), \
            patch.object(main.requests, "post", return_value=resp):
        r = client.post("/api/endpoints/regenerate_token", json={"endpoint": "Pi DAC"})
        assert r.status_code == 409


# --- теги RuTracker: source / rip / quality ---

def test_parse_torrent_title_source_rip_quality():
    p = main._parse_torrent_title(
        "(Post-Bop, Hard Bop) [CD] McCoy Tyner - Expansions - 1968, FLAC (tracks+.cue), lossless")
    assert p["genre"] == "Post-Bop, Hard Bop"
    assert p["source"] == "CD"
    assert p["rip"] == "tracks"
    assert p["format"] == "FLAC"
    assert p["quality"] == "cd"
    assert p["lossless"] == 1


def test_parse_torrent_title_image_web_hires():
    p = main._parse_torrent_title(
        "(Smooth Jazz) [WEB] Diana Krall - Candlelit Evening - 2010, FLAC (image+.cue), lossless")
    assert p["source"] == "WEB"
    assert p["rip"] == "image"

    p = main._parse_torrent_title(
        "[TR24][OF] Massimo Farao Quintet - Lonely One - 2026 (Mainstream Jazz)")
    assert p["source"] == "WEB"
    assert p["quality"] == "hires"


def test_parse_torrent_title_sacd_mp3():
    p = main._parse_torrent_title(
        "[SACD-R][OF] The Square - Sports - 1986 (Jazz Fusion)")
    assert p["source"] == "SACD"
    assert p["quality"] == "dsd"

    p = main._parse_torrent_title(
        "(Jazz) Nina Simone - Atlantic Jazz Legends - 2026, MP3, 320 kbps")
    assert p["format"] == "MP3"
    assert p["quality"] == "lossy"
    assert p["lossless"] == 0


def test_split_genres():
    assert main._split_genres("Hard Bop, Post-Bop / Fusion") == \
        ["Hard Bop", "Post-Bop", "Fusion"]
    assert main._split_genres("Jazz, jazz, Bop") == ["Jazz", "Bop"]
    assert main._split_genres(None) == []


# ---------- обложки каталога: тамбнейлы, прокси-кэш ----------
def test_thumb_url():
    u = "https://coverartarchive.org/release/3c5f41b4/22083009552.jpg"
    assert main._thumb_url(u) == u.replace(".jpg", "-500.jpg")
    # идемпотентно: уже тамбнейл — не трогаем размер
    assert main._thumb_url(main._thumb_url(u)) == main._thumb_url(u)
    assert main._thumb_url("http://example.com/a.jpg") == "http://example.com/a.jpg"
    assert main._thumb_url(None) is None


def test_migrate_cover_thumbs(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")):
        with main.db() as c:
            c.execute("""INSERT INTO catalog(t,forum_id,title,size,seeders,leechers,
                          crawled_at,cover_url) VALUES(1,7,'x','1GB',1,0,0,?)""",
                      ("https://coverartarchive.org/release/abc/123.jpg",))
            c.commit()
        assert main._migrate_cover_thumbs() == 1
        with main.db() as c:
            u = c.execute("SELECT cover_url FROM catalog WHERE t=1").fetchone()["cover_url"]
        assert u.endswith("-500.jpg")
        assert main._migrate_cover_thumbs() == 0  # повтор — no-op


def test_cover_proxy_caches_and_whitelists(tmp_path):
    jpg = b"\xff\xd8\xff\xe0fakejpeg"
    url = "https://coverartarchive.org/release/abc/123-500.jpg"
    with patch.object(main, "COVER_CACHE_DIR", str(tmp_path)), \
         patch.object(main.requests, "get") as get:
        get.return_value = Mock(status_code=200, content=jpg,
                                raise_for_status=Mock())
        r1 = client.get(f"/api/catalog/cover?url={url}")
        r2 = client.get(f"/api/catalog/cover?url={url}")
        assert r1.status_code == 200 and r1.content == jpg
        assert r2.status_code == 200
        get.assert_called_once()  # второй запрос — из кэша, без сети
        assert list(tmp_path.glob("cat_*.jpg"))
    # чужой домен — 400
    r = client.get("/api/catalog/cover?url=https://evil.com/a.jpg")
    assert r.status_code == 400
    # HTML вместо картинки — 404
    with patch.object(main, "COVER_CACHE_DIR", str(tmp_path / "x")), \
         patch.object(main.requests, "get") as get:
        get.return_value = Mock(status_code=200, content=b"<html>404</html>",
                                raise_for_status=Mock())
        r = client.get(f"/api/catalog/cover?url={url}")
        assert r.status_code == 404


# ---------- in_library: точная связь + fuzzy-матчинг ----------
def test_key_tokens_fuzzy_match():
    a = main._key_tokens("|the who - 1975 - the who by numbers  super deluxe  (16_44)")
    b = main._key_tokens("the who|the who by numbers")
    assert main._keys_match(a, b)  # b ⊂ a после очистки от шума
    assert not main._keys_match(b, main._key_tokens("miles davis|kind of blue"))
    assert not main._keys_match(frozenset(), b)
    assert not main._keys_match(frozenset({"x"}), b)  # <2 токенов — не матч


def test_in_library_fuzzy_and_exact(tmp_path):
    def _ins(c, t, album, rk):
        c.execute("""INSERT INTO catalog(t,forum_id,title,size,seeders,leechers,
                      crawled_at,lossless,artist,album,release_key)
                     VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                  (t, 2277, f"{album} - X (2020)", "1GB", 1, 0, 0, 1, "X", album, rk))

    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")):
        with main.db() as c:
            _ins(c, 100, "The Who By Numbers", "the who|the who by numbers")
            _ins(c, 200, "Concerts", "jacques|concerts")
            # fuzzy: albums-ключ из полного имени торрента (год/издание/формат — шум)
            c.execute("""INSERT INTO albums(hash,name,status,added_at,release_key)
                         VALUES('h1','torrent','complete',0,
                                '|the who - 1975 - the who by numbers  super deluxe  (16_44)')""")
            # exact: catalog_t = 200, ключ-мусор
            c.execute("""INSERT INTO albums(hash,name,status,added_at,release_key,catalog_t)
                         VALUES('h2','torrent','complete',0,'|zzz qqq',200)""")
            c.commit()
        r = client.get("/api/catalog/groups?forum_id=2277")
        assert r.status_code == 200
        by_album = {g["album"]: g["in_library"] for g in r.json()["groups"]}
        assert by_album["The Who By Numbers"] is True   # fuzzy-матчинг
        assert by_album["Concerts"] is True             # точный catalog_t


# ---------- краулер: авто-глубина и retry ----------
def test_crawl_forum_auto_pages():
    page1 = '<a href="viewforum.php?f=7&amp;start=50">2</a><a href="viewforum.php?f=7&amp;start=100">3</a>'
    calls = []

    def fake_get(url):
        calls.append(url)
        return page1 if "start=" not in url else ""

    rows = [{"t": 1, "title": "a", "seeders": 1}, {"t": 2, "title": "b", "seeders": 1}]
    with patch.object(main, "_flaresolverr_get", side_effect=fake_get), \
         patch.object(main, "_parse_forum_page", side_effect=[rows, rows, rows]):
        out = main.crawl_forum(7, pages=0)
    # авто-глубина: max(start)=100 → 3 страницы (start 0,50,100)
    assert len(out) == 6
    assert len(calls) == 3
    assert all(r["forum_id"] == 7 for r in out)


def test_crawl_forum_retries_flaky_page():
    calls = []

    def fake_get(url):
        calls.append(url)
        return ""

    rows = [{"t": 1, "title": "a", "seeders": 1}]
    # страница 2: первый fetch пустой (сбой FlareSolverr), retry отдаёт строки
    with patch.object(main, "_flaresolverr_get", side_effect=fake_get), \
         patch.object(main, "_parse_forum_page", side_effect=[rows, [], rows, []]):
        out = main.crawl_forum(7, pages=2)
    assert len(out) == 2
    assert len(calls) == 3  # start=50 запрошен дважды (retry)


def test_flaresolverr_get_uses_session():
    with patch.object(main.requests, "post") as post:
        post.return_value = Mock(status_code=200, raise_for_status=Mock(),
                                 json=Mock(return_value={"solution": {"response": "<html>"}}))
        assert main._flaresolverr_get("https://rutracker.org/forum/viewforum.php?f=1") == "<html>"
    payload = post.call_args.kwargs["json"]
    assert payload["session"] == "soundvault"
    assert payload["session_ttl"] == 1800


def test_parse_title_album_format_tail():
    # форматный хвост не должен попадать в album (ломал группировку изданий)
    p = main._parse_torrent_title("Diana Krall - The Girl in the Other Room - APE")
    assert p["album"] == "The Girl in the Other Room"
    p = main._parse_torrent_title(
        "Jacques Loussier Trio - Mozart Piano Concertos 20/23 - .ape+.cue/ lossless")
    assert p["album"] == "Mozart Piano Concertos 20/23"
    # год в конце — по-прежнему срезается
    p = main._parse_torrent_title("Artist - Album (2020)")
    assert p["album"] == "Album"


def test_groups_pagination_and_artist_filter(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")):
        with main.db() as c:
            for i in range(5):
                c.execute("""INSERT INTO catalog(t,forum_id,title,size,seeders,leechers,
                              crawled_at,lossless,artist,album,release_key)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                          (i, 2277, f"AA - Album{i} (2020)", "1GB", 10 - i, 0, 0, 1,
                           "AA", f"Album{i}", f"aa|album{i}"))
            c.execute("""INSERT INTO catalog(t,forum_id,title,size,seeders,leechers,
                          crawled_at,lossless,artist,album,release_key)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                      (99, 2277, "BB - Other (2020)", "1GB", 5, 0, 0, 1, "BB", "Other", "bb|other"))
            c.commit()
        r = client.get("/api/catalog/groups?forum_id=2277&limit=2&offset=0")
        d = r.json()
        assert r.status_code == 200
        assert len(d["groups"]) == 2
        assert d["total"] == 6 and d["count"] == 6
        r2 = client.get("/api/catalog/groups?forum_id=2277&limit=2&offset=4")
        assert len(r2.json()["groups"]) == 2
        # фильтр по исполнителю
        r3 = client.get("/api/catalog/groups?artist=BB")
        d3 = r3.json()
        assert d3["total"] == 1
        assert d3["groups"][0]["artist"] == "BB"
        # limit=0 (дефолт) — отдаёт всё, как раньше
        assert len(client.get("/api/catalog/groups?forum_id=2277").json()["groups"]) == 6


def test_catalog_artists_endpoint(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")):
        with main.db() as c:
            rows = [("Miles Davis", "Kind of Blue", "miles davis|kind of blue", 20),
                    ("Miles Davis", "Birth of the Cool", "miles davis|birth of the cool", 15),
                    ("Miles Davis", "Kind of Blue", "miles davis|kind of blue", 10),
                    ("John Coltrane", "A Love Supreme", "john coltrane|a love supreme", 30)]
            for i, (ar, al, rk, sd) in enumerate(rows):
                c.execute("""INSERT INTO catalog(t,forum_id,title,size,seeders,leechers,
                              crawled_at,lossless,artist,album,release_key)
                             VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                          (i, 2277, f"{ar} - {al}", "1GB", sd, 0, 0, 1, ar, al, rk))
            c.commit()
        r = client.get("/api/catalog/artists")
        d = r.json()
        assert r.status_code == 200
        assert d["total"] == 2
        miles = next(a for a in d["artists"] if a["artist"] == "Miles Davis")
        assert miles["albums"] == 2 and miles["torrents"] == 3 and miles["seeders"] == 20
        r2 = client.get("/api/catalog/artists?q=coltrane")
        assert r2.json()["total"] == 1
        r3 = client.get("/api/catalog/artists?letter=j")
        assert [a["artist"] for a in r3.json()["artists"]] == ["John Coltrane"]


# ---------- плеер: repeat/shuffle, автопереход, персистенция ----------
def test_repeat_shuffle_endpoints(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")):
        r = client.post("/api/player/repeat", json={"mode": "all"})
        assert r.status_code == 200 and r.json()["repeat"] == "all"
        r = client.post("/api/player/repeat", json={"mode": "bogus"})
        assert r.status_code == 400
        r = client.post("/api/player/shuffle", json={"on": True})
        assert r.status_code == 200 and r.json()["shuffle"] is True
        with patch.object(main, "_ts_cmd", return_value={"state": "stop"}):
            p = client.get("/api/player").json()
        assert p["repeat"] == "all" and p["shuffle"] is True
        # возврат к дефолтам, чтобы не влиять на другие тесты
        client.post("/api/player/repeat", json={"mode": "off"})
        client.post("/api/player/shuffle", json={"on": False})


def test_auto_advance_modes():
    started = []
    with patch.object(main, "_start_track", side_effect=lambda a, i: started.append((a, i))), \
         patch.object(main, "_persist_player_state"):
        q = [{"aid": 1, "idx": 0}, {"aid": 1, "idx": 1}, {"aid": 1, "idx": 2}]
        # обычный next
        with patch.object(main, "_QUEUE", q), patch.object(main, "_QUEUE_POS", 0), \
             patch.object(main, "_REPEAT", "off"):
            main._auto_advance()
            assert started[-1] == (1, 1) and main._QUEUE_POS == 1
        # конец очереди, repeat off — стоим
        with patch.object(main, "_QUEUE", q), patch.object(main, "_QUEUE_POS", 2), \
             patch.object(main, "_REPEAT", "off"):
            main._auto_advance()
            assert started[-1] == (1, 1) and main._QUEUE_POS == 2
        # конец очереди, repeat all — заворот на начало
        with patch.object(main, "_QUEUE", q), patch.object(main, "_QUEUE_POS", 2), \
             patch.object(main, "_REPEAT", "all"), patch.object(main, "_SHUFFLE", False):
            main._auto_advance()
            assert started[-1] == (1, 0) and main._QUEUE_POS == 0
        # repeat one — тот же трек
        with patch.object(main, "_QUEUE", q), patch.object(main, "_QUEUE_POS", 1), \
             patch.object(main, "_REPEAT", "one"):
            main._auto_advance()
            assert started[-1] == (1, 1) and main._QUEUE_POS == 1


def test_apply_shuffle_keeps_current_first():
    q = [{"aid": 1, "idx": i} for i in range(10)]
    with patch.object(main, "_QUEUE", q), patch.object(main, "_QUEUE_POS", 4):
        main._apply_shuffle()
        assert main._QUEUE[0] == {"aid": 1, "idx": 4}
        assert main._QUEUE_POS == 0
        assert sorted(e["idx"] for e in main._QUEUE) == list(range(10))


def test_player_state_persistence_roundtrip(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")):
        q = [{"aid": 7, "idx": 2}, {"aid": 7, "idx": 3}]
        with patch.object(main, "_QUEUE", q), patch.object(main, "_QUEUE_POS", 1), \
             patch.object(main, "_REPEAT", "all"), patch.object(main, "_SHUFFLE", True):
            main._persist_player_state()
        with patch.object(main, "_QUEUE", []), patch.object(main, "_QUEUE_POS", -1), \
             patch.object(main, "_REPEAT", "off"), patch.object(main, "_SHUFFLE", False):
            main._restore_player_state()
            assert main._QUEUE == q
            assert main._QUEUE_POS == 1
            assert main._REPEAT == "all" and main._SHUFFLE is True


def test_measure_gain_parses_and_clamps():
    stderr = b"[Parsed_ebur128_0] Summary:\n  Integrated loudness:\n    I:         -23.0 LUFS\n"
    fake = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=stderr)
    with patch.object(main.subprocess, "run", return_value=fake):
        g = main._measure_gain("/nonexistent.flac")
    assert g is not None and abs(g - 5.0) < 0.01  # -18 - (-23)
    stderr_quiet = b"    I:         -45.0 LUFS\n"
    fake2 = subprocess.CompletedProcess(args=[], returncode=0, stdout=b"", stderr=stderr_quiet)
    with patch.object(main.subprocess, "run", return_value=fake2):
        g2 = main._measure_gain("/nonexistent.flac")
    assert g2 == main._RG_CLAMP_DB  # clamp +12
    fake3 = subprocess.CompletedProcess(args=[], returncode=1, stdout=b"", stderr=b"error")
    with patch.object(main.subprocess, "run", return_value=fake3):
        assert main._measure_gain("/nonexistent.flac") is None


def test_replaygain_endpoint(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")), \
         patch.object(main, "_measure_all_gains"):  # не запускаем фоновый замер в тесте
        r = client.post("/api/player/replaygain", json={"on": True})
        assert r.status_code == 200 and r.json()["replaygain"] is True
        with patch.object(main, "_ts_cmd", return_value={"state": "stop"}):
            assert client.get("/api/player").json()["replaygain"] is True
        r = client.post("/api/player/replaygain", json={"on": False})
        assert r.json()["replaygain"] is False
        with patch.object(main, "_ts_cmd", return_value={"state": "stop"}):
            assert client.get("/api/player").json()["replaygain"] is False


def test_wav_path_rg_variant():
    assert main._wav_path(3, 1) == "/tmp/oaat_stream_3_1.wav"
    assert main._wav_path(3, 1, rg=True) == "/tmp/oaat_stream_3_1_rg.wav"


def test_groups_cache_hit_and_clear(tmp_path):
    with patch.object(main, "DB_PATH", str(tmp_path / "catalog.db")):
        with main.db() as c:
            c.execute("""INSERT INTO catalog(t,forum_id,title,size,seeders,leechers,crawled_at,
                          lossless,artist,album,release_key)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                      (1, 2277, "X - A (2000) FLAC", "1GB", 5, 0, 0, 1, "X", "A", "x|a"))
            c.commit()
        r1 = client.get("/api/catalog/groups?limit=10")
        assert r1.json()["total"] == 1
        assert len(main._GROUPS_CACHE) == 1
        # вторая строка в БД — закэшированный ответ её ещё не видит
        with main.db() as c:
            c.execute("""INSERT INTO catalog(t,forum_id,title,size,seeders,leechers,crawled_at,
                          lossless,artist,album,release_key)
                         VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
                      (2, 2277, "Y - B (2001) FLAC", "1GB", 3, 0, 0, 1, "Y", "B", "y|b"))
            c.commit()
        assert client.get("/api/catalog/groups?limit=10").json()["total"] == 1
        main._GROUPS_CACHE.clear()  # как _save_crawled после краула
        assert client.get("/api/catalog/groups?limit=10").json()["total"] == 2


def test_device_watch_pauses_when_device_gone():
    """P1.7: текущее устройство пропало из списка → авто-пауза."""
    with patch.object(main, "_active_ep_name", return_value="mac"), \
         patch.object(main, "_ep_status", return_value={
             "current_device": "USB DAC", "available_devices": ["Speakers"],
             "connected": True, "playing": True}), \
         patch.object(main, "player_cmd") as pc:
        main._device_watch("playing")
        assert main._DEVICE_LOST_NAME == "USB DAC"
        pc.assert_called_once_with("pause")


def test_device_watch_resumes_and_reselects_when_device_back():
    """P1.7: устройство вернулось → переселект (endpoint отвалился в default) + resume."""
    with patch.object(main, "_active_ep_name", return_value="mac"), \
         patch.object(main, "_ep_status", return_value={
             "current_device": "Speakers",
             "available_devices": ["Speakers", "USB DAC"],
             "connected": True, "playing": False}), \
         patch.object(main, "player_cmd") as pc, \
         patch.object(main.requests, "post") as rp, \
         patch.object(main.time, "sleep"):
        main._DEVICE_LOST_NAME = "USB DAC"
        main._device_watch("paused")
        assert main._DEVICE_LOST_NAME is None
        pc.assert_called_once_with("resume")
        rp.assert_called_once()  # POST /api/device {"device": "USB DAC"}


def test_device_watch_no_pause_when_device_present():
    with patch.object(main, "_active_ep_name", return_value="mac"), \
         patch.object(main, "_ep_status", return_value={
             "current_device": "USB DAC", "available_devices": ["USB DAC"],
             "connected": True, "playing": True}), \
         patch.object(main, "player_cmd") as pc:
        main._device_watch("playing")
        assert main._DEVICE_LOST_NAME is None
        pc.assert_not_called()


def test_device_watch_manual_resume_not_hijacked():
    """пользователь вручную возобновил при пропавшем устройстве → не мешаем."""
    main._DEVICE_LOST_NAME = "USB DAC"
    main._device_watch("playing")
    assert main._DEVICE_LOST_NAME is None


def test_device_watch_stop_clears_lost():
    main._DEVICE_LOST_NAME = "USB DAC"
    main._device_watch("stop")
    assert main._DEVICE_LOST_NAME is None
