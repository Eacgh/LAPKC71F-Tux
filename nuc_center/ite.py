"""ITE 8291 RGB keyboard controller (userspace hidraw driver).

The NUC X15 keyboard backlight is not wired to the Uniwill EC: it is a
separate USB HID device ``048d:6006 ITE Device(8291)``.  The protocol below
is the same one used by the in-tree/out-of-tree ``hid-ite8291r3`` driver:

* 9-byte HID feature reports, report id 0
* command 8  = set effect / direct colour
* command 9  = set brightness (0..50)
* command 22 = select row, followed by a 65-byte output report containing
  blue/green/red values for 6 rows x 21 columns.

The helper is root, so it can open ``/dev/hidrawN`` directly.  The installer
also adds a udev rule that makes the node writable by the ``wheel`` group.
"""

from __future__ import annotations

import fcntl
import glob
import os
import re
import threading
import time
from pathlib import Path
from typing import Optional

from .util import clamp

ITE_IDS = {
    (0x048D, 0x6004),
    (0x048D, 0x6006),
    (0x048D, 0x600B),
    (0x048D, 0xCE00),
}

HID_REPORT_LEN = 9
ROWS = 6
COLS = 21
ROW_REPORT_LEN = 2 + 3 * COLS
MAX_BRIGHTNESS = 50
MAX_LEVEL = 4

OP_SET_EFFECT = 8
OP_SET_BRIGHTNESS = 9
OP_GET_EFFECT = 136
OP_SET_ROW_INDEX = 22

# _IOWR('H', nr, size)
def _iowr(nr: int, size: int) -> int:
    return (3 << 30) | (size << 16) | (ord("H") << 8) | nr


IOCTL_SET_FEATURE = _iowr(0x06, HID_REPORT_LEN)
IOCTL_GET_FEATURE = _iowr(0x07, HID_REPORT_LEN)
IOCTL_OUTPUT = _iowr(0x0B, ROW_REPORT_LEN)


class IteError(RuntimeError):
    pass


class IteKeyboard:
    def __init__(self) -> None:
        self.path: Optional[Path] = None
        self.fd: Optional[int] = None
        self.last_color: Optional[tuple[int, int, int]] = None
        self.last_brightness: Optional[int] = None
        self._lock = threading.RLock()
        self._find()

    # ------------------------------------------------------------------
    def _find(self) -> None:
        for entry in sorted(Path("/sys/class/hidraw").glob("hidraw*")):
            try:
                uevent = (entry / "device/uevent").read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            match = re.search(r"HID_ID=0003:([0-9A-Fa-f]{8}):([0-9A-Fa-f]{8})", uevent)
            if not match:
                continue
            vid, pid = int(match.group(1), 16), int(match.group(2), 16)
            if (vid, pid) in ITE_IDS:
                self.path = Path("/dev") / entry.name
                return
        self.path = None

    @property
    def available(self) -> bool:
        return self.path is not None and self.path.exists()

    def _open(self) -> int:
        if not self.available:
            self._find()
        if not self.available or self.path is None:
            raise IteError("ITE 8291 HID keyboard was not found")
        if self.fd is None:
            try:
                self.fd = os.open(str(self.path), os.O_RDWR)
            except OSError as exc:
                raise IteError(f"cannot open {self.path}: {exc}") from exc
        return self.fd

    def close(self) -> None:
        with self._lock:
            if self.fd is not None:
                try:
                    os.close(self.fd)
                except OSError:
                    pass
                self.fd = None

    # ------------------------------------------------------------------
    def _send_feature(self, payload: list[int]) -> None:
        buf = bytearray(HID_REPORT_LEN)
        for index, value in enumerate(payload[:HID_REPORT_LEN]):
            buf[index] = value & 0xFF
        fd = self._open()
        try:
            fcntl.ioctl(fd, IOCTL_SET_FEATURE, buf, True)
        except OSError as exc:
            self.close()
            raise IteError(f"ITE feature report failed: {exc}") from exc

    def _get_feature(self) -> bytes:
        buf = bytearray(HID_REPORT_LEN)
        fd = self._open()
        try:
            fcntl.ioctl(fd, IOCTL_GET_FEATURE, buf, True)
        except OSError as exc:
            self.close()
            raise IteError(f"ITE feature read failed: {exc}") from exc
        return bytes(buf)

    def get_brightness(self) -> int:
        with self._lock:
            try:
                self._send_feature([0, OP_GET_EFFECT, 0, 0, 0, 0, 0, 0, 0])
                value = self._get_feature()[5]
                self.last_brightness = value
                return value
            except IteError:
                if self.last_brightness is not None:
                    return self.last_brightness
                raise

    def set_brightness(self, value: int) -> int:
        value = int(clamp(value, 0, MAX_BRIGHTNESS))
        with self._lock:
            self._send_feature([0, OP_SET_BRIGHTNESS, 0x02, value, 0, 0, 0, 0, 0])
            self.last_brightness = value
            return value

    def set_color(self, rgb: tuple[int, int, int]) -> tuple[int, int, int]:
        red, green, blue = (int(clamp(channel, 0, 255)) for channel in rgb)
        with self._lock:
            try:
                brightness = self.get_brightness()
            except IteError:
                brightness = self.last_brightness if self.last_brightness is not None else 25
            # Direct colour mode.  The value 0x33 is what the hid-ite8291r3
            # driver uses for a static per-row colour.
            self._send_feature([0, OP_SET_EFFECT, 0x02, 0x33, 0, brightness, 0, 0, 0])
            row = bytearray(ROW_REPORT_LEN)
            # Byte 0 is the output report id, byte 1 is reserved.  The
            # controller expects blue first, then green, then red.
            row[2 : 2 + COLS] = bytes([blue]) * COLS
            row[2 + COLS : 2 + 2 * COLS] = bytes([green]) * COLS
            row[2 + 2 * COLS : 2 + 3 * COLS] = bytes([red]) * COLS
            fd = self._open()
            for index in range(ROWS):
                self._send_feature([0, OP_SET_ROW_INDEX, 0x00, index, 0, 0, 0, 0, 0])
                out = bytearray(row)
                try:
                    fcntl.ioctl(fd, IOCTL_OUTPUT, out, True)
                except OSError as exc:
                    self.close()
                    raise IteError(f"ITE output report failed: {exc}") from exc
            self.last_color = (red, green, blue)
            return self.last_color

    # ------------------------------------------------------------------
    def apply(
        self,
        rgb: Optional[tuple[int, int, int]] = None,
        level: Optional[int] = None,
    ) -> dict:
        """Set colour and/or the 0-4 brightness level used by the UI."""
        with self._lock:
            if level is not None:
                level = int(clamp(level, 0, MAX_LEVEL))
                brightness = round(level / MAX_LEVEL * MAX_BRIGHTNESS)
            else:
                brightness = None

            # Turn the keyboard on before writing the colour when the
            # requested level is non-zero, otherwise some controllers ignore
            # the row data while their brightness is 0.
            if brightness is not None and brightness > 0:
                self.set_brightness(brightness)
            if rgb is not None:
                self.set_color(tuple(rgb))
            if brightness is not None:
                self.set_brightness(brightness)
            elif rgb is not None:
                # Preserve the current brightness while changing colour.
                try:
                    self.set_brightness(self.get_brightness())
                except IteError:
                    pass

            current = self.last_brightness
            if current is None:
                try:
                    current = self.get_brightness()
                except IteError:
                    current = 0
            return {
                "brightness": current,
                "level": int(round(current / MAX_BRIGHTNESS * MAX_LEVEL)),
                "color": self.last_color,
                "available": True,
            }

    def state(self) -> dict:
        if not self.available:
            return {"available": False, "brightness": None, "level": None, "color": None}
        try:
            brightness = self.get_brightness()
        except IteError:
            brightness = self.last_brightness
        return {
            "available": True,
            "brightness": brightness,
            "level": (
                int(round(brightness / MAX_BRIGHTNESS * MAX_LEVEL))
                if brightness is not None
                else None
            ),
            "color": self.last_color,
        }
