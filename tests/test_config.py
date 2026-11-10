import pytest

from pukiwiki_mcp.config import ConfigError, Settings, encode_hint_char


def test_minimal_env_defaults():
    s = Settings.from_env({"PUKIWIKI_URL": "https://w.example/"})
    assert s.base_url == "https://w.example/"
    assert s.encoding == "euc-jp"
    assert s.timeout == 30.0
    assert s.verify_tls is True
    assert s.basic_auth is None
    assert s.encode_hint == "ぷ"


def test_missing_url_raises():
    with pytest.raises(ConfigError):
        Settings.from_env({})
    with pytest.raises(ConfigError):
        Settings.from_env({"PUKIWIKI_URL": "  "})


def test_bool_and_timeout_parsing():
    s = Settings.from_env(
        {
            "PUKIWIKI_URL": "https://w/",
            "PUKIWIKI_VERIFY_TLS": "false",
            "PUKIWIKI_TIMEOUT": "5.5",
        }
    )
    assert s.verify_tls is False
    assert s.timeout == 5.5


def test_invalid_bool_and_timeout():
    with pytest.raises(ConfigError):
        Settings.from_env({"PUKIWIKI_URL": "x", "PUKIWIKI_VERIFY_TLS": "maybe"})
    with pytest.raises(ConfigError):
        Settings.from_env({"PUKIWIKI_URL": "x", "PUKIWIKI_TIMEOUT": "abc"})


def test_invalid_encoding():
    with pytest.raises(ConfigError):
        Settings.from_env({"PUKIWIKI_URL": "x", "PUKIWIKI_ENCODING": "no-such-enc"})


def test_basic_auth_tuple():
    s = Settings.from_env(
        {"PUKIWIKI_URL": "x", "PUKIWIKI_BASIC_USER": "u", "PUKIWIKI_BASIC_PASSWORD": "p"}
    )
    assert s.basic_auth == ("u", "p")


def test_encode_hint_char():
    assert encode_hint_char("euc-jp") == "ぷ"
    assert encode_hint_char("UTF-8") == "ぷ"
    assert encode_hint_char("iso-8859-1") == ""
