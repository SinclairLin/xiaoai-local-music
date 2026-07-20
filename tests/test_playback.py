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


def test_play_defaults_to_sequential_order_and_no_repeat(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        tracks = client.get("/api/tracks").json()["tracks"]
        single = client.post("/api/play", json={"track_id": tracks[0]["id"]}).json()
        assert single["order"] == "sequential"
        assert single["repeat"] == "off"
        grouped = client.post("/api/play", json={"track_id": tracks[0]["id"], "queue_ids": [item["id"] for item in tracks]}).json()
        assert grouped["order"] == "sequential"
        assert grouped["repeat"] == "off"


def test_play_rejects_legacy_mode_and_invalid_options(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        track_id = client.get("/api/tracks").json()["tracks"][0]["id"]
        assert client.post("/api/play", json={"track_id": track_id, "mode": "sequential"}).status_code == 422
        assert client.post("/api/play", json={"track_id": track_id, "order": "random"}).status_code == 422
        assert client.post("/api/play", json={"track_id": track_id, "repeat": "loop"}).status_code == 422


def test_track_id_missing_from_queue_ids_is_rejected_with_400(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        tracks = client.get("/api/tracks").json()["tracks"]
        client.post("/api/play", json={"track_id": tracks[0]["id"]})
        response = client.post("/api/play", json={"track_id": tracks[0]["id"], "queue_ids": ["missing"]})
        assert response.status_code == 400
        assert client.get("/api/queue").json()["current"]["id"] == tracks[0]["id"]


def test_unknown_queue_item_returns_404_and_keeps_state(tmp_path: Path) -> None:
    client, mina = make_client(tmp_path)
    with client:
        tracks = client.get("/api/tracks").json()["tracks"]
        client.post("/api/play", json={"track_id": tracks[0]["id"]})
        calls_before = len(mina.calls)
        response = client.post(
            "/api/play",
            json={"track_id": tracks[0]["id"], "queue_ids": [tracks[0]["id"], "missing"]},
        )
        assert response.status_code == 404
        assert len(mina.calls) == calls_before
        assert client.get("/api/queue").json()["current"]["id"] == tracks[0]["id"]


def test_pause_resume_and_volume_delegate_to_mina(tmp_path: Path) -> None:
    client, mina = make_client(tmp_path)
    with client:
        tracks = client.get("/api/tracks").json()["tracks"]
        client.post("/api/play", json={"track_id": tracks[0]["id"]})
        assert client.post("/api/pause").json()["state"] == "paused"
        assert mina.calls[-1] == ("pause", ("device-1",))
        resumed = client.post("/api/resume").json()
        assert resumed["state"] == "playing"
        assert resumed["current"]["id"] == tracks[0]["id"]
        assert mina.calls[-1] == ("play", ("device-1",))
        assert client.post("/api/volume", json={"volume": 30}).status_code == 200
        assert mina.calls[-1] == ("set_volume", (30, "device-1"))


def test_resume_with_empty_queue_returns_409(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        assert client.post("/api/resume").status_code == 409


def test_devices_lists_mock_device_and_selection(tmp_path: Path) -> None:
    client, _ = make_client(tmp_path)
    with client:
        assert client.get("/api/devices").json() == {
            "devices": [{"id": "device-1", "name": "Mock Mina"}],
            "selected_device_id": "device-1",
        }


class _NoDeviceClient:
    """Minimal MinaClient stub without a configured device."""

    def play_by_url(self, url: str, device_id: str) -> None:
        raise AssertionError("play_by_url must not be called without a device")


def test_play_without_configured_device_returns_409(tmp_path: Path) -> None:
    (tmp_path / "one.mp3").touch()
    settings = Settings(public_base_url="http://speaker:8123", music_dir=tmp_path)
    service = MusicService(tmp_path, settings.public_base_url, mina_client=_NoDeviceClient())
    with TestClient(create_app(settings=settings, service=service)) as client:
        track_id = client.get("/api/tracks").json()["tracks"][0]["id"]
        assert client.post("/api/play", json={"track_id": track_id}).status_code == 409
