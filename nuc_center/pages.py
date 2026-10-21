"""The five pages of the NUC Center window."""

from __future__ import annotations

import shlex
import time
from pathlib import Path
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from .controls import ControlError  # noqa: E402
from .fan import default_points, normalize_points  # noqa: E402
from .theme import Palette  # noqa: E402
from .util import (
    format_bytes_gib,
    format_power,
    format_temp,
    hex_to_rgb,
    human_uptime,
    translate_battery_status,
    translate_governor,
    translate_health,
    translate_technology,
)  # noqa: E402
from .widgets import (  # noqa: E402
    Bar,
    BatteryIcon,
    Chart,
    ColorDot,
    ColorPickerButton,
    ColorSwatch,
    FanCard,
    FanCurveChart,
    Gauge,
    IconBadge,
    KeyboardPreview,
    MetricCard,
    Pill,
    SectionHeader,
    SegmentedControl,
    make_label,
)

MODE_ZH = {"quiet": "安静", "balanced": "均衡", "performance": "性能"}
TOGGLE_ZH = {"fn_lock": "FN 锁定", "super_key": "Super 键", "touchpad_toggle": "触控板开关"}


def mode_zh(mode: str) -> str:
    return MODE_ZH.get(mode, mode)


PRESET_COLORS = [
    "#4cc2ff",
    "#3b82f6",
    "#a855f7",
    "#ec4899",
    "#ef4444",
    "#f59e0b",
    "#22c55e",
    "#e5e7eb",
]


def card(spacing: int = 12) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)
    box.get_style_context().add_class("card")
    return box


def hbox(spacing: int = 10) -> Gtk.Box:
    return Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing)


def vbox(spacing: int = 6) -> Gtk.Box:
    return Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing)


def chip(text: str) -> Gtk.Label:
    label = make_label(text, "chip")
    return label


def legend_item(color: str, text: str) -> Gtk.Box:
    box = hbox(6)
    box.set_valign(Gtk.Align.CENTER)
    box.pack_start(ColorDot(color, 8), False, False, 0)
    box.pack_start(make_label(text, "legend-label"), False, False, 0)
    return box


class Debouncer:
    def __init__(self, delay_ms: int, callback: Callable[[], None]) -> None:
        self.delay_ms = delay_ms
        self.callback = callback
        self.source: Optional[int] = None

    def schedule(self) -> None:
        self.cancel()
        self.source = GLib.timeout_add(self.delay_ms, self._fire)

    def cancel(self) -> None:
        if self.source is not None:
            GLib.source_remove(self.source)
            self.source = None

    def _fire(self) -> bool:
        self.source = None
        self.callback()
        return False


class ToggleRow(Gtk.Box):
    def __init__(
        self,
        title: str,
        subtitle: str = "",
        on_change: Optional[Callable[[bool], None]] = None,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.on_change = on_change
        self._updating = False
        labels = vbox(2)
        labels.pack_start(make_label(title), False, False, 0)
        if subtitle:
            sub = make_label(subtitle, "subtext", wrap=True)
            sub.set_max_width_chars(60)
            labels.pack_start(sub, False, False, 0)
        self.pack_start(labels, True, True, 0)
        self.switch = Gtk.Switch()
        self.switch.set_valign(Gtk.Align.CENTER)
        self.switch.connect("notify::active", self._changed)
        self.pack_end(self.switch, False, False, 0)

    def set_active(self, active: bool) -> None:
        self._updating = True
        self.switch.set_active(bool(active))
        self._updating = False

    def set_sensitive(self, sensitive: bool) -> None:
        super().set_sensitive(sensitive)
        self.switch.set_sensitive(sensitive)

    def _changed(self, switch: Gtk.Switch, _param) -> None:
        if self._updating or self.on_change is None:
            return
        self.on_change(bool(switch.get_active()))


class ModeButton(Gtk.ToggleButton):
    def __init__(self, mode: str, icon: str, title: str, subtitle: str, color_class: str = "") -> None:
        super().__init__()
        self.mode = mode
        self.get_style_context().add_class("mode-card")
        self.set_relief(Gtk.ReliefStyle.NONE)
        child = vbox(8)
        top = hbox(9)
        top.pack_start(IconBadge(icon, color_class, 30), False, False, 0)
        top.pack_start(make_label(title, "mode-title"), True, True, 0)
        child.pack_start(top, False, False, 0)
        sub = make_label(subtitle, "mode-sub", wrap=True)
        sub.set_max_width_chars(32)
        child.pack_start(sub, False, False, 0)
        self.add(child)


class InfoRow(Gtk.Box):
    def __init__(self, key: str, value: str = "--") -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.key_label = make_label(key, "info-key")
        self.value_label = make_label(value, "info-value", xalign=1.0)
        self.value_label.set_halign(Gtk.Align.END)
        self.value_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.value_label.set_max_width_chars(55)
        self.value_label.set_tooltip_text(value)
        self.pack_start(self.key_label, True, True, 0)
        self.pack_end(self.value_label, False, False, 0)

    def set_value(self, value: str) -> None:
        self.value_label.set_text(value)
        self.value_label.set_tooltip_text(value)


class Page(Gtk.Box):
    def __init__(self, app, title: str, subtitle: str, action: Optional[Gtk.Widget] = None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.app = app
        self.set_margin_start(26)
        self.set_margin_end(26)
        self.set_margin_top(22)
        self.set_margin_bottom(28)
        header = hbox(12)
        header.set_valign(Gtk.Align.CENTER)
        titles = vbox(1)
        titles.pack_start(make_label(title, "page-title"), False, False, 0)
        if subtitle:
            titles.pack_start(make_label(subtitle, "page-subtitle"), False, False, 0)
        header.pack_start(titles, True, True, 0)
        if action is not None:
            header.pack_end(action, False, False, 0)
        self.pack_start(header, False, False, 0)
        self.body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
        self.pack_start(self.body, False, False, 0)

    def update(self, snap, state: dict) -> None:  # pragma: no cover - UI hook
        pass

    def on_show(self) -> None:  # pragma: no cover - UI hook
        pass

    def controls_updated(self, state: dict) -> None:  # pragma: no cover - UI hook
        pass


# ======================================================================
# Overview
# ======================================================================
class OverviewPage(Page):
    def __init__(self, app) -> None:
        super().__init__(app, "概览", "来自 Uniwill EC 与 Intel 传感器的实时数据")
        self._updating = False

        hero = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=18)
        hero.get_style_context().add_class("hero")
        left = vbox(8)
        left.set_valign(Gtk.Align.CENTER)
        self.device_label = make_label(app.device.friendly_name, "hero-title")
        self.model_label = make_label(
            f"{app.device.product_name} · {app.device.sys_vendor}", "subtext"
        )
        left.pack_start(self.device_label, False, False, 0)
        left.pack_start(self.model_label, False, False, 0)
        chips = hbox(6)
        cpu_chip = chip(app.device.cpu_model.replace("11th Gen Intel(R) Core(TM) ", ""))
        gpu_chip = chip(app.device.gpus[-1].replace("NVIDIA Corporation ", "") if app.device.gpus else "显卡")
        ram_chip = chip(format_bytes_gib(app.device.memory_total))
        chips.pack_start(cpu_chip, False, False, 0)
        chips.pack_start(gpu_chip, False, False, 0)
        chips.pack_start(ram_chip, False, False, 0)
        left.pack_start(chips, False, False, 0)
        status_row = hbox(8)
        self.health_pill = Gtk.Label(label="系统正常")
        self.health_pill.get_style_context().add_class("pill")
        status_row.pack_start(self.health_pill, False, False, 0)
        self.uptime_label = make_label("运行时间 --", "subtext")
        status_row.pack_start(self.uptime_label, False, False, 0)
        left.pack_start(status_row, False, False, 0)
        hero.pack_start(left, True, True, 0)

        gauges = hbox(6)
        gauges.set_valign(Gtk.Align.CENTER)
        self.cpu_gauge = Gauge(150, 100, "°C", "CPU")
        self.gpu_gauge = Gauge(150, 100, "°C", "显卡")
        gauges.pack_start(self.cpu_gauge, False, False, 0)
        gauges.pack_start(self.gpu_gauge, False, False, 0)
        hero.pack_end(gauges, False, False, 0)
        self.body.pack_start(hero, False, False, 0)

        metrics = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, homogeneous=True)
        self.cpu_load_card = MetricCard("CPU 负载", "applications-system-symbolic")
        self.memory_card = MetricCard("内存", "drive-harddisk-symbolic", "purple")
        self.fan_card = MetricCard("主风扇", "weather-windy-symbolic")
        self.battery_card = MetricCard("电池", "battery-good-symbolic", "green")
        for widget in (self.cpu_load_card, self.memory_card, self.fan_card, self.battery_card):
            metrics.pack_start(widget, True, True, 0)
        self.body.pack_start(metrics, False, False, 0)

        chart_card = card(10)
        chart_header = hbox(8)
        chart_header.pack_start(
            SectionHeader("温度历史", "最近 90 秒"), True, True, 0
        )
        legend = hbox(12)
        legend.pack_start(legend_item(app.theme.palette.accent, "CPU"), False, False, 0)
        legend.pack_start(legend_item(app.theme.palette.purple, "显卡"), False, False, 0)
        chart_header.pack_end(legend, False, False, 0)
        chart_card.pack_start(chart_header, False, False, 0)
        self.chart = Chart(170, 90, "temp", app.units)
        chart_card.pack_start(self.chart, False, False, 0)
        self.body.pack_start(chart_card, False, False, 0)

        bottom = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        quick = card(10)
        quick.pack_start(
            SectionHeader("快捷控制", "系统性能与电池保养"), False, False, 0
        )
        mode_row = hbox(12)
        mode_row.pack_start(make_label("系统模式"), True, True, 0)
        self.mode_control = SegmentedControl(
            [("quiet", "安静"), ("balanced", "均衡"), ("performance", "性能")]
        )
        self.mode_control.connect_changed(self._mode_changed)
        mode_row.pack_end(self.mode_control, False, False, 0)
        quick.pack_start(mode_row, False, False, 0)
        kbd_row = hbox(12)
        kbd_row.pack_start(make_label("键盘亮度"), True, True, 0)
        self.kbd_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 4, 1)
        self.kbd_scale.set_draw_value(False)
        self.kbd_scale.set_size_request(150, -1)
        self.kbd_scale.connect("value-changed", self._kbd_changed)
        self.kbd_label = make_label("--", "subtext")
        kbd_row.pack_end(self.kbd_label, False, False, 0)
        kbd_row.pack_end(self.kbd_scale, False, False, 0)
        quick.pack_start(kbd_row, False, False, 0)
        battery_row = hbox(8)
        battery_row.pack_start(make_label("充电上限"), True, True, 0)
        self.limit_label = make_label("--", "subtext")
        battery_row.pack_start(self.limit_label, False, False, 0)
        self.limit_80 = Gtk.Button(label="80%")
        self.limit_80.get_style_context().add_class("secondary-button")
        self.limit_80.connect("clicked", lambda *_: self._set_limit(80))
        self.limit_100 = Gtk.Button(label="100%")
        self.limit_100.get_style_context().add_class("secondary-button")
        self.limit_100.connect("clicked", lambda *_: self._set_limit(100))
        battery_row.pack_end(self.limit_100, False, False, 0)
        battery_row.pack_end(self.limit_80, False, False, 0)
        quick.pack_start(battery_row, False, False, 0)
        bottom.pack_start(quick, True, True, 0)

        sensors_card = card(9)
        sensors_card.pack_start(SectionHeader("其他传感器", "存储与无线"), False, False, 0)
        self.sensor_rows: list[InfoRow] = []
        for key in ("NVMe 1", "NVMe 2", "Wi-Fi", "ACPI"):
            row = InfoRow(key)
            self.sensor_rows.append(row)
            sensors_card.pack_start(row, False, False, 0)
        bottom.pack_start(sensors_card, True, True, 0)
        self.body.pack_start(bottom, False, False, 0)

        self._kbd_debounce = Debouncer(250, self._apply_kbd)
        self._user_kbd_at = 0.0

    # ------------------------------------------------------------------
    def _mode_changed(self, mode: str) -> None:
        if self._updating:
            return
        if not self.app.controls.caps.get("profile"):
            self.app.toast.show("EC 性能模式不可用。请先运行 install.sh。", "error")
            self._sync_mode_from_state()
            return
        try:
            self.app.controls.set_profile(mode)
            self.app.toast.show(f"系统模式：{mode_zh(mode)}", "success")
            self.app.refresh_controls_async()
        except ControlError as exc:
            self.app.toast.show(str(exc), "error")
            self._sync_mode_from_state()

    def _kbd_changed(self, scale: Gtk.Scale) -> None:
        if self._updating:
            return
        self._user_kbd_at = time.monotonic()
        self.kbd_label.set_text(f"{int(scale.get_value())} 级")
        self._kbd_debounce.schedule()

    def _apply_kbd(self) -> None:
        level = int(self.kbd_scale.get_value())
        if not (
            self.app.controls.caps.get("kbd_ite")
            or self.app.controls.caps.get("kbd_ec")
            or self.app.controls.caps.get("kbd_led")
        ):
            return
        try:
            if self.app.controls.caps.get("kbd_led"):
                self.app.controls.set_kbd_led(
                    self.app.config.get("keyboard_color", [77, 194, 255]),
                    level / 4.0 * 100.0,
                )
            else:
                self.app.controls.set_keyboard(
                    rgb=tuple(self.app.config.get("keyboard_color", [77, 194, 255])),
                    level=level,
                )
        except ControlError as exc:
            self.app.toast.show(str(exc), "error")

    def _set_limit(self, limit: int) -> None:
        try:
            self.app.controls.set_battery_limit(limit)
            self.app.toast.show(f"电池充电上限已设为 {limit}%", "success")
            self.app.refresh_controls_async()
        except ControlError as exc:
            self.app.toast.show(str(exc), "error")

    # ------------------------------------------------------------------
    def _sync_mode_from_state(self) -> None:
        mode = (self.app.state.get("ec") or {}).get("perf_mode") or "balanced"
        self._updating = True
        self.mode_control.set_active(mode)
        self.mode_control.set_sensitive(bool(self.app.controls.caps.get("profile")))
        self._updating = False

    def controls_updated(self, state: dict) -> None:
        self._sync_mode_from_state()
        ec = state.get("ec") or {}
        ite = state.get("ite") or {}
        level = ite.get("level") if ite.get("available") else ec.get("kbd_brightness")
        if level is not None and time.monotonic() - self._user_kbd_at > 2.0:
            self._updating = True
            self.kbd_scale.set_value(min(4, max(0, int(level))))
            self._updating = False
            self.kbd_label.set_text(f"{int(level)} 级")
        battery = state.get("battery") or {}
        limit = battery.get("limit")
        if limit is not None:
            self.limit_label.set_text(f"{limit}%")
            self.limit_80.set_sensitive(self.app.controls.can_control)
            self.limit_100.set_sensitive(self.app.controls.can_control)
        has_kbd = bool(
            self.app.controls.caps.get("kbd_ite")
            or self.app.controls.caps.get("kbd_ec")
            or self.app.controls.caps.get("kbd_led")
        )
        self.kbd_scale.set_sensitive(has_kbd and self.app.controls.can_control)

    def update(self, snap, state: dict) -> None:
        palette = self.app.theme.palette
        self.uptime_label.set_text(f"运行时间 {human_uptime(snap.uptime)}")
        cpu = snap.cpu_temp
        gpu = snap.gpu_temp
        unit = self.app.units
        cpu_display = "--" if cpu is None else f"{format_temp(cpu, unit, 0)[:-2]}"
        gpu_display = "--" if gpu is None else f"{format_temp(gpu, unit, 0)[:-2]}"
        self.cpu_gauge.set_unit(f"°{unit}")
        self.gpu_gauge.set_unit(f"°{unit}")
        self.cpu_gauge.set_value(
            cpu,
            display=cpu_display,
            color=self.app.theme.temperature_color(cpu),
            subtext="封装",
        )
        self.gpu_gauge.set_value(
            gpu,
            display=gpu_display,
            color=self.app.theme.temperature_color(gpu),
            subtext="EC 传感器" if gpu is not None else "",
        )
        if cpu is None:
            self.health_pill.set_text("等待传感器")
            kind = "neutral"
        elif cpu >= 88:
            self.health_pill.set_text("温度偏高")
            kind = "err"
        elif cpu >= 78:
            self.health_pill.set_text("偏热")
            kind = "warn"
        else:
            self.health_pill.set_text("系统正常")
            kind = ""
        context = self.health_pill.get_style_context()
        for cls in ("warn", "err", "neutral"):
            context.remove_class(cls)
        if kind:
            context.add_class(kind)

        if snap.cpu_load is not None:
            self.cpu_load_card.set_value(
                f"{snap.cpu_load:.0f}",
                "%",
                f"{snap.cpu_freq / 1000.0:.2f} GHz · {translate_governor(snap.cpu_governor)}"
                if snap.cpu_freq
                else translate_governor(snap.cpu_governor),
                snap.cpu_load,
                self.app.theme.load_color(snap.cpu_load),
            )
        else:
            self.cpu_load_card.set_value("--", "%", "测量中…", 0)
        if snap.mem_used and snap.mem_total:
            self.memory_card.set_value(
                f"{format_bytes_gib(snap.mem_used, 1).replace(' GiB', '')}",
                "GiB",
                f"{format_bytes_gib(snap.mem_used, 1)} / {format_bytes_gib(snap.mem_total, 0)}",
                snap.mem_percent,
                palette.purple,
            )
        curve = self.app.state.get("fan_curve") or {}
        pwm_display = (
            curve.get("percent")
            if curve.get("enabled") and curve.get("percent") is not None
            else snap.pwm_main
        )
        self.fan_card.set_value(
            f"{snap.fan_main:,}" if snap.fan_main is not None else "--",
            "RPM",
            f"自定义曲线 {pwm_display:.0f}% · 副风扇 {snap.fan_secondary:,} RPM"
            if curve.get("enabled") and pwm_display is not None and snap.fan_secondary is not None
            else (
                f"PWM {pwm_display:.0f}% · 副风扇 {snap.fan_secondary:,} RPM"
                if snap.fan_secondary is not None and pwm_display is not None
                else "EC 风扇"
            ),
            pwm_display,
            palette.accent,
        )
        bat = snap.battery
        self.battery_card.set_value(
            f"{bat.capacity}" if bat.capacity is not None else "--",
            "%",
            f"{translate_battery_status(bat.status)} · 上限 {bat.charge_limit}%"
            if bat.charge_limit is not None
            else translate_battery_status(bat.status),
            bat.capacity,
            self.app.theme.battery_color(bat.capacity),
        )
        self.chart.set_unit(unit)
        self.chart.set_series(
            [
                {
                    "name": "CPU",
                    "color": palette.accent,
                    "values": list(self.app.history["cpu"]),
                },
                {
                    "name": "显卡",
                    "color": palette.purple,
                    "values": list(self.app.history["gpu"]),
                },
            ]
        )
        sensors = [
            snap.nvme[0] if len(snap.nvme) > 0 else None,
            snap.nvme[1] if len(snap.nvme) > 1 else None,
            ("Wi-Fi", snap.wifi_temp) if snap.wifi_temp is not None else None,
            ("ACPI", snap.acpi_temp) if snap.acpi_temp is not None else None,
        ]
        for row, value in zip(self.sensor_rows, sensors):
            if value is None:
                row.set_value("--")
            else:
                label, temp = value
                row.key_label.set_text(label)
                row.set_value(format_temp(temp, unit, 0))

    def on_show(self) -> None:
        self.controls_updated(self.app.state)


# ======================================================================
# Cooling
# ======================================================================
class CoolingPage(Page):
    def __init__(self, app) -> None:
        super().__init__(app, "散热", "风扇、温度与固件性能模式")
        self._updating = False
        self._user_mode_at = 0.0

        fans = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, homogeneous=True)
        self.fan_main_card = FanCard("主风扇", "CPU / 系统")
        self.fan_secondary_card = FanCard("副风扇", "显卡")
        fans.pack_start(self.fan_main_card, True, True, 0)
        fans.pack_start(self.fan_secondary_card, True, True, 0)
        self.body.pack_start(fans, False, False, 0)

        mode_card = card(12)
        mode_header = hbox(8)
        mode_header.pack_start(
            SectionHeader(
                "系统性能",
                "EC 会调整风扇曲线、显卡 TGP 与模式指示灯",
            ),
            True,
            True,
            0,
        )
        self.mode_pill = Gtk.Label(label="--")
        self.mode_pill.get_style_context().add_class("pill")
        mode_header.pack_end(self.mode_pill, False, False, 0)
        mode_card.pack_start(mode_header, False, False, 0)
        modes = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, homogeneous=True)
        self.mode_buttons: dict[str, ModeButton] = {}
        definitions = [
            ("quiet", "weather-clear-night-symbolic", "安静", "更低功耗，风扇更安静", "green"),
            ("balanced", "power-profile-balanced-symbolic", "均衡", "日常性能，风扇自动调节", ""),
            ("performance", "power-profile-performance-symbolic", "性能", "最高显卡功耗，激进散热", "amber"),
        ]
        for mode, icon, title, subtitle, color_class in definitions:
            button = ModeButton(mode, icon, title, subtitle, color_class)
            button.connect("toggled", self._mode_toggled, mode)
            modes.pack_start(button, True, True, 0)
            self.mode_buttons[mode] = button
        mode_card.pack_start(modes, False, False, 0)
        self.ec_warning = make_label(
            "EC 性能模式不可用。请安装特权服务与 acpi_call 后重试。",
            "subtext",
            wrap=True,
        )
        self.ec_warning.set_no_show_all(True)
        mode_card.pack_start(self.ec_warning, False, False, 0)
        self.fan_boost_row = ToggleRow(
            "最大散热",
            "强制 EC 进入风扇加速模式（噪音较大），适合持续高负载。",
            self._fan_boost_changed,
        )
        mode_card.pack_start(self.fan_boost_row, False, False, 0)
        self.body.pack_start(mode_card, False, False, 0)

        self._build_curve_card()
        self._curve_debounce = Debouncer(450, self._apply_curve)
        self._curve_user_at = 0.0

        charts = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10, homogeneous=True)
        temp_card = card(8)
        temp_header = hbox(8)
        temp_header.pack_start(SectionHeader("温度", "最近 90 秒"), True, True, 0)
        temp_legend = hbox(10)
        temp_legend.pack_start(legend_item(app.theme.palette.accent, "CPU"), False, False, 0)
        temp_legend.pack_start(legend_item(app.theme.palette.purple, "显卡"), False, False, 0)
        temp_header.pack_end(temp_legend, False, False, 0)
        temp_card.pack_start(temp_header, False, False, 0)
        self.temp_chart = Chart(160, 90, "temp", app.units)
        temp_card.pack_start(self.temp_chart, False, False, 0)
        charts.pack_start(temp_card, True, True, 0)

        fan_chart_card = card(8)
        fan_header = hbox(8)
        fan_header.pack_start(SectionHeader("风扇转速", "最近 90 秒"), True, True, 0)
        fan_legend = hbox(10)
        fan_legend.pack_start(legend_item(app.theme.palette.accent, "主风扇"), False, False, 0)
        fan_legend.pack_start(legend_item(app.theme.palette.success, "副风扇"), False, False, 0)
        fan_header.pack_end(fan_legend, False, False, 0)
        fan_chart_card.pack_start(fan_header, False, False, 0)
        self.fan_chart = Chart(160, 90, "fan", "RPM")
        fan_chart_card.pack_start(self.fan_chart, False, False, 0)
        charts.pack_start(fan_chart_card, True, True, 0)
        self.body.pack_start(charts, False, False, 0)

        cores_card = card(10)
        cores_card.pack_start(
            SectionHeader("CPU 核心", "各核心温度"), False, False, 0
        )
        self.cores_grid = Gtk.Grid(column_spacing=10, row_spacing=7)
        self.core_labels: list[tuple[Gtk.Label, Gtk.Label]] = []
        for index in range(8):
            name = make_label(f"核心 {index}", "subtext")
            value = make_label("--", "info-value", xalign=1.0)
            value.set_halign(Gtk.Align.END)
            cell = hbox(6)
            cell.pack_start(name, True, True, 0)
            cell.pack_end(value, False, False, 0)
            self.cores_grid.attach(cell, index % 4, index // 4, 1, 1)
            cell.set_hexpand(True)
            self.core_labels.append((name, value))
        cores_card.pack_start(self.cores_grid, False, False, 0)
        self.body.pack_start(cores_card, False, False, 0)

        extras = card(9)
        extras.pack_start(SectionHeader("其他传感器", "NVMe、Wi-Fi 与 ACPI"), False, False, 0)
        self.extra_rows: list[InfoRow] = []
        for key in ("NVMe 1", "NVMe 2", "Wi-Fi", "ACPI"):
            row = InfoRow(key)
            self.extra_rows.append(row)
            extras.pack_start(row, False, False, 0)
        self.body.pack_start(extras, False, False, 0)

    # ------------------------------------------------------------------
    def _build_curve_card(self) -> None:
        curve_card = card(10)
        header = hbox(8)
        header.pack_start(
            SectionHeader("自动散热曲线", "温度 → 百分比，平滑缓升缓降（WMI PWM）"), True, True, 0
        )
        self.curve_pill = Pill("自动", "neutral")
        header.pack_end(self.curve_pill, False, False, 0)
        curve_card.pack_start(header, False, False, 0)
        self.curve_chart = FanCurveChart(170)
        curve_card.pack_start(self.curve_chart, False, False, 0)
        grid = Gtk.Grid(column_spacing=10, row_spacing=6)
        grid.attach(make_label("温度", "info-key"), 0, 0, 1, 1)
        grid.attach(make_label("风扇转速", "info-key"), 2, 0, 2, 1)
        self.curve_spins: list[tuple[Gtk.SpinButton, Gtk.SpinButton]] = []
        for index in range(5):
            label = make_label(f"第 {index + 1} 点", "subtext")
            temp = Gtk.SpinButton.new_with_range(30, 100, 1)
            temp.set_numeric(True)
            temp.set_size_request(74, -1)
            percent = Gtk.SpinButton.new_with_range(10, 100, 1)
            percent.set_numeric(True)
            percent.set_size_request(74, -1)
            temp.connect("value-changed", self._curve_edited)
            percent.connect("value-changed", self._curve_edited)
            grid.attach(label, 0, index + 1, 1, 1)
            grid.attach(temp, 1, index + 1, 1, 1)
            grid.attach(make_label("°C", "subtext"), 2, index + 1, 1, 1)
            grid.attach(percent, 3, index + 1, 1, 1)
            grid.attach(make_label("%", "subtext"), 4, index + 1, 1, 1)
            self.curve_spins.append((temp, percent))
        curve_card.pack_start(grid, False, False, 0)
        self.curve_toggle = ToggleRow(
            "启用自动散热曲线",
            "每秒按最热温度平滑调整 PWM；关闭或 helper 停止时恢复自动",
            self._curve_toggled,
        )
        curve_card.pack_start(self.curve_toggle, False, False, 0)
        buttons = hbox(6)
        default_button = Gtk.Button(label="恢复默认曲线")
        default_button.get_style_context().add_class("secondary-button")
        default_button.connect("clicked", lambda *_: self._curve_default())
        auto_button = Gtk.Button(label="恢复自动")
        auto_button.get_style_context().add_class("secondary-button")
        auto_button.connect("clicked", lambda *_: self._curve_disable())
        buttons.pack_start(default_button, False, False, 0)
        buttons.pack_start(auto_button, False, False, 0)
        curve_card.pack_start(buttons, False, False, 0)
        self.curve_status = make_label("EC 自动风扇控制", "subtext", wrap=True)
        curve_card.pack_start(self.curve_status, False, False, 0)
        note = make_label(
            "默认曲线已经可用，不需要手工调点。升温每秒最多 +4%（85°C 以上 +8%），"
            "降温每秒最多 -2%；90°C 立即 100%。实验性 WMI PWM 模块带 5 秒看门狗。",
            "subtext",
            wrap=True,
        )
        note.set_max_width_chars(110)
        curve_card.pack_start(note, False, False, 0)
        self._set_curve_ui(default_points())
        self.body.pack_start(curve_card, False, False, 0)

    def _set_curve_ui(self, points) -> None:
        normalized = normalize_points(points)
        self._updating = True
        for index, (temp, percent) in enumerate(self.curve_spins):
            if index < len(normalized):
                temp.set_value(normalized[index].temp)
                percent.set_value(normalized[index].percent)
        self._updating = False
        self.curve_chart.set_points(normalized)

    def _curve_values(self):
        return normalize_points(
            [
                (int(temp.get_value()), int(percent.get_value()))
                for temp, percent in self.curve_spins
            ]
        )

    def _curve_edited(self, _spin) -> None:
        if self._updating:
            return
        self._curve_user_at = time.monotonic()
        self.curve_chart.set_points(self._curve_values())
        if self.curve_toggle.switch.get_active():
            self._curve_debounce.schedule()

    def _curve_default(self) -> None:
        self._set_curve_ui(default_points())
        self._curve_user_at = time.monotonic()
        if self.curve_toggle.switch.get_active():
            self._curve_debounce.schedule()

    def _curve_disable(self) -> None:
        self.curve_toggle.set_active(False)
        try:
            self.app.controls.set_fan_curve(self._curve_values(), enabled=False)
            self.app.toast.show("已恢复 EC 自动风扇", "success")
            self.app.refresh_controls_async()
        except ControlError as exc:
            self.app.toast.show(str(exc), "error")

    def _curve_toggled(self, enabled: bool) -> None:
        if not self.app.controls.caps.get("fan_curve"):
            self.app.toast.show("风扇曲线模块未加载（/dev/uniwill_pwm）。", "error")
            self.curve_toggle.set_active(False)
            return
        self._curve_user_at = time.monotonic()
        try:
            self.app.controls.set_fan_curve(self._curve_values(), enabled=enabled)
            self.app.toast.show(
                "自定义风扇曲线已启用" if enabled else "已恢复 EC 自动风扇", "success"
            )
            self.app.refresh_controls_async()
        except ControlError as exc:
            self.curve_toggle.set_active(not enabled)
            self.app.toast.show(str(exc), "error")

    def _apply_curve(self) -> None:
        try:
            self.app.controls.set_fan_curve(self._curve_values(), enabled=True)
            self.app.refresh_controls_async()
        except ControlError as exc:
            self.app.toast.show(str(exc), "error")

    # ------------------------------------------------------------------
    def _mode_toggled(self, button: ModeButton, mode: str) -> None:
        if self._updating:
            return
        if not button.get_active():
            # Clicking the active mode should not leave the group empty.
            current = ((self.app.state or {}).get("ec") or {}).get("perf_mode") or "balanced"
            if mode == current:
                self._updating = True
                button.set_active(True)
                self._updating = False
            return
        if not self.app.controls.caps.get("profile"):
            self.app.toast.show("EC 性能模式不可用。", "error")
            self._sync_modes()
            return
        self._updating = True
        for key, other in self.mode_buttons.items():
            other.set_active(key == mode)
        self._updating = False
        try:
            self._user_mode_at = time.monotonic()
            self.app.controls.set_profile(mode)
            self.app.toast.show(f"系统模式：{mode_zh(mode)}", "success")
            self.app.refresh_controls_async()
        except ControlError as exc:
            self.app.toast.show(str(exc), "error")
            self._sync_modes()

    def _fan_boost_changed(self, enabled: bool) -> None:
        if not self.app.controls.caps.get("fan_boost"):
            self.app.toast.show("此 EC 不支持风扇加速。", "error")
            return
        try:
            self.app.controls.set_fan_boost(enabled)
            self.app.toast.show("已开启最大散热" if enabled else "已关闭最大散热", "success")
            self.app.refresh_controls_async()
        except ControlError as exc:
            self.fan_boost_row.set_active(not enabled)
            self.app.toast.show(str(exc), "error")

    def _sync_modes(self) -> None:
        state = self.app.state or {}
        ec = state.get("ec") or {}
        mode = ec.get("perf_mode") or "balanced"
        profile = bool(self.app.controls.caps.get("profile"))
        self._updating = True
        for key, button in self.mode_buttons.items():
            button.set_active(key == mode)
        self._updating = False
        if profile:
            self.ec_warning.hide()
        else:
            self.ec_warning.show()
        boost = bool(ec.get("fan_boost"))
        if not profile:
            pill_text = "不可用"
        elif boost:
            pill_text = "FAN BOOST"
        else:
            pill_text = mode_zh(mode)
        self.mode_pill.set_text(pill_text)
        self.mode_pill.get_style_context().remove_class("warn")
        self.mode_pill.get_style_context().remove_class("neutral")
        self.mode_pill.get_style_context().add_class(
            "warn" if (boost and not curve_active) or not profile else "neutral"
        )

    def controls_updated(self, state: dict) -> None:
        self._sync_modes()
        caps = self.app.controls.caps
        curve = state.get("fan_curve") or {}
        supported = bool(caps.get("fan_curve")) and self.app.controls.can_control
        self.curve_toggle.set_sensitive(supported)
        for temp_spin, percent_spin in self.curve_spins:
            temp_spin.set_sensitive(supported)
            percent_spin.set_sensitive(supported)
        if curve and time.monotonic() - getattr(self, "_curve_user_at", 0.0) > 2.0:
            points = curve.get("points")
            if points:
                self._set_curve_ui(points)
            self._updating = True
            self.curve_toggle.set_active(bool(curve.get("enabled")))
            self._updating = False
        curve_enabled = bool(curve.get("enabled"))
        temp = curve.get("temp")
        percent = curve.get("percent")
        self.curve_chart.set_current(temp, percent)
        if not supported:
            self.curve_pill.set_text("未加载", "warn")
            self.curve_status.set_text("风扇曲线模块未加载；使用 EC 自动风扇。")
        elif curve_enabled:
            self.curve_pill.set_text("手动曲线", "warn")
            if temp is not None:
                target = curve.get("target")
                self.curve_status.set_text(
                    f"当前 {temp:.0f}°C · 目标 {target if target is not None else '--'}%"
                    f" · 当前 {percent if percent is not None else '--'}%"
                )
            else:
                self.curve_status.set_text("自动散热曲线运行中")
        else:
            self.curve_pill.set_text("自动", "neutral")
            self.curve_status.set_text("EC 自动风扇控制")
        if curve.get("error"):
            self.curve_status.set_text(str(curve["error"]))
        profile = bool(self.app.controls.caps.get("profile"))
        for button in self.mode_buttons.values():
            button.set_sensitive(profile)
        fan_boost = bool(self.app.controls.caps.get("fan_boost"))
        self.fan_boost_row.set_sensitive(
            fan_boost and self.app.controls.can_control and not curve_enabled
        )
        self.fan_boost_row.set_active(bool((state.get("ec") or {}).get("fan_boost")))

    def update(self, snap, state: dict) -> None:
        palette = self.app.theme.palette
        curve = self.app.state.get("fan_curve") or {}
        pwm_display = (
            curve.get("percent")
            if curve.get("enabled") and curve.get("percent") is not None
            else snap.pwm_main
        )
        self.fan_main_card.set_values(
            snap.fan_main, pwm_display, active=(snap.fan_main or 0) > 0
        )
        self.fan_secondary_card.set_values(
            snap.fan_secondary,
            pwm_display if curve.get("enabled") else snap.pwm_secondary,
            active=(snap.fan_secondary or 0) > 0,
        )
        self.temp_chart.set_unit(self.app.units)
        self.temp_chart.set_series(
            [
                {"name": "CPU", "color": palette.accent, "values": list(self.app.history["cpu"])},
                {"name": "显卡", "color": palette.purple, "values": list(self.app.history["gpu"])},
            ]
        )
        self.fan_chart.set_series(
            [
                {
                    "name": "主风扇",
                    "color": palette.accent,
                    "values": list(self.app.history["fan_main"]),
                },
                {
                    "name": "副风扇",
                    "color": palette.success,
                    "values": list(self.app.history["fan_secondary"]),
                },
            ]
        )
        for index, (name_label, value_label) in enumerate(self.core_labels):
            if index < len(snap.cpu_cores):
                name, temp = snap.cpu_cores[index]
                name_label.set_text(name)
                value_label.set_text(format_temp(temp, self.app.units, 0))
            else:
                name_label.set_text(f"核心 {index}")
                value_label.set_text("--")
        extras = [
            snap.nvme[0] if len(snap.nvme) > 0 else None,
            snap.nvme[1] if len(snap.nvme) > 1 else None,
            ("Wi-Fi", snap.wifi_temp) if snap.wifi_temp is not None else None,
            ("ACPI", snap.acpi_temp) if snap.acpi_temp is not None else None,
        ]
        for row, value in zip(self.extra_rows, extras):
            if value is None:
                row.set_value("--")
            else:
                label, temp = value
                row.key_label.set_text(label)
                row.set_value(format_temp(temp, self.app.units, 0))

    def on_show(self) -> None:
        self.controls_updated(self.app.state)


# ======================================================================
# Lighting
# ======================================================================
class LightingPage(Page):
    def __init__(self, app) -> None:
        super().__init__(app, "灯效", "RGB 键盘与前部灯带")
        self._updating = False
        self._last_user_at = 0.0
        self.keyboard_color = tuple(app.config.get("keyboard_color", [77, 194, 255]))
        self.lightbar_color = tuple(app.config.get("lightbar_color", [77, 194, 255]))
        self.keyboard_level = 3
        self.lightbar_percent = 80.0
        self._light_debounce = Debouncer(180, self._apply_lighting)
        self._kbd_debounce = Debouncer(220, self._apply_lighting)

        preview_card = card(8)
        preview_card.pack_start(
            SectionHeader("实时预览", "键盘与前部灯带"), False, False, 0
        )
        self.preview = KeyboardPreview(230)
        preview_card.pack_start(self.preview, True, True, 0)
        self.preview_note = make_label(
            "颜色会实时写入硬件。",
            "subtext",
            wrap=True,
        )
        preview_card.pack_start(self.preview_note, False, False, 0)

        controls = card(12)
        controls.pack_start(SectionHeader("灯效控制", "实时应用"), False, False, 0)
        self.target_control = SegmentedControl(
            [("keyboard", "键盘"), ("lightbar", "灯带")]
        )
        self.target_control.connect_changed(self._target_changed)
        controls.pack_start(self.target_control, False, False, 0)
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.stack.set_transition_duration(180)
        self.stack.add_named(self._build_keyboard_panel(), "keyboard")
        self.stack.add_named(self._build_lightbar_panel(), "lightbar")
        controls.pack_start(self.stack, False, False, 0)
        self.control_note = make_label("", "subtext", wrap=True)
        controls.pack_start(self.control_note, False, False, 0)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        top.pack_start(preview_card, True, True, 0)
        top.pack_start(controls, False, False, 0)
        controls.set_size_request(380, -1)
        self.body.pack_start(top, False, False, 0)

        info = card(9)
        info.pack_start(
            SectionHeader(
                "关于灯效",
                "RGB 键盘由 ITE 8291 HID 控制器驱动；灯带使用内核 LED 驱动。",
            ),
            False,
            False,
            0,
        )
        note = make_label(
            "NUC X15 的前部灯带由内核暴露为多色 LED。RGB 键盘是独立的 "
            "ITE 8291 USB HID 设备（048d:6006），不会出现在 LED 子系统里；"
            "程序通过 /dev/hidraw 直接控制它。",
            "subtext",
            wrap=True,
        )
        note.set_max_width_chars(120)
        info.pack_start(note, False, False, 0)
        self.body.pack_start(info, False, False, 0)

        saved_target = app.config.get("lighting_target", "keyboard")
        self.target_control.set_active(
            saved_target if saved_target in ("keyboard", "lightbar") else "keyboard"
        )
        self.stack.set_visible_child_name(self.target_control.active_id or "keyboard")
        self.preview.set_state(
            self.target_control.active_id or "keyboard",
            self.keyboard_color,
            self.keyboard_level / 4.0,
        )

    # ------------------------------------------------------------------
    def _build_keyboard_panel(self) -> Gtk.Box:
        panel = vbox(10)
        panel.set_margin_top(4)
        row = hbox(8)
        row.pack_start(make_label("亮度"), True, True, 0)
        self.kbd_level_label = make_label("3 级", "subtext")
        row.pack_end(self.kbd_level_label, False, False, 0)
        panel.pack_start(row, False, False, 0)
        self.kbd_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 4, 1)
        self.kbd_scale.set_draw_value(False)
        self.kbd_scale.set_value(3)
        self.kbd_scale.connect("value-changed", self._kbd_slider_changed)
        panel.pack_start(self.kbd_scale, False, False, 0)
        panel.pack_start(make_label("颜色", "info-key"), False, False, 0)
        self.kbd_swatches, self.kbd_swatch_list = self._build_swatches(
            self.keyboard_color, self._kbd_color_selected
        )
        panel.pack_start(self.kbd_swatches, False, False, 0)
        self.kbd_color_button = ColorPickerButton(self.keyboard_color, self._kbd_custom_color)
        panel.pack_start(self.kbd_color_button, False, False, 0)
        self.kbd_status = make_label("", "subtext", wrap=True)
        panel.pack_start(self.kbd_status, False, False, 0)
        return panel

    def _build_lightbar_panel(self) -> Gtk.Box:
        panel = vbox(10)
        panel.set_margin_top(4)
        row = hbox(8)
        row.pack_start(make_label("亮度"), True, True, 0)
        self.light_value_label = make_label("80%", "subtext")
        row.pack_end(self.light_value_label, False, False, 0)
        panel.pack_start(row, False, False, 0)
        self.light_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.light_scale.set_draw_value(False)
        self.light_scale.set_value(80)
        self.light_scale.connect("value-changed", self._light_slider_changed)
        panel.pack_start(self.light_scale, False, False, 0)
        panel.pack_start(make_label("颜色", "info-key"), False, False, 0)
        self.light_swatches, self.light_swatch_list = self._build_swatches(
            self.lightbar_color, self._light_color_selected
        )
        panel.pack_start(self.light_swatches, False, False, 0)
        self.light_color_button = ColorPickerButton(self.lightbar_color, self._light_custom_color)
        panel.pack_start(self.light_color_button, False, False, 0)
        self.rainbow_row = ToggleRow(
            "彩虹动画",
            "内核灯带的彩虹效果。",
            self._rainbow_changed,
        )
        panel.pack_start(self.rainbow_row, False, False, 0)
        self.breathing_row = ToggleRow(
            "休眠呼吸灯",
            "使用电源适配器休眠时缓慢呼吸。",
            self._breathing_changed,
        )
        panel.pack_start(self.breathing_row, False, False, 0)
        self.light_status = make_label("", "subtext", wrap=True)
        panel.pack_start(self.light_status, False, False, 0)
        return panel

    def _build_swatches(
        self, selected: tuple[int, int, int], callback
    ) -> tuple[Gtk.Box, list[ColorSwatch]]:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=7)
        selected_hex = "#%02x%02x%02x" % selected
        swatches: list[ColorSwatch] = []
        for color in PRESET_COLORS:
            swatch = ColorSwatch(color, 28, callback)
            swatch.set_selected(color.lower() == selected_hex.lower())
            box.pack_start(swatch, False, False, 0)
            swatches.append(swatch)
        return box, swatches

    # ------------------------------------------------------------------
    def _mark_swatches(self, swatches: list[ColorSwatch], color: tuple[int, int, int]) -> None:
        selected = "#%02x%02x%02x" % color
        for swatch in swatches:
            swatch.set_selected(swatch.color.lower() == selected.lower())

    def _target_changed(self, target: str) -> None:
        if self._updating:
            return
        self.stack.set_visible_child_name(target)
        self.app.config.set("lighting_target", target)
        self._update_preview()
        self._sync_controls()

    def _kbd_slider_changed(self, scale: Gtk.Scale) -> None:
        if self._updating:
            return
        self._last_user_at = time.monotonic()
        self.keyboard_level = int(round(scale.get_value()))
        self.kbd_level_label.set_text(f"{self.keyboard_level} 级")
        self._update_preview()
        self._kbd_debounce.schedule()

    def _light_slider_changed(self, scale: Gtk.Scale) -> None:
        if self._updating:
            return
        self._last_user_at = time.monotonic()
        self.lightbar_percent = float(scale.get_value())
        self.light_value_label.set_text(f"{self.lightbar_percent:.0f}%")
        self._update_preview()
        self._light_debounce.schedule()

    def _kbd_color_selected(self, color: str) -> None:
        self._last_user_at = time.monotonic()
        self.keyboard_color = hex_to_rgb(color)
        # Selecting a colour should also turn the keyboard on when it was
        # previously off.  Otherwise the user sees "nothing happens".
        if self.keyboard_level == 0:
            self.keyboard_level = 4
            self._updating = True
            self.kbd_scale.set_value(4)
            self._updating = False
            self.kbd_level_label.set_text("4 级")
        self.kbd_color_button.set_color(self.keyboard_color)
        self._mark_swatches(self.kbd_swatch_list, self.keyboard_color)
        self._update_preview()
        self._kbd_debounce.schedule()

    def _light_color_selected(self, color: str) -> None:
        self._last_user_at = time.monotonic()
        self.lightbar_color = hex_to_rgb(color)
        self.light_color_button.set_color(self.lightbar_color)
        self._mark_swatches(self.light_swatch_list, self.lightbar_color)
        self._update_preview()
        self._light_debounce.schedule()

    def _kbd_custom_color(self, color: tuple[int, int, int]) -> None:
        self._kbd_color_selected("#%02x%02x%02x" % color)

    def _light_custom_color(self, color: tuple[int, int, int]) -> None:
        self._light_color_selected("#%02x%02x%02x" % color)

    def _rainbow_changed(self, enabled: bool) -> None:
        if self._updating:
            return
        self._last_user_at = time.monotonic()
        try:
            self.app.controls.set_animations(rainbow=enabled)
            self.app.toast.show("彩虹动画" + ("已开启" if enabled else "已关闭"), "success")
        except ControlError as exc:
            self.app.toast.show(str(exc), "error")

    def _breathing_changed(self, enabled: bool) -> None:
        if self._updating:
            return
        self._last_user_at = time.monotonic()
        try:
            self.app.controls.set_animations(breathing=enabled)
            self.app.toast.show("休眠呼吸灯" + ("已开启" if enabled else "已关闭"), "success")
        except ControlError as exc:
            self.app.toast.show(str(exc), "error")

    def _update_preview(self) -> None:
        target = self.target_control.active_id or "keyboard"
        if target == "keyboard":
            self.preview.set_state(target, self.keyboard_color, self.keyboard_level / 4.0)
        else:
            self.preview.set_state(target, self.lightbar_color, self.lightbar_percent / 100.0)

    # ------------------------------------------------------------------
    def _apply_lighting(self) -> None:
        target = self.target_control.active_id or "keyboard"
        caps = self.app.controls.caps
        if not self.app.controls.can_control:
            self.app.toast.show("仅监控模式。运行 install.sh 后可控制灯效。", "error")
            return
        try:
            if target == "keyboard":
                if caps.get("kbd_led"):
                    self.app.controls.set_kbd_led(
                        self.keyboard_color, self.keyboard_level / 4.0 * 100.0
                    )
                elif caps.get("kbd_ite") or caps.get("kbd_ec"):
                    self.app.controls.set_keyboard(
                        rgb=self.keyboard_color, level=self.keyboard_level
                    )
                else:
                    self.app.toast.show("键盘 RGB 控制不可用。", "error")
                    return
                self.app.config.set("keyboard_color", list(self.keyboard_color))
            else:
                if not caps.get("lightbar"):
                    self.app.toast.show("未找到前部灯带。", "error")
                    return
                self.app.controls.set_lightbar(self.lightbar_color, self.lightbar_percent)
                self.app.config.set("lightbar_color", list(self.lightbar_color))
        except ControlError as exc:
            self.app.toast.show(str(exc), "error")

    # ------------------------------------------------------------------
    def _sync_controls(self) -> None:
        caps = self.app.controls.caps
        state = self.app.state or {}
        self._updating = True
        keyboard_ok = bool(caps.get("kbd_ite") or caps.get("kbd_ec") or caps.get("kbd_led"))
        lightbar_ok = bool(caps.get("lightbar"))
        for option_id, button in self.target_control.buttons.items():
            button.set_sensitive(
                keyboard_ok if option_id == "keyboard" else lightbar_ok
            )
        if not keyboard_ok and lightbar_ok and self.target_control.active_id == "keyboard":
            self.target_control.set_active("lightbar")
            self._update_preview()
        elif not lightbar_ok and keyboard_ok and self.target_control.active_id == "lightbar":
            self.target_control.set_active("keyboard")
            self._update_preview()
        self.stack.set_visible_child_name(self.target_control.active_id or "keyboard")
        self.kbd_scale.set_sensitive(keyboard_ok and self.app.controls.can_control)
        self.light_scale.set_sensitive(lightbar_ok and self.app.controls.can_control)
        self.kbd_color_button.set_sensitive(keyboard_ok and self.app.controls.can_control)
        self.light_color_button.set_sensitive(lightbar_ok and self.app.controls.can_control)
        for swatch in self.kbd_swatch_list + self.light_swatch_list:
            swatch.set_sensitive(self.app.controls.can_control)
        self.rainbow_row.set_sensitive(
            bool(caps.get("rainbow")) and self.app.controls.can_control
        )
        self.breathing_row.set_sensitive(
            bool(caps.get("breathing")) and self.app.controls.can_control
        )
        if not keyboard_ok:
            self.kbd_status.set_text("键盘 RGB 不可用。")
        elif not self.app.controls.can_control:
            self.kbd_status.set_text("仅监控模式：安装特权服务后可控制。")
        elif caps.get("kbd_ite"):
            self.kbd_status.set_text("ITE 8291 HID 键盘 · 4 级亮度")
        else:
            self.kbd_status.set_text("EC RGB 键盘 · 4 级亮度")
        if not lightbar_ok:
            self.light_status.set_text("未找到前部灯带。")
        elif not self.app.controls.can_control:
            self.light_status.set_text("仅监控模式：安装特权服务后可控制。")
        else:
            self.light_status.set_text("内核 LED 驱动 · 支持彩虹与呼吸效果")
        animations = state.get("animations") or {}
        self.rainbow_row.set_active(bool(animations.get("rainbow")))
        self.breathing_row.set_active(bool(animations.get("breathing")))
        self._updating = False

    def controls_updated(self, state: dict) -> None:
        self._sync_controls()
        if time.monotonic() - self._last_user_at < 2.5:
            return
        ec = state.get("ec") or {}
        ite = state.get("ite") or {}
        if ite.get("available"):
            if ite.get("color"):
                try:
                    self.keyboard_color = tuple(int(part) for part in ite["color"])
                except (TypeError, ValueError):
                    pass
            if ite.get("level") is not None:
                self.keyboard_level = max(0, min(4, int(ite["level"])))
        else:
            if ec.get("kbd_rgb"):
                try:
                    self.keyboard_color = tuple(
                        max(0, min(255, round(int(part) * 255 / 50)))
                        for part in ec["kbd_rgb"]
                    )
                except (TypeError, ValueError):
                    pass
            if ec.get("kbd_brightness") is not None:
                self.keyboard_level = max(0, min(4, int(ec["kbd_brightness"])))
                if ec.get("kbd_power_off"):
                    self.keyboard_level = 0
        light = state.get("lightbar") or {}
        if light.get("rgb"):
            max_intensity = light.get("max_intensity") or 200
            self.lightbar_color = tuple(
                max(0, min(255, round(int(part) * 255 / max_intensity)))
                for part in light["rgb"]
            )
            if light.get("brightness"):
                self.lightbar_percent = max(
                    0.0,
                    min(
                        100.0,
                        max(int(part) for part in light["rgb"]) / max_intensity * 100.0,
                    ),
                )
            else:
                self.lightbar_percent = 0.0
        kbd_led = state.get("kbd_led") or {}
        if kbd_led.get("rgb"):
            max_intensity = kbd_led.get("max_intensity") or 200
            self.keyboard_color = tuple(
                max(0, min(255, round(int(part) * 255 / max_intensity)))
                for part in kbd_led["rgb"]
            )
        self._updating = True
        self.kbd_scale.set_value(self.keyboard_level)
        self.light_scale.set_value(self.lightbar_percent)
        self.kbd_level_label.set_text(f"{self.keyboard_level} 级")
        self.light_value_label.set_text(f"{self.lightbar_percent:.0f}%")
        self._mark_swatches(self.kbd_swatch_list, self.keyboard_color)
        self._mark_swatches(self.light_swatch_list, self.lightbar_color)
        self.kbd_color_button.set_color(self.keyboard_color)
        self.light_color_button.set_color(self.lightbar_color)
        self._updating = False
        if self.app.controls.caps.get("kbd_ite") and self.keyboard_level == 0:
            self.kbd_status.set_text("ITE 8291 HID 键盘 · 当前关闭；选择颜色或调高亮度即可开启")
        self._update_preview()

    def update(self, snap, state: dict) -> None:
        # The preview and controls are updated when the control state changes
        # (and immediately after user interaction), not on every sensor tick.
        pass


# ======================================================================
# Battery
# ======================================================================
class BatteryPage(Page):
    def __init__(self, app) -> None:
        super().__init__(app, "电池", "充电上限、健康度与功耗")
        self._updating = False
        self._last_user_at = 0.0
        self._limit_debounce = Debouncer(350, self._apply_limit)

        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        hero = card(12)
        hero_row = hbox(18)
        self.battery_icon = BatteryIcon(150, 68)
        hero_row.pack_start(self.battery_icon, False, False, 0)
        info = vbox(4)
        self.battery_percent = make_label("--", "big-value")
        self.battery_status = make_label("--", "subtext")
        info.pack_start(self.battery_percent, False, False, 0)
        info.pack_start(self.battery_status, False, False, 0)
        hero_row.pack_start(info, True, True, 0)
        hero.pack_start(hero_row, False, False, 0)
        divider = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        divider.set_size_request(-1, 1)
        divider.get_style_context().add_class("nav-separator")
        hero.pack_start(divider, False, False, 0)
        self.time_row = InfoRow("预计剩余时间", "--")
        self.power_row = InfoRow("功耗", "--")
        self.ac_row = InfoRow("电源", "--")
        hero.pack_start(self.time_row, False, False, 0)
        hero.pack_start(self.power_row, False, False, 0)
        hero.pack_start(self.ac_row, False, False, 0)
        top.pack_start(hero, True, True, 0)

        limit_card = card(10)
        limit_card.pack_start(
            SectionHeader("充电上限", "限制充电上限可延长电池寿命"), False, False, 0
        )
        limit_row = hbox(8)
        limit_row.pack_start(make_label("停止充电于"), True, True, 0)
        self.limit_value = make_label("--", "medium-value")
        limit_row.pack_end(self.limit_value, False, False, 0)
        limit_card.pack_start(limit_row, False, False, 0)
        self.limit_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 40, 100, 1)
        self.limit_scale.set_draw_value(False)
        self.limit_scale.connect("value-changed", self._limit_changed)
        limit_card.pack_start(self.limit_scale, False, False, 0)
        marks = hbox(6)
        self.preset_buttons: list[Gtk.Button] = []
        for value in (60, 80, 100):
            button = Gtk.Button(label=f"{value}%")
            button.get_style_context().add_class("secondary-button")
            button.connect("clicked", lambda _b, v=value: self._set_limit(v))
            marks.pack_start(button, True, True, 0)
            self.preset_buttons.append(button)
        limit_card.pack_start(marks, False, False, 0)
        self.limit_note = make_label(
            "EC 会在此电量停止充电。日常使用建议 80%。",
            "subtext",
            wrap=True,
        )
        limit_card.pack_start(self.limit_note, False, False, 0)
        top.pack_start(limit_card, True, True, 0)
        self.body.pack_start(top, False, False, 0)

        stats = card(10)
        stats.pack_start(SectionHeader("电池健康", "BAT0 报告的详细信息"), False, False, 0)
        grid = Gtk.Grid(column_spacing=26, row_spacing=9)
        self.health_row = InfoRow("健康度", "--")
        self.wear_row = InfoRow("损耗", "--")
        self.design_row = InfoRow("设计容量", "--")
        self.full_row = InfoRow("满充容量", "--")
        self.voltage_row = InfoRow("电压", "--")
        self.current_row = InfoRow("电流", "--")
        self.cycles_row = InfoRow("循环次数", "--")
        self.tech_row = InfoRow("电池类型", "--")
        grid.attach(self.health_row, 0, 0, 1, 1)
        grid.attach(self.wear_row, 1, 0, 1, 1)
        grid.attach(self.design_row, 0, 1, 1, 1)
        grid.attach(self.full_row, 1, 1, 1, 1)
        grid.attach(self.voltage_row, 0, 2, 1, 1)
        grid.attach(self.current_row, 1, 2, 1, 1)
        grid.attach(self.cycles_row, 0, 3, 1, 1)
        grid.attach(self.tech_row, 1, 3, 1, 1)
        self.health_row.set_hexpand(True)
        self.wear_row.set_hexpand(True)
        stats.pack_start(grid, False, False, 0)
        self.body.pack_start(stats, False, False, 0)

        care = card(8)
        care.pack_start(SectionHeader("电池保养", "94 Wh 电池的使用建议"), False, False, 0)
        text = make_label(
            "• 长期插电使用时，把充电上限设为 80%。\n"
            "• 避免长时间让电池保持在 0%。\n"
            "• 健康度 = 满充容量 / 设计容量。",
            "subtext",
            wrap=True,
        )
        text.set_max_width_chars(100)
        care.pack_start(text, False, False, 0)
        self.body.pack_start(care, False, False, 0)

    # ------------------------------------------------------------------
    def _limit_changed(self, scale: Gtk.Scale) -> None:
        if self._updating:
            return
        self._last_user_at = time.monotonic()
        value = int(round(scale.get_value()))
        self.limit_value.set_text(f"{value}%")
        self._limit_debounce.schedule()

    def _set_limit(self, value: int) -> None:
        self._last_user_at = time.monotonic()
        self._updating = True
        self.limit_scale.set_value(value)
        self._updating = False
        self.limit_value.set_text(f"{value}%")
        self._apply_limit()

    def _apply_limit(self) -> None:
        value = int(round(self.limit_scale.get_value()))
        try:
            self.app.controls.set_battery_limit(value)
            self.app.toast.show(f"充电上限已设为 {value}%", "success")
            self.app.refresh_controls_async()
        except ControlError as exc:
            self.app.toast.show(str(exc), "error")

    def controls_updated(self, state: dict) -> None:
        battery = state.get("battery") or {}
        limit = battery.get("limit")
        caps = self.app.controls.caps
        can_limit = bool(caps.get("battery_limit")) and self.app.controls.can_control
        self.limit_scale.set_sensitive(can_limit)
        for button in self.preset_buttons:
            button.set_sensitive(can_limit)
        if limit is not None and time.monotonic() - self._last_user_at > 2.0:
            self._updating = True
            self.limit_scale.set_value(limit)
            self._updating = False
            self.limit_value.set_text(f"{limit}%")
        self.limit_note.set_text(
            "EC 会在此电量停止充电。"
            if caps.get("battery_limit") and self.app.controls.can_control
            else "充电上限控制需要特权服务。"
        )

    def update(self, snap, state: dict) -> None:
        bat = snap.battery
        self.battery_icon.set_value(
            bat.capacity, bat.status.lower().startswith("charg")
        )
        self.battery_percent.set_text(f"{bat.capacity}%" if bat.capacity is not None else "--")
        self.battery_status.set_text(
            f"{translate_battery_status(bat.status)} · {'已连接电源' if bat.ac_online else '未连接电源'}"
        )
        remaining = bat.time_remaining
        self.time_row.set_value(
            human_uptime(remaining) if remaining else "--"
        )
        self.power_row.set_value(format_power(bat.power))
        self.ac_row.set_value("电源适配器" if bat.ac_online else "电池")
        health = bat.health_percent
        self.health_row.set_value(
            f"{health:.0f}% ({translate_health(bat.health)})" if health is not None else translate_health(bat.health)
        )
        self.wear_row.set_value(f"{100 - health:.0f}%" if health is not None else "--")
        self.design_row.set_value(
            f"{bat.charge_full_design / 1000.0:.0f} mAh" if bat.charge_full_design else "--"
        )
        self.full_row.set_value(
            f"{bat.charge_full / 1000.0:.0f} mAh" if bat.charge_full else "--"
        )
        self.voltage_row.set_value(f"{bat.voltage:.2f} V" if bat.voltage else "--")
        self.current_row.set_value(f"{bat.current:+.2f} A" if bat.current is not None else "--")
        self.cycles_row.set_value(str(bat.cycles) if bat.cycles else "未报告")
        self.tech_row.set_value(translate_technology(bat.technology))

    def on_show(self) -> None:
        self.controls_updated(self.app.state)


# ======================================================================
# Settings
# ======================================================================
class SettingsPage(Page):
    def __init__(self, app) -> None:
        super().__init__(app, "设置", "键盘、显示、启动与特权服务")
        self._updating = False

        helper_card = card(9)
        helper_header = hbox(9)
        self.helper_dot = ColorDot("#fbbf24", 9)
        helper_header.pack_start(self.helper_dot, False, False, 0)
        self.helper_label = make_label("正在检查特权服务…", "section-title")
        helper_header.pack_start(self.helper_label, True, True, 0)
        self.reconnect_button = Gtk.Button(label="重新连接")
        self.reconnect_button.get_style_context().add_class("secondary-button")
        self.reconnect_button.connect("clicked", lambda *_: self.app.reconnect_controls())
        helper_header.pack_end(self.reconnect_button, False, False, 0)
        helper_card.pack_start(helper_header, False, False, 0)
        self.helper_detail = make_label("", "subtext", wrap=True)
        helper_card.pack_start(self.helper_detail, False, False, 0)
        self.copy_install_button = Gtk.Button(label="复制安装命令")
        self.copy_install_button.get_style_context().add_class("secondary-button")
        self.copy_install_button.set_halign(Gtk.Align.START)
        self.copy_install_button.connect("clicked", self._copy_install)
        helper_card.pack_start(self.copy_install_button, False, False, 0)
        self.body.pack_start(helper_card, False, False, 0)

        keyboard = card(10)
        keyboard.pack_start(SectionHeader("键盘", "Uniwill EC 开关"), False, False, 0)
        self.fn_lock_row = ToggleRow("FN 锁定", "默认把顶排按键当作功能键")
        self.super_key_row = ToggleRow("Super 键", "启用 Windows / Super 键")
        self.touchpad_row = ToggleRow("触控板开关", "允许触控板开关快捷键")
        for row, name in (
            (self.fn_lock_row, "fn_lock"),
            (self.super_key_row, "super_key"),
            (self.touchpad_row, "touchpad_toggle"),
        ):
            row.name = name  # type: ignore[attr-defined]
            keyboard.pack_start(row, False, False, 0)
        self.body.pack_start(keyboard, False, False, 0)

        display = card(10)
        display.pack_start(SectionHeader("显示", "界面偏好"), False, False, 0)
        units_row = hbox(12)
        units_row.pack_start(make_label("温度单位"), True, True, 0)
        self.units_control = SegmentedControl([("C", "摄氏度"), ("F", "华氏度")])
        self.units_control.connect_changed(self.app.set_units)
        units_row.pack_end(self.units_control, False, False, 0)
        display.pack_start(units_row, False, False, 0)
        theme_row = hbox(12)
        theme_row.pack_start(make_label("主题"), True, True, 0)
        self.theme_control = SegmentedControl([("dark", "深色"), ("light", "浅色")])
        self.theme_control.connect_changed(self.app.set_theme)
        theme_row.pack_end(self.theme_control, False, False, 0)
        display.pack_start(theme_row, False, False, 0)
        poll_row = hbox(12)
        poll_row.pack_start(make_label("刷新间隔"), True, True, 0)
        self.poll_combo = Gtk.ComboBoxText()
        for ms, label in ((1000, "1 秒"), (2000, "2 秒"), (5000, "5 秒")):
            self.poll_combo.append(str(ms), label)
        self.poll_combo.connect("changed", self._poll_changed)
        poll_row.pack_end(self.poll_combo, False, False, 0)
        display.pack_start(poll_row, False, False, 0)
        self.body.pack_start(display, False, False, 0)

        startup = card(10)
        startup.pack_start(SectionHeader("启动", "自动启动 NUC 中心"), False, False, 0)
        self.autostart_row = ToggleRow(
            "登录时启动",
            "登录 Plasma 后自动打开仪表盘",
            self._autostart_changed,
        )
        startup.pack_start(self.autostart_row, False, False, 0)
        self.body.pack_start(startup, False, False, 0)

        about = card(10)
        about.pack_start(SectionHeader("关于此设备", "硬件与软件"), False, False, 0)
        grid = Gtk.Grid(column_spacing=26, row_spacing=8)
        self.about_rows: dict[str, InfoRow] = {}
        entries = [
            ("产品", app.device.model_line),
            ("SKU", app.device.product_sku),
            ("主板", app.device.board_name),
            ("BIOS", app.device.bios_version),
            ("CPU", app.device.cpu_model),
            ("显卡", " · ".join(app.device.gpus) if app.device.gpus else "--"),
            ("内存", format_bytes_gib(app.device.memory_total, 0)),
            ("内核", app.device.kernel),
            ("系统", app.device.os_name),
            ("程序", "NUC 中心 1.0.0"),
        ]
        for index, (key, value) in enumerate(entries):
            row = InfoRow(key, value or "--")
            grid.attach(row, index % 2, index // 2, 1, 1)
            self.about_rows[key] = row
        about.pack_start(grid, False, False, 0)
        report_button = Gtk.Button(label="复制系统报告")
        report_button.get_style_context().add_class("secondary-button")
        report_button.connect("clicked", self._copy_report)
        about.pack_start(report_button, False, False, 0)
        credit = make_label(
            "为 Intel NUC X15（LAPKC71F）打造 · Uniwill EC · GTK3",
            "subtext",
        )
        about.pack_start(credit, False, False, 0)
        self.body.pack_start(about, False, False, 0)

        self._sync_from_config()
        self.controls_updated(app.state)

    # ------------------------------------------------------------------
    def _bind_toggle(self, row: ToggleRow, name: str) -> None:
        def changed(enabled: bool) -> None:
            if self._updating:
                return
            try:
                self.app.controls.set_toggle(name, enabled)
                self.app.toast.show(TOGGLE_ZH.get(name, name) + ("已启用" if enabled else "已关闭"), "success")
            except ControlError as exc:
                self.app.toast.show(str(exc), "error")
                row.set_active(not enabled)

        row.on_change = changed

    def _sync_from_config(self) -> None:
        self._updating = True
        self.units_control.set_active(self.app.config.get("units", "C"))
        self.theme_control.set_active(self.app.config.get("theme", "dark"))
        self.poll_combo.set_active_id(str(self.app.config.get("poll_ms", 1000)))
        self.autostart_row.set_active(bool(self.app.config.get("autostart", False)))
        self._updating = False
        self._bind_toggle(self.fn_lock_row, "fn_lock")
        self._bind_toggle(self.super_key_row, "super_key")
        self._bind_toggle(self.touchpad_row, "touchpad_toggle")
        self.autostart_row.on_change = self._autostart_changed

    def _poll_changed(self, combo: Gtk.ComboBoxText) -> None:
        if self._updating:
            return
        try:
            self.app.set_poll_interval(int(combo.get_active_id() or "1000"))
        except ValueError:
            pass

    def _autostart_changed(self, enabled: bool) -> None:
        if self._updating:
            return
        ok = self.app.set_autostart(enabled)
        if not ok:
            self.app.toast.show("无法更新开机自启动项。", "error")
            self.autostart_row.set_active(not enabled)
        else:
            self.app.toast.show("开机自启动" + ("已启用" if enabled else "已关闭"), "success")

    def _copy_install(self) -> None:
        path = Path(__file__).resolve().parents[1] / "install.sh"
        command = f"sudo {shlex.quote(str(path))}"
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(command, -1)
        clipboard.store()
        self.app.toast.show("安装命令已复制", "success")

    def _copy_report(self) -> None:
        report = self.app.build_report()
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        clipboard.set_text(report, -1)
        clipboard.store()
        self.app.toast.show("系统报告已复制", "success")

    def controls_updated(self, state: dict) -> None:
        self._updating = True
        caps = self.app.controls.caps
        toggles = state.get("toggles") or {}
        for row, name, cap_key in (
            (self.fn_lock_row, "fn_lock", "fn_lock"),
            (self.super_key_row, "super_key", "super_key"),
            (self.touchpad_row, "touchpad_toggle", "touchpad_toggle"),
        ):
            value = toggles.get(name)
            if value is not None:
                row.set_active(bool(value))
            row.set_sensitive(bool(caps.get(cap_key)) and self.app.controls.can_control)
        mode = self.app.controls.mode
        if mode in ("helper", "root"):
            if caps.get("ec"):
                self.helper_dot.set_color(self.app.theme.palette.success)
                self.helper_label.set_text("特权服务已连接")
                self.helper_detail.set_text(
                    "性能模式、键盘 RGB、灯带和电池控制均可用。"
                )
                self.copy_install_button.hide()
            else:
                self.helper_dot.set_color(self.app.theme.palette.warning)
                self.helper_label.set_text("特权服务已连接 · EC 不可用")
                self.helper_detail.set_text(
                    "特权服务正在运行，但 acpi_call 没有暴露 Uniwill EC。"
                    "灯带和电池控制可能仍然可用。请安装 acpi_call 并重启特权服务，"
                    "以启用性能模式和键盘 RGB。"
                )
                self.copy_install_button.show()
        else:
            self.helper_dot.set_color(self.app.theme.palette.warning)
            self.helper_label.set_text("仅监控模式")
            self.helper_detail.set_text(
                "温度和风扇数据已实时显示。运行一次安装脚本即可启用 "
                "性能模式、键盘 RGB、灯带和充电上限。"
            )
            self.copy_install_button.show()
        self._updating = False

    def on_show(self) -> None:
        self.controls_updated(self.app.state)
