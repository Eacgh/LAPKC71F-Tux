#!/usr/bin/env python3
"""Read or set the GPU power budget through the Uniwill EC (LAPKC71F).

The NVIDIA power limit on this laptop is owned by the EC, not by the driver,
so ``nvidia-smi -pl`` is ignored.  NUC Studio on Windows writes the budget to
0x0743-0x0746; the values below were read back from a real LAPKC71F running
NUC Studio, once in Balanced and once in Performance:

    mode   request   0x0743  0x0744  0x0745  0x0746   nvidia-smi
    80 W   Battery   0x01    0       0       0       80 W
    100 W  Balanced  0x03    0       45      20      100 W
    125 W  Perf.     0x07    25      45      20      121-125 W
    145 W  Benchmark 0x07    45      45      20      145 W (not read back)

``0x0743`` is a flag byte: bit0 = common enable, bit1 = Dynamic Boost,
bit2 = TGP offset.  ``0x0745`` is 45 whenever Dynamic Boost is on (the EC's
companion/cap value), ``0x0746`` is the Dynamic Boost wattage.

Usage (needs root, because /proc/acpi/call is root-only):

    sudo python3 tools/gpu-power.py status
    sudo python3 tools/gpu-power.py set 100
    sudo python3 tools/gpu-power.py set 125
    sudo python3 tools/gpu-power.py restore

``set`` also records the choice in ``/var/lib/nuc-center/gpu-power``; the
NUC Center helper re-applies it at boot, because the BIOS resets 0x0743 when
the machine starts.  ``restore`` writes back the values saved right before the
first ``set`` (``/var/lib/nuc-center/gpu-power.json``) and removes that boot
record.  ``--dry-run`` prints the plan without touching the EC.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from nuc_center.ec import (  # noqa: E402
    EC_AP_OEM,
    EC_CPU_PL1,
    EC_CPU_PL2,
    EC_GPU_CTGP,
    EC_GPU_DB_CAP,
    EC_GPU_DB_WATT,
    EC_GPU_TGP_CONTROL,
    EC_MANUAL_FAN_CTRL,
    EC_MODE_LED,
    EC_PROJECT_ID,
    GPU_CTRL_ENABLE,
    GPU_POWER_MODES,
    AcpiEc,
    gpu_power_registers,
)

LAPKC71F_PROJECT_ID = 0x08
BACKUP = Path("/var/lib/nuc-center/gpu-power.json")
# Shared with the GUI helper: it re-applies this value on start (the EC
# resets 0x0743 at boot).
MODE_STATE = Path("/var/lib/nuc-center/gpu-power")
AC_ONLINE = Path("/sys/class/power_supply/AC0/online")


def fmt(value: int | None) -> str:
    return "--" if value is None else f"0x{value:02X} ({value})"


def read_optional(ec: AcpiEc, address: int) -> int | None:
    try:
        return ec.read(address)
    except Exception:
        return None


def nvidia_smi() -> None:
    # ``--query-gpu=power.limit`` reports N/A on this vBIOS even when the
    # detailed query shows the real ceiling, so parse the detailed output.
    try:
        result = subprocess.run(
            ["nvidia-smi", "-q", "-d", "POWER"],
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        print(f"nvidia-smi 不可用: {exc}")
        return
    if result.returncode != 0:
        print(f"nvidia-smi 失败: {result.stderr.strip() or result.stdout.strip()}")
        return

    def value(label: str) -> str:
        match = re.search(rf"{label}\s*:\s*(.+)$", result.stdout, re.MULTILINE)
        return match.group(1).strip() if match else "--"

    print(
        "GPU 当前 {} / 上限 {}（默认 {}，最大 {}）".format(
            value("Instantaneous Power Draw"),
            value("Current Power Limit"),
            value("Default Power Limit"),
            value("Max Power Limit"),
        )
    )


def show(ec: AcpiEc) -> None:
    print("EC 寄存器:")
    for name, address in (
        ("project id  0x0740", EC_PROJECT_ID),
        ("AP OEM      0x0741", EC_AP_OEM),
        ("TGP control 0x0743", EC_GPU_TGP_CONTROL),
        ("CTGP        0x0744", EC_GPU_CTGP),
        ("DB cap      0x0745", EC_GPU_DB_CAP),
        ("DB watt     0x0746", EC_GPU_DB_WATT),
        ("fan mode    0x0751", EC_MANUAL_FAN_CTRL),
        ("CPU PL1     0x0783", EC_CPU_PL1),
        ("CPU PL2     0x0784", EC_CPU_PL2),
        ("mode LED    0x07AB", EC_MODE_LED),
    ):
        print(f"  {name} = {fmt(read_optional(ec, address))}")

    ctgp = read_optional(ec, EC_GPU_CTGP)
    db = read_optional(ec, EC_GPU_DB_WATT)
    if ctgp is not None and db is not None:
        print(f"\nEC 请求的 TGP = 80 + {ctgp} + {db} = {80 + ctgp + db} W")
    print()
    nvidia_smi()


def save_backup(values: dict[int, int]) -> None:
    BACKUP.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "saved_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
        "registers": {f"0x{address:04X}": value for address, value in values.items()},
    }
    BACKUP.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    BACKUP.chmod(0o600)
    print(f"原值已保存到 {BACKUP}")


def apply(ec: AcpiEc, mode: int, dry_run: bool) -> int:
    if ec.read(EC_PROJECT_ID) != LAPKC71F_PROJECT_ID:
        print(f"EC project id 不是 0x08（LAPKC71F），拒绝写入。")
        return 3

    ap_oem = ec.read(EC_AP_OEM)
    if not ap_oem & GPU_CTRL_ENABLE:
        print(
            f"0x0741 = {fmt(ap_oem)}：EC 未处于 AP OEM 手动控制模式，写入可能被忽略。\n"
            "在 Windows 上该值为 0x01。先确认这一位，再决定是否强写。"
        )

    current_ctrl = ec.read(EC_GPU_TGP_CONTROL)
    writes = gpu_power_registers(mode, current_ctrl)
    print(f"目标: {mode} W")
    for address, value in writes.items():
        print(f"  0x{address:04X} <- {fmt(value)}")
    if dry_run:
        print("--dry-run：未写入 EC。")
        return 0

    originals = {address: ec.read(address) for address in writes}
    if load_backup() is None:
        save_backup({EC_AP_OEM: ap_oem, **originals})

    # 先写数值，最后写控制位；和 Windows 的写入顺序一致。
    for address in (EC_GPU_CTGP, EC_GPU_DB_CAP, EC_GPU_DB_WATT):
        ec.write(address, writes[address])
    ec.write(EC_GPU_TGP_CONTROL, writes[EC_GPU_TGP_CONTROL])

    time.sleep(0.3)
    print("\n回读:")
    ok = True
    for address, expected in writes.items():
        actual = read_optional(ec, address)
        mark = "OK" if actual == expected else "MISMATCH"
        ok = ok and actual == expected
        print(f"  0x{address:04X} = {fmt(actual)}  期望 {fmt(expected)}  [{mark}]")

    print()
    nvidia_smi()
    if not ok:
        print("寄存器没有写成期望值：检查 acpi_call 是否可用、0x0741 是否已使能。")
        return 4
    try:
        MODE_STATE.parent.mkdir(parents=True, exist_ok=True)
        MODE_STATE.write_text(f"{mode}\n", encoding="utf-8")
    except OSError:
        pass
    print(
        "\n提示：NVIDIA 驱动可能只在 GPU 上电/驱动加载时读取 EC 预算。\n"
        "若 nvidia-smi 仍是旧值，先跑几秒 GPU 负载；仍然不变就重启或写一个\n"
        "早期 systemd oneshot 服务。"
    )
    return 0


def load_backup() -> tuple[dict[int, int], dict] | None:
    try:
        payload = json.loads(BACKUP.read_text(encoding="utf-8"))
        values = {
            int(address, 16): int(value)
            for address, value in payload["registers"].items()
        }
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        return None
    return values, payload


def restore(ec: AcpiEc) -> int:
    loaded = load_backup()
    if loaded is None:
        print(f"没有可用的备份文件 {BACKUP}，无法恢复。")
        return 2
    values, payload = loaded
    print(f"从 {BACKUP}（{payload.get('saved_at')}）恢复:")
    for address, value in values.items():
        print(f"  0x{address:04X} <- {fmt(value)}")
    # 0x0743 最后写，避免中间态。
    for address in sorted(values, key=lambda a: (a == EC_GPU_TGP_CONTROL, a)):
        ec.write(address, values[address])
    MODE_STATE.unlink(missing_ok=True)
    time.sleep(0.3)
    print()
    nvidia_smi()
    return 0


def main(argv: list[str]) -> int:
    args = [arg for arg in argv[1:] if arg != "--dry-run"]
    dry_run = "--dry-run" in argv[1:]
    command = args[0] if args else "status"

    if command == "status":
        ec = AcpiEc()
        if not ec.available:
            print(f"EC 不可用: {ec.last_error}")
            print("先安装并加载 acpi_call（pacman -S acpi_call && modprobe acpi_call）。")
            nvidia_smi()
            return 1
        show(ec)
        return 0

    if command == "restore":
        ec = AcpiEc()
        if not ec.available:
            print(f"EC 不可用: {ec.last_error}")
            return 1
        return restore(ec)

    if command == "set":
        if len(args) != 2 or not args[1].isdigit():
            print("用法: sudo python3 tools/gpu-power.py set 80|100|125|145")
            return 2
        mode = int(args[1])
        if mode not in GPU_POWER_MODES:
            print(f"不支持 {mode} W，可选: {', '.join(map(str, GPU_POWER_MODES))}")
            return 2
        if mode == 145:
            print("145 W 是 Benchmark 档，仅适合短时跑分；长期使用请用 100/125 W。")
        if AC_ONLINE.exists() and AC_ONLINE.read_text().strip() != "1":
            print("警告：当前在电池供电，EC 可能会覆盖这里的 TGP 设置。")
        ec = AcpiEc()
        if not ec.available:
            if dry_run:
                print(f"EC 不可用（{ec.last_error}），按控制位 0x00 预演：")
                for address, value in gpu_power_registers(mode, 0).items():
                    print(f"  0x{address:04X} <- {fmt(value)}")
                print("--dry-run：未写入 EC。")
                return 0
            print(f"EC 不可用: {ec.last_error}")
            print("先安装并加载 acpi_call（pacman -S acpi_call && modprobe acpi_call）。")
            return 1
        return apply(ec, mode, dry_run)

    print(__doc__)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
