"""NUC Center GUI entry point."""

from __future__ import annotations

import argparse
import fcntl
import json
import logging
import os
import sys
import threading
import time
from collections import deque
from dataclasses import asdict
from pathlib import Path
from typing import Optional

# PyGObject needs Pycairo to convert the cairo.Context passed to draw signals.
# Use the system package when available; fall back to the small vendored copy
# for this machine so the GUI can run before install.sh is executed.
try:
    import cairo  # noqa: F401
except ImportError:
    _vendor = Path(__file__).resolve().parent.parent / "vendor"
    if (_vendor / "cairo").is_dir():
        sys.path.insert(0, str(_vendor))
        try:
            import cairo  # noqa: F401
        except ImportError:
            pass

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from .config import Config  # noqa: E402
from .controls import ControlError, ControlManager  # noqa: E402
from .pages import (  # noqa: E402
    BatteryPage,
    CoolingPage,
    LightingPage,
    OverviewPage,
    SettingsPage,
    make_label,
)
from .sensors import SensorReader  # noqa: E402
from .theme import ThemeManager  # noqa: E402
from .util import APP_NAME, APP_VERSION, format_bytes_gib, format_temp  # noqa: E402
from .widgets import ColorDot, FanIcon, Pill, Toast  # noqa: E402

LOG = logging.getLogger("nuc-center")
_INSTANCE_LOCK = None

ASSETS = Path(__file__).resolve().parent.parent / "assets"
PROJECT_ROOT = Path(__file__).resolve().parent.parent

NAV_ITEMS = [
    ("overview", "概览", "go-home-symbolic"),
    ("cooling", "散热", "weather-windy-symbolic"),
    ("lighting", "灯效", "keyboard-brightness-symbolic"),
    ("battery", "电池", "battery-good-symbolic"),
    ("settings", "设置", "preferences-system-symbolic"),
]


class NucCenterWindow(Gtk.Window):
    def __init__(self, start_page: str = "overview") -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self.set_title(APP_NAME)
        self.set_default_size(1280, 840)
        self.set_size_request(1120, 720)
        self.set_position(Gtk.WindowPosition.CENTER)
        self.set_wmclass("nuc-center", "NUC Center")
        icon = ASSETS / "nuc-center.svg"
        if icon.exists():
            try:
                self.set_icon_from_file(str(icon))
            except GLib.Error:
                pass

        self.config = Config()
        self.theme = ThemeManager()
        self.theme.apply(self.config.get("theme", "dark"))
        self._units = self.config.get("units", "C")

        self.sensors = SensorReader()
        self.device = self.sensors.device
        self.controls = ControlManager()
        self.snapshot = self.sensors.read()
        self.state: dict = {}
        self.history = {
            "cpu": deque(maxlen=90),
            "gpu": deque(maxlen=90),
            "fan_main": deque(maxlen=90),
            "fan_secondary": deque(maxlen=90),
        }
        self._append_history(self.snapshot)

        self._current_page = "overview"
        self._nav_updating = False
        self._syncing = False
        self._connecting = False
        self._last_sync_started = time.monotonic()
        self._poll_source: Optional[int] = None
        self._poll_ms = int(self.config.get("poll_ms", 1000))
        self.pages: dict[str, object] = {}

        self._build_ui()
        self._tick()
        self._schedule_poll()
        GLib.idle_add(self._start_connect)
        self.connect("key-press-event", self._on_key)
        self.connect("destroy", self._on_destroy)
        self._switch_page(start_page, force=True)

    def _on_destroy(self, *_args) -> None:
        if Gtk.main_level() > 0:
            Gtk.main_quit()

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------
    def _build_ui(self) -> None:
        header = Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title(APP_NAME)
        header.get_style_context().add_class("titlebar")
        title_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        title_box.pack_start(make_label(APP_NAME, "section-title"), False, False, 0)
        title_box.pack_start(
            make_label(
                f"{self.device.friendly_name} · {self.device.product_name}", "subtext"
            ),
            False,
            False,
            0,
        )
        header.set_custom_title(title_box)
        self.conn_pill = Pill("正在连接…", "neutral")
        self.conn_pill.set_margin_end(4)
        header.pack_end(self.conn_pill)
        refresh = Gtk.Button()
        refresh.set_image(Gtk.Image.new_from_icon_name("view-refresh-symbolic", Gtk.IconSize.BUTTON))
        refresh.get_style_context().add_class("flat-button")
        refresh.set_tooltip_text("刷新传感器与控制状态")
        refresh.connect("clicked", lambda *_: self._manual_refresh())
        header.pack_end(refresh)
        self.set_titlebar(header)

        main = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        main.pack_start(self._build_sidebar(), False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(160)
        self.stack.set_hexpand(True)
        self.stack.set_vexpand(True)
        main.pack_start(self.stack, True, True, 0)

        self.pages = {
            "overview": OverviewPage(self),
            "cooling": CoolingPage(self),
            "lighting": LightingPage(self),
            "battery": BatteryPage(self),
            "settings": SettingsPage(self),
        }
        for name, page in self.pages.items():
            scroller = Gtk.ScrolledWindow()
            scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
            scroller.set_kinetic_scrolling(True)
            scroller.get_style_context().add_class("page-scroll")
            viewport = Gtk.Viewport()
            viewport.set_shadow_type(Gtk.ShadowType.NONE)
            viewport.set_hexpand(True)
            viewport.get_style_context().add_class("page-viewport")
            page.set_hexpand(True)
            viewport.add(page)
            scroller.add(viewport)
            self.stack.add_named(scroller, name)

        self.overlay = Gtk.Overlay()
        self.overlay.add(main)
        self.add(self.overlay)
        self.toast = Toast(self.overlay)
        self.show_all()
        self._set_active_nav("overview")
        self._update_connection_ui()

    def _build_sidebar(self) -> Gtk.Box:
        sidebar = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        sidebar.get_style_context().add_class("sidebar")
        sidebar.set_size_request(224, -1)

        brand = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        brand.set_margin_bottom(14)
        logo = FanIcon(40)
        logo.set_accent(self.theme.palette.accent)
        brand.pack_start(logo, False, False, 0)
        brand_text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        brand_text.pack_start(make_label(APP_NAME, "brand-title"), False, False, 0)
        brand_text.pack_start(
            make_label(f"{self.device.product_name} · NUC X15", "brand-sub"),
            False,
            False,
            0,
        )
        brand.pack_start(brand_text, True, True, 0)
        sidebar.pack_start(brand, False, False, 0)

        self.nav_buttons: dict[str, Gtk.ToggleButton] = {}
        for name, title, icon in NAV_ITEMS:
            button = Gtk.ToggleButton()
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.get_style_context().add_class("nav-button")
            child = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=11)
            child.pack_start(
                Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.BUTTON),
                False,
                False,
                0,
            )
            child.pack_start(make_label(title), True, True, 0)
            button.add(child)
            button.connect("toggled", self._nav_toggled, name)
            sidebar.pack_start(button, False, False, 0)
            self.nav_buttons[name] = button
        separator = Gtk.Box()
        separator.get_style_context().add_class("nav-separator")
        separator.set_size_request(-1, 1)
        separator.set_margin_top(6)
        separator.set_margin_bottom(6)
        sidebar.pack_start(separator, False, False, 0)

        sidebar.pack_end(self._build_sidebar_footer(), False, False, 0)
        return sidebar

    def _build_sidebar_footer(self) -> Gtk.Box:
        footer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        status = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        self.sidebar_dot = ColorDot(self.theme.palette.warning, 8)
        status.pack_start(self.sidebar_dot, False, False, 0)
        self.sidebar_status = make_label("正在连接…", "sidebar-footer")
        status.pack_start(self.sidebar_status, True, True, 0)
        footer.pack_start(status, False, False, 0)
        footer.pack_start(
            make_label(f"v{APP_VERSION} · Uniwill EC", "sidebar-footer"),
            False,
            False,
            0,
        )
        return footer

    # ------------------------------------------------------------------
    # navigation
    # ------------------------------------------------------------------
    def _nav_toggled(self, button: Gtk.ToggleButton, name: str) -> None:
        if self._nav_updating:
            return
        if not button.get_active():
            if self._current_page == name:
                self._nav_updating = True
                button.set_active(True)
                self._nav_updating = False
            return
        self._nav_updating = True
        for other_name, other in self.nav_buttons.items():
            other.set_active(other_name == name)
        self._nav_updating = False
        self._switch_page(name)

    def _set_active_nav(self, name: str) -> None:
        self._nav_updating = True
        for other_name, button in self.nav_buttons.items():
            button.set_active(other_name == name)
        self._nav_updating = False

    def _switch_page(self, name: str, force: bool = False) -> None:
        if name not in self.pages:
            name = "overview"
        if not force and name == self._current_page:
            return
        self._current_page = name
        self.stack.set_visible_child_name(name)
        self._set_active_nav(name)
        page = self.pages[name]
        page.on_show()  # type: ignore[attr-defined]
        page.update(self.snapshot, self.state)  # type: ignore[attr-defined]

    def _on_key(self, _widget, event) -> bool:
        ctrl = bool(event.state & Gdk.ModifierType.CONTROL_MASK)
        if ctrl and event.keyval in (Gdk.KEY_q, Gdk.KEY_Q):
            Gtk.main_quit()
            return True
        if ctrl and event.keyval in (Gdk.KEY_r, Gdk.KEY_R):
            self._manual_refresh()
            return True
        if event.keyval == Gdk.KEY_F11:
            if self.is_maximized():
                self.unmaximize()
            else:
                self.maximize()
            return True
        if ctrl and Gdk.KEY_1 <= event.keyval <= Gdk.KEY_5:
            index = int(event.keyval - Gdk.KEY_1)
            if index < len(NAV_ITEMS):
                self._switch_page(NAV_ITEMS[index][0])
            return True
        return False

    # ------------------------------------------------------------------
    # refresh / sync
    # ------------------------------------------------------------------
    def _append_history(self, snap) -> None:
        if snap.cpu_temp is not None:
            self.history["cpu"].append(snap.cpu_temp)
        if snap.gpu_temp is not None:
            self.history["gpu"].append(snap.gpu_temp)
        if snap.fan_main is not None:
            self.history["fan_main"].append(snap.fan_main)
        if snap.fan_secondary is not None:
            self.history["fan_secondary"].append(snap.fan_secondary)

    def _tick(self) -> bool:
        try:
            snap = self.sensors.read()
            self.snapshot = snap
            self._append_history(snap)
            for page in self.pages.values():
                page.update(snap, self.state)  # type: ignore[attr-defined]
        except Exception:  # noqa: BLE001 - keep the UI alive
            LOG.exception("sensor refresh failed")
        now = time.monotonic()
        if now - self._last_sync_started > 5.0:
            self.refresh_controls_async()
        return True

    def _schedule_poll(self) -> None:
        if self._poll_source is not None:
            GLib.source_remove(self._poll_source)
        self._poll_source = GLib.timeout_add(self._poll_ms, self._tick)

    def _manual_refresh(self) -> None:
        self._tick()
        self.refresh_controls_async()
        self.toast.show("已刷新")

    # ------------------------------------------------------------------
    # privileged helper connection
    # ------------------------------------------------------------------
    def _start_connect(self) -> bool:
        if self._connecting:
            return False
        self._connecting = True
        self._set_connection("正在连接…", "warn")
        thread = threading.Thread(target=self._connect_worker, daemon=True)
        thread.start()
        return False

    def _connect_worker(self) -> None:
        error = None
        state = None
        try:
            self.controls.connect()
            state = self.controls.state()
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            LOG.warning("control connection failed: %s", exc)
        GLib.idle_add(self._connected, state, error)

    def _connected(self, state: Optional[dict], error: Optional[str]) -> bool:
        self._connecting = False
        if state:
            self._on_state(state)
        if error and self.controls.mode == "monitor":
            self.toast.show(error, "error")
        self._update_connection_ui()
        for page in self.pages.values():
            page.controls_updated(self.state)  # type: ignore[attr-defined]
        return False

    def refresh_controls_async(self) -> None:
        if self._syncing:
            return
        if self._connecting:
            return
        self._syncing = True
        self._last_sync_started = time.monotonic()
        thread = threading.Thread(target=self._sync_worker, daemon=True)
        thread.start()

    def _sync_worker(self) -> None:
        error = None
        state = None
        try:
            state = self.controls.state()
        except Exception as exc:  # noqa: BLE001
            error = str(exc)
            LOG.debug("control sync failed: %s", exc)
        GLib.idle_add(self._sync_done, state, error)

    def _sync_done(self, state: Optional[dict], error: Optional[str]) -> bool:
        self._syncing = False
        if state:
            self._on_state(state)
        if error:
            self.controls.last_error = error
            self._set_connection("特权服务异常", "error")
        else:
            self._update_connection_ui()
        return False

    def _on_state(self, state: dict) -> None:
        self.state = state
        caps = state.get("caps")
        if caps:
            self.controls.caps = caps
        for page in self.pages.values():
            page.controls_updated(state)  # type: ignore[attr-defined]
        self._update_connection_ui()

    def _set_connection(self, text: str, kind: str) -> None:
        pill_kind = {"error": "err", "err": "err", "warn": "warn", "success": "", "neutral": "neutral"}.get(
            kind, "neutral"
        )
        self.conn_pill.set_text(text, pill_kind or None)
        palette = self.theme.palette
        color = {
            "success": palette.success,
            "warn": palette.warning,
            "warning": palette.warning,
            "error": palette.danger,
            "err": palette.danger,
            "neutral": palette.muted,
        }.get(kind, palette.muted)
        self.sidebar_dot.set_color(color)
        self.sidebar_status.set_text(text)

    def _update_connection_ui(self) -> None:
        mode = self.controls.mode
        if mode in ("helper", "root"):
            text = "控制已启用" if mode == "helper" else "Root 直连模式"
            self._set_connection(text, "success")
        else:
            self._set_connection("仅监控", "warn")

    def reconnect_controls(self) -> None:
        self.controls = ControlManager()
        self._start_connect()
        self.toast.show("正在重新连接特权服务…")

    # ------------------------------------------------------------------
    # preferences / actions
    # ------------------------------------------------------------------
    @property
    def units(self) -> str:
        return self._units

    def set_units(self, unit: str) -> None:
        if unit not in ("C", "F"):
            return
        self._units = unit
        self.config.set("units", unit)
        for page in self.pages.values():
            page.update(self.snapshot, self.state)  # type: ignore[attr-defined]
        self.queue_draw()

    def set_theme(self, name: str) -> None:
        if name not in ("dark", "light"):
            return
        self.theme.apply(name)
        self.config.set("theme", name)
        self._redraw_all(self)
        self._update_connection_ui()
        for page in self.pages.values():
            page.update(self.snapshot, self.state)  # type: ignore[attr-defined]
        self.queue_draw()

    def set_poll_interval(self, ms: int) -> None:
        self._poll_ms = max(250, int(ms))
        self.config.set("poll_ms", self._poll_ms)
        self._schedule_poll()

    def set_autostart(self, enabled: bool) -> bool:
        autostart = Path(
            os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")
        ) / "autostart" / "nuc-center.desktop"
        try:
            if enabled:
                autostart.parent.mkdir(parents=True, exist_ok=True)
                installed = Path("/usr/local/bin/nuc-center")
                if installed.exists():
                    command = str(installed)
                else:
                    command = (
                        f"env PYTHONPATH={PROJECT_ROOT} "
                        f"{sys.executable} -m nuc_center"
                    )
                icon = "nuc-center" if installed.exists() else str(ASSETS / "nuc-center.svg")
                content = (
                    "[Desktop Entry]\n"
                    "Type=Application\n"
                    "Name=NUC 中心\n"
                    "Comment=Intel NUC X15 系统管理器\n"
                    f"Exec={command}\n"
                    f"Icon={icon}\n"
                    "Terminal=false\n"
                    "X-GNOME-Autostart-enabled=true\n"
                )
                autostart.write_text(content, encoding="utf-8")
            else:
                autostart.unlink(missing_ok=True)
        except OSError as exc:
            LOG.warning("cannot update autostart: %s", exc)
            return False
        self.config.set("autostart", enabled)
        return True

    def _redraw_all(self, widget) -> None:
        widget.queue_draw()
        if isinstance(widget, Gtk.Container):
            for child in widget.get_children():
                self._redraw_all(child)

    # ------------------------------------------------------------------
    # reports
    # ------------------------------------------------------------------
    def build_report(self) -> str:
        d = self.device
        s = self.snapshot
        lines = [
            f"{APP_NAME} {APP_VERSION} 系统报告",
            "",
            f"产品: {d.friendly_name} ({d.product_name})",
            f"厂商: {d.sys_vendor}",
            f"SKU: {d.product_sku}",
            f"主板: {d.board_name} {d.board_version}",
            f"BIOS: {d.bios_version} ({d.bios_date})",
            f"CPU: {d.cpu_model} ({d.cpu_cores}C/{d.cpu_threads}T)",
            f"显卡: {'; '.join(d.gpus) if d.gpus else '--'}",
            f"内存: {format_bytes_gib(d.memory_total, 1)}",
            f"内核: {d.kernel}",
            f"系统: {d.os_name}",
            "",
            f"CPU 温度: {format_temp(s.cpu_temp, self.units, 0)}",
            f"显卡温度: {format_temp(s.gpu_temp, self.units, 0)}",
            f"主风扇: {s.fan_main} RPM (PWM {s.pwm_main}%)",
            f"副风扇: {s.fan_secondary} RPM (PWM {s.pwm_secondary}%)",
            f"电池: {s.battery.capacity}% {s.battery.status}; "
            f"health {s.battery.health_percent:.0f}%"
            if s.battery.health_percent is not None
            else f"电池: {s.battery.capacity}% {s.battery.status}",
            f"充电上限: {s.battery.charge_limit}%",
            "",
            f"控制模式: {self.controls.mode}",
            f"能力: {json.dumps(self.controls.caps, default=str)}",
        ]
        ec = self.state.get("ec") or {}
        if ec:
            lines.append("EC 状态: " + json.dumps(ec, default=str))
        return "\n".join(lines)


# ----------------------------------------------------------------------
# self tests / entry point
# ----------------------------------------------------------------------
def self_test() -> int:
    reader = SensorReader()
    snap = reader.read()
    controls = ControlManager()
    payload = {
        "device": asdict(reader.device),
        "snapshot": asdict(snap),
        "caps": controls.caps,
    }
    print(json.dumps(payload, indent=2, default=str, ensure_ascii=False))
    return 0


def ui_test() -> int:
    init_result = Gtk.init_check()
    ok = init_result[0] if isinstance(init_result, tuple) else init_result
    if not ok:
        print("GTK 初始化失败（没有可用的显示）", file=sys.stderr)
        return 1
    window = NucCenterWindow("overview")
    window.show_all()
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)
    print(f"已构建 {len(window.pages)} 个页面：{', '.join(window.pages)}")
    window.destroy()
    return 0


def main(argv: Optional[list[str]] = None) -> int:
    parser = argparse.ArgumentParser(description="NUC X15 系统管理器")
    parser.add_argument("--version", action="store_true", help="显示版本")
    parser.add_argument("--self-test", action="store_true", help="打印传感器数据后退出")
    parser.add_argument("--ui-test", action="store_true", help="构建界面后退出")
    parser.add_argument(
        "--page",
        choices=[name for name, _, _ in NAV_ITEMS],
        default="overview",
        help="启动时显示的页面",
    )
    parser.add_argument("--debug", action="store_true", help="输出调试日志")
    args, _unknown = parser.parse_known_args(argv)
    if args.version:
        print(f"{APP_NAME} {APP_VERSION}")
        return 0
    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    if args.self_test:
        return self_test()
    if args.ui_test:
        return ui_test()

    try:
        import cairo  # noqa: F401
    except ImportError:
        print(
            "NUC 中心需要 python-cairo 来绘制仪表和图表。\n"
            "安装命令：sudo pacman -S python-cairo\n"
            "或运行项目中的 install.sh，它会自动安装依赖。",
            file=sys.stderr,
        )
        return 1

    global _INSTANCE_LOCK
    runtime_dir = Path(os.environ.get("XDG_RUNTIME_DIR", "/tmp"))
    lock_path = runtime_dir / f"nuc-center-{os.getuid()}.lock"
    lock_file = open(lock_path, "w", encoding="utf-8")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        print("NUC 中心已经在运行。", file=sys.stderr)
        return 0
    _INSTANCE_LOCK = lock_file

    init_result = Gtk.init_check()
    ok = init_result[0] if isinstance(init_result, tuple) else init_result
    if not ok:
        print(
            "无法打开显示。请在桌面会话中启动 NUC 中心。",
            file=sys.stderr,
        )
        return 1
    window = NucCenterWindow(args.page)
    try:
        Gtk.main()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    sys.exit(main())
