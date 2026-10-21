"""Colour palettes and the GTK stylesheet."""

from __future__ import annotations

from dataclasses import dataclass
from string import Template
from typing import Optional

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from .util import hex_to_rgb  # noqa: E402


@dataclass(frozen=True)
class Palette:
    name: str
    bg: str
    bg2: str
    card: str
    card2: str
    border: str
    text: str
    text2: str
    muted: str
    accent: str
    accent2: str
    on_accent: str
    success: str
    warning: str
    danger: str
    purple: str
    shadow: str
    chart_grid: str


DARK = Palette(
    name="dark",
    bg="#0e1014",
    bg2="#13161c",
    card="#191c23",
    card2="#1f242d",
    border="#2a303b",
    text="#f4f6f9",
    text2="#aab2c0",
    muted="#737c8b",
    accent="#4da3ff",
    accent2="#82c6ff",
    on_accent="#06121f",
    success="#4ade80",
    warning="#fbbf24",
    danger="#f87171",
    purple="#b18cff",
    shadow="0 10px 30px rgba(0, 0, 0, 0.30)",
    chart_grid="#2a303b",
)

LIGHT = Palette(
    name="light",
    bg="#f3f6fa",
    bg2="#e9eef5",
    card="#ffffff",
    card2="#f7f9fc",
    border="#dbe2ec",
    text="#111827",
    text2="#4b5563",
    muted="#7b8494",
    accent="#0067c0",
    accent2="#0057a3",
    on_accent="#ffffff",
    success="#15803d",
    warning="#b45309",
    danger="#b91c1c",
    purple="#6d28d9",
    shadow="0 8px 24px rgba(25, 40, 65, 0.08)",
    chart_grid="#dbe2ec",
)

PALETTES = {"dark": DARK, "light": LIGHT}

CSS_TEMPLATE = """
* {
    font-family: "Noto Sans CJK SC", "Noto Sans", "Segoe UI", "Cantarell", sans-serif;
}

window {
    background-color: $bg;
    color: $text;
}

.page-scroll,
.page-scroll viewport,
.page-viewport {
    background-color: transparent;
    background-image: none;
}

headerbar {
    background-color: $bg2;
    background-image: none;
    border: none;
    border-bottom: 1px solid $border;
    box-shadow: none;
    min-height: 48px;
    padding: 0 10px;
}

headerbar button {
    background: transparent;
    border: none;
    box-shadow: none;
    color: $text2;
    min-height: 30px;
    min-width: 30px;
    border-radius: 8px;
}

headerbar button:hover {
    background-color: alpha($text, 0.08);
    color: $text;
}

.sidebar {
    background-color: $bg2;
    border-right: 1px solid $border;
    padding: 18px 12px 14px 12px;
}

.brand-title {
    font-size: 14pt;
    font-weight: 700;
    color: $text;
}

.brand-sub {
    font-size: 8.5pt;
    color: $muted;
}

.brand-mark {
    min-width: 38px;
    min-height: 38px;
}

.nav-button {
    background: transparent;
    background-image: none;
    border: none;
    box-shadow: none;
    border-radius: 10px;
    padding: 9px 12px;
    color: $text2;
    min-height: 36px;
}

.nav-button:hover {
    background-color: alpha($text, 0.06);
    color: $text;
}

.nav-button:checked {
    background-color: alpha($accent, 0.15);
    color: $accent2;
}

.nav-button:checked label {
    color: $accent2;
    font-weight: 600;
}

.nav-button label {
    font-size: 10pt;
    color: inherit;
}

.nav-separator {
    background-color: $border;
    min-height: 1px;
    margin: 8px 6px;
}

.sidebar-footer {
    color: $muted;
    font-size: 8.5pt;
}

.page-title {
    font-size: 23pt;
    font-weight: 700;
    color: $text;
}

.page-subtitle {
    font-size: 9.5pt;
    color: $muted;
}

.section-title {
    font-size: 13.5pt;
    font-weight: 700;
    color: $text;
}

.section-subtitle {
    font-size: 9pt;
    color: $muted;
}

.card {
    background-color: $card;
    border: 1px solid $border;
    border-radius: 14px;
    padding: 16px;
}

.card-flat {
    background-color: $card;
    border: 1px solid $border;
    border-radius: 12px;
    padding: 14px;
}

.hero {
    background-color: $card;
    background-image: linear-gradient(125deg, alpha($accent, 0.17), alpha($purple, 0.07) 55%, alpha($card, 0.0));
    border: 1px solid alpha($accent, 0.28);
    border-radius: 16px;
    padding: 20px;
}

.hero-title {
    font-size: 19pt;
    font-weight: 700;
    color: $text;
}

.legend-dot {
    min-width: 8px;
    min-height: 8px;
    border-radius: 50%;
}

.legend-label {
    font-size: 8.5pt;
    color: $text2;
}

.info-key {
    font-size: 9pt;
    color: $muted;
}

.info-value {
    font-size: 9.5pt;
    font-weight: 500;
    color: $text;
}

.mode-sub {
    font-size: 8pt;
    color: $muted;
}

.metric-card {
    background-color: $card;
    border: 1px solid $border;
    border-radius: 14px;
    padding: 14px 16px;
}

.card-label {
    font-size: 8.5pt;
    font-weight: 600;
    color: $muted;
    letter-spacing: 0.3px;
}

.big-value {
    font-size: 25pt;
    font-weight: 700;
    color: $text;
}

.medium-value {
    font-size: 16pt;
    font-weight: 700;
    color: $text;
}

.unit {
    font-size: 9.5pt;
    font-weight: 500;
    color: $muted;
}

.subtext {
    font-size: 8.5pt;
    color: $muted;
}

.text-secondary {
    color: $text2;
}

.dim {
    color: $muted;
}

.mono {
    font-family: "Noto Sans Mono", monospace;
}

.pill {
    background-color: alpha($success, 0.13);
    color: $success;
    border: 1px solid alpha($success, 0.25);
    border-radius: 999px;
    padding: 2px 9px;
    font-size: 8.5pt;
    font-weight: 600;
}

.pill.warn {
    background-color: alpha($warning, 0.13);
    color: $warning;
    border-color: alpha($warning, 0.25);
}

.pill.err {
    background-color: alpha($danger, 0.13);
    color: $danger;
    border-color: alpha($danger, 0.25);
}

.pill.neutral {
    background-color: alpha($text, 0.06);
    color: $text2;
    border-color: $border;
}

.dot {
    min-width: 8px;
    min-height: 8px;
    border-radius: 50%;
    background-color: $success;
}

.dot.warn {
    background-color: $warning;
}

.dot.err {
    background-color: $danger;
}

.icon-badge {
    background-color: alpha($accent, 0.13);
    border-radius: 10px;
    min-width: 32px;
    min-height: 32px;
    color: $accent;
}

.icon-badge.green {
    background-color: alpha($success, 0.13);
    color: $success;
}

.icon-badge.purple {
    background-color: alpha($purple, 0.14);
    color: $purple;
}

.icon-badge.amber {
    background-color: alpha($warning, 0.14);
    color: $warning;
}

.icon-badge.red {
    background-color: alpha($danger, 0.14);
    color: $danger;
}

.mode-card {
    background-color: $card2;
    border: 1px solid $border;
    border-radius: 12px;
    padding: 13px;
    color: $text;
}

.mode-card:hover {
    border-color: alpha($accent, 0.55);
    background-color: alpha($accent, 0.06);
}

.mode-card:checked {
    background-color: alpha($accent, 0.13);
    border-color: $accent;
}

.mode-card:disabled {
    opacity: 0.55;
}

.mode-card:disabled .mode-title {
    color: $muted;
}

.mode-card:disabled .mode-sub {
    color: $muted;
}

.mode-title {
    font-size: 11pt;
    font-weight: 600;
    color: $text;
}

.mode-card:checked .mode-title {
    color: $accent2;
}

.segment {
    background-color: $card2;
    border: 1px solid $border;
    border-radius: 9px;
    padding: 6px 12px;
    color: $text2;
    font-size: 9pt;
    font-weight: 500;
    min-height: 27px;
}

.segment:hover {
    background-color: alpha($text, 0.06);
    color: $text;
}

.segment:checked {
    background-color: alpha($accent, 0.16);
    border-color: alpha($accent, 0.6);
    color: $accent2;
    font-weight: 600;
}

button.primary-button {
    background-color: $accent;
    background-image: none;
    color: $on_accent;
    border: 1px solid $accent;
    border-radius: 9px;
    padding: 7px 14px;
    font-weight: 600;
    min-height: 28px;
}

button.primary-button:hover {
    background-color: $accent2;
}

button.secondary-button {
    background-color: alpha($text, 0.055);
    background-image: none;
    color: $text;
    border: 1px solid $border;
    border-radius: 9px;
    padding: 6px 12px;
    min-height: 28px;
}

button.secondary-button:hover {
    background-color: alpha($text, 0.10);
}

button.danger-button {
    background-color: alpha($danger, 0.12);
    background-image: none;
    color: $danger;
    border: 1px solid alpha($danger, 0.3);
    border-radius: 9px;
    padding: 6px 12px;
    min-height: 28px;
}

button.flat-button {
    background: transparent;
    border: none;
    box-shadow: none;
    color: $text2;
    padding: 4px 8px;
    border-radius: 7px;
}

button.flat-button:hover {
    background-color: alpha($text, 0.07);
    color: $text;
}

scale {
    min-height: 30px;
}

scale trough {
    background-color: $border;
    border-radius: 4px;
    min-height: 6px;
    min-width: 60px;
}

scale highlight {
    background-color: $accent;
    border-radius: 4px;
    min-height: 6px;
}

scale slider {
    background-color: #ffffff;
    border: 1px solid alpha(#000000, 0.16);
    border-radius: 50%;
    min-width: 17px;
    min-height: 17px;
    margin: -6px;
    box-shadow: 0 2px 6px rgba(0, 0, 0, 0.35);
}

scale value {
    color: $text2;
    font-size: 8.5pt;
}

switch {
    background-color: $border;
    border-radius: 999px;
    min-width: 42px;
    min-height: 22px;
}

switch:checked {
    background-color: $accent;
}

switch:disabled,
scale:disabled,
button:disabled {
    opacity: 0.45;
}

scale:disabled trough {
    background-color: alpha($border, 0.55);
}

scale:disabled trough highlight {
    background-color: $muted;
}

scale:disabled slider {
    background-color: $muted;
    border-color: $border;
}

switch:disabled {
    background-color: alpha($border, 0.55);
}

switch:disabled slider {
    background-color: $muted;
}

switch slider {
    background-color: #ffffff;
    border-radius: 50%;
    min-width: 18px;
    min-height: 18px;
    margin: 2px;
    box-shadow: 0 1px 3px rgba(0, 0, 0, 0.35);
}

check {
    background-color: $card2;
    border: 1px solid $border;
    border-radius: 5px;
    min-width: 16px;
    min-height: 16px;
}

check:checked {
    background-color: $accent;
    border-color: $accent;
    color: $on_accent;
}

combobox button {
    background-color: $card2;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 4px 10px;
    color: $text;
    min-height: 26px;
}

separator {
    background-color: $border;
    min-width: 1px;
    min-height: 1px;
}

scrollbar {
    background: transparent;
}

scrollbar slider {
    background-color: alpha($text, 0.14);
    border-radius: 5px;
    min-width: 6px;
    min-height: 24px;
}

scrollbar slider:hover {
    background-color: alpha($text, 0.24);
}

.toast {
    background-color: $card2;
    border: 1px solid $border;
    border-radius: 11px;
    padding: 10px 16px;
    color: $text;
    box-shadow: $shadow;
}

.toast.error {
    border-color: alpha($danger, 0.55);
    background-color: alpha($danger, 0.12);
    color: $danger;
}

.toast.success {
    border-color: alpha($success, 0.45);
    background-color: alpha($success, 0.10);
    color: $success;
}

.tooltip {
    background-color: $card2;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 4px 8px;
}

.link {
    color: $accent2;
}

.chip {
    background-color: alpha($text, 0.055);
    border: 1px solid $border;
    border-radius: 6px;
    padding: 2px 8px;
    font-size: 8pt;
    color: $text2;
}

.history-label {
    font-size: 8pt;
    color: $muted;
}
"""


class ThemeManager:
    def __init__(self, app: Optional[Gtk.Application] = None) -> None:
        self.app = app
        self.provider: Optional[Gtk.CssProvider] = None
        self.palette: Palette = DARK
        self.name = "dark"

    def apply(self, name: str) -> Palette:
        self.name = name if name in PALETTES else "dark"
        self.palette = PALETTES[self.name]
        css = Template(CSS_TEMPLATE).substitute(self.palette.__dict__)
        if self.provider is not None:
            screen = Gdk.Screen.get_default()
            if screen is not None:
                Gtk.StyleContext.remove_provider_for_screen(screen, self.provider)
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode("utf-8"))
        screen = Gdk.Screen.get_default()
        if screen is not None:
            Gtk.StyleContext.add_provider_for_screen(
                screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )
        self.provider = provider
        settings = Gtk.Settings.get_default()
        if settings is not None:
            settings.set_property("gtk-application-prefer-dark-theme", self.name == "dark")
        return self.palette

    # ------------------------------------------------------------------
    # drawing helpers
    # ------------------------------------------------------------------
    def rgba(self, color: str, alpha: float = 1.0) -> tuple[float, float, float, float]:
        r, g, b = hex_to_rgb(color)
        return (r / 255.0, g / 255.0, b / 255.0, alpha)

    def temperature_color(self, celsius: Optional[float]) -> str:
        if celsius is None:
            return self.palette.muted
        if celsius >= 90:
            return self.palette.danger
        if celsius >= 80:
            return "#fb923c"
        if celsius >= 70:
            return self.palette.warning
        if celsius >= 55:
            return self.palette.accent
        return self.palette.success

    def load_color(self, percent: Optional[float]) -> str:
        if percent is None:
            return self.palette.muted
        if percent >= 85:
            return self.palette.danger
        if percent >= 65:
            return self.palette.warning
        return self.palette.accent

    def battery_color(self, percent: Optional[float]) -> str:
        if percent is None:
            return self.palette.muted
        if percent <= 15:
            return self.palette.danger
        if percent <= 35:
            return self.palette.warning
        return self.palette.success
