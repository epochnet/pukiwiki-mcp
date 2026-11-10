"""Configuration loaded from environment variables.

See docs/01_requirements.md (5.3) and docs/03_detailed_design.md (1).
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass

from . import __version__


class ConfigError(Exception):
    """Raised when required configuration is missing or invalid."""


# Encodings for which PukiWiki ships an ``encode_hint`` character ('ja' lang).
# The hint character (U+3077 'ぷ') is encoded in the wiki encoding and sent in
# the POST body so PukiWiki can detect the request encoding (lib/init.php).
_HINT_CHAR = "ぷ"  # 'ぷ'
_HINT_ENCODINGS = {"euc-jp", "eucjp", "euc_jp", "utf-8", "utf8", "utf_8"}

_TRUE = {"1", "true", "yes", "on"}
_FALSE = {"0", "false", "no", "off"}


def _as_bool(value: str, *, name: str) -> bool:
    v = value.strip().lower()
    if v in _TRUE:
        return True
    if v in _FALSE:
        return False
    raise ConfigError(f"{name} must be a boolean (got {value!r})")


def encode_hint_char(encoding: str) -> str:
    """Return the ``encode_hint`` character for the given wiki encoding.

    Returns an empty string for encodings without a known hint, in which case
    the hint field is omitted and PukiWiki falls back to auto-detection.
    """
    return _HINT_CHAR if encoding.strip().lower() in _HINT_ENCODINGS else ""


@dataclass(frozen=True)
class Settings:
    """Immutable runtime configuration."""

    base_url: str
    encoding: str = "euc-jp"
    timeout: float = 30.0
    verify_tls: bool = True
    user_agent: str = f"pukiwiki-mcp/{__version__}"
    basic_user: str | None = None
    basic_password: str | None = None

    @classmethod
    def from_env(cls, env: Mapping[str, str] | None = None) -> "Settings":
        env = os.environ if env is None else env

        base_url = (env.get("PUKIWIKI_URL") or "").strip()
        if not base_url:
            raise ConfigError(
                "PUKIWIKI_URL is required (e.g. https://wiki.example.com/)"
            )

        encoding = (env.get("PUKIWIKI_ENCODING") or "euc-jp").strip() or "euc-jp"
        # Validate the encoding name early so failures are obvious.
        try:
            "x".encode(encoding)
        except LookupError as exc:
            raise ConfigError(f"Unknown PUKIWIKI_ENCODING: {encoding!r}") from exc

        timeout_raw = (env.get("PUKIWIKI_TIMEOUT") or "30").strip()
        try:
            timeout = float(timeout_raw)
        except ValueError as exc:
            raise ConfigError(
                f"PUKIWIKI_TIMEOUT must be a number (got {timeout_raw!r})"
            ) from exc

        verify_tls = _as_bool(
            env.get("PUKIWIKI_VERIFY_TLS", "true"), name="PUKIWIKI_VERIFY_TLS"
        )

        user_agent = (
            env.get("PUKIWIKI_USER_AGENT") or f"pukiwiki-mcp/{__version__}"
        ).strip()

        basic_user = env.get("PUKIWIKI_BASIC_USER") or None
        basic_password = env.get("PUKIWIKI_BASIC_PASSWORD") or None

        return cls(
            base_url=base_url,
            encoding=encoding,
            timeout=timeout,
            verify_tls=verify_tls,
            user_agent=user_agent,
            basic_user=basic_user,
            basic_password=basic_password,
        )

    @property
    def encode_hint(self) -> str:
        return encode_hint_char(self.encoding)

    @property
    def basic_auth(self) -> tuple[str, str] | None:
        if self.basic_user is not None:
            return (self.basic_user, self.basic_password or "")
        return None
