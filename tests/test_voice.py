from app.voice import parse_play_command


def test_parse_play_command_variants() -> None:
    assert parse_play_command("播放 周杰伦") == "周杰伦"
    assert parse_play_command("  播放   本地 稻香  ") == "稻香"
    assert parse_play_command("播放周杰伦") == "周杰伦"
    assert parse_play_command("暂停") is None
    assert parse_play_command("播放") is None

