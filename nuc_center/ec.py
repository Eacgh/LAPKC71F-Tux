"""Access to the Uniwill embedded controller through the ACPI ECRR/ECRW methods.

The Linux ``uniwill`` driver talks to the EC through the ``INOU0000`` ACPI
device.  The same methods can be reached from userspace with the ``acpi_call``
kernel module, which is exactly what this module does.

Only root can use ``/proc/acpi/call``.  The privileged helper daemon owns that
access; the GUI never talks to the EC directly unless it is started as root.

Register map (subset, taken from the in-tree ``uniwill-laptop`` driver and the
out-of-tree ``qc71_laptop`` driver):

    0x0400..   battery / temperature / fan telemetry
    0x0740     project id
    0x0741     AP OEM (manual control enable, fan abnormal)
    0x0742     support 5 (fan support)
    0x0748     lightbar control
    0x0749..   lightbar RGB
    0x074E     BIOS OEM (fn lock)
    0x0751     manual fan control / firmware performance mode
    0x0766     support 2 (RGB keyboard, China mode)
    0x0767     trigger register
    0x0768     switch status
    0x0769..   keyboard RGB intensity
    0x0782     BIOS OEM 2 (China mode enable)
    0x078C     keyboard status / brightness
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Optional

from .util import clamp, parse_hex, run_command

# EC registers used by this application.
EC_PROJECT_ID = 0x0740
EC_AP_OEM = 0x0741
EC_SUPPORT_5 = 0x0742
EC_LIGHTBAR_CTRL = 0x0748
EC_LIGHTBAR_RGB = (0x0749, 0x074A, 0x074B)
EC_BIOS_OEM = 0x074E
EC_MANUAL_FAN_CTRL = 0x0751
EC_PWM_1 = 0x075B
EC_PWM_2 = 0x075C
EC_SUPPORT_2 = 0x0766
EC_TRIGGER = 0x0767
EC_SWITCH_STATUS = 0x0768
EC_KBD_RGB = (0x0769, 0x076A, 0x076B)
EC_BIOS_OEM_2 = 0x0782
EC_KBD_STATUS = 0x078C
EC_FAN_CTRL = 0x078E
EC_BIOS_OEM_3 = 0x07A3
EC_OEM_3 = 0x07A5
EC_OEM_4 = 0x07A6
EC_CHARGE_CTRL = 0x07B9

# Bits.
AP_OEM_ENABLE_MANUAL_CTRL = 1 << 0
SUPPORT_5_FAN_TURBO = 1 << 4
SUPPORT_5_FAN = 1 << 5
SUPPORT_2_SILENT_MODE = 1 << 0
SUPPORT_2_RGB_KEYBOARD = 1 << 2
SUPPORT_2_CHINA_MODE = 1 << 5
TRIGGER_FAN_BOOST = 1 << 2
TRIGGER_RGB_APPLY_COLOR = 1 << 5
TRIGGER_RGB_LOGO_EFFECT = 1 << 6
TRIGGER_RGB_RAINBOW_EFFECT = 1 << 7
SWITCH_FAN_BOOST = 1 << 2
KBD_WHITE_ONLY = 1 << 0
KBD_POWER_OFF = 1 << 1
KBD_APPLY = 1 << 4
KBD_BRIGHTNESS_MASK = 0xE0
KBD_BRIGHTNESS_SHIFT = 5
BIOS_OEM_2_ENABLE_CHINA_MODE = 1 << 6

# Firmware performance modes, as stored in EC_MANUAL_FAN_CTRL.
PERF_MODE_MASK = 0xB0  # FAN_MODE_USER | FAN_MODE_HIGH | FAN_MODE_TURBO
PERF_MODE_OFFICE = 0xA0
PERF_MODE_BALANCE = 0x20
PERF_MODE_TURBO = 0x30

PERF_MODE_VALUES = {
    "quiet": PERF_MODE_OFFICE,
    "balanced": PERF_MODE_BALANCE,
    "performance": PERF_MODE_TURBO,
}

# The NUC X15's EC uses a different encoding than the newer Uniwill models:
# 0x10/0x20/0x30 are the three firmware modes and are consumed as one-shot
# commands (the register clears afterwards).
NUC_PERF_MODE_VALUES = {
    "quiet": 0x10,
    "balanced": 0x20,
    "performance": 0x30,
}

KBD_MAX_LEVEL = 4
KBD_MAX_INTENSITY = 50
LIGHTBAR_MAX_INTENSITY = 200
EC_DELAY = 0.006

CALL_PATH = Path("/proc/acpi/call")
_METHOD_CANDIDATES = ("\\_SB.INOU", "\\_SB_.INOU")


class ECError(RuntimeError):
    """Raised when the EC cannot be reached or rejects a request."""


class AcpiEc:
    """Serialized access to the INOU0000 EC methods via acpi_call."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._method_prefix: Optional[str] = None
        self._probed = False
        self._available = False
        self.last_error: Optional[str] = None
        self._kbd_initialised = False
        self._china_mode_enabled = False
        # On the NUC X15 the firmware power-mode register behaves like a
        # command: the EC applies the mode and then clears the bits.  Cache
        # the last mode we set so the UI does not fall back to "balanced"
        # immediately after a successful change.
        self._last_perf_mode: Optional[str] = None

    # ------------------------------------------------------------------
    # low level plumbing
    # ------------------------------------------------------------------
    @property
    def available(self) -> bool:
        if not self._probed:
            self.probe()
        return self._available

    def _ensure_call_path(self) -> None:
        if CALL_PATH.exists():
            return
        # acpi_call is a tiny out-of-tree module.  Try to load it; this only
        # works when we are root (the helper daemon is).
        if os.geteuid() != 0:
            raise ECError("acpi_call is not loaded (root is required to load it)")
        run_command(["modprobe", "acpi_call"], timeout=10.0)
        if not CALL_PATH.exists():
            raise ECError("acpi_call is not available; install the acpi_call package")

    def _call(self, method: str, *args: int) -> str:
        self._ensure_call_path()
        command = " ".join([method, *(f"0x{arg:X}" for arg in args)])
        try:
            with self._lock:
                with CALL_PATH.open("w", encoding="ascii") as handle:
                    handle.write(command + "\n")
                    handle.flush()
                with CALL_PATH.open("r", encoding="ascii", errors="replace") as handle:
                    result = handle.read().strip()
        except OSError as exc:
            raise ECError(f"ACPI call failed: {exc}") from exc
        if "Error" in result or "error" in result or "AE_" in result:
            raise ECError(result or "ACPI call failed")
        return result

    def probe(self) -> bool:
        with self._lock:
            if self._probed:
                return self._available
            self._probed = True
            self._available = False
            for prefix in _METHOD_CANDIDATES:
                try:
                    result = self._call(f"{prefix}.ECRR", EC_PROJECT_ID)
                    value = parse_hex(result)
                except ECError as exc:
                    self.last_error = str(exc)
                    continue
                if value is not None and 0 <= value <= 0xFF:
                    self._method_prefix = prefix
                    self._available = True
                    self.last_error = None
                    return True
                self.last_error = f"unexpected ECRR result: {result!r}"
            return False

    def _require(self) -> str:
        if not self.available or self._method_prefix is None:
            raise ECError(self.last_error or "EC access is unavailable")
        return self._method_prefix

    def read(self, address: int) -> int:
        prefix = self._require()
        result = self._call(f"{prefix}.ECRR", address)
        value = parse_hex(result)
        time.sleep(EC_DELAY)
        if value is None:
            raise ECError(f"cannot parse ECRR result: {result!r}")
        if value > 0xFF:
            raise ECError(f"EC read at 0x{address:04X} returned 0x{value:X}")
        return value

    def write(self, address: int, value: int) -> None:
        prefix = self._require()
        if not 0 <= value <= 0xFF:
            raise ValueError("EC values must fit into one byte")
        self._call(f"{prefix}.ECRW", address, value)
        time.sleep(EC_DELAY)

    # ------------------------------------------------------------------
    # decoded state
    # ------------------------------------------------------------------
    def read_state(self) -> dict:
        state: dict = {
            "ec": False,
            "project_id": None,
            "perf_value": None,
            "perf_mode": None,
            "fan_boost": None,
            "fan_boost_supported": False,
            "fan_support": False,
            "kbd_rgb": None,
            "kbd_brightness": None,
            "kbd_power_off": None,
            "kbd_white_only": None,
            "rgb_keyboard": None,
            "china_mode": None,
            "kbd_status": None,
            "support2": None,
            "support5": None,
            "switch_status": None,
            "lightbar_ctrl": None,
            "lightbar_rgb": None,
            "ap_oem": None,
        }
        if not self.available:
            state["error"] = self.last_error
            return state

        errors: list[str] = []

        def get(address: int) -> Optional[int]:
            try:
                return self.read(address)
            except ECError as exc:
                errors.append(f"0x{address:04X}: {exc}")
                return None

        project = get(EC_PROJECT_ID)
        if project is None:
            state["error"] = self.last_error or "; ".join(errors) or "EC read failed"
            return state

        support2 = get(EC_SUPPORT_2)
        support5 = get(EC_SUPPORT_5)
        perf = get(EC_MANUAL_FAN_CTRL)
        switch = get(EC_SWITCH_STATUS)
        kbd = get(EC_KBD_STATUS)
        kbd_rgb = [get(address) for address in EC_KBD_RGB]
        light_ctrl = get(EC_LIGHTBAR_CTRL)
        light_rgb = [get(address) for address in EC_LIGHTBAR_RGB]
        ap_oem = get(EC_AP_OEM)

        if perf not in (None, 0):
            self._last_perf_mode = perf_mode_from_value(perf)
        perf_mode = self._last_perf_mode or perf_mode_from_value(perf)

        state.update(
            ec=True,
            project_id=project,
            support2=support2,
            support5=support5,
            perf_value=perf,
            perf_mode=perf_mode,
            switch_status=switch,
            fan_boost=(bool(switch & SWITCH_FAN_BOOST) if switch is not None else None),
            fan_boost_supported=(
                bool(support5 & SUPPORT_5_FAN_TURBO) if support5 is not None else False
            ),
            fan_support=bool(support5 & SUPPORT_5_FAN) if support5 is not None else False,
            kbd_status=kbd,
            kbd_rgb=(
                tuple(kbd_rgb) if all(value is not None for value in kbd_rgb) else None
            ),
            kbd_brightness=(
                (kbd & KBD_BRIGHTNESS_MASK) >> KBD_BRIGHTNESS_SHIFT
                if kbd is not None
                else None
            ),
            kbd_power_off=bool(kbd & KBD_POWER_OFF) if kbd is not None else None,
            kbd_white_only=bool(kbd & KBD_WHITE_ONLY) if kbd is not None else None,
            rgb_keyboard=(
                bool(support2 & SUPPORT_2_RGB_KEYBOARD) if support2 is not None else None
            ),
            china_mode=(
                bool(support2 & SUPPORT_2_CHINA_MODE) if support2 is not None else None
            ),
            lightbar_ctrl=light_ctrl,
            lightbar_rgb=(
                tuple(light_rgb)
                if all(value is not None for value in light_rgb)
                else None
            ),
            ap_oem=ap_oem,
        )
        if errors:
            state["error"] = "; ".join(errors)
        return state

    # ------------------------------------------------------------------
    # actions
    # ------------------------------------------------------------------
    def set_perf_mode(self, mode: str, values: Optional[dict] = None) -> dict:
        mode_values = values or PERF_MODE_VALUES
        if mode not in mode_values:
            raise ValueError(f"unknown performance mode: {mode}")
        desired = mode_values[mode]
        current = self.read(EC_MANUAL_FAN_CTRL)
        # Fan boost (bit 6) is mutually exclusive with the firmware power
        # modes on the NUC X15: while it is set the EC ignores commands in
        # bits 4/5, so clear it first.
        if current & 0x40:
            self.write(EC_MANUAL_FAN_CTRL, current & ~0x40 & 0xFF)
            time.sleep(0.05)
            current = self.read(EC_MANUAL_FAN_CTRL)
        value = (current & ~PERF_MODE_MASK & 0xFF) | desired
        if value != current:
            self.write(EC_MANUAL_FAN_CTRL, value)
        time.sleep(0.08)
        readback = self.read(EC_MANUAL_FAN_CTRL)
        # The NUC X15 EC treats these bits as a one-shot command and clears
        # them after a few milliseconds.  A zero readback is therefore a
        # success; anything else must match the requested mode.
        observed = readback & PERF_MODE_MASK
        if observed not in (desired & PERF_MODE_MASK, 0):
            raise ECError(
                "the EC did not accept the performance mode "
                f"(wrote 0x{value:02X}, read 0x{readback:02X})"
            )
        self._last_perf_mode = mode
        return {"perf_value": readback, "perf_mode": mode}

    def set_fan_boost(self, enabled: bool) -> dict:
        current = bool(self.read(EC_SWITCH_STATUS) & SWITCH_FAN_BOOST)
        if current == enabled:
            return {"fan_boost": current}

        # On the NUC X15 bit 6 of EC_MANUAL_FAN_CTRL is the persistent fan
        # boost bit (the value 0x40 was verified to take the fans to ~6000
        # RPM).  The trigger register is kept as a fallback for other ECs.
        value = self.read(EC_MANUAL_FAN_CTRL)
        if enabled:
            value |= 0x40
        else:
            value &= ~0x40
        self.write(EC_MANUAL_FAN_CTRL, value)
        time.sleep(0.15)
        readback = bool(self.read(EC_SWITCH_STATUS) & SWITCH_FAN_BOOST)
        if readback != enabled:
            trigger = self.read(EC_TRIGGER)
            self.write(EC_TRIGGER, trigger | TRIGGER_FAN_BOOST)
            time.sleep(0.15)
            readback = bool(self.read(EC_SWITCH_STATUS) & SWITCH_FAN_BOOST)
        if readback != enabled:
            raise ECError(
                "the EC did not change the fan boost state "
                f"(requested {'on' if enabled else 'off'})"
            )
        return {"fan_boost": readback}

    def _ensure_keyboard(self) -> None:
        if self._kbd_initialised:
            return
        support2 = self.read(EC_SUPPORT_2)
        if not support2 & SUPPORT_2_CHINA_MODE:
            bios_oem2 = self.read(EC_BIOS_OEM_2)
            self.write(EC_BIOS_OEM_2, bios_oem2 | BIOS_OEM_2_ENABLE_CHINA_MODE)
            self._china_mode_enabled = True
        status = self.read(EC_KBD_STATUS)
        self.write(EC_KBD_STATUS, (status | KBD_APPLY) & ~KBD_POWER_OFF)
        self._kbd_initialised = True

    def set_keyboard(
        self,
        rgb: Optional[tuple[int, int, int]] = None,
        level: Optional[int] = None,
    ) -> dict:
        """Set keyboard RGB (0-255 input) and/or brightness level (0-4)."""
        self._ensure_keyboard()
        status = self.read(EC_KBD_STATUS)
        if rgb is not None:
            scaled = tuple(
                max(1, round(clamp(channel, 0, 255) * KBD_MAX_INTENSITY / 255.0))
                for channel in rgb
            )
            # A value of 0x000000 makes the EC restore its default color, so
            # use the faintest non-zero value instead.
            for address, value in zip(EC_KBD_RGB, scaled):
                self.write(address, int(value))
            trigger = self.read(EC_TRIGGER)
            self.write(EC_TRIGGER, trigger | TRIGGER_RGB_APPLY_COLOR)
            status = self.read(EC_KBD_STATUS)

        if level is not None:
            level = int(clamp(level, 0, KBD_MAX_LEVEL))
            status = (status & ~KBD_BRIGHTNESS_MASK) | (level << KBD_BRIGHTNESS_SHIFT)
            if level == 0:
                status |= KBD_POWER_OFF
            else:
                status &= ~KBD_POWER_OFF
        status |= KBD_APPLY
        self.write(EC_KBD_STATUS, status)
        time.sleep(0.04)
        final = self.read(EC_KBD_STATUS)
        return {
            "kbd_status": final,
            "kbd_brightness": (final & KBD_BRIGHTNESS_MASK) >> KBD_BRIGHTNESS_SHIFT,
            "kbd_power_off": bool(final & KBD_POWER_OFF),
            "kbd_rgb": tuple(self.read(addr) for addr in EC_KBD_RGB),
        }


def perf_mode_from_value(value: Optional[int]) -> Optional[str]:
    if value is None:
        return None
    if value & 0x80:
        return "quiet"
    if value & 0x10:
        return "performance"
    return "balanced"


def perf_mode_label(mode: Optional[str]) -> str:
    return {
        "quiet": "Quiet",
        "balanced": "Balanced",
        "performance": "Performance",
    }.get(mode or "", "Unknown")


def read_low_ec_byte(address: int) -> Optional[int]:
    """Read a byte through ec_sys (only addresses 0x00-0xFF).

    This is a small diagnostic fallback; it is not used for control because
    the Uniwill registers live above 0xFF.
    """
    path = Path("/sys/kernel/debug/ec/ec0/io")
    try:
        with path.open("rb") as handle:
            handle.seek(address)
            data = handle.read(1)
        return data[0] if data else None
    except OSError:
        return None


def debug_dump(ec: AcpiEc) -> str:
    """Return a human-readable EC diagnostic block for the About page."""
    state = ec.read_state()
    lines = ["EC access: " + ("yes" if state.get("ec") else "no")]
    if state.get("error"):
        lines.append(f"EC error: {state['error']}")
    for key, value in state.items():
        if key in {"ec", "error"}:
            continue
        if isinstance(value, tuple):
            value = " ".join(f"0x{part:02X}" for part in value)
        elif isinstance(value, int) and not isinstance(value, bool):
            value = f"0x{value:04X} ({value})"
        lines.append(f"{key}: {value}")
    return "\n".join(lines)
