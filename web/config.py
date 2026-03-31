"""Loads config.toml from the project root."""

from __future__ import annotations

import tomllib
from pathlib import Path

_CONFIG_PATH = Path(__file__).parent.parent / "config.toml"


def load_config() -> dict:
    with open(_CONFIG_PATH, "rb") as f:
        return tomllib.load(f)
