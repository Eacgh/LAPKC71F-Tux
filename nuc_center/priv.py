"""Privileged control backend.

This module is imported by the root helper daemon and by the GUI when it is
started as root.  Every action is a small, validated operation; the helper
never accepts arbitrary paths or raw EC writes from the GUI.
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional

from .ite import IteError, IteKeyboard
from .fan import FanCurveController, FanGuard, FanPwm
from .ec import (
    ECError,
    KBD_MAX_LEVEL,
    LIGHTBAR_MAX_INTENSITY,
    NUC_PERF_MODE_VALUES,
    AcpiEc,
)
from .util import clamp, read_int, read_text, run_command, write_text


def _find_led(*names: str) -> Optional[Path]:
    candidates: list[Path] = []
    for name in names:
        candidates.extend(Path("/sys/class/leds").glob(name))
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    return None


def _find_platform_dir() -> Optional[Path]:
    for pattern in ("/sys/devices/platform/INOU*", "/sys/bus/platform/devices/INOU*"):
        for entry in Path("/").glob(pattern.lstrip("/")):
            if (entry / "fn_lock").exists() or (entry / "rainbow_animation").exists():
                return entry
    return None


def _find_battery() -> Optional[Path]:
    for pattern in ("/sys/class/power_supply/BAT0", "/sys/class/power_supply/BAT*"):
        for entry in Path("/").glob(pattern.lstrip("/")):
            if (entry / "capacity").exists():
                return entry
    return None


def _read_bool(path: Path) -> Optional[bool]:
    value = read_int(path)
    if value is None:
        return None
    return bool(value)


class PrivilegedBackend:
    """Validated root-level operations on this laptop."""

    def __init__(self) -> None:
        self.ec = AcpiEc()
        self.ite = IteKeyboard()
        self.fan_guard = FanGuard(self.ec)
        self.fan_pwm = FanPwm()
        if not self.fan_pwm.available:
            run_command(["modprobe", "uniwill_pwm"], timeout=5.0)
            self.fan_pwm = FanPwm()
        self.fan_curve = FanCurveController(self.fan_pwm)
        self.lightbar_led = _find_led(
            "uniwill:multicolor:status",
            "uniwill:rgb:status",
            "uniwill:*:status",
        )
        self.kbd_led = _find_led(
            "uniwill:multicolor:kbd_backlight",
            "uniwill:rgb:kbd_backlight",
            "uniwill:white:kbd_backlight",
            "uniwill:*kbd_backlight*",
        )
        self.platform = _find_platform_dir()
        self.battery = _find_battery()
        self.product = read_text("/sys/class/dmi/id/product_name", "") or ""
        self._perf_cache = Path("/run/nuc-center/last-perf-mode")
        try:
            cached_mode = self._perf_cache.read_text(encoding="utf-8").strip()
        except OSError:
            cached_mode = ""
        if cached_mode in {"quiet", "balanced", "performance"}:
            self.ec._last_perf_mode = cached_mode

        self._ec_state: dict = {}
        self._ec_checked = False

    # ------------------------------------------------------------------
    # capability discovery
    # ------------------------------------------------------------------
    def ec_state(self, refresh: bool = False) -> dict:
        if refresh or not self._ec_checked:
            self._ec_state = self.ec.read_state()
            self._ec_checked = True
        return dict(self._ec_state)

    def capabilities(self) -> dict:
        state = self.ec_state()
        ec_ok = bool(state.get("ec"))
        kbd_ec = bool(
            ec_ok
            and (
                state.get("rgb_keyboard")
                or state.get("kbd_white_only") is not None
                or self.product.startswith("LAPKC")
            )
        )
        battery_limit = bool(
            self.battery
            and (self.battery / "charge_control_end_threshold").exists()
        )
        return {
            "helper": True,
            "ec": ec_ok,
            "product": self.product,
            "lightbar": bool(self.lightbar_led),
            "kbd_led": bool(self.kbd_led),
            "kbd_ec": kbd_ec,
            "kbd_ite": bool(self.ite.available),
            "battery_limit": battery_limit,
            "fn_lock": bool(self.platform and (self.platform / "fn_lock").exists()),
            "super_key": bool(self.platform and (self.platform / "super_key_enable").exists()),
            "touchpad_toggle": bool(
                self.platform and (self.platform / "touchpad_toggle_enable").exists()
            ),
            "rainbow": bool(self.platform and (self.platform / "rainbow_animation").exists()),
            "breathing": bool(
                self.platform and (self.platform / "breathing_in_suspend").exists()
            ),
            "profile": bool(ec_ok and state.get("perf_value") is not None),
            # The NUC X15 supports the persistent fan-boost bit even though
            # its SUPPORT_5 register does not advertise it.
            "fan_boost": bool(
                ec_ok
                and (
                    state.get("fan_boost_supported")
                    or self.product.startswith("LAPKC")
                )
            ),
            "fan_guard": bool(ec_ok and self.product.startswith("LAPKC")),
            "fan_curve": bool(self.fan_pwm.available),
            "kbd_white_only": bool(state.get("kbd_white_only")),
            "kbd_max_level": KBD_MAX_LEVEL,
            "lightbar_max_intensity": self._light_max_intensity(),
        }

    def _light_max_intensity(self) -> int:
        if not self.lightbar_led:
            return LIGHTBAR_MAX_INTENSITY
        return read_int(self.lightbar_led / "multi_max_intensity") or LIGHTBAR_MAX_INTENSITY

    def _led_state(self, led: Optional[Path]) -> Optional[dict]:
        if not led:
            return None
        brightness = read_int(led / "brightness")
        max_brightness = read_int(led / "max_brightness") or 1
        intensity_text = read_text(led / "multi_intensity")
        intensity: list[int] = []
        if intensity_text:
            try:
                intensity = [int(part) for part in intensity_text.split()[:3]]
            except ValueError:
                intensity = []
        max_intensity = read_int(led / "multi_max_intensity") or LIGHTBAR_MAX_INTENSITY
        return {
            "brightness": brightness,
            "max_brightness": max_brightness,
            "rgb": intensity,
            "max_intensity": max_intensity,
        }

    def state(self) -> dict:
        ec = self.ec_state(refresh=True)
        caps = self.capabilities()
        platform = self.platform
        battery_limit = None
        if self.battery:
            battery_limit = read_int(self.battery / "charge_control_end_threshold")
        return {
            "caps": caps,
            "lightbar": self._led_state(self.lightbar_led),
            "kbd_led": self._led_state(self.kbd_led),
            "battery": {
                "limit": battery_limit,
                "status": read_text(self.battery / "status") if self.battery else None,
                "capacity": read_int(self.battery / "capacity") if self.battery else None,
            },
            "toggles": {
                "fn_lock": _read_bool(platform / "fn_lock") if platform else None,
                "super_key": _read_bool(platform / "super_key_enable") if platform else None,
                "touchpad_toggle": (
                    _read_bool(platform / "touchpad_toggle_enable") if platform else None
                ),
            },
            "animations": {
                "rainbow": _read_bool(platform / "rainbow_animation") if platform else None,
                "breathing": (
                    _read_bool(platform / "breathing_in_suspend") if platform else None
                ),
            },
            "ec": ec,
            "ite": self.ite.state(),
            "fan_guard": self.fan_guard.state(),
            "fan_curve": self.fan_curve.state(),
        }

    # ------------------------------------------------------------------
    # lightbar / keyboard
    # ------------------------------------------------------------------
    def _set_rgb_led(self, led: Path, rgb: tuple[int, int, int], percent: float) -> dict:
        percent = clamp(float(percent), 0.0, 100.0)
        rgb = tuple(int(clamp(int(channel), 0, 255)) for channel in rgb)
        if len(rgb) != 3:
            raise ValueError("RGB needs three components")
        max_intensity = read_int(led / "multi_max_intensity") or LIGHTBAR_MAX_INTENSITY
        max_brightness = read_int(led / "max_brightness") or 1
        if percent <= 0.0:
            write_text(led / "brightness", "0")
            return self._led_state(led) or {}
        scaled = tuple(
            int(round(channel * max_intensity / 255.0 * percent / 100.0))
            for channel in rgb
        )
        # Avoid the all-zero "restore default" corner case for LEDs that have
        # one.  A brightness of zero is used for a real off state instead.
        if not any(scaled):
            scaled = (1, 1, 1)
        write_text(led / "multi_intensity", " ".join(str(part) for part in scaled))
        write_text(led / "brightness", str(max_brightness))
        return self._led_state(led) or {}

    def set_lightbar(self, rgb: tuple[int, int, int], percent: float) -> dict:
        if not self.lightbar_led:
            raise ECError("no Uniwill lightbar LED found")
        return self._set_rgb_led(self.lightbar_led, rgb, percent)

    def set_kbd_led(self, rgb: tuple[int, int, int], percent: float) -> dict:
        if not self.kbd_led:
            raise ECError("no keyboard backlight LED exposed by the kernel")
        return self._set_rgb_led(self.kbd_led, rgb, percent)

    def set_keyboard(
        self,
        rgb: Optional[tuple[int, int, int]] = None,
        level: Optional[int] = None,
    ) -> dict:
        # Prefer the real ITE 8291 HID keyboard controller.  The EC keyboard
        # registers are only a fallback for other Uniwill models.
        if self.ite.available:
            try:
                return {"ite": self.ite.apply(rgb=rgb, level=level)}
            except IteError:
                self.ite.close()
                # Fall through to the EC path only if the HID device vanished.
                if self.ite.available:
                    raise
        state = self.ec_state()
        if not state.get("ec"):
            raise ECError("EC access is unavailable")
        result = self.ec.set_keyboard(rgb=rgb, level=level)
        self._ec_checked = False
        return result

    def set_animations(
        self,
        rainbow: Optional[bool] = None,
        breathing: Optional[bool] = None,
    ) -> dict:
        if not self.platform:
            raise ECError("the Uniwill platform device was not found")
        if rainbow is not None:
            write_text(self.platform / "rainbow_animation", "1" if rainbow else "0")
        if breathing is not None:
            write_text(
                self.platform / "breathing_in_suspend", "1" if breathing else "0"
            )
        return {
            "rainbow": _read_bool(self.platform / "rainbow_animation"),
            "breathing": _read_bool(self.platform / "breathing_in_suspend"),
        }

    # ------------------------------------------------------------------
    # performance / fans
    # ------------------------------------------------------------------
    def set_profile(self, mode: str) -> dict:
        state = self.ec_state()
        if not state.get("ec"):
            raise ECError("EC access is unavailable")
        values = NUC_PERF_MODE_VALUES if self.product.startswith("LAPKC") else None
        result = self.ec.set_perf_mode(mode, values=values)
        try:
            self._perf_cache.parent.mkdir(parents=True, exist_ok=True)
            self._perf_cache.write_text(mode + "\n", encoding="utf-8")
        except OSError:
            pass
        self._ec_checked = False
        return result

    def set_fan_boost(self, enabled: bool) -> dict:
        state = self.ec_state()
        if not state.get("ec"):
            raise ECError("EC access is unavailable")
        result = self.ec.set_fan_boost(bool(enabled))
        self._ec_checked = False
        return result

    def set_fan_guard(self, enabled: bool, threshold: int) -> dict:
        state = self.ec_state()
        if not state.get("ec"):
            raise ECError("EC access is unavailable")
        threshold = max(60, min(90, int(threshold)))
        if enabled:
            self.fan_curve.stop()
            return {"fan_guard": self.fan_guard.start(threshold)}
        self.fan_guard.threshold = threshold
        return {"fan_guard": self.fan_guard.stop()}

    def set_fan_curve(self, points, enabled: bool) -> dict:
        if enabled:
            if not self.fan_pwm.available:
                raise ECError("风扇曲线模块未加载（/dev/uniwill_pwm 不存在）")
            if self.fan_curve.enabled:
                return {"fan_curve": self.fan_curve.update_points(points)}
            self.fan_guard.stop()
            return {"fan_curve": self.fan_curve.start(points)}
        return {"fan_curve": self.fan_curve.stop()}

    def shutdown(self) -> None:
        try:
            self.fan_curve.stop()
        except Exception:
            pass
        try:
            self.fan_guard.stop()
        except Exception:
            pass

    # ------------------------------------------------------------------
    # battery / switches
    # ------------------------------------------------------------------
    def set_battery_limit(self, limit: int) -> dict:
        if not self.battery:
            raise ECError("no battery found")
        path = self.battery / "charge_control_end_threshold"
        if not path.exists():
            raise ECError("this battery does not expose a charge limit")
        limit = int(clamp(int(limit), 40, 100))
        write_text(path, str(limit))
        readback = read_int(path)
        if readback is not None and abs(readback - limit) > 1:
            raise ECError(f"the battery rejected the charge limit (read {readback})")
        return {"limit": readback}

    def set_toggle(self, name: str, enabled: bool) -> dict:
        if not self.platform:
            raise ECError("the Uniwill platform device was not found")
        filenames = {
            "fn_lock": "fn_lock",
            "super_key": "super_key_enable",
            "touchpad_toggle": "touchpad_toggle_enable",
        }
        filename = filenames.get(name)
        if not filename:
            raise ValueError(f"unknown switch: {name}")
        path = self.platform / filename
        if not path.exists():
            raise ECError(f"{name} is not supported on this device")
        write_text(path, "1" if enabled else "0")
        return {name: _read_bool(path)}

    # ------------------------------------------------------------------
    # dispatch table used by the daemon
    # ------------------------------------------------------------------
    def dispatch(self, command: str, params: dict) -> dict:
        if command == "ping":
            return {"pong": True, "version": "1.0.0"}
        if command == "caps":
            return {"caps": self.capabilities()}
        if command == "state":
            return self.state()
        if command == "set_lightbar":
            return {"lightbar": self.set_lightbar(tuple(params["rgb"]), params["percent"])}
        if command == "set_kbd_led":
            return {"kbd_led": self.set_kbd_led(tuple(params["rgb"]), params["percent"])}
        if command == "set_keyboard":
            rgb = params.get("rgb")
            return {
                "keyboard": self.set_keyboard(
                    rgb=tuple(rgb) if rgb is not None else None,
                    level=params.get("level"),
                )
            }
        if command == "set_animations":
            return {
                "animations": self.set_animations(
                    rainbow=params.get("rainbow"),
                    breathing=params.get("breathing"),
                )
            }
        if command == "set_profile":
            return {"profile": self.set_profile(params["mode"])}
        if command == "set_fan_boost":
            return {"fan_boost": self.set_fan_boost(bool(params["enabled"]))}
        if command == "set_fan_guard":
            return {
                "fan_guard": self.set_fan_guard(
                    bool(params["enabled"]), int(params["threshold"])
                )
            }
        if command == "set_fan_curve":
            return {
                "fan_curve": self.set_fan_curve(
                    params.get("points") or [], bool(params["enabled"])
                )
            }
        if command == "set_battery_limit":
            return {"battery": self.set_battery_limit(int(params["limit"]))}
        if command == "set_toggle":
            return {"toggle": self.set_toggle(params["name"], bool(params["enabled"]))}
        raise ValueError(f"unknown command: {command}")


def quick_capabilities() -> dict:
    """Small helper for the GUI's monitor-only mode (no EC probe)."""
    return {
        "helper": False,
        "ec": False,
        "lightbar": bool(_find_led("uniwill:*:status")),
        "kbd_ec": False,
        "battery_limit": bool(
            _find_battery()
            and (_find_battery() / "charge_control_end_threshold").exists()  # type: ignore[union-attr]
        ),
    }
