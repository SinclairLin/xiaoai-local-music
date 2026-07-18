from fastapi.testclient import TestClient

from app.main import create_app
from app.service import MusicService


def test_api_health_tracks_and_play(tmp_path) -> None:
    (tmp_path / "稻香.mp3").touch()
    with TestClient(create_app(service=MusicService(tmp_path))) as client:
        assert client.get("/healthz").json() == {"status": "ok"}
        tracks = client.get("/api/tracks").json()["tracks"]
        assert tracks[0]["title"] == "稻香"
        track_id = tracks[0]["id"]
        assert client.post("/api/play", json={"track_id": track_id}).status_code == 200
        assert client.post("/api/play", json={"track_id": "missing"}).status_code == 404
        assert client.post("/api/voice", json={"text": "播放 稻香"}).status_code == 200
