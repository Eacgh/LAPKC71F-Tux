#!/usr/bin/env bash
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "Please run as root: sudo $0"
    exit 1
fi

systemctl disable --now nuc-center-helper.service 2>/dev/null || true
rm -f /etc/systemd/system/nuc-center-helper.service
systemctl daemon-reload

rm -rf /usr/local/lib/nuc-center
rm -rf /run/nuc-center
rm -f /usr/local/bin/nuc-center
rm -f /usr/share/applications/nuc-center.desktop
rm -f /usr/share/icons/hicolor/scalable/apps/nuc-center.svg
rm -f /etc/modules-load.d/nuc-center.conf
rm -f /etc/modules-load.d/nuc-center-pwm.conf
rm -f /etc/udev/rules.d/99-nuc-center-ite.rules

# Experimental fan PWM module.
modprobe -r uniwill_pwm 2>/dev/null || true
rm -f "/usr/lib/modules/$(uname -r)/extra/uniwill_pwm.ko"
depmod -a 2>/dev/null || true

if [[ -n "${SUDO_USER:-}" ]]; then
    user_home="$(getent passwd "$SUDO_USER" | cut -d: -f6)"
    if [[ -n "$user_home" ]]; then
        rm -f "$user_home/.config/autostart/nuc-center.desktop"
    fi
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi

udevadm control --reload-rules 2>/dev/null || true

echo "NUC 中心已卸载。"
echo "The acpi_call package was left installed on purpose; remove it with:"
echo "    sudo pacman -Rns acpi_call"
