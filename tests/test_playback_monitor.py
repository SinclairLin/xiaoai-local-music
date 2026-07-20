import asyncio
from pathlib import Path

from app.mina_client import MockMinaClient
from app.playback_monitor import PlaybackMonitor, normalize_playback_status
from app.service import MusicService


def make_service(tmp_path: Path) -> tuple[MusicService, MockMinaClient, list]:
    (tmp_path / "one.mp3").touch()
    (tmp_path / "two.mp3").touch()
    mina = MockMinaClient("device-1")
    service = MusicService(tmp_path, "http://speaker:8123", mina_client=mina, device_id="device-1")
    tracks = service.scan()
    return service, mina, tracks


def test_normalize_status_aliases() -> None:
    assert normalize_playback_status({"state": "PLAYING"}) == "playing"
    assert normalize_playback_status({"play_status": "paused"}) == "paused"
    assert normalize_playback_status({"status": "finished"}) == "finished"
    assert normalize_playback_status({"state": "idle"}) == "stopped"
    assert normalize_playback_status({"other": "value"}) == "unknown"
    assert normalize_playback_status({"state": "playback_stopped"}) == "stopped"
    assert normalize_playback_status({"state": "play_state_paused"}) == "paused"
    assert normalize_playback_status({"state": "suspended"}) == "paused"


def test_normalize_status_integer_codes() -> None:
    # Real MiNA hardware reports data.info with an integer status field.
    assert normalize_playback_status({"status": 1}) == "playing"
    assert normalize_playback_status({"status": 2}) == "paused"
    assert normalize_playback_status({"status": 0}) == "stopped"
    assert normalize_playback_status({"status": 3}) == "stopped"
    assert normalize_playback_status({"status": 9}) == "unknown"
    assert normalize_playback_status({"status": {"status": 0}}) == "stopped"


def test_stale_probe_does_not_override_new_playback(tmp_path: Path) -> None:
    service, mina, tracks = make_service(tmp_path)
    service.play(tracks[0].id, [track.id for track in tracks], "sequential")
    monitor = PlaybackMonitor(service, grace_sec=0)
    original = mina.get_playback_status

    def racy_get(device_id: str) -> dict | None:
        result = original(device_id)
        # A user request lands while the probe is still in flight.
        service.play(tracks[1].id, [tracks[1].id], "once")
        return result

    mina.playback_status = {"status": "finished"}
    mina.get_playback_status = racy_get  # type: ignore[method-assign]
    asyncio.run(monitor.poll_once())
    state = service.queue_state()
    assert state["state"] == "playing"
    assert state["current"].id == tracks[1].id
    assert state["mode"] == "once"


def test_pause_during_probe_is_not_overridden(tmp_path: Path) -> None:
    service, mina, tracks = make_service(tmp_path)
    service.play(tracks[0].id, [track.id for track in tracks], "sequential")
    monitor = PlaybackMonitor(service, grace_sec=0)
    original = mina.get_playback_status

    def racy_get(device_id: str) -> dict | None:
        result = original(device_id)
        service.pause()
        return result

    mina.playback_status = {"status": "finished"}
    mina.get_playback_status = racy_get  # type: ignore[method-assign]
    asyncio.run(monitor.poll_once())
    state = service.queue_state()
    assert state["state"] == "paused"
    assert state["current"].id == tracks[0].id


def test_monitor_advances_and_stops_at_end(tmp_path: Path) -> None:
    service, mina, tracks = make_service(tmp_path)
    service.play(tracks[0].id, [track.id for track in tracks], "sequential")
    monitor = PlaybackMonitor(service, grace_sec=0)

    async def run() -> None:
        mina.playback_status = {"status": "finished"}
        assert await monitor.poll_once() == "finished"

    asyncio.run(run())
    assert service.queue_state()["current"].id == tracks[1].id
    mina.playback_status = {"status": "finished"}
    asyncio.run(monitor.poll_once())
    assert service.queue_state()["state"] == "stopped"


def test_monitor_once_mode_does_not_advance_on_probe_error(tmp_path: Path) -> None:
    service, mina, tracks = make_service(tmp_path)
    service.play(tracks[0].id, [tracks[0].id], "once")
    monitor = PlaybackMonitor(service, grace_sec=0)

    async def run() -> None:
        mina.playback_status = None
        assert await monitor.poll_once() == "unknown"

    asyncio.run(run())
    state = service.queue_state()
    assert state["state"] == "playing"
    assert state["playback_status"] == "unknown"
    assert state["current"].id == tracks[0].id


def test_monitor_list_and_single_loop_modes(tmp_path: Path) -> None:
    service, mina, tracks = make_service(tmp_path)
    monitor = PlaybackMonitor(service, grace_sec=0)

    service.play(tracks[0].id, [track.id for track in tracks], "list_loop")
    mina.playback_status = {"status": "finished"}
    asyncio.run(monitor.poll_once())
    mina.playback_status = {"status": "finished"}
    asyncio.run(monitor.poll_once())
    assert service.queue_state()["current"].id == tracks[0].id

    service.play(tracks[1].id, [track.id for track in tracks], "single_loop")
    mina.playback_status = {"status": "finished"}
    asyncio.run(monitor.poll_once())
    assert service.queue_state()["current"].id == tracks[1].id
