"""Fan-curve data model and interpolation.

The curve is a list of (temperature °C, fan speed %) points.  The helper
interpolates linearly between the points and applies a safety floor at high
temperatures.
"""

from __future__ import annotations

import os
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Optional

MIN_TEMP = 30
MAX_TEMP = 100
MIN_PERCENT = 0
MAX_PERCENT = 100

DEFAULT_POINTS: tuple[tuple[int, int], ...] = (
    (40, 20),
    (55, 30),
    (70, 50),
    (80, 75),
    (90, 100),
)


@dataclass(frozen=True)
class FanPoint:
    temp: int
    percent: int

    def as_dict(self) -> dict:
        return {"temp": self.temp, "percent": self.percent}


def normalize_points(points: Iterable) -> list[FanPoint]:
    result: list[FanPoint] = []
    for item in points:
        if isinstance(item, FanPoint):
            temp, percent = item.temp, item.percent
        elif isinstance(item, dict):
            temp, percent = item.get("temp"), item.get("percent")
        else:
            try:
                temp, percent = item
            except (TypeError, ValueError):
                continue
        try:
            temp = int(temp)
            percent = int(percent)
        except (TypeError, ValueError):
            continue
        temp = max(MIN_TEMP, min(MAX_TEMP, temp))
        percent = max(MIN_PERCENT, min(MAX_PERCENT, percent))
        result.append(FanPoint(temp, percent))
    result.sort(key=lambda point: point.temp)
    # Keep the last point for duplicate temperatures.
    deduped: list[FanPoint] = []
    for point in result:
        if deduped and point.temp == deduped[-1].temp:
            deduped[-1] = point
        else:
            deduped.append(point)
    if len(deduped) < 2:
        return [FanPoint(temp, percent) for temp, percent in DEFAULT_POINTS]
    return deduped


def interpolate(points: Iterable, temperature: Optional[float]) -> int:
    curve = normalize_points(points)
    if temperature is None:
        return curve[0].percent
    if temperature <= curve[0].temp:
        return curve[0].percent
    if temperature >= curve[-1].temp:
        return curve[-1].percent
    for left, right in zip(curve, curve[1:]):
        if left.temp <= temperature <= right.temp:
            span = right.temp - left.temp
            if span <= 0:
                return right.percent
            ratio = (temperature - left.temp) / span
            return int(round(left.percent + (right.percent - left.percent) * ratio))
    return curve[-1].percent


def safe_percent(points: Iterable, temperature: Optional[float]) -> int:
    percent = interpolate(points, temperature)
    # Independent safety net: never let a hot machine run on a low curve.
    if temperature is not None:
        if temperature >= 90:
            percent = 100
        elif temperature >= 85:
            percent = max(percent, 75)
        elif temperature >= 80:
            percent = max(percent, 55)
    return max(0, min(100, percent))


def default_points() -> list[FanPoint]:
    return [FanPoint(temp, percent) for temp, percent in DEFAULT_POINTS]


def read_max_temp() -> Optional[float]:
    """Read the hottest hwmon temperature on the machine."""
    import glob

    values: list[float] = []
    for name_path in glob.glob("/sys/class/hwmon/hwmon*/name"):
        try:
            name = open(name_path).read().strip()
        except OSError:
            continue
        base = name_path.rsplit("/", 1)[0]
        if name in ("uniwill", "coretemp", "acpitz"):
            for temp_path in glob.glob(base + "/temp*_input"):
                try:
                    values.append(float(open(temp_path).read()) / 1000.0)
                except (OSError, ValueError):
                    pass
    return max(values) if values else None


class FanPwm:
    """Open the experimental /dev/uniwill_pwm device and keep it open.

    Closing the fd makes the kernel module restore automatic fan control, so
    the curve controller holds it for its whole lifetime.
    """

    def __init__(self, path: str = "/dev/uniwill_pwm") -> None:
        self.path = Path(path)
        self.fd: Optional[int] = None

    @property
    def available(self) -> bool:
        return self.path.exists()

    def open(self) -> int:
        if self.fd is None:
            if not self.available:
                raise RuntimeError("uniwill_pwm module is not loaded")
            self.fd = os.open(str(self.path), os.O_RDWR)
        return self.fd

    def set_percent(self, percent: float) -> int:
        fd = self.open()
        pwm = max(0, min(200, int(round(percent / 100.0 * 200.0))))
        os.write(fd, f"{pwm} {pwm}\n".encode("ascii"))
        return pwm

    def auto(self) -> None:
        try:
            fd = self.open()
            os.write(fd, b"auto\n")
        except (OSError, RuntimeError):
            pass

    def close(self) -> None:
        if self.fd is not None:
            try:
                os.write(self.fd, b"auto\n")
            except OSError:
                pass
            try:
                os.close(self.fd)
            except OSError:
                pass
            self.fd = None


class FanCurveController:
    """Continuous temperature -> fan percentage curve through the WMI module."""

    def __init__(self, pwm: FanPwm) -> None:
        self.pwm = pwm
        self.enabled = False
        self.points = default_points()
        self.temp: Optional[float] = None
        self.smoothed_temp: Optional[float] = None
        self.target_percent: Optional[int] = None
        self.percent: Optional[int] = None
        self.error: Optional[str] = None
        self._thread: Optional[threading.Thread] = None
        self._stop = threading.Event()
        self._lock = threading.RLock()

    def start(self, points) -> dict:
        self.pwm.open()
        self.points = normalize_points(points)
        self.enabled = True
        self.error = None
        self.smoothed_temp = None
        self.target_percent = None
        self.percent = None
        self._stop.clear()
        if self._thread is None or not self._thread.is_alive():
            self._thread = threading.Thread(target=self._run, name="fan-curve", daemon=True)
            self._thread.start()
        self._evaluate()
        return self.state()

    def update_points(self, points) -> dict:
        with self._lock:
            self.points = normalize_points(points)
            if self.enabled:
                self._evaluate()
        return self.state()

    def stop(self) -> dict:
        self.enabled = False
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        with self._lock:
            self.pwm.close()
            self.percent = None
            self.target_percent = None
            self.smoothed_temp = None
        return self.state()

    def _evaluate(self) -> None:
        temp = read_max_temp()
        self.temp = temp
        if not self.enabled:
            return
        if temp is None:
            self.error = "无法读取温度，已恢复自动风扇"
            self.pwm.auto()
            self.percent = None
            self.target_percent = None
            return
        # Low-pass filter the temperature so a short CPU spike does not
        # instantly slam the fans to 100%.
        if self.smoothed_temp is None:
            self.smoothed_temp = temp
        else:
            self.smoothed_temp += (temp - self.smoothed_temp) * 0.25

        target = max(10, min(100, safe_percent(self.points, self.smoothed_temp)))
        self.target_percent = target
        if self.percent is None:
            # Start from a quiet speed and ramp up instead of jumping.
            self.percent = min(target, 25)

        if temp >= 90:
            # Hard safety override: immediate full speed.
            self.percent = 100
            self.target_percent = 100
        else:
            # Slew-rate limit: ~4%/s up normally, 8%/s when hot, 2%/s down.
            up_step = 8 if temp >= 85 else 4
            down_step = 2
            delta = target - self.percent
            if delta > 0:
                self.percent += min(delta, up_step)
            elif delta < 0:
                self.percent += max(delta, -down_step)

        percent = max(10, min(100, int(round(self.percent))))
        self.percent = percent
        try:
            self.pwm.set_percent(percent)
            self.error = None
        except (OSError, RuntimeError) as exc:
            self.error = str(exc)
            self.pwm.auto()

    def _run(self) -> None:
        while not self._stop.is_set():
            with self._lock:
                if not self.enabled:
                    break
                try:
                    self._evaluate()
                except Exception as exc:  # noqa: BLE001
                    self.error = str(exc)
            self._stop.wait(1.0)

    def state(self) -> dict:
        return {
            "available": self.pwm.available,
            "enabled": self.enabled,
            "points": [point.as_dict() for point in self.points],
            "temp": self.temp,
            "smoothed_temp": self.smoothed_temp,
            "target": self.target_percent,
            "percent": self.percent,
            "error": self.error,
        }


class FanGuard:
    """Safely use the EC's only manual fan state: auto vs. maximum.

    The NUC X15 EC does not expose a continuous PWM register through ACPI
    ECRW (0x1804/0x1809 read 0xFF and the 0x075B/0x075C duty registers are
    overwritten by the EC).  The reliable, tested controls are:
      * 0x0751 = 0x00  -> EC automatic fan curve
      * 0x0751 = 0x40  -> both fans at maximum

    This guard uses those two states with a temperature threshold and 5°C
    hysteresis.  It is intentionally not a fake 0-100% curve.
    """

    def __init__(self, ec) -> None:
        self.ec = ec
        self.enabled = False
        self.threshold = 80
        self.hysteresis = 5
        self.boost = False
        self.last_temp: Optional[float] = None
        self.last_error: Optional[str] = None
        self._thread = None
        self._stop = None
        self._lock = None

    def _ensure_lock(self):
        import threading

        if self._lock is None:
            self._lock = threading.RLock()
            self._stop = threading.Event()
        return self._lock

    def _set_boost(self, enabled: bool) -> None:
        if self.boost == enabled:
            return
        try:
            self.ec.set_fan_boost(enabled)
            self.boost = enabled
            self.last_error = None
        except Exception as exc:  # noqa: BLE001
            self.last_error = str(exc)

    def start(self, threshold: int) -> dict:
        self._ensure_lock()
        self.threshold = max(60, min(90, int(threshold)))
        self.enabled = True
        self._stop.clear()
        if self._thread is None or not self._thread.is_alive():
            import threading

            self._thread = threading.Thread(target=self._run, name="fan-guard", daemon=True)
            self._thread.start()
        self._evaluate()
        return self.state()

    def stop(self) -> dict:
        self._ensure_lock()
        self.enabled = False
        self._stop.set()
        thread = self._thread
        self._thread = None
        if thread is not None and thread.is_alive():
            thread.join(timeout=3.0)
        with self._lock:
            # Always restore the EC automatic curve, even if our cached flag
            # was stale (e.g. the user had enabled Maximum cooling manually).
            try:
                self.ec.set_fan_boost(False)
                self.boost = False
                self.last_error = None
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
        return self.state()

    def _evaluate(self) -> None:
        temp = read_max_temp()
        self.last_temp = temp
        if not self.enabled:
            return
        # A performance-mode change or the manual "Maximum cooling" switch
        # can change the boost bit behind our back.  Always sync with the EC.
        try:
            self.boost = bool(self.ec.read(0x0768) & 0x04)
        except Exception:
            pass
        if temp is None:
            self._set_boost(False)
            return
        if temp >= self.threshold or temp >= 90:
            self._set_boost(True)
        elif temp <= self.threshold - self.hysteresis:
            self._set_boost(False)

    def _run(self) -> None:
        import time

        while not self._stop.is_set():
            try:
                with self._lock:
                    if not self.enabled:
                        break
                    self._evaluate()
            except Exception as exc:  # noqa: BLE001
                self.last_error = str(exc)
            self._stop.wait(2.0)

    def state(self) -> dict:
        return {
            "enabled": self.enabled,
            "threshold": self.threshold,
            "hysteresis": self.hysteresis,
            "boost": self.boost,
            "temp": self.last_temp,
            "error": self.last_error,
        }
