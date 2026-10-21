"""Custom Cairo widgets used by the NUC Center interface."""

from __future__ import annotations

import math
from typing import Callable, Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Gdk, GLib, Gtk, Pango, PangoCairo  # noqa: E402

from .theme import ThemeManager  # noqa: E402
from .util import clamp, hex_to_rgb  # noqa: E402


# ----------------------------------------------------------------------
# small drawing helpers
# ----------------------------------------------------------------------
def rounded_rect(ctx, x: float, y: float, w: float, h: float, r: float) -> None:
    r = max(0.0, min(r, w / 2.0, h / 2.0))
    ctx.new_sub_path()
    ctx.arc(x + w - r, y + r, r, -math.pi / 2.0, 0)
    ctx.arc(x + w - r, y + h - r, r, 0, math.pi / 2.0)
    ctx.arc(x + r, y + h - r, r, math.pi / 2.0, math.pi)
    ctx.arc(x + r, y + r, r, math.pi, 3.0 * math.pi / 2.0)
    ctx.close_path()


def set_rgba(ctx, color: str, alpha: float = 1.0) -> None:
    r, g, b = hex_to_rgb(color)
    ctx.set_source_rgba(r / 255.0, g / 255.0, b / 255.0, alpha)


def draw_text(
    widget: Gtk.Widget,
    ctx,
    text: str,
    x: float,
    y: float,
    size: float = 10.0,
    color: str = "#ffffff",
    weight: Pango.Weight = Pango.Weight.NORMAL,
    anchor: str = "center",
    alpha: float = 1.0,
) -> tuple[int, int]:
    layout = widget.create_pango_layout(text)
    desc = Pango.FontDescription("Noto Sans CJK SC")
    desc.set_size(int(size * Pango.SCALE))
    desc.set_weight(weight)
    layout.set_font_description(desc)
    width, height = layout.get_pixel_size()
    if anchor == "center":
        x -= width / 2.0
        y -= height / 2.0
    elif anchor == "east":
        x -= width
    elif anchor == "west":
        pass
    elif anchor == "north":
        x -= width / 2.0
    elif anchor == "south":
        x -= width / 2.0
        y -= height
    set_rgba(ctx, color, alpha)
    ctx.move_to(round(x), round(y))
    PangoCairo.show_layout(ctx, layout)
    return width, height


def make_label(
    text: str = "",
    css_class: Optional[str] = None,
    xalign: float = 0.0,
    wrap: bool = False,
    max_width_chars: int = -1,
) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=xalign)
    if css_class:
        label.get_style_context().add_class(css_class)
    if wrap:
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if max_width_chars > 0:
        label.set_max_width_chars(max_width_chars)
    return label


def make_icon(name: str, size: Gtk.IconSize = Gtk.IconSize.BUTTON) -> Gtk.Image:
    return Gtk.Image.new_from_icon_name(name, size)


def separator(vertical: bool = False) -> Gtk.Separator:
    return Gtk.Separator(
        orientation=Gtk.Orientation.VERTICAL if vertical else Gtk.Orientation.HORIZONTAL
    )


# ----------------------------------------------------------------------
# cards / badges
# ----------------------------------------------------------------------
class IconBadge(Gtk.Box):
    def __init__(self, icon: str, color_class: str = "", size: int = 32) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.set_size_request(size, size)
        self.set_halign(Gtk.Align.CENTER)
        self.set_valign(Gtk.Align.CENTER)
        context = self.get_style_context()
        context.add_class("icon-badge")
        if color_class:
            context.add_class(color_class)
        image = make_icon(icon, Gtk.IconSize.LARGE_TOOLBAR)
        image.set_halign(Gtk.Align.CENTER)
        image.set_valign(Gtk.Align.CENTER)
        self.pack_start(image, True, True, 0)


class SectionHeader(Gtk.Box):
    def __init__(self, title: str, subtitle: str = "", action: Optional[Gtk.Widget] = None) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        self.set_valign(Gtk.Align.CENTER)
        text_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        text_box.pack_start(make_label(title, "section-title"), False, False, 0)
        if subtitle:
            text_box.pack_start(make_label(subtitle, "section-subtitle"), False, False, 0)
        self.pack_start(text_box, True, True, 0)
        if action is not None:
            self.pack_end(action, False, False, 0)


class Pill(Gtk.Box):
    def __init__(self, text: str = "", kind: str = "") -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        self.set_valign(Gtk.Align.CENTER)
        self.label = make_label(text, "pill")
        self.label.set_xalign(0.5)
        if kind:
            self.label.get_style_context().add_class(kind)
        self.pack_start(self.label, False, False, 0)

    def set_text(self, text: str, kind: Optional[str] = None) -> None:
        self.label.set_text(text)
        context = self.label.get_style_context()
        for cls in ("warn", "err", "neutral"):
            context.remove_class(cls)
        if kind:
            context.add_class(kind)


class MetricCard(Gtk.Box):
    def __init__(
        self,
        label: str,
        icon: str,
        accent_class: str = "",
        with_bar: bool = True,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.get_style_context().add_class("metric-card")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        top.pack_start(IconBadge(icon, accent_class, 30), False, False, 0)
        top.pack_start(make_label(label, "card-label"), True, True, 0)
        self.pack_start(top, False, False, 0)
        value_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=5)
        value_row.set_valign(Gtk.Align.END)
        self.value_label = make_label("--", "big-value")
        self.unit_label = make_label("", "unit")
        self.unit_label.set_valign(Gtk.Align.END)
        self.unit_label.set_margin_bottom(3)
        value_row.pack_start(self.value_label, False, False, 0)
        value_row.pack_start(self.unit_label, False, False, 0)
        self.pack_start(value_row, False, False, 0)
        self.sub_label = make_label("", "subtext")
        self.pack_start(self.sub_label, False, False, 0)
        self.bar = Bar(6) if with_bar else None
        if self.bar is not None:
            self.bar.set_margin_top(2)
            self.pack_start(self.bar, False, False, 0)

    def set_value(
        self,
        value: str,
        unit: str = "",
        subtext: str = "",
        percent: Optional[float] = None,
        color: Optional[str] = None,
    ) -> None:
        self.value_label.set_text(value)
        self.unit_label.set_text(unit)
        self.sub_label.set_text(subtext)
        if self.bar is not None:
            self.bar.set_value(percent if percent is not None else 0.0, color)


class Bar(Gtk.DrawingArea):
    def __init__(self, height: int = 7) -> None:
        super().__init__()
        self.height = height
        self.percent = 0.0
        self.color: Optional[str] = None
        self.set_size_request(-1, height)
        self.connect("draw", self._draw)

    def set_value(self, percent: Optional[float], color: Optional[str] = None) -> None:
        self.percent = clamp(percent or 0.0, 0.0, 100.0)
        self.color = color
        self.queue_draw()

    def _draw(self, widget, ctx) -> bool:
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        palette = _palette(widget)
        rounded_rect(ctx, 0, 0, width, height, height / 2.0)
        set_rgba(ctx, palette.border, 0.8)
        ctx.fill()
        if self.percent > 0:
            fill_width = max(height, width * self.percent / 100.0)
            rounded_rect(ctx, 0, 0, fill_width, height, height / 2.0)
            set_rgba(ctx, self.color or palette.accent, 1.0)
            ctx.fill()
        return False


class Divider(Gtk.Box):
    def __init__(self) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.set_size_request(-1, 1)
        self.get_style_context().add_class("nav-separator")


# ----------------------------------------------------------------------
# gauges
# ----------------------------------------------------------------------
class Gauge(Gtk.DrawingArea):
    def __init__(
        self,
        size: int = 170,
        maximum: float = 100.0,
        unit: str = "%",
        label: str = "",
        line_width: int = 11,
    ) -> None:
        super().__init__()
        self.set_size_request(size, size)
        self.maximum = maximum
        self.unit = unit
        self.label = label
        self.line_width = line_width
        self.value: Optional[float] = None
        self.display: Optional[str] = None
        self.color: Optional[str] = None
        self.subtext = ""
        self.connect("draw", self._draw)

    def set_value(
        self,
        value: Optional[float],
        display: Optional[str] = None,
        color: Optional[str] = None,
        subtext: Optional[str] = None,
    ) -> None:
        self.value = value
        self.display = display
        self.color = color
        if subtext is not None:
            self.subtext = subtext
        self.queue_draw()

    def set_unit(self, unit: str) -> None:
        if self.unit != unit:
            self.unit = unit
            self.queue_draw()

    def set_label(self, label: str) -> None:
        self.label = label
        self.queue_draw()

    def _draw(self, widget, ctx) -> bool:
        palette = _palette(widget)
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        cx, cy = width / 2.0, height / 2.0
        radius = min(width, height) / 2.0 - self.line_width - 6
        start = 0.75 * math.pi
        sweep = 1.5 * math.pi
        color = self.color or palette.accent

        # Track.
        ctx.set_line_width(self.line_width)
        ctx.set_line_cap(1)  # round
        ctx.arc(cx, cy, radius, start, start + sweep)
        set_rgba(ctx, palette.border, 0.85)
        ctx.stroke()

        if self.value is not None and self.maximum > 0:
            fraction = clamp(self.value / self.maximum, 0.0, 1.0)
            if fraction > 0.001:
                end = start + sweep * fraction
                # Soft glow.
                ctx.set_line_width(self.line_width + 7)
                ctx.arc(cx, cy, radius, start, end)
                set_rgba(ctx, color, 0.12)
                ctx.stroke()
                # Actual arc.
                ctx.set_line_width(self.line_width)
                ctx.arc(cx, cy, radius, start, end)
                set_rgba(ctx, color, 1.0)
                ctx.stroke()

        display = self.display
        if display is None:
            display = "--" if self.value is None else f"{self.value:.0f}"
        draw_text(
            widget,
            ctx,
            display,
            cx,
            cy - 5,
            size=26.0,
            color=palette.text,
            weight=Pango.Weight.BOLD,
        )
        draw_text(
            widget,
            ctx,
            self.unit,
            cx,
            cy + 22,
            size=9.5,
            color=palette.muted,
            weight=Pango.Weight.MEDIUM,
        )
        if self.label:
            draw_text(
                widget,
                ctx,
                self.label,
                cx,
                cy + radius + 1,
                size=8.5,
                color=palette.muted,
            )
        if self.subtext:
            draw_text(
                widget,
                ctx,
                self.subtext,
                cx,
                cy + radius + 15,
                size=8.0,
                color=palette.muted,
                alpha=0.9,
            )
        return False


# ----------------------------------------------------------------------
# fan
# ----------------------------------------------------------------------
class FanIcon(Gtk.DrawingArea):
    def __init__(self, size: int = 150) -> None:
        super().__init__()
        self.set_size_request(size, size)
        self.rpm = 0
        self.angle = 0.0
        self.accent: Optional[str] = None
        self._timeout: Optional[int] = None
        self.connect("draw", self._draw)
        self.connect("destroy", self._stop)

    def set_rpm(self, rpm: Optional[int]) -> None:
        self.rpm = rpm or 0
        if self.rpm > 0:
            if self._timeout is None:
                self._timeout = GLib.timeout_add(50, self._tick)
        else:
            self._stop()
        self.queue_draw()

    def set_accent(self, color: str) -> None:
        self.accent = color
        self.queue_draw()

    def _tick(self) -> bool:
        if self.rpm <= 0:
            self._timeout = None
            return False
        speed = 0.035 + min(self.rpm, 6000) / 6000.0 * 0.28
        self.angle = (self.angle + speed) % (2 * math.pi)
        self.queue_draw()
        return True

    def _stop(self, *args) -> None:
        if self._timeout is not None:
            GLib.source_remove(self._timeout)
            self._timeout = None

    def _draw(self, widget, ctx) -> bool:
        palette = _palette(widget)
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        cx, cy = width / 2.0, height / 2.0
        radius = min(width, height) / 2.0 - 8
        accent = self.accent or palette.accent

        # Outer ring.
        ctx.set_line_width(2)
        ctx.arc(cx, cy, radius, 0, 2 * math.pi)
        set_rgba(ctx, accent, 0.18)
        ctx.stroke()

        # Blades.
        blade_radius = radius * 0.78
        ctx.save()
        ctx.translate(cx, cy)
        ctx.rotate(self.angle)
        for index in range(5):
            ctx.save()
            ctx.rotate(index * 2 * math.pi / 5.0)
            ctx.move_to(0, -blade_radius * 0.10)
            ctx.curve_to(
                blade_radius * 0.28,
                -blade_radius * 0.58,
                blade_radius * 0.74,
                -blade_radius * 0.48,
                blade_radius * 0.80,
                -blade_radius * 0.05,
            )
            ctx.curve_to(
                blade_radius * 0.64,
                blade_radius * 0.30,
                blade_radius * 0.22,
                blade_radius * 0.25,
                0,
                -blade_radius * 0.10,
            )
            ctx.close_path()
            set_rgba(ctx, accent, 0.72)
            ctx.fill_preserve()
            set_rgba(ctx, accent, 0.95)
            ctx.set_line_width(1)
            ctx.stroke()
            ctx.restore()
        ctx.restore()

        # Hub.
        ctx.arc(cx, cy, radius * 0.20, 0, 2 * math.pi)
        set_rgba(ctx, palette.card2, 1.0)
        ctx.fill_preserve()
        set_rgba(ctx, accent, 0.8)
        ctx.set_line_width(2)
        ctx.stroke()
        ctx.arc(cx, cy, radius * 0.065, 0, 2 * math.pi)
        set_rgba(ctx, accent, 1.0)
        ctx.fill()
        return False


class FanCard(Gtk.Box):
    def __init__(self, title: str, subtitle: str = "") -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=10)
        self.get_style_context().add_class("card")
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        top.pack_start(make_label(title, "section-title"), True, True, 0)
        self.status = Pill("AUTO", "neutral")
        top.pack_end(self.status, False, False, 0)
        self.pack_start(top, False, False, 0)
        if subtitle:
            self.pack_start(make_label(subtitle, "subtext"), False, False, 0)
        body = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.fan = FanIcon(132)
        body.pack_start(self.fan, False, False, 0)
        values = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        values.set_valign(Gtk.Align.CENTER)
        self.rpm_label = make_label("--", "big-value")
        values.pack_start(self.rpm_label, False, False, 0)
        values.pack_start(make_label("RPM", "unit"), False, False, 0)
        self.pwm_label = make_label("PWM --", "subtext")
        values.pack_start(self.pwm_label, False, False, 0)
        body.pack_start(values, True, True, 0)
        self.pack_start(body, False, False, 0)
        self.bar = Bar(6)
        self.pack_start(self.bar, False, False, 0)

    def set_values(self, rpm: Optional[int], pwm: Optional[float], active: bool = True) -> None:
        self.fan.set_rpm(rpm if active else 0)
        self.rpm_label.set_text(f"{rpm:,}" if rpm is not None else "--")
        self.pwm_label.set_text(f"PWM {pwm:.0f}%" if pwm is not None else "PWM --")
        self.bar.set_value(pwm, _palette(self).accent if active else _palette(self).border)
        self.status.set_text("运行" if active and (rpm or 0) > 0 else "待机", "neutral")


# ----------------------------------------------------------------------
# chart
# ----------------------------------------------------------------------
class Chart(Gtk.DrawingArea):
    def __init__(
        self,
        height: int = 190,
        window: int = 90,
        kind: str = "temp",
        unit: str = "C",
    ) -> None:
        super().__init__()
        self.set_size_request(-1, height)
        self.window = window
        self.kind = kind
        self.unit = unit
        self.series: list[dict] = []
        self.y_max = 100.0
        self.connect("draw", self._draw)

    def set_series(self, series: list[dict]) -> None:
        self.series = series
        if series:
            values = [value for item in series for value in item.get("values", []) if value is not None]
            if self.kind == "temp":
                self.y_max = 100.0
            elif values:
                self.y_max = max(1000.0, max(values) * 1.15)
        self.queue_draw()

    def set_unit(self, unit: str) -> None:
        self.unit = unit
        self.queue_draw()

    def _display(self, value: float) -> float:
        if self.kind == "temp" and self.unit == "F":
            return value * 9.0 / 5.0 + 32.0
        return value

    def _draw(self, widget, ctx) -> bool:
        palette = _palette(widget)
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        left, right, top, bottom = 40.0, 12.0, 10.0, 22.0
        plot_w = max(1.0, width - left - right)
        plot_h = max(1.0, height - top - bottom)
        max_value = self.y_max if self.kind != "temp" else (212.0 if self.unit == "F" else 100.0)

        # Grid + y labels.
        for index in range(5):
            fraction = index / 4.0
            y = top + plot_h * (1.0 - fraction)
            ctx.set_line_width(1)
            set_rgba(ctx, palette.chart_grid, 0.55 if index not in (0, 4) else 0.8)
            ctx.move_to(left, y)
            ctx.line_to(left + plot_w, y)
            ctx.stroke()
            label_value = max_value * fraction
            if self.kind == "temp":
                label = f"{label_value:.0f}°{self.unit}"
            elif self.kind == "fan":
                label = f"{label_value:.0f}"
            else:
                label = f"{label_value:.0f}"
            draw_text(
                widget,
                ctx,
                label,
                left - 7,
                y,
                size=7.5,
                color=palette.muted,
                anchor="east",
            )

        # X labels.  Until the history is full, the line grows from the left;
        # showing "-90s" at the left would be misleading.
        count = max((len(item.get("values", [])) for item in self.series), default=0)
        if count < self.window:
            labels = ((0.0, "开始"), (1.0, "现在"))
        else:
            labels = (
                (0.0, f"-{self.window}秒"),
                (1 / 3, f"-{round(self.window * 2 / 3)}秒"),
                (2 / 3, f"-{round(self.window / 3)}秒"),
                (1.0, "现在"),
            )
        for fraction, text in labels:
            x = left + plot_w * fraction
            draw_text(
                widget,
                ctx,
                text,
                x,
                height - 8,
                size=7.0,
                color=palette.muted,
                anchor="center",
            )

        if not self.series or not any(item.get("values") for item in self.series):
            draw_text(
                widget,
                ctx,
                "正在采集数据…",
                left + plot_w / 2,
                top + plot_h / 2,
                size=9.0,
                color=palette.muted,
            )
            return False

        # Draw fills first.
        for series_index, item in enumerate(self.series):
            values = item.get("values", [])
            if len(values) < 2:
                continue
            points = self._points(values, left, top, plot_w, plot_h, max_value)
            if series_index == 0:
                ctx.move_to(points[0][0], top + plot_h)
                for x, y in points:
                    ctx.line_to(x, y)
                ctx.line_to(points[-1][0], top + plot_h)
                ctx.close_path()
                set_rgba(ctx, item.get("color", palette.accent), 0.075)
                ctx.fill()

        # Lines.
        for item in self.series:
            values = item.get("values", [])
            if len(values) < 2:
                continue
            points = self._points(values, left, top, plot_w, plot_h, max_value)
            ctx.set_line_width(2.0)
            ctx.set_line_join(1)
            set_rgba(ctx, item.get("color", palette.accent), 0.95)
            ctx.move_to(points[0][0], points[0][1])
            for x, y in points[1:]:
                ctx.line_to(x, y)
            ctx.stroke()
            # End point.
            x, y = points[-1]
            ctx.arc(x, y, 3.2, 0, 2 * math.pi)
            set_rgba(ctx, item.get("color", palette.accent), 1.0)
            ctx.fill()
            ctx.arc(x, y, 6.0, 0, 2 * math.pi)
            set_rgba(ctx, item.get("color", palette.accent), 0.18)
            ctx.fill()
        return False

    def _points(
        self,
        values: list[float],
        left: float,
        top: float,
        plot_w: float,
        plot_h: float,
        max_value: float,
    ) -> list[tuple[float, float]]:
        values = values[-self.window :]
        count = len(values)
        denominator = count - 1 if count < self.window else self.window - 1
        points: list[tuple[float, float]] = []
        for index, value in enumerate(values):
            displayed = self._display(value)
            x = left + plot_w * index / max(1, denominator)
            y = top + plot_h * (1.0 - clamp(displayed / max_value, 0.0, 1.0))
            points.append((x, y))
        return points


# ----------------------------------------------------------------------
# battery / keyboard illustration
# ----------------------------------------------------------------------
class BatteryIcon(Gtk.DrawingArea):
    def __init__(self, width: int = 150, height: int = 66) -> None:
        super().__init__()
        self.set_size_request(width, height)
        self.percent: Optional[float] = None
        self.charging = False
        self.connect("draw", self._draw)

    def set_value(self, percent: Optional[float], charging: bool = False) -> None:
        self.percent = percent
        self.charging = charging
        self.queue_draw()

    def _draw(self, widget, ctx) -> bool:
        palette = _palette(widget)
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        body_w = width - 14
        body_h = height - 4
        rounded_rect(ctx, 2, 2, body_w, body_h, 10)
        set_rgba(ctx, palette.border, 1.0)
        ctx.set_line_width(2)
        ctx.stroke()
        # Terminal.
        rounded_rect(ctx, body_w + 3, height / 2 - 8, 7, 16, 2)
        set_rgba(ctx, palette.muted, 0.8)
        ctx.fill()
        if self.percent is not None:
            inner_w = max(0.0, (body_w - 10) * clamp(self.percent, 0, 100) / 100.0)
            if inner_w > 0:
                rounded_rect(ctx, 7, 7, inner_w, body_h - 10, 6)
                color = _theme(widget).battery_color(self.percent)
                set_rgba(ctx, color, 0.95)
                ctx.fill()
        if self.charging:
            cx, cy = body_w / 2 + 2, height / 2
            ctx.move_to(cx + 3, cy - 13)
            ctx.line_to(cx - 8, cy + 2)
            ctx.line_to(cx - 1, cy + 2)
            ctx.line_to(cx - 4, cy + 14)
            ctx.line_to(cx + 8, cy - 3)
            ctx.line_to(cx + 1, cy - 3)
            ctx.close_path()
            set_rgba(ctx, palette.text, 0.9)
            ctx.fill()
        return False


class KeyboardPreview(Gtk.DrawingArea):
    def __init__(self, height: int = 230) -> None:
        super().__init__()
        self.set_size_request(420, height)
        self.target = "keyboard"
        self.rgb = (77, 194, 255)
        self.brightness = 0.8
        self.connect("draw", self._draw)

    def set_state(self, target: str, rgb: tuple[int, int, int], brightness: float) -> None:
        self.target = target
        self.rgb = rgb
        self.brightness = clamp(brightness, 0.0, 1.0)
        self.queue_draw()

    def _draw(self, widget, ctx) -> bool:
        palette = _palette(widget)
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        margin = 18.0
        deck_x = margin
        deck_y = 30.0
        deck_w = width - 2 * margin
        deck_h = height - 75.0
        r, g, b = self.rgb
        glow = self.brightness
        keyboard_active = self.target == "keyboard"
        lightbar_active = self.target == "lightbar"

        # Soft ambient glow.
        for index, alpha in enumerate((0.06, 0.04, 0.025)):
            expand = 8 + index * 10
            rounded_rect(
                ctx,
                deck_x - expand,
                deck_y - expand,
                deck_w + 2 * expand,
                deck_h + 2 * expand,
                18 + expand,
            )
            ctx.set_source_rgba(r / 255, g / 255, b / 255, alpha * glow * (1 if keyboard_active else 0.35))
            ctx.fill()

        rounded_rect(ctx, deck_x, deck_y, deck_w, deck_h, 16)
        set_rgba(ctx, palette.card2, 1.0)
        ctx.fill_preserve()
        set_rgba(ctx, palette.border, 1.0)
        ctx.set_line_width(1.2)
        ctx.stroke()

        draw_text(
            widget,
            ctx,
            "RGB 键盘",
            deck_x + 4,
            deck_y - 11,
            size=7.5,
            color=palette.muted,
            anchor="west",
        )
        draw_text(
            widget,
            ctx,
            "前部灯带",
            deck_x + 4,
            deck_y + deck_h + 27,
            size=7.5,
            color=palette.muted,
            anchor="west",
        )

        # Key rows: (keys, key width weight).
        rows = [
            [1.0] * 14,
            [1.0] * 14,
            [1.4, 1.0] * 6 + [1.4, 1.4],
            [1.8, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.8],
            [2.2, 1.3, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 1.3, 2.2],
        ]
        row_gap = 5.0
        key_gap = 5.0
        available_w = deck_w - 32.0
        available_h = deck_h - 34.0
        row_h = (available_h - row_gap * (len(rows) - 1)) / len(rows)
        for row_index, weights in enumerate(rows):
            total = sum(weights)
            natural = (available_w - key_gap * (len(weights) - 1)) / total
            x = deck_x + 16.0
            y = deck_y + 12.0 + row_index * (row_h + row_gap)
            for weight in weights:
                key_w = natural * weight
                rounded_rect(ctx, x, y, key_w, row_h, 4.0)
                if keyboard_active:
                    ctx.set_source_rgba(
                        r / 255,
                        g / 255,
                        b / 255,
                        0.10 + 0.55 * glow,
                    )
                else:
                    set_rgba(ctx, palette.bg, 0.7)
                ctx.fill_preserve()
                set_rgba(ctx, palette.border, 0.75)
                ctx.set_line_width(0.8)
                ctx.stroke()
                x += key_w + key_gap

        # Front light bar.
        bar_w = deck_w * 0.72
        bar_x = deck_x + (deck_w - bar_w) / 2
        bar_y = deck_y + deck_h + 12
        if lightbar_active:
            for index, alpha in enumerate((0.10, 0.06, 0.035)):
                rounded_rect(
                    ctx,
                    bar_x - index * 6,
                    bar_y - index * 3,
                    bar_w + index * 12,
                    8 + index * 6,
                    6 + index * 3,
                )
                ctx.set_source_rgba(r / 255, g / 255, b / 255, alpha * glow)
                ctx.fill()
            set_rgba(ctx, f"#{r:02x}{g:02x}{b:02x}", 0.35 + 0.65 * glow)
        else:
            set_rgba(ctx, palette.border, 0.9)
        rounded_rect(ctx, bar_x, bar_y, bar_w, 8, 4)
        ctx.fill()
        return False


# ----------------------------------------------------------------------
# colour swatches / segmented controls
# ----------------------------------------------------------------------
class ColorDot(Gtk.DrawingArea):
    def __init__(self, color: str, size: int = 8) -> None:
        super().__init__()
        self.color = color
        self.set_size_request(size, size)
        self.connect("draw", self._draw)

    def set_color(self, color: str) -> None:
        self.color = color
        self.queue_draw()

    def _draw(self, widget, ctx) -> bool:
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        ctx.arc(width / 2, height / 2, min(width, height) / 2, 0, 2 * math.pi)
        set_rgba(ctx, self.color, 1.0)
        ctx.fill()
        return False


class ColorSwatch(Gtk.DrawingArea):
    def __init__(self, color: str, size: int = 28, callback: Optional[Callable[[str], None]] = None) -> None:
        super().__init__()
        self.color = color
        self.size = size
        self.selected = False
        self.callback = callback
        self.set_size_request(size, size)
        self.set_tooltip_text(color)
        self.add_events(Gdk.EventMask.BUTTON_PRESS_MASK)
        self.connect("draw", self._draw)
        self.connect("button-press-event", self._clicked)

    def set_selected(self, selected: bool) -> None:
        self.selected = selected
        self.queue_draw()

    def _clicked(self, widget, event) -> bool:
        if self.callback:
            self.callback(self.color)
        return True

    def _draw(self, widget, ctx) -> bool:
        palette = _palette(widget)
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        cx, cy = width / 2, height / 2
        radius = min(width, height) / 2 - 2
        if self.selected:
            ctx.arc(cx, cy, radius, 0, 2 * math.pi)
            set_rgba(ctx, palette.text, 0.95)
            ctx.set_line_width(2)
            ctx.stroke()
            radius -= 4
        ctx.arc(cx, cy, radius, 0, 2 * math.pi)
        set_rgba(ctx, self.color, 1.0)
        ctx.fill_preserve()
        set_rgba(ctx, palette.bg, 0.6)
        ctx.set_line_width(1)
        ctx.stroke()
        return False


class ColorPickerButton(Gtk.Button):
    """A compact custom-colour button with a proper GTK colour dialog."""

    def __init__(
        self,
        color: tuple[int, int, int],
        callback: Callable[[tuple[int, int, int]], None],
        title: str = "选择自定义颜色",
    ) -> None:
        super().__init__()
        self.color = color
        self.callback = callback
        self.dialog_title = title
        self.set_relief(Gtk.ReliefStyle.NONE)
        self.get_style_context().add_class("secondary-button")
        self.set_halign(Gtk.Align.START)
        child = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.dot = ColorDot("#%02x%02x%02x" % color, 16)
        child.pack_start(self.dot, False, False, 0)
        child.pack_start(make_label("自定义颜色"), False, False, 0)
        self.add(child)
        self.connect("clicked", self._clicked)

    def set_color(self, color: tuple[int, int, int]) -> None:
        self.color = color
        self.dot.set_color("#%02x%02x%02x" % color)

    def _clicked(self, _button: Gtk.Button) -> None:
        dialog = Gtk.ColorChooserDialog(
            title=self.dialog_title,
            transient_for=self.get_toplevel(),
            use_alpha=False,
        )
        rgba = Gdk.RGBA()
        rgba.red = self.color[0] / 255.0
        rgba.green = self.color[1] / 255.0
        rgba.blue = self.color[2] / 255.0
        rgba.alpha = 1.0
        dialog.set_rgba(rgba)
        response = dialog.run()
        if response == Gtk.ResponseType.OK:
            selected = dialog.get_rgba()
            color = (
                int(selected.red * 255),
                int(selected.green * 255),
                int(selected.blue * 255),
            )
            self.set_color(color)
            self.callback(color)
        dialog.destroy()


class SegmentedControl(Gtk.Box):
    def __init__(self, options: list[tuple[str, str]]) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.buttons: dict[str, Gtk.ToggleButton] = {}
        self.active_id: Optional[str] = None
        self.callback: Optional[Callable[[str], None]] = None
        self._updating = False
        for option_id, label in options:
            button = Gtk.ToggleButton(label=label)
            button.get_style_context().add_class("segment")
            button.set_relief(Gtk.ReliefStyle.NONE)
            button.connect("toggled", self._toggled, option_id)
            self.pack_start(button, False, False, 0)
            self.buttons[option_id] = button

    def connect_changed(self, callback: Callable[[str], None]) -> None:
        self.callback = callback

    def _toggled(self, button: Gtk.ToggleButton, option_id: str) -> None:
        if self._updating or not button.get_active():
            return
        self.active_id = option_id
        self._updating = True
        for other_id, other in self.buttons.items():
            if other_id != option_id:
                other.set_active(False)
        self._updating = False
        if self.callback:
            self.callback(option_id)

    def set_active(self, option_id: str, emit: bool = False) -> None:
        if option_id not in self.buttons:
            return
        self._updating = True
        for other_id, button in self.buttons.items():
            button.set_active(other_id == option_id)
        self._updating = False
        self.active_id = option_id
        if emit and self.callback:
            self.callback(option_id)




class FanCurveChart(Gtk.DrawingArea):
    """A small temperature-to-fan-speed curve preview."""

    def __init__(self, height: int = 170) -> None:
        super().__init__()
        self.set_size_request(-1, height)
        self.points: list[tuple[int, int]] = []
        self.current_temp: Optional[float] = None
        self.current_percent: Optional[float] = None
        self.connect("draw", self._draw)

    def set_points(self, points) -> None:
        from .fan import normalize_points

        self.points = [(point.temp, point.percent) for point in normalize_points(points)]
        self.queue_draw()

    def set_current(self, temperature: Optional[float], percent: Optional[float]) -> None:
        self.current_temp = temperature
        self.current_percent = percent
        self.queue_draw()

    def _draw(self, widget, ctx) -> bool:
        from .fan import MAX_TEMP, MIN_TEMP

        palette = _palette(widget)
        width = widget.get_allocated_width()
        height = widget.get_allocated_height()
        left, right, top, bottom = 42.0, 14.0, 14.0, 24.0
        plot_w = max(1.0, width - left - right)
        plot_h = max(1.0, height - top - bottom)

        def px(temp: float) -> float:
            return left + (temp - MIN_TEMP) / (MAX_TEMP - MIN_TEMP) * plot_w

        def py(percent: float) -> float:
            return top + (1.0 - percent / 100.0) * plot_h

        # Grid.
        for temp in (30, 45, 60, 75, 90):
            x = px(temp)
            ctx.set_line_width(1)
            set_rgba(ctx, palette.chart_grid, 0.45)
            ctx.move_to(x, top)
            ctx.line_to(x, top + plot_h)
            ctx.stroke()
            draw_text(widget, ctx, f"{temp}°", x, height - 8, 7.0, palette.muted)
        for percent in (0, 25, 50, 75, 100):
            y = py(percent)
            ctx.set_line_width(1)
            set_rgba(ctx, palette.chart_grid, 0.7 if percent in (0, 100) else 0.4)
            ctx.move_to(left, y)
            ctx.line_to(left + plot_w, y)
            ctx.stroke()
            draw_text(
                widget, ctx, f"{percent}%", left - 7, y, 7.0, palette.muted, anchor="east"
            )

        if not self.points:
            draw_text(
                widget, ctx, "暂无曲线", left + plot_w / 2, top + plot_h / 2, 9.0, palette.muted
            )
            return False

        curve_points = [(px(temp), py(percent)) for temp, percent in self.points]
        # Area.
        ctx.move_to(curve_points[0][0], top + plot_h)
        for x, y in curve_points:
            ctx.line_to(x, y)
        ctx.line_to(curve_points[-1][0], top + plot_h)
        ctx.close_path()
        set_rgba(ctx, palette.accent, 0.10)
        ctx.fill()
        # Line.
        ctx.set_line_width(2.2)
        set_rgba(ctx, palette.accent, 0.95)
        ctx.move_to(curve_points[0][0], curve_points[0][1])
        for x, y in curve_points[1:]:
            ctx.line_to(x, y)
        ctx.stroke()
        # Points.
        for x, y in curve_points:
            ctx.arc(x, y, 4.0, 0, 2 * math.pi)
            set_rgba(ctx, palette.card, 1.0)
            ctx.fill_preserve()
            set_rgba(ctx, palette.accent, 1.0)
            ctx.set_line_width(2)
            ctx.stroke()
        # Current temperature marker.
        if self.current_temp is not None:
            x = px(max(MIN_TEMP, min(MAX_TEMP, self.current_temp)))
            ctx.set_line_width(1.4)
            ctx.set_dash([4, 4])
            set_rgba(ctx, palette.warning, 0.85)
            ctx.move_to(x, top)
            ctx.line_to(x, top + plot_h)
            ctx.stroke()
            ctx.set_dash([])
            if self.current_percent is not None:
                y = py(max(0.0, min(100.0, self.current_percent)))
                ctx.arc(x, y, 5.5, 0, 2 * math.pi)
                set_rgba(ctx, palette.warning, 0.95)
                ctx.fill_preserve()
                set_rgba(ctx, palette.card, 1.0)
                ctx.set_line_width(2)
                ctx.stroke()
        return False


# ----------------------------------------------------------------------
# toast
# ----------------------------------------------------------------------
class Toast:
    def __init__(self, overlay: Gtk.Overlay) -> None:
        self.timeout: Optional[int] = None
        self.revealer = Gtk.Revealer()
        self.revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_UP)
        self.revealer.set_transition_duration(180)
        self.revealer.set_halign(Gtk.Align.CENTER)
        self.revealer.set_valign(Gtk.Align.END)
        self.revealer.set_margin_bottom(22)
        self.label = make_label("", None, xalign=0.5)
        self.label.set_line_wrap(True)
        self.label.set_max_width_chars(70)
        self.box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self.box.get_style_context().add_class("toast")
        self.box.pack_start(self.label, True, True, 0)
        self.revealer.add(self.box)
        overlay.add_overlay(self.revealer)

    def show(self, message: str, kind: str = "info") -> None:
        if self.timeout is not None:
            GLib.source_remove(self.timeout)
            self.timeout = None
        context = self.box.get_style_context()
        for cls in ("error", "success"):
            context.remove_class(cls)
        if kind == "error":
            context.add_class("error")
        elif kind == "success":
            context.add_class("success")
        self.label.set_text(message)
        self.revealer.set_reveal_child(True)
        self.timeout = GLib.timeout_add(3600, self._hide)

    def _hide(self) -> bool:
        self.revealer.set_reveal_child(False)
        self.timeout = None
        return False


# ----------------------------------------------------------------------
# lookup helpers
# ----------------------------------------------------------------------
def _palette(widget: Gtk.Widget):
    return _theme(widget).palette


def _theme(widget: Gtk.Widget) -> ThemeManager:
    root = widget.get_toplevel()
    theme = getattr(root, "theme", None)
    if theme is not None:
        return theme
    # Fallback for widgets created before being attached.
    from .theme import DARK

    class _Fallback:
        palette = DARK

        @staticmethod
        def battery_color(percent):
            if percent is None:
                return DARK.muted
            if percent <= 15:
                return DARK.danger
            if percent <= 35:
                return DARK.warning
            return DARK.success

    return _Fallback()  # type: ignore[return-value]
