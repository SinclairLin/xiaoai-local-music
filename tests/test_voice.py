from app.voice import VoiceIntent, parse_command, parse_play_command
import pytest


def test_parse_play_command_variants() -> None:
    assert parse_play_command("播放 周杰伦") == "周杰伦"
    assert parse_play_command("  播放   本地 稻香  ") == "稻香"
    assert parse_play_command("播放周杰伦") == "周杰伦"
    assert parse_play_command("暂停") is None
    assert parse_play_command("播放") is None


@pytest.mark.parametrize(
    ("text", "title"),
    [
        ("播放 稻香", "稻香"),
        ("播放周杰伦", "周杰伦"),
        (" 播放   晴天 ", "晴天"),
        ("播放 本地 青花瓷", "青花瓷"),
        ("播放本地夜曲", "夜曲"),
        ("播放  五月天 - 知足", "五月天-知足"),
        ("播放 许嵩《有何不可》", "许嵩《有何不可》"),
        ("播放  赵雷 / 成都", "赵雷/成都"),
        ("播放本地  陈奕迅  十年", "陈奕迅十年"),
        ("\t播放\t刘若英\t后来", "刘若英后来"),
        ("播放 周深 - 大鱼 (Live)", "周深-大鱼(Live)"),
        ("播放本地  张学友-吻别  ", "张学友-吻别"),
    ],
)
def test_parse_play_command_chinese_samples(text: str, title: str) -> None:
    """常见中文口语/格式变体均应提取出可检索曲名。"""
    assert parse_play_command(text) == title


@pytest.mark.parametrize("text", [
    "",
    "暂停",
    "停止播放",
    "下一首",
    "播放",
    "播放   ",
    "本地播放 稻香",
    None,
])
def test_parse_play_command_rejects_non_play_commands(text: str | None) -> None:
    assert parse_play_command(text) is None


@pytest.mark.parametrize(
    ("text", "intent"),
    [
        ("小爱同学请播放稻香", VoiceIntent.PLAY),
        ("停止", VoiceIntent.STOP),
        ("停下", VoiceIntent.STOP),
        ("暂停", VoiceIntent.PAUSE),
        ("请继续", VoiceIntent.RESUME),
        ("接着放", VoiceIntent.RESUME),
        ("下一首", VoiceIntent.NEXT),
        ("切歌", VoiceIntent.NEXT),
        ("上一首", VoiceIntent.PREVIOUS),
        ("上一曲", VoiceIntent.PREVIOUS),
    ],
)
def test_parse_all_voice_intents(text: str, intent: VoiceIntent) -> None:
    parsed = parse_command(text)
    assert parsed is not None
    assert parsed.intent is intent


@pytest.mark.parametrize("text", ["播放", "播放本地", "放一首", "我想听", "小爱同学请播放"])
def test_bare_play_prefixes_without_title_are_rejected(text: str) -> None:
    assert parse_command(text) is None
