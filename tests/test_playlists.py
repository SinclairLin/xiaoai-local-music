from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.mina_client import MockMinaClient
from app.service import MusicService


def make_client(tmp_path: Path) -> tuple[TestClient, MockMinaClient, list[dict]]:
    (tmp_path / "one.mp3").touch()
    (tmp_path / "two.mp3").touch()
    config_dir = tmp_path / "config"
    settings = Settings(
        public_base_url="http://speaker:8123",
        music_dir=tmp_path,
        config_dir=config_dir,
        mina_device_id="device-1",
    )
    mina = MockMinaClient("device-1")
    service = MusicService(tmp_path, settings.public_base_url, mina_client=mina, device_id="device-1")
    client = TestClient(create_app(settings=settings, service=service))
    with client:
        tracks = client.get("/api/tracks").json()["tracks"]
    return client, mina, tracks


def test_playlist_crud_persists_and_preserves_order(tmp_path: Path) -> None:
    client, _, tracks = make_client(tmp_path)
    with client:
        created = client.post(
            "/api/playlists",
            json={"name": "  通勤  ", "track_ids": [tracks[1]["id"], tracks[0]["id"]]},
        )
        assert created.status_code == 200
        playlist = created.json()
        assert playlist["name"] == "通勤"
        assert [track["id"] for track in playlist["tracks"]] == [tracks[1]["id"], tracks[0]["id"]]
        playlist_id = playlist["id"]
        renamed = client.put(f"/api/playlists/{playlist_id}", json={"name": "晨间", "track_ids": [tracks[0]["id"]]})
        assert renamed.status_code == 200
        assert renamed.json()["track_ids"] == [tracks[0]["id"]]
        assert client.get(f"/api/playlists/{playlist_id}").json()["name"] == "晨间"

    # A fresh app instance reads the same config_dir-backed JSON store.
    settings = Settings(
        public_base_url="http://speaker:8123",
        music_dir=tmp_path,
        config_dir=tmp_path / "config",
        mina_device_id="device-1",
    )
    service = MusicService(tmp_path, settings.public_base_url, mina_client=MockMinaClient("device-1"), device_id="device-1")
    with TestClient(create_app(settings=settings, service=service)) as fresh:
        playlists = fresh.get("/api/playlists").json()["playlists"]
        assert [(item["name"], item["track_ids"]) for item in playlists] == [("晨间", [tracks[0]["id"]])]
        assert fresh.delete(f"/api/playlists/{playlists[0]['id']}").json()["deleted"] is True
        assert fresh.get("/api/playlists").json()["playlists"] == []


def test_playlist_validation_and_play_order_and_repeat(tmp_path: Path) -> None:
    client, mina, tracks = make_client(tmp_path)
    with client:
        assert client.post("/api/playlists", json={"name": " ", "track_ids": []}).status_code == 422
        assert client.post("/api/playlists", json={"name": "重复", "track_ids": [tracks[0]["id"], tracks[0]["id"]]}).status_code == 422
        assert client.post("/api/playlists", json={"name": "未知", "track_ids": ["missing"]}).status_code == 404
        created = client.post("/api/playlists", json={"name": "顺序", "track_ids": [item["id"] for item in tracks]}).json()
        response = client.post(
            f"/api/playlists/{created['id']}/play",
            json={"order": "sequential", "repeat": "off"},
        )
        assert response.status_code == 200
        assert response.json()["order"] == "sequential"
        assert response.json()["repeat"] == "off"
        assert response.json()["current_index"] == 0
        assert mina.calls[-1][0] == "play_by_url"
        repeated = client.post(
            f"/api/playlists/{created['id']}/play",
            json={"order": "shuffle", "repeat": "all"},
        )
        assert repeated.status_code == 200
        assert repeated.json()["order"] == "shuffle"
        assert repeated.json()["repeat"] == "all"
        assert client.post(
            f"/api/playlists/{created['id']}/play", json={"mode": "sequential"}
        ).status_code == 422


def test_empty_playlist_cannot_start(tmp_path: Path) -> None:
    client, _, _ = make_client(tmp_path)
    with client:
        created = client.post("/api/playlists", json={"name": "空歌单", "track_ids": []}).json()
        response = client.post(f"/api/playlists/{created['id']}/play", json={})
        assert response.status_code == 422


def test_deleted_track_is_reported_without_partial_play(tmp_path: Path) -> None:
    client, mina, tracks = make_client(tmp_path)
    with client:
        created = client.post("/api/playlists", json={"name": "易失", "track_ids": [tracks[0]["id"]]}).json()
        (tmp_path / "one.mp3").unlink()
        response = client.post(f"/api/playlists/{created['id']}/play", json={})
        assert response.status_code == 409
        assert not any(call[0] == "play_by_url" for call in mina.calls)


def test_corrupt_store_is_quarantined_and_service_starts(tmp_path: Path) -> None:
    config_dir = tmp_path / "config"
    config_dir.mkdir()
    (config_dir / "playlists.json").write_text("{not json", encoding="utf-8")
    client, _, tracks = make_client(tmp_path)
    with client:
        assert client.get("/api/playlists").json()["playlists"] == []
        assert client.post("/api/playlists", json={"name": "重建", "track_ids": [tracks[0]["id"]]}).status_code == 200
    backups = list(config_dir.glob("playlists.json.corrupt-*"))
    assert len(backups) == 1
    assert backups[0].read_text(encoding="utf-8") == "{not json"
