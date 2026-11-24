"""GUI-side control client.

The GUI prefers the root helper daemon.  If there is no helper but the GUI was
started as root, it talks to the hardware directly.  Otherwise it stays in
monitor-only mode and simply reads sysfs.
"""

from __future__ import annotations

import json
import os
import socket
from pathlib import Path
from typing import Any, Optional

from .priv import PrivilegedBackend, quick_capabilities
from .util import read_int, read_text

SOCKET_PATH = Path(
    os.environ.get("NUC_CENTER_SOCKET", "/run/nuc-center/helper.sock")
)


class ControlError(RuntimeError):
    pass


_ERROR_ZH = (
    ("EC access is unavailable", "EC 访问不可用"),
    ("the EC did not accept the performance mode", "EC 未接受性能模式"),
    ("this EC has no separate quiet mode", "此机型的 EC 没有独立的安静模式"),
    ("the EC did not accept the GPU power limit", "EC 未接受显卡功耗上限"),
    ("GPU power control is not supported on this device", "此设备不支持显卡功耗控制"),
    ("GPU power control is only supported on the LAPKC71F", "显卡功耗控制仅支持 LAPKC71F"),
    ("unsupported GPU power limit", "不支持的显卡功耗上限"),
    ("the EC did not change the fan boost state", "EC 未切换风扇加速状态"),
    ("no Uniwill lightbar LED found", "未找到 Uniwill 灯带 LED"),
    ("no keyboard backlight LED exposed by the kernel", "内核未暴露键盘背光 LED"),
    ("no battery found", "未找到电池"),
    ("this battery does not expose a charge limit", "此电池不支持充电上限"),
    ("the battery rejected the charge limit", "电池拒绝了充电上限设置"),
    ("the Uniwill platform device was not found", "未找到 Uniwill 平台设备"),
    ("is not supported on this device", "此设备不支持该功能"),
    ("unknown switch", "未知开关"),
    ("ITE 8291 HID keyboard was not found", "未找到 ITE 8291 键盘控制器"),
    ("cannot open", "无法打开"),
    ("ITE feature report failed", "ITE 特性报告失败"),
    ("ITE feature read failed", "ITE 特性报告读取失败"),
    ("ITE output report failed", "ITE 输出报告失败"),
    ("helper is not reachable", "无法连接特权服务"),
    ("helper response is too large", "特权服务响应过大"),
    ("helper returned an invalid response", "特权服务返回无效响应"),
    ("helper request failed", "特权服务请求失败"),
    ("control helper is not running", "特权服务未运行"),
)


def localize_error(message: str) -> str:
    if not message:
        return "操作失败"
    for english, chinese in _ERROR_ZH:
        if english in message:
            return message.replace(english, chinese)
    return message


class HelperClient:
    def __init__(self, path: Path = SOCKET_PATH) -> None:
        self.path = path

    def _request(self, payload: dict, timeout: float = 4.0) -> dict:
        try:
            with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as sock:
                sock.settimeout(timeout)
                sock.connect(str(self.path))
                sock.sendall(json.dumps(payload).encode("utf-8") + b"\n")
                chunks = bytearray()
                while b"\n" not in chunks:
                    part = sock.recv(65536)
                    if not part:
                        break
                    chunks.extend(part)
                    if len(chunks) > 4 * 1024 * 1024:
                        raise ControlError("特权服务响应过大")
        except (OSError, socket.timeout) as exc:
            raise ControlError(localize_error(f"helper is not reachable: {exc}")) from exc
        try:
            response = json.loads(chunks.decode("utf-8").strip() or "{}")
        except ValueError as exc:
            raise ControlError("特权服务返回无效响应") from exc
        if not response.get("ok"):
            raise ControlError(localize_error(response.get("error") or "helper request failed"))
        return response.get("data") or {}

    def available(self, timeout: float = 0.4) -> bool:
        try:
            self._request({"cmd": "ping"}, timeout=timeout)
            return True
        except ControlError:
            return False

    def state(self) -> dict:
        return self._request({"cmd": "state"}, timeout=6.0)

    def call(self, cmd: str, **params: Any) -> dict:
        return self._request({"cmd": cmd, **params}, timeout=8.0)


class ControlManager:
    def __init__(self) -> None:
        self.helper = HelperClient()
        self.direct: Optional[PrivilegedBackend] = None
        self.mode = "monitor"
        self.caps: dict = quick_capabilities()
        self.last_state: dict = {}
        self.last_error: Optional[str] = None

    # ------------------------------------------------------------------
    def connect(self) -> str:
        if self.helper.available():
            try:
                state = self.helper.state()
            except ControlError as exc:
                self.last_error = str(exc)
                self.mode = "monitor"
                self.caps = quick_capabilities()
                return self.mode
            self.mode = "helper"
            self.caps = state.get("caps", {})
            self.last_state = state
            return self.mode
        if os.geteuid() == 0:
            self.direct = PrivilegedBackend()
            self.mode = "root"
            try:
                self.caps = self.direct.capabilities()
            except Exception as exc:  # noqa: BLE001 - shown in the UI
                self.last_error = str(exc)
            return self.mode
        self.mode = "monitor"
        self.caps = quick_capabilities()
        return self.mode

    @property
    def mode_label(self) -> str:
        return {
            "helper": "控制已启用",
            "root": "Root 直连模式",
            "monitor": "仅监控",
        }.get(self.mode, self.mode)

    @property
    def can_control(self) -> bool:
        return self.mode in {"helper", "root"}

    # ------------------------------------------------------------------
    def _call(self, cmd: str, **params: Any) -> dict:
        if self.mode == "helper":
            return self.helper.call(cmd, **params)
        if self.mode == "root" and self.direct is not None:
            return self.direct.dispatch(cmd, params)
        raise ControlError("特权服务未运行（仅监控模式）")

    def state(self) -> dict:
        if self.mode == "helper":
            try:
                state = self.helper.state()
                self.caps = state.get("caps", self.caps)
                self.last_state = state
                return state
            except ControlError as exc:
                self.last_error = str(exc)
                raise
        if self.mode == "root" and self.direct is not None:
            state = self.direct.state()
            self.last_state = state
            return state
        state = self._monitor_state()
        self.last_state = state
        return state

    def _monitor_state(self) -> dict:
        lightbar = _read_led(
            _find_led("uniwill:multicolor:status", "uniwill:rgb:status", "uniwill:*:status")
        )
        kbd_led = _read_led(
            _find_led(
                "uniwill:multicolor:kbd_backlight",
                "uniwill:rgb:kbd_backlight",
                "uniwill:white:kbd_backlight",
                "uniwill:*kbd_backlight*",
            )
        )
        battery = None
        battery_limit = None
        for candidate in Path("/sys/class/power_supply").glob("BAT*"):
            limit_path = candidate / "charge_control_end_threshold"
            if limit_path.exists():
                battery = candidate
                battery_limit = read_int(limit_path)
                break
        platform = _find_platform()
        return {
            "caps": quick_capabilities(),
            "lightbar": lightbar,
            "kbd_led": kbd_led,
            "battery": {
                "limit": battery_limit,
                "status": read_text(battery / "status") if battery else None,
                "capacity": read_int(battery / "capacity") if battery else None,
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
            "ec": {"ec": False, "error": self.last_error},
        }

    # ------------------------------------------------------------------
    def set_lightbar(self, rgb: tuple[int, int, int], percent: float) -> dict:
        return self._call("set_lightbar", rgb=list(rgb), percent=percent)

    def set_kbd_led(self, rgb: tuple[int, int, int], percent: float) -> dict:
        return self._call("set_kbd_led", rgb=list(rgb), percent=percent)

    def set_keyboard(
        self,
        rgb: Optional[tuple[int, int, int]] = None,
        level: Optional[int] = None,
    ) -> dict:
        params: dict[str, Any] = {}
        if rgb is not None:
            params["rgb"] = list(rgb)
        if level is not None:
            params["level"] = level
        return self._call("set_keyboard", **params)

    def set_animations(
        self,
        rainbow: Optional[bool] = None,
        breathing: Optional[bool] = None,
    ) -> dict:
        params: dict[str, Any] = {}
        if rainbow is not None:
            params["rainbow"] = bool(rainbow)
        if breathing is not None:
            params["breathing"] = bool(breathing)
        return self._call("set_animations", **params)

    def set_profile(self, mode: str) -> dict:
        return self._call("set_profile", mode=mode)

    def set_gpu_power(self, watts: int) -> dict:
        return self._call("set_gpu_power", watts=int(watts))

    def set_fan_boost(self, enabled: bool) -> dict:
        return self._call("set_fan_boost", enabled=bool(enabled))

    def set_fan_guard(self, enabled: bool, threshold: int) -> dict:
        return self._call(
            "set_fan_guard", enabled=bool(enabled), threshold=int(threshold)
        )

    def set_fan_curve(self, points, enabled: bool) -> dict:
        return self._call(
            "set_fan_curve",
            points=[
                point.as_dict() if hasattr(point, "as_dict") else point
                for point in points
            ],
            enabled=bool(enabled),
        )

    def set_battery_limit(self, limit: int) -> dict:
        return self._call("set_battery_limit", limit=int(limit))

    def set_toggle(self, name: str, enabled: bool) -> dict:
        return self._call("set_toggle", name=name, enabled=bool(enabled))


# ----------------------------------------------------------------------
# local read-only helpers
# ----------------------------------------------------------------------
def _find_led(*names: str) -> Optional[Path]:
    for name in names:
        for candidate in Path("/sys/class/leds").glob(name):
            if candidate.is_dir():
                return candidate
    return None


def _read_led(led: Optional[Path]) -> Optional[dict]:
    if not led:
        return None
    rgb: list[int] = []
    text = read_text(led / "multi_intensity")
    if text:
        try:
            rgb = [int(part) for part in text.split()[:3]]
        except ValueError:
            rgb = []
    return {
        "brightness": read_int(led / "brightness"),
        "max_brightness": read_int(led / "max_brightness") or 1,
        "rgb": rgb,
        "max_intensity": read_int(led / "multi_max_intensity") or 200,
    }


def _find_platform() -> Optional[Path]:
    for entry in Path("/sys/devices/platform").glob("INOU*"):
        if (entry / "rainbow_animation").exists():
            return entry
    return None


def _read_bool(path: Path) -> Optional[bool]:
    value = read_int(path)
    return None if value is None else bool(value)
