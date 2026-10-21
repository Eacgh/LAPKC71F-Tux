#!/usr/bin/env bash
# Install NUC Center and its privileged helper.
#
#   sudo ./install.sh              # ask about acpi_call
#   sudo ./install.sh --with-ec    # install acpi_call without asking
#   sudo ./install.sh --without-ec # monitor-only installation
#
set -euo pipefail

if [[ ${EUID:-$(id -u)} -ne 0 ]]; then
    echo "Please run the installer as root:"
    echo "    sudo $0 $*"
    exit 1
fi

SRC="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PREFIX="/usr/local/lib/nuc-center"
BIN="/usr/local/bin/nuc-center"
SERVICE="/etc/systemd/system/nuc-center-helper.service"
DESKTOP="/usr/share/applications/nuc-center.desktop"
ICON="/usr/share/icons/hicolor/scalable/apps/nuc-center.svg"
WITH_EC="ask"
START_SERVICE=1

for arg in "$@"; do
    case "$arg" in
        --with-ec) WITH_EC="yes" ;;
        --without-ec) WITH_EC="no" ;;
        --no-start) START_SERVICE=0 ;;
        -h|--help)
            sed -n '2,9p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown option: $arg"
            exit 1
            ;;
    esac
done

if ! command -v pacman >/dev/null 2>&1; then
    echo "This installer targets Arch Linux (pacman was not found)." >&2
    exit 1
fi

echo "==> Checking runtime dependencies"
if ! pacman -Q python-cairo >/dev/null 2>&1; then
    echo "Installing python-cairo (needed for the GTK gauges and charts)…"
    pacman -S --needed --noconfirm python-cairo || {
        echo "WARNING: python-cairo could not be installed."
        echo "         NUC Center will use its vendored fallback copy."
    }
fi
for pkg in python-gobject gtk3; do
    if ! pacman -Q "$pkg" >/dev/null 2>&1; then
        echo "Installing $pkg…"
        pacman -S --needed --noconfirm "$pkg"
    fi
done

# ----------------------------------------------------------------------
# acpi_call: EC access for performance modes and keyboard RGB
# ----------------------------------------------------------------------
if [[ "$WITH_EC" == "ask" ]]; then
    echo
    echo "The fan performance modes and the RGB keyboard need access to the"
    echo "Uniwill embedded controller.  This uses the small acpi_call kernel"
    echo "module.  It is safe to install and can be removed later."
    read -r -p "Install acpi_call? [Y/n] " reply || true
    case "${reply:-Y}" in
        n|N|no|NO) WITH_EC="no" ;;
        *) WITH_EC="yes" ;;
    esac
fi

if [[ "$WITH_EC" == "yes" ]]; then
    echo
    echo "==> Setting up acpi_call"
    if ! pacman -Q acpi_call >/dev/null 2>&1; then
        pacman -S --needed --noconfirm acpi_call || {
            echo "WARNING: acpi_call could not be installed. EC features stay disabled." >&2
            WITH_EC="no"
        }
    fi
    if [[ "$WITH_EC" == "yes" ]]; then
        echo "acpi_call" > /etc/modules-load.d/nuc-center.conf
        if modprobe acpi_call 2>/dev/null && [[ -e /proc/acpi/call ]]; then
            printf '\\_SB.INOU.ECRR 0x740\n' > /proc/acpi/call || true
            project_id="$(tr -d '\000' < /proc/acpi/call 2>/dev/null || true)"
            echo "    EC project id: ${project_id:-unavailable}"
            if [[ -z "$project_id" || "$project_id" == *"Error"* ]]; then
                echo "WARNING: the EC did not answer. Check that the installed kernel"
                echo "         matches /lib/modules/$(uname -r)."
            fi
        else
            echo "WARNING: acpi_call did not load. You may need to reboot after a"
            echo "         kernel update. EC features will stay disabled."
        fi
    fi
fi

# ----------------------------------------------------------------------
# application files
# ----------------------------------------------------------------------
echo
echo "==> Installing NUC Center"
install -d "$PREFIX"
rm -rf "$PREFIX/nuc_center" "$PREFIX/assets" "$PREFIX/vendor" "$PREFIX/tools" "$PREFIX/fanmod"
cp -a "$SRC/nuc_center" "$PREFIX/nuc_center"
cp -a "$SRC/assets" "$PREFIX/assets"
cp -a "$SRC/vendor" "$PREFIX/vendor"
cp -a "$SRC/tools" "$PREFIX/tools"
cp -a "$SRC/fanmod" "$PREFIX/fanmod"
install -m 755 "$SRC/nucd.py" "$PREFIX/nucd.py"
install -m 644 "$SRC/README.md" "$PREFIX/README.md"
install -m 755 "$SRC/uninstall.sh" "$PREFIX/uninstall.sh"

cat > "$BIN" <<EOF
#!/bin/sh
export PYTHONPATH="$PREFIX"
exec /usr/bin/python3 -m nuc_center "\$@"
EOF
chmod 755 "$BIN"

install -Dm644 "$SRC/assets/nuc-center.svg" "$ICON"
install -Dm644 "$SRC/nuc-center.desktop" "$DESKTOP"

# Let the wheel group access the ITE 8291 RGB keyboard HID device.  The
# helper itself runs as root, but this also makes it easy to use other
# userspace tools and to debug the controller.
cat > /etc/udev/rules.d/99-nuc-center-ite.rules <<'EOF'
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="048d", ATTRS{idProduct}=="6006", GROUP="wheel", MODE="0660"
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="048d", ATTRS{idProduct}=="6004", GROUP="wheel", MODE="0660"
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="048d", ATTRS{idProduct}=="600b", GROUP="wheel", MODE="0660"
SUBSYSTEM=="hidraw", ATTRS{idVendor}=="048d", ATTRS{idProduct}=="ce00", GROUP="wheel", MODE="0660"
EOF
udevadm control --reload-rules 2>/dev/null || true
udevadm trigger --subsystem-match=hidraw 2>/dev/null || true

# ----------------------------------------------------------------------
# helper service
# ----------------------------------------------------------------------
cat > "$SERVICE" <<EOF
[Unit]
Description=NUC Center privileged helper
Documentation=file://$PREFIX/README.md
After=local-fs.target

[Service]
Type=simple
ExecStart=/usr/bin/python3 $PREFIX/nucd.py
Restart=on-failure
RestartSec=2
RuntimeDirectory=nuc-center
RuntimeDirectoryMode=0755
RuntimeDirectoryPreserve=yes

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
if [[ "$START_SERVICE" -eq 1 ]]; then
    systemctl enable nuc-center-helper.service
    systemctl restart nuc-center-helper.service
else
    systemctl enable nuc-center-helper.service
fi

if command -v update-desktop-database >/dev/null 2>&1; then
    update-desktop-database >/dev/null 2>&1 || true
fi
if command -v gtk-update-icon-cache >/dev/null 2>&1; then
    gtk-update-icon-cache -f -t /usr/share/icons/hicolor >/dev/null 2>&1 || true
fi

# ----------------------------------------------------------------------
echo
echo "NUC Center installed."
echo "  Start the GUI:  nuc-center"
echo "  Helper status:  systemctl status nuc-center-helper.service"
echo "  Helper log:     journalctl -u nuc-center-helper.service -b"
if [[ "$WITH_EC" == "yes" ]]; then
    echo "  Fan modes / Fan Boost: enabled through acpi_call."
else
    echo "  Fan modes / Fan Boost: monitor-only (re-run with --with-ec to enable)."
fi
echo "  RGB keyboard: ITE 8291 HID (/dev/hidraw)."
echo
echo "Tip: add 'nuc-center' to your desktop autostart from Settings → Startup."
