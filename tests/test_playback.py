from pathlib import Path

from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.mina_client import MockMinaClient
from app.service import MusicService


def make_client(tmp_path: Path) -> tuple[TestClient, MockMinaClient]:
    (tmp_path / "one.mp3").touch()
    (tmp_path / "two.mp3").touch()
    settings = Settings(public_base_url="http://speaker:8123", music_dir=tmp_path, mina_device_id="device-1")
    mina = MockMinaClient("device-1")
    service = MusicService(tmp_path, settings.public_base_url, mina_client=mina, device_id="device-1")
    return TestClient(create_app(settings=settings, service=service)), mina


def test_play_calls_play_by_url_and_queue_controls_update_state(tmp_path: Path) -> None:
    client, mina = make_client(tmp_path)
    with client:
        tracks = client.get("/api/tracks").json()["tracks"]
        response = client.post("/api/play", json={"track_id": tracks[0]["id"], "queue_ids": [item["id"] for item in tracks]})
        assert response.status_code == 200
        assert mina.calls[-1] == ("play_by_url", (tracks[0]["path"], "device-1"))
        assert response.json()["current"]["id"] == tracks[0]["id"]
        assert client.post("/api/next").json()["current"]["id"] == tracks[1]["id"]
        assert client.post("/api/previous").json()["current"]["id"] == tracks[0]["id"]
        assert client.post("/api/stop").json()["state"] == "stopped"


def test_unknown_queue_item_does_not_replace_existing_state(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        tracks = client.get("/api/tracks").json()["tracks"]
        client.post("/api/play", json={"track_id": tracks[0]["id"]})
        response = client.post("/api/play", json={"track_id": tracks[0]["id"], "queue_ids": ["missing"]})
        assert response.status_code == 400
        assert client.get("/api/queue").json()["current"]["id"] == tracks[0]["id"]
