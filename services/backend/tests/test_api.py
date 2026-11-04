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
             f"/tmp/oaat_stream_{aid}.wav"], check=False, capture_output=True)
        assert ts.call_args.args[0]["path"] == f"/tmp/oaat_stream_{aid}.wav"
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
    r = client.post("/api/queue", json={"album_id": 1, "track_index": 2, "mode": "end"})
    assert r.status_code == 200
    assert r.json()["queue_len"] == 1

    r = client.post("/api/queue", json={"album_id": 1, "track_index": 5, "mode": "next"})
    assert r.json()["queue_len"] == 2

    r = client.get("/api/queue")
    assert r.status_code == 200
    body = r.json()
    assert len(body["queue"]) == 2
    assert body["queue"][0]["track_index"] == 5  # mode=next вставил в начало (pos=-1)
    assert body["queue"][1]["track_index"] == 2
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
            for t, fid in ((100, 2277), (101, 2278), (102, 9999)):
                c.execute("INSERT INTO catalog(t,forum_id,title,size,seeders,leechers,crawled_at)"
                          " VALUES(?,?,?,?,?,?,?)", (t, fid, f"title{t}", "1GB", 1, 0, 0))
            c.commit()
        r = client.get("/api/catalog?forum_id=2277&forum_id=2278")
        assert r.status_code == 200
        ids = {x["t"] for x in r.json()["items"]}
        assert ids == {100, 101}


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


def test_catalog_tree_save_get(tmp_path):
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
