"""Configuration - exclusively from environment variables.

Credentials must never be passed as MCP tool parameters: tool arguments flow
through the LLM context and would leak the password. The server reads them
from the environment at call time and never persists them anywhere.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from isds_client.client import IsdsEnvironment


class ConfigError(RuntimeError):
    """Missing or invalid environment configuration."""


@dataclass(frozen=True)
class Settings:
    username: str
    password: str
    environment: IsdsEnvironment
    archive_dir: Path
    allow_send: bool


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


def load_settings() -> Settings:
    """Read configuration from the environment.

    ISDS_ENV defaults to "test" on purpose: nobody should accidentally trigger
    delivery of production mail while trying the server out.
    """
    username = os.environ.get("ISDS_USERNAME", "")
    password = os.environ.get("ISDS_PASSWORD", "")
    if not username or not password:
        raise ConfigError(
            "ISDS_USERNAME and ISDS_PASSWORD must be set in the environment. "
            "Credentials are intentionally not accepted as tool parameters."
        )

    env_raw = os.environ.get("ISDS_ENV", "test").strip().lower()
    if env_raw == "production":
        environment = IsdsEnvironment.PRODUCTION
    elif env_raw == "test":
        environment = IsdsEnvironment.TEST
    else:
        raise ConfigError(f"ISDS_ENV must be 'test' or 'production', got {env_raw!r}")

    archive_dir = Path(os.environ.get("AGENTOVKA_ARCHIVE_DIR", "~/.agentovka/archive")).expanduser()

    return Settings(
        username=username,
        password=password,
        environment=environment,
        archive_dir=archive_dir,
        allow_send=_env_bool("AGENTOVKA_ALLOW_SEND", default=False),
    )
