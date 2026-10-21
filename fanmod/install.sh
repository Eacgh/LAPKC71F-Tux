#!/usr/bin/env bash
# Build and install the experimental Uniwill WMI fan PWM module.
set -euo pipefail

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "Please run as root: sudo $0"
    exit 1
fi

if ! pacman -Q gcc make linux-headers >/dev/null 2>&1; then
    pacman -S --needed --noconfirm gcc make linux-headers
fi

make -C "$SRC"
KVER="$(uname -r)"
install -Dm644 "$SRC/uniwill_pwm.ko" "/usr/lib/modules/$KVER/extra/uniwill_pwm.ko"
depmod -a "$KVER"
echo "uniwill_pwm" > /etc/modules-load.d/nuc-center-pwm.conf
modprobe uniwill_pwm 2>/dev/null || true

if [[ -e /dev/uniwill_pwm ]]; then
    echo "uniwill_pwm installed; /dev/uniwill_pwm is ready."
else
    echo "WARNING: module built but /dev/uniwill_pwm is missing."
    echo "Check: dmesg | tail -20"
fi

systemctl restart nuc-center-helper.service 2>/dev/null || true
