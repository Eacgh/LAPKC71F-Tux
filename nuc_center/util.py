"""Small, dependency-free helpers shared by the GUI and the helper daemon."""

from __future__ import annotations

import os
import re
import subprocess
from pathlib import Path
from typing import Iterable, Optional

APP_NAME = "NUC 中心"
APP_ID = "org.nuc.center"
APP_VERSION = "1.0.0"

SYSFS = Path("/sys")
PROC = Path("/proc")


def read_text(path: os.PathLike | str, default: Optional[str] = None) -> Optional[str]:
    try:
        return Path(path).read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return default


def read_int(path: os.PathLike | str, default: Optional[int] = None) -> Optional[int]:
    value = read_text(path)
    if value is None:
        return default
    try:
        return int(value, 0)
    except (TypeError, ValueError):
        return default


def read_float(path: os.PathLike | str, default: Optional[float] = None) -> Optional[float]:
    value = read_text(path)
    if value is None:
        return default
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def write_text(path: os.PathLike | str, value: str) -> None:
    Path(path).write_text(str(value), encoding="utf-8")


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def scale(value: float, in_min: float, in_max: float, out_min: float, out_max: float) -> float:
    if in_max == in_min:
        return out_min
    return out_min + (value - in_min) * (out_max - out_min) / (in_max - in_min)


def find_hwmon(name: str) -> list[Path]:
    """Return every hwmon directory whose ``name`` attribute matches."""
    result: list[Path] = []
    for entry in Path("/sys/class/hwmon").glob("hwmon*"):
        if read_text(entry / "name") == name:
            result.append(entry)
    return result


def first_existing(paths: Iterable[os.PathLike | str]) -> Optional[Path]:
    for path in paths:
        candidate = Path(path)
        if candidate.exists():
            return candidate
    return None


def format_temp(celsius: Optional[float], unit: str = "C", decimals: int = 0) -> str:
    if celsius is None:
        return "--"
    value = celsius if unit == "C" else celsius * 9.0 / 5.0 + 32.0
    suffix = "°C" if unit == "C" else "°F"
    return f"{value:.{decimals}f}{suffix}"


def temp_value(celsius: Optional[float], unit: str = "C") -> Optional[float]:
    if celsius is None:
        return None
    return celsius if unit == "C" else celsius * 9.0 / 5.0 + 32.0


def format_rpm(rpm: Optional[int]) -> str:
    if rpm is None:
        return "--"
    return f"{rpm:,}"


def format_bytes_gib(size: Optional[float], decimals: int = 1) -> str:
    if size is None:
        return "--"
    return f"{size / (1024 ** 3):.{decimals}f} GiB"


def format_power(watts: Optional[float]) -> str:
    if watts is None:
        return "--"
    return f"{watts:.1f} W"


def human_uptime(seconds: Optional[float]) -> str:
    if seconds is None:
        return "--"
    seconds = int(seconds)
    days, seconds = divmod(seconds, 86400)
    hours, seconds = divmod(seconds, 3600)
    minutes, _ = divmod(seconds, 60)
    if days:
        return f"{days}天 {hours}小时"
    if hours:
        return f"{hours}小时 {minutes}分"
    return f"{minutes}分钟"


def translate_governor(governor: str | None) -> str:
    return {
        "powersave": "节能",
        "performance": "性能",
        "schedutil": "调度",
        "ondemand": "按需",
        "conservative": "保守",
    }.get(governor or "", governor or "--")


def translate_battery_status(status: str | None) -> str:
    return {
        "Charging": "充电中",
        "Discharging": "放电中",
        "Not charging": "未充电",
        "Full": "已充满",
        "Unknown": "未知",
    }.get(status or "", status or "未知")


def translate_health(health: str | None) -> str:
    return {
        "Good": "良好",
        "Fair": "一般",
        "Poor": "较差",
        "Unknown": "未知",
    }.get(health or "", health or "未知")


def translate_technology(technology: str | None) -> str:
    return {
        "Li-ion": "锂离子",
        "Li-poly": "锂聚合物",
        "Unknown": "未知",
    }.get(technology or "", technology or "未知")


def parse_hex(value: str) -> Optional[int]:
    match = re.search(r"0x[0-9a-fA-F]+", value)
    if match:
        try:
            return int(match.group(0), 16)
        except ValueError:
            return None
    match = re.search(r"\b\d+\b", value)
    if match:
        try:
            return int(match.group(0), 10)
        except ValueError:
            return None
    return None


def run_command(args: list[str], timeout: float = 3.0) -> tuple[int, str, str]:
    """Run a command and return (returncode, stdout, stderr), never raising."""
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout.strip(), proc.stderr.strip()
    except (OSError, subprocess.SubprocessError) as exc:
        return 127, "", str(exc)


def hex_to_rgb(color: str) -> tuple[int, int, int]:
    color = color.lstrip("#")
    if len(color) == 3:
        color = "".join(ch * 2 for ch in color)
    try:
        return tuple(int(color[i : i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
    except ValueError:
        return (255, 255, 255)
