import os

import pytest
import yaml

from app.config import ConfigError, Settings
from app.main import create_app


def clear_config_env(monkeypatch) -> None:
    for name in (
        "CONFIG_DIR",
        "MUSIC_ROOT",
        "MUSIC_DIR",
        "XIAOMI_USER",
        "XIAOMI_PASSWORD",
        "MINA_API_BASE_URL",
        "MINA_MODE",
        "MINA_DEVICE_ID",
        "PUBLIC_BASE_URL",
        "HOST",
        "PORT",
    ):
        monkeypatch.delenv(name, raising=False)


def test_defaults_without_yaml(monkeypatch, tmp_path) -> None:
    clear_config_env(monkeypatch)
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")

    settings = Settings.from_env()

    assert settings.music_root == "/music"
    assert settings.config_path == tmp_path / "config.yaml"
    assert settings.port == 8123
    assert settings.xiaomi_user is None


def test_yaml_values_and_environment_overrides(monkeypatch, tmp_path) -> None:
    clear_config_env(monkeypatch)
    (tmp_path / "config.yaml").write_text(
        yaml.safe_dump(
            {
                "xiaomi_user": "yaml-user",
                "xiaomi_password": "yaml-password",
                "public_base_url": "https://yaml.example",
                "music_root": "/yaml-music",
                "host": "127.0.0.1",
                "port": 9000,
            },
            allow_unicode=True,
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("XIAOMI_USER", "env-user")
    monkeypatch.setenv("XIAOMI_PASSWORD", "env-password")
    monkeypatch.setenv("PUBLIC_BASE_URL", "https://env.example")
    monkeypatch.setenv("MUSIC_ROOT", "/env-music")
    monkeypatch.setenv("PORT", "9010")

    settings = Settings.from_env()

    assert settings.xiaomi_user == "env-user"
    assert settings.xiaomi_password == "env-password"
    assert settings.public_base_url == "https://env.example"
    assert settings.music_root == "/env-music"
    assert settings.host == "127.0.0.1"
    assert settings.port == 9010


def test_empty_environment_values_do_not_override_yaml(monkeypatch, tmp_path) -> None:
    clear_config_env(monkeypatch)
    (tmp_path / "config.yaml").write_text(
        "music_root: /yaml-music\nxiaomi_user: yaml-user\npublic_base_url: http://yaml.example\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("MUSIC_ROOT", "")
    monkeypatch.setenv("XIAOMI_USER", "")

    settings = Settings.from_env()

    assert settings.music_root == "/yaml-music"
    assert settings.xiaomi_user == "yaml-user"


def test_music_root_precedes_legacy_music_dir(monkeypatch, tmp_path) -> None:
    clear_config_env(monkeypatch)
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("MUSIC_DIR", "/legacy")
    monkeypatch.setenv("MUSIC_ROOT", "/canonical")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")

    assert Settings.from_env().music_root == "/canonical"


def test_legacy_settings_constructor_alias(tmp_path) -> None:
    settings = Settings(public_base_url="http://testserver", music_dir=tmp_path)

    assert settings.music_root == str(tmp_path)
    assert settings.music_dir == str(tmp_path)


def test_save_round_trip_creates_directory(monkeypatch, tmp_path) -> None:
    target_dir = tmp_path / "nested" / "config"
    settings = Settings(
        config_dir=str(target_dir),
        music_root="/music-library",
        xiaomi_user="user",
        xiaomi_password="password",
        public_base_url="https://music.example",
        host="127.0.0.1",
        port=8124,
    )

    target = settings.save()

    assert target == target_dir / "config.yaml"
    if os.name == "posix":
        assert target.stat().st_mode & 0o777 == 0o600
    clear_config_env(monkeypatch)
    monkeypatch.setenv("CONFIG_DIR", str(target_dir))
    assert Settings.from_env() == settings


@pytest.mark.parametrize(
    ("content", "message"),
    [
        ("[not, a, mapping]\n", "mapping"),
        ("port: wrong\n", "port"),
        ("port: 70000\n", "port"),
        ("music_root: 123\n", "music_root"),
        ("music_root: [bad]\n", "music_root"),
    ],
)
def test_invalid_yaml_values_fail(monkeypatch, tmp_path, content, message) -> None:
    clear_config_env(monkeypatch)
    (tmp_path / "config.yaml").write_text(content, encoding="utf-8")
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))

    with pytest.raises(ConfigError, match=message):
        Settings.from_env()


def test_invalid_yaml_syntax_fails(monkeypatch, tmp_path) -> None:
    clear_config_env(monkeypatch)
    (tmp_path / "config.yaml").write_text("music_root: [unterminated\n", encoding="utf-8")
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))

    with pytest.raises(ConfigError, match="invalid YAML"):
        Settings.from_env()


def test_unreadable_config_path_fails(monkeypatch, tmp_path) -> None:
    clear_config_env(monkeypatch)
    (tmp_path / "config.yaml").mkdir()
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))

    with pytest.raises(ConfigError, match="cannot read"):
        Settings.from_env()


def test_invalid_port_environment_fails(monkeypatch, tmp_path) -> None:
    clear_config_env(monkeypatch)
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("PORT", "not-a-port")

    with pytest.raises(ConfigError, match="PORT"):
        Settings.from_env()


def test_out_of_range_port_environment_reports_range(monkeypatch, tmp_path) -> None:
    clear_config_env(monkeypatch)
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("PORT", "70000")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")

    with pytest.raises(ConfigError, match="between 1 and 65535"):
        Settings.from_env()


def test_valid_port_environment_overrides_invalid_yaml_port(monkeypatch, tmp_path) -> None:
    clear_config_env(monkeypatch)
    (tmp_path / "config.yaml").write_text("port: 70000\n", encoding="utf-8")
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("PORT", "9001")
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")

    assert Settings.from_env().port == 9001


def test_create_app_uses_music_root(monkeypatch, tmp_path) -> None:
    clear_config_env(monkeypatch)
    music_root = tmp_path / "music"
    music_root.mkdir()
    (music_root / "稻香.mp3").touch()
    monkeypatch.setenv("CONFIG_DIR", str(tmp_path / "config"))
    monkeypatch.setenv("MUSIC_ROOT", str(music_root))
    monkeypatch.setenv("PUBLIC_BASE_URL", "http://testserver")

    app = create_app()

    assert app.state.service.music_dir == music_root
    assert app.state.service.list_tracks()[0].title == "稻香"
