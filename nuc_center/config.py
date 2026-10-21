"""Tiny JSON configuration store for the GUI."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "theme": "dark",
    "units": "C",
    "poll_ms": 1000,
    "autostart": False,
    "lighting_target": "keyboard",
    "keyboard_color": [77, 194, 255],
    "lightbar_color": [77, 194, 255],
}


class Config:
    def __init__(self, path: Path | None = None) -> None:
        self.path = path or Path(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        ) / "nuc-center" / "config.json"
        self.data: dict[str, Any] = dict(DEFAULTS)
        self.load()

    def load(self) -> None:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if isinstance(raw, dict):
            self.data.update({key: value for key, value in raw.items() if key in DEFAULTS})

    def save(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            temp = self.path.with_suffix(".tmp")
            temp.write_text(
                json.dumps(self.data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            temp.replace(self.path)
        except OSError:
            pass

    def get(self, key: str, default: Any = None) -> Any:
        return self.data.get(key, DEFAULTS.get(key, default))

    def set(self, key: str, value: Any) -> None:
        if self.data.get(key) != value:
            self.data[key] = value
            self.save()
