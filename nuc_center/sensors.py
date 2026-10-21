"""Read-only hardware telemetry.

Everything in this module is available to a normal user.  It deliberately
does not write to sysfs; privileged actions live in ``priv.py``.
"""

from __future__ import annotations

import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Optional

from .util import (
    find_hwmon,
    read_float,
    read_int,
    read_text,
    run_command,
)

CPU_BASE = Path("/sys/devices/system/cpu")


@dataclass
class BatteryInfo:
    present: bool = False
    capacity: Optional[int] = None
    status: str = "Unknown"
    charge_now: Optional[int] = None          # µAh
    charge_full: Optional[int] = None         # µAh
    charge_full_design: Optional[int] = None  # µAh
    voltage: Optional[float] = None           # V
    current: Optional[float] = None           # A
    power: Optional[float] = None             # W
    health: str = "Unknown"
    cycles: Optional[int] = None
    charge_limit: Optional[int] = None
    ac_online: bool = False
    technology: str = "Unknown"

    @property
    def health_percent(self) -> Optional[float]:
        if not self.charge_full or not self.charge_full_design:
            return None
        return self.charge_full / self.charge_full_design * 100.0

    @property
    def energy_now(self) -> Optional[float]:
        if self.charge_now is None or self.voltage is None:
            return None
        return self.charge_now * self.voltage / 1_000_000.0

    @property
    def energy_full(self) -> Optional[float]:
        if self.charge_full is None or self.voltage is None:
            return None
        return self.charge_full * self.voltage / 1_000_000.0

    @property
    def time_remaining(self) -> Optional[float]:
        """Seconds until empty/full, very approximate."""
        if not self.power or self.power <= 0.1:
            return None
        if self.status.lower().startswith("discharg"):
            energy = self.energy_now
            return energy / self.power * 3600.0 if energy else None
        if self.status.lower().startswith("charg"):
            energy = self.energy_full
            if energy and self.energy_now is not None:
                return max(0.0, (energy - self.energy_now) / self.power * 3600.0)
        return None


@dataclass
class Snapshot:
    timestamp: float = field(default_factory=time.time)
    cpu_temp: Optional[float] = None
    gpu_temp: Optional[float] = None
    ec_cpu_temp: Optional[float] = None
    ec_gpu_temp: Optional[float] = None
    cpu_cores: list[tuple[str, float]] = field(default_factory=list)
    fan_main: Optional[int] = None
    fan_secondary: Optional[int] = None
    pwm_main: Optional[float] = None
    pwm_secondary: Optional[float] = None
    nvme: list[tuple[str, float]] = field(default_factory=list)
    wifi_temp: Optional[float] = None
    acpi_temp: Optional[float] = None
    cpu_load: Optional[float] = None
    cpu_freq: Optional[float] = None
    cpu_max_freq: Optional[float] = None
    cpu_governor: str = "--"
    mem_used: Optional[int] = None
    mem_total: Optional[int] = None
    battery: BatteryInfo = field(default_factory=BatteryInfo)
    uptime: Optional[float] = None

    @property
    def mem_percent(self) -> Optional[float]:
        if not self.mem_used or not self.mem_total:
            return None
        return self.mem_used / self.mem_total * 100.0


@dataclass
class DeviceInfo:
    product_name: str = "Unknown"
    product_version: str = ""
    product_sku: str = ""
    sys_vendor: str = ""
    board_name: str = ""
    board_version: str = ""
    bios_version: str = ""
    bios_date: str = ""
    chassis_type: str = ""
    cpu_model: str = "Unknown"
    cpu_cores: int = 0
    cpu_threads: int = 0
    cpu_max_mhz: Optional[float] = None
    memory_total: Optional[int] = None
    kernel: str = ""
    os_name: str = "Linux"
    os_build: str = ""
    gpus: list[str] = field(default_factory=list)
    nvme_models: list[str] = field(default_factory=list)

    @property
    def friendly_name(self) -> str:
        if self.product_name.startswith("LAPKC"):
            return "Intel NUC X15 笔记本"
        return self.product_name

    @property
    def model_line(self) -> str:
        if self.product_name.startswith("LAP"):
            return f"{self.friendly_name} · {self.product_name}"
        return self.product_name


class SensorReader:
    def __init__(self) -> None:
        self.device = self._read_device_info()
        self._last_cpu_times: Optional[tuple[int, int]] = None
        self._uniwill = (find_hwmon("uniwill") or [None])[0]
        self._coretemp = (find_hwmon("coretemp") or [None])[0]
        self._nvme = find_hwmon("nvme")
        self._wifi = (find_hwmon("iwlwifi_1") or [None])[0]
        self._acpitz = (find_hwmon("acpitz") or [None])[0]
        self._nvidia_available = bool(
            list(Path("/proc/driver/nvidia/gpus").glob("*/information"))
        ) or os.path.exists("/dev/nvidia0")

    # ------------------------------------------------------------------
    # static information
    # ------------------------------------------------------------------
    def _read_device_info(self) -> DeviceInfo:
        info = DeviceInfo()
        info.product_name = read_text("/sys/class/dmi/id/product_name", "Unknown") or "Unknown"
        info.product_version = read_text("/sys/class/dmi/id/product_version", "") or ""
        info.product_sku = read_text("/sys/class/dmi/id/product_sku", "") or ""
        info.sys_vendor = read_text("/sys/class/dmi/id/sys_vendor", "") or ""
        info.board_name = read_text("/sys/class/dmi/id/board_name", "") or ""
        info.board_version = read_text("/sys/class/dmi/id/board_version", "") or ""
        info.bios_version = read_text("/sys/class/dmi/id/bios_version", "") or ""
        info.bios_date = read_text("/sys/class/dmi/id/bios_date", "") or ""
        info.chassis_type = read_text("/sys/class/dmi/id/chassis_type", "") or ""

        cpuinfo = read_text("/proc/cpuinfo", "") or ""
        match = re.search(r"^model name\s*:\s*(.+)$", cpuinfo, re.MULTILINE)
        if match:
            info.cpu_model = match.group(1).strip()
        info.cpu_threads = len(re.findall(r"^processor\s*:", cpuinfo, re.MULTILINE))
        info.cpu_cores = self._count_physical_cores()
        info.cpu_max_mhz = self._cpu_max_mhz()
        info.memory_total = self._mem_total()
        info.kernel = os.uname().release
        os_release = read_text("/etc/os-release", "") or ""
        for line in os_release.splitlines():
            if line.startswith("PRETTY_NAME="):
                info.os_name = line.split("=", 1)[1].strip().strip('"')
            elif line.startswith("BUILD_ID="):
                info.os_build = line.split("=", 1)[1].strip().strip('"')
        info.gpus = self._read_gpus()
        info.nvme_models = self._read_nvme_models()
        return info

    def _count_physical_cores(self) -> int:
        core_ids: set[tuple[str, str]] = set()
        for cpu in CPU_BASE.glob("cpu[0-9]*"):
            package = read_text(cpu / "topology/physical_package_id")
            core = read_text(cpu / "topology/core_id")
            if package is not None and core is not None:
                core_ids.add((package, core))
        return len(core_ids) or (os.cpu_count() or 0)

    def _cpu_max_mhz(self) -> Optional[float]:
        freqs: list[int] = []
        for path in CPU_BASE.glob("cpu[0-9]*/cpufreq/cpuinfo_max_freq"):
            value = read_int(path)
            if value:
                freqs.append(value)
        if freqs:
            return max(freqs) / 1000.0
        return None

    @staticmethod
    def _mem_total() -> Optional[int]:
        meminfo = read_text("/proc/meminfo", "") or ""
        match = re.search(r"^MemTotal:\s+(\d+)\s+kB", meminfo, re.MULTILINE)
        return int(match.group(1)) * 1024 if match else None

    def _read_gpus(self) -> list[str]:
        code, stdout, _ = run_command(["lspci"], timeout=2.0)
        gpus: list[str] = []
        if code == 0:
            for line in stdout.splitlines():
                if "VGA compatible controller" in line or "3D controller" in line:
                    name = line.split(": ", 1)[-1]
                    # Drop the trailing "(rev xx)" for a cleaner label.
                    name = re.sub(r"\s*\(rev [0-9a-fA-F]+\)$", "", name)
                    gpus.append(name)
        if not gpus:
            for device in Path("/sys/bus/pci/devices").glob("*"):
                class_id = read_text(device / "class", "")
                if class_id.startswith("0x03"):
                    vendor = read_text(device / "vendor", "")
                    dev = read_text(device / "device", "")
                    gpus.append(f"{vendor}:{dev}")
        return gpus

    @staticmethod
    def _read_nvme_models() -> list[str]:
        models: list[str] = []
        for block in sorted(Path("/sys/block").glob("nvme[0-9]*n[0-9]*")):
            model = read_text(block / "device/model")
            if model and model not in models:
                models.append(model)
        return models

    # ------------------------------------------------------------------
    # dynamic telemetry
    # ------------------------------------------------------------------
    def _battery(self) -> BatteryInfo:
        base = Path("/sys/class/power_supply/BAT0")
        if not base.exists():
            for candidate in Path("/sys/class/power_supply").glob("BAT*"):
                base = candidate
                break
        bat = BatteryInfo(present=base.exists())
        if bat.present:
            bat.capacity = read_int(base / "capacity")
            bat.status = read_text(base / "status", "Unknown") or "Unknown"
            bat.charge_now = read_int(base / "charge_now")
            bat.charge_full = read_int(base / "charge_full")
            bat.charge_full_design = read_int(base / "charge_full_design")
            voltage_uv = read_int(base / "voltage_now")
            current_ua = read_int(base / "current_now")
            bat.voltage = voltage_uv / 1_000_000.0 if voltage_uv is not None else None
            bat.current = current_ua / 1_000_000.0 if current_ua is not None else None
            power_uw = read_int(base / "power_now")
            if power_uw is not None:
                bat.power = abs(power_uw) / 1_000_000.0
            elif bat.voltage is not None and bat.current is not None:
                bat.power = abs(bat.voltage * bat.current)
            bat.health = read_text(base / "health", "Unknown") or "Unknown"
            bat.cycles = read_int(base / "cycle_count")
            bat.charge_limit = read_int(base / "charge_control_end_threshold")
            bat.technology = read_text(base / "technology", "Unknown") or "Unknown"
        ac = Path("/sys/class/power_supply/AC0")
        if not ac.exists():
            for candidate in Path("/sys/class/power_supply").glob("A*"):
                if (candidate / "online").exists():
                    ac = candidate
                    break
        bat.ac_online = bool(read_int(ac / "online", 0))
        return bat

    def _cpu_load(self) -> Optional[float]:
        try:
            first = Path("/proc/stat").read_text(encoding="utf-8").splitlines()[0]
        except OSError:
            return None
        fields = [int(part) for part in first.split()[1:]]
        if len(fields) < 4:
            return None
        idle = fields[3] + (fields[4] if len(fields) > 4 else 0)
        total = sum(fields)
        if self._last_cpu_times is None:
            self._last_cpu_times = (total, idle)
            return None
        last_total, last_idle = self._last_cpu_times
        self._last_cpu_times = (total, idle)
        total_delta = total - last_total
        idle_delta = idle - last_idle
        if total_delta <= 0:
            return None
        return max(0.0, min(100.0, (1.0 - idle_delta / total_delta) * 100.0))

    def _cpu_freq(self) -> tuple[Optional[float], str]:
        freqs: list[int] = []
        for path in CPU_BASE.glob("cpu[0-9]*/cpufreq/scaling_cur_freq"):
            value = read_int(path)
            if value:
                freqs.append(value)
        governor = read_text(CPU_BASE / "cpu0/cpufreq/scaling_governor", "--") or "--"
        if not freqs:
            return None, governor
        return sum(freqs) / len(freqs) / 1000.0, governor

    def _temperatures(self, snap: Snapshot) -> None:
        # The EC reports the CPU/GPU temperatures used by the firmware.
        if self._uniwill:
            snap.ec_cpu_temp = self._temp(self._uniwill, "temp1_input")
            snap.ec_gpu_temp = self._temp(self._uniwill, "temp2_input")
            snap.fan_main = read_int(self._uniwill / "fan1_input")
            snap.fan_secondary = read_int(self._uniwill / "fan2_input")
            pwm1 = read_int(self._uniwill / "pwm1")
            pwm2 = read_int(self._uniwill / "pwm2")
            snap.pwm_main = round(pwm1 / 255.0 * 100.0, 1) if pwm1 is not None else None
            snap.pwm_secondary = round(pwm2 / 255.0 * 100.0, 1) if pwm2 is not None else None

        if self._coretemp:
            snap.cpu_temp = self._temp(self._coretemp, "temp1_input")
            cores: list[tuple[str, float]] = []
            for path in sorted(self._coretemp.glob("temp*_input")):
                index = re.search(r"temp(\d+)_input", path.name)
                if not index:
                    continue
                num = int(index.group(1))
                if num == 1:
                    continue
                label = read_text(self._coretemp / f"temp{num}_label") or f"Core {num - 2}"
                value = read_float(path)
                if value is not None:
                    cores.append((label, value / 1000.0))
            snap.cpu_cores = cores
        if snap.cpu_temp is None:
            snap.cpu_temp = snap.ec_cpu_temp
        if snap.ec_cpu_temp is None:
            snap.ec_cpu_temp = snap.cpu_temp
        snap.gpu_temp = snap.ec_gpu_temp
        if self._nvidia_available:
            temp = self._nvidia_temp()
            if temp is not None:
                snap.gpu_temp = max(snap.gpu_temp or 0.0, temp)

        for index, hwmon in enumerate(self._nvme):
            value = self._temp(hwmon, "temp1_input")
            if value is not None:
                snap.nvme.append((f"NVMe {index + 1}", value))
        if self._wifi:
            snap.wifi_temp = self._temp(self._wifi, "temp1_input")
        if self._acpitz:
            snap.acpi_temp = self._temp(self._acpitz, "temp1_input")

    @staticmethod
    def _temp(hwmon: Path, name: str) -> Optional[float]:
        value = read_float(hwmon / name)
        if value is None:
            return None
        return value / 1000.0

    @staticmethod
    def _nvidia_temp() -> Optional[float]:
        code, stdout, _ = run_command(
            [
                "nvidia-smi",
                "--query-gpu=temperature.gpu",
                "--format=csv,noheader,nounits",
            ],
            timeout=2.0,
        )
        if code != 0:
            return None
        try:
            return float(stdout.splitlines()[0].strip())
        except (ValueError, IndexError):
            return None

    def _memory_used(self) -> tuple[Optional[int], Optional[int]]:
        meminfo = read_text("/proc/meminfo", "") or ""
        total = re.search(r"^MemTotal:\s+(\d+)\s+kB", meminfo, re.MULTILINE)
        available = re.search(r"^MemAvailable:\s+(\d+)\s+kB", meminfo, re.MULTILINE)
        if not total or not available:
            return None, None
        total_bytes = int(total.group(1)) * 1024
        available_bytes = int(available.group(1)) * 1024
        return total_bytes - available_bytes, total_bytes

    def read(self) -> Snapshot:
        snap = Snapshot()
        try:
            self._temperatures(snap)
        except OSError:
            pass
        snap.cpu_load = self._cpu_load()
        snap.cpu_freq, snap.cpu_governor = self._cpu_freq()
        snap.cpu_max_freq = self._cpu_max_mhz()
        snap.mem_used, snap.mem_total = self._memory_used()
        snap.battery = self._battery()
        uptime = read_text("/proc/uptime", "")
        if uptime:
            try:
                snap.uptime = float(uptime.split()[0])
            except ValueError:
                pass
        return snap
