import asyncio

from app.config import Settings
from app.main import create_app
from app.mina_client import MockMinaClient
from app.service import MusicService
from app.voice_worker import ConversationEvent, RingLog, VoicePollResult, VoiceWorker
from app.voice import VoiceIntent, parse_command


class FakeSource:
    def __init__(self, batches):
        self.batches = iter(batches)

    async def poll(self, after_timestamp):
        try:
            return next(self.batches)
        except StopIteration:
            await asyncio.sleep(0.01)
            return VoicePollResult(())


def make_service(tmp_path):
    (tmp_path / "稻香.mp3").touch()
    mina = MockMinaClient("d1")
    return MusicService(tmp_path, "http://testserver", mina_client=mina, device_id="d1"), mina


def test_worker_dispatches_play_stop_and_next(tmp_path):
    service, mina = make_service(tmp_path)
    worker = VoiceWorker(FakeSource([]), service, mina_client=mina, device_id="d1", speak_confirm=True)

    async def run():
        play = await worker.dispatch_text("小爱同学 请 播放 稻香")
        stop = await worker.dispatch_text("停止")
        return play, stop

    play, stop = asyncio.run(run())
    assert play[0]["matched_track"]["title"] == "稻香"
    assert stop == [{}]
    assert [call[0] for call in mina.calls] == ["text_to_speech", "play_by_url", "stop"]


def test_worker_first_poll_only_sets_watermark_and_deduplicates(tmp_path):
    service, mina = make_service(tmp_path)
    source = FakeSource([
        VoicePollResult((ConversationEvent("播放稻香", 100),)),
        VoicePollResult((ConversationEvent("播放稻香", 100), ConversationEvent("停止", 101))),
    ])
    worker = VoiceWorker(source, service, mina_client=mina, device_id="d1", poll_interval_sec=0.01)

    async def run():
        await worker.start()
        await asyncio.sleep(0.05)
        await worker.stop()

    asyncio.run(run())
    assert [call[0] for call in mina.calls] == ["stop"]


def test_ring_log_contains_raw_query_and_result(tmp_path):
    service, mina = make_service(tmp_path)
    worker = VoiceWorker(FakeSource([]), service, mina_client=mina, device_id="d1")

    async def run():
        await worker.dispatch_text("播放稻香")
        return await worker.log.snapshot()

    logs = asyncio.run(run())
    assert logs[-1]["raw_query"] == "播放稻香"
    assert logs[-1]["intent"] == "play"
    assert logs[-1]["matched_track"]["title"] == "稻香"
    assert logs[-1]["stream_url"].endswith("/media/by-id/" + service.list_tracks()[0].id)


def test_parser_supports_all_control_intents():
    assert parse_command("暂停").intent is VoiceIntent.PAUSE
    assert parse_command("请继续").intent is VoiceIntent.RESUME
    assert parse_command("切歌").intent is VoiceIntent.NEXT
    assert parse_command("上一曲").intent is VoiceIntent.PREVIOUS
