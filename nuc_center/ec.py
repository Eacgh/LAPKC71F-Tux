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
    0x0743     GPU power control flags (enable / Dynamic Boost / TGP)
    0x0744     GPU CTGP offset in watts
    0x0745     Dynamic Boost companion value (45 when enabled)
    0x0746     Dynamic Boost watts
    0x0748     lightbar control
    0x0749..   lightbar RGB
    0x074E     BIOS OEM (fn lock)
    0x0751     manual fan control / firmware performance mode
    0x0766     support 2 (RGB keyboard, China mode)
    0x0767     trigger register
    0x0768     switch status
    0x0769..   keyboard RGB intensity
    0x0782     BIOS OEM 2 (China mode enable)
    0x0783     CPU PL1 (watts)
    0x0784     CPU PL2 (watts)
    0x078C     keyboard status / brightness
    0x07AB     performance-mode indicator LED
"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path
from typing import Optional

from .fan import interpolate, normalize_points
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

# GPU power budget block.  NUC Studio writes these four registers when the
# user picks a performance profile; the NVIDIA driver only reads the result,
# which is why ``nvidia-smi -pl`` has no effect on this laptop.
EC_GPU_TGP_CONTROL = 0x0743
EC_GPU_CTGP = 0x0744
EC_GPU_DB_CAP = 0x0745
EC_GPU_DB_WATT = 0x0746
EC_CPU_PL1 = 0x0783
EC_CPU_PL2 = 0x0784
EC_MODE_LED = 0x07AB

# EC fan-table block (16 slots, slot 0 is a fixed sentinel).  Writing the
# tables and clearing the duty of slot 0 makes the EC run the curve on its
# own; slot 0 duty 0xFF hands control back to the EC's built-in curve.
EC_FAN_CPU_UP = 0x0F00
EC_FAN_CPU_DOWN = 0x0F10
EC_FAN_CPU_DUTY = 0x0F20
EC_FAN_GPU_UP = 0x0F30
EC_FAN_GPU_DOWN = 0x0F40
EC_FAN_GPU_DUTY = 0x0F50
EC_FAN_SPLIT = 0x07C5
EC_FAN_TABLE_ENABLE = 0x07C6
EC_FAN_SPLIT_ENABLE = 0x80
EC_FAN_TABLE_ENABLE_BIT = 0x04
EC_FAN_MODE_MANUAL_MASK = 0x40
FAN_TABLE_SLOTS = 15

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

# The NUC X15 uses a different encoding in EC_MANUAL_FAN_CTRL.  Both values
# below were read back from a LAPKC71F running NUC Studio: Balanced leaves
# 0x0751 at 0x00, Performance sets 0x10 (the firmware's "Turbo" bit).  The
# previous 0x10/0x20/0x30 table was wrong: 0x10 made the "quiet" button pick
# Turbo, and 0x20/0x30 are not part of the firmware's fan-control flags.
# This firmware has no separate "quiet" byte; a real quiet profile needs the
# EC fan table (0xF00 block), which is not implemented yet.
NUC_PERF_MODE_VALUES = {
    "balanced": 0x00,
    "performance": 0x10,
}

# GPU power budget: (CTGP offset W, Dynamic Boost W, 0x0745 companion value).
# Read back from NUC Studio: BatterySaver 80 W, Balanced 100 W, Performance
# 125 W, Benchmark 145 W.  Effective TGP = 80 + CTGP + Dynamic Boost.
GPU_BASE_POWER = 80
GPU_POWER_MODES = {
    80: (0, 0, 0),
    100: (0, 20, 45),
    125: (25, 20, 45),
    145: (45, 20, 45),
}
GPU_CTRL_ENABLE = 0x01
GPU_CTRL_DYNAMIC_BOOST = 0x02
GPU_CTRL_TGP = 0x04

# Full power profiles, matching the three EC default PL tables:
# 0x0730/0x0731 = 45/65 W, 0x0734/0x0735 = 30/60 W, 0x07A7/0x07A8 = 65/90 W.
POWER_PROFILES = {
    "quiet": {"gpu_power": 80, "pl1": 30, "pl2": 60, "fan": 0x00, "led": 0x03},
    "balanced": {"gpu_power": 100, "pl1": 45, "pl2": 65, "fan": 0x00, "led": 0x02},
    "performance": {"gpu_power": 125, "pl1": 65, "pl2": 90, "fan": 0x10, "led": 0x01},
}


def gpu_power_registers(watts: int, current_control: int = 0) -> dict:
    """Return the EC writes that select ``watts`` on a LAPKC71F.

    0x0743 is a flag byte (bit0 enable, bit1 Dynamic Boost, bit2 TGP
    offset); bits outside those three are preserved.  The order matches what
    NUC Studio does: values first, 0x0743 last.
    """
    if watts not in GPU_POWER_MODES:
        raise ValueError(f"unsupported GPU power limit: {watts} W")
    ctgp, dynamic_boost, companion = GPU_POWER_MODES[watts]
    control = current_control & ~(GPU_CTRL_DYNAMIC_BOOST | GPU_CTRL_TGP)
    control |= GPU_CTRL_ENABLE
    if dynamic_boost:
        control |= GPU_CTRL_DYNAMIC_BOOST
    if ctgp:
        control |= GPU_CTRL_TGP
    return {
        EC_GPU_CTGP: ctgp,
        EC_GPU_DB_CAP: companion,
        EC_GPU_DB_WATT: dynamic_boost,
        EC_GPU_TGP_CONTROL: control,
    }


def expand_fan_curve(points, slots: int = FAN_TABLE_SLOTS) -> list[tuple[int, int]]:
    """Expand a user curve into the EC's fixed 15-slot table.

    The EC has 15 usable slots (1..15) and runs its own hysteresis, so the
    handful of UI points are sampled evenly over their temperature range.
    """
    curve = normalize_points(points)
    low = curve[0].temp
    high = curve[-1].temp
    if high <= low:
        high = low + 1
    result: list[tuple[int, int]] = []
    for index in range(slots):
        temperature = low + (high - low) * index / (slots - 1)
        result.append((int(round(temperature)), int(interpolate(curve, temperature))))
    return result


def fan_table_writes(points, slots: int = FAN_TABLE_SLOTS) -> list[tuple[int, int]]:
    """Return the ordered EC writes for a 15-point table.

    Slot 0 is fixed (20/17/0).  Duty is the UI percentage multiplied by two,
    matching what the OEM software writes.
    """
    curve = expand_fan_curve(points, slots)
    writes: list[tuple[int, int]] = [
        (EC_FAN_CPU_UP, 20),
        (EC_FAN_CPU_DOWN, 17),
        (EC_FAN_CPU_DUTY, 0),
        (EC_FAN_GPU_UP, 20),
        (EC_FAN_GPU_DOWN, 17),
        (EC_FAN_GPU_DUTY, 0),
    ]
    for index, (temperature, percent) in enumerate(curve, start=1):
        up = max(0, min(255, int(temperature)))
        down = max(0, up - 3)
        duty = max(0, min(200, int(percent) * 2))
        writes.extend(
            (
                (EC_FAN_CPU_UP + index, up),
                (EC_FAN_CPU_DOWN + index, down),
                (EC_FAN_CPU_DUTY + index, duty),
                (EC_FAN_GPU_UP + index, up),
                (EC_FAN_GPU_DOWN + index, down),
                (EC_FAN_GPU_DUTY + index, duty),
            )
        )
    return writes

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
        self._fan_table_active = False

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
            "gpu_power": None,
            "gpu_control": None,
            "gpu_ctgp": None,
            "gpu_db": None,
            "fan_table": None,
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
        gpu_ctrl = get(EC_GPU_TGP_CONTROL)
        gpu_ctgp = get(EC_GPU_CTGP)
        gpu_db = get(EC_GPU_DB_WATT)
        fan_duty = get(EC_FAN_CPU_DUTY)
        fan_table = None if fan_duty is None else fan_duty != 0xFF
        if gpu_ctrl is None:
            gpu_power = None
        elif gpu_ctrl & GPU_CTRL_ENABLE:
            gpu_power = GPU_BASE_POWER + (gpu_ctgp or 0) + (gpu_db or 0)
        else:
            gpu_power = GPU_BASE_POWER

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
            gpu_power=gpu_power,
            gpu_control=gpu_ctrl,
            gpu_ctgp=gpu_ctgp,
            gpu_db=gpu_db,
            fan_table=fan_table,
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

    def set_gpu_power(self, watts: int) -> dict:
        """Set the GPU power ceiling (80/100/125/145 W on this model).

        The EC owns the budget; the NVIDIA driver picks it up without a
        reload.  Values are only written after a project-id check so the
        action refuses to run on a different Uniwill machine.
        """
        if self.read(EC_PROJECT_ID) != 0x08:
            raise ECError("GPU power control is only supported on the LAPKC71F")
        current = self.read(EC_GPU_TGP_CONTROL)
        writes = gpu_power_registers(watts, current)
        for address in (EC_GPU_CTGP, EC_GPU_DB_CAP, EC_GPU_DB_WATT):
            self.write(address, writes[address])
        self.write(EC_GPU_TGP_CONTROL, writes[EC_GPU_TGP_CONTROL])
        time.sleep(0.3)
        observed = {address: self.read(address) for address in writes}
        if any(observed[address] != value for address, value in writes.items()):
            raise ECError("the EC did not accept the GPU power limit")
        return {
            "gpu_power": watts,
            "gpu_control": observed[EC_GPU_TGP_CONTROL],
            "gpu_ctgp": observed[EC_GPU_CTGP],
            "gpu_db": observed[EC_GPU_DB_WATT],
        }

    def set_power_profile(self, mode: str, apply_fan: bool = True) -> dict:
        """Apply a full Windows-style profile (CPU PL, GPU TGP, fan, LED).

        ``apply_fan=False`` keeps the EC fan table in charge when the user
        has enabled a custom curve.
        """
        if mode not in POWER_PROFILES:
            raise ValueError(f"unknown power profile: {mode}")
        profile = POWER_PROFILES[mode]
        gpu = self.set_gpu_power(profile["gpu_power"])
        self.write(EC_CPU_PL1, profile["pl1"])
        self.write(EC_CPU_PL2, profile["pl2"])
        if apply_fan:
            fan = self.read(EC_MANUAL_FAN_CTRL)
            self.write(
                EC_MANUAL_FAN_CTRL,
                (fan & ~PERF_MODE_MASK & 0xFF) | profile["fan"],
            )
            led = self.read(EC_MODE_LED)
            self.write(EC_MODE_LED, (led & 0xF0) | profile["led"])
        self._last_perf_mode = mode
        return {
            "perf_mode": mode,
            "gpu_power": profile["gpu_power"],
            "pl1": profile["pl1"],
            "pl2": profile["pl2"],
            "gpu": gpu,
        }

    def set_fan_table(self, points) -> dict:
        """Upload a fan curve and let the EC execute it autonomously.

        The sequence matches NUCtool: manual mode is bit 6 of 0x0751 cleared,
        0x07C5 bit 7 enables the split CPU/GPU tables, 0x07C6 bit 2 enables
        the table engine, and duty slot 0 must be 0 (0xFF means "EC auto").
        """
        writes = fan_table_writes(points)
        mode = self.read(EC_MANUAL_FAN_CTRL)
        # The table only runs in Normal mode: clear Turbo/High/User.  The
        # mode LED low nibble also forces a fan mode (1 -> Turbo), so keep
        # it at Standard while the custom table is in charge.
        if mode & PERF_MODE_MASK:
            self.write(EC_MANUAL_FAN_CTRL, mode & ~PERF_MODE_MASK & 0xFF)
        led = self.read(EC_MODE_LED)
        if led & 0x0F != 0x02:
            self.write(EC_MODE_LED, (led & 0xF0) | 0x02)
        ap_oem = self.read(EC_AP_OEM)
        if not ap_oem & AP_OEM_ENABLE_MANUAL_CTRL:
            self.write(EC_AP_OEM, ap_oem | AP_OEM_ENABLE_MANUAL_CTRL)
        self.write(EC_FAN_SPLIT, EC_FAN_SPLIT_ENABLE)
        self.write(EC_FAN_TABLE_ENABLE, EC_FAN_TABLE_ENABLE_BIT)
        for address, value in writes:
            self.write(address, value)
        time.sleep(0.05)
        self._fan_table_active = True
        return {"fan_table": True, "fan_points": len(writes) // 6 - 1}

    def clear_fan_table(self) -> dict:
        """Hand the fans back to the EC's built-in curve."""
        self.write(EC_FAN_CPU_DUTY, 0xFF)
        self.write(EC_FAN_GPU_DUTY, 0xFF)
        for index in range(16):
            self.write(EC_FAN_CPU_UP + index, 0)
            self.write(EC_FAN_CPU_DOWN + index, 0)
            self.write(EC_FAN_GPU_UP + index, 0)
            self.write(EC_FAN_GPU_DOWN + index, 0)
        self.write(EC_FAN_SPLIT, 0)
        self.write(EC_FAN_TABLE_ENABLE, 0)
        self._fan_table_active = False
        return {"fan_table": False}

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
