"""Draws the dashboard in light and dark themes.

Usage: python -m dashboard.render
Reads DATA_DIR (default "data") and writes OUT_DIR (default "assets").
"""

import os
import sys

import matplotlib

matplotlib.use("Agg")
# pylint: disable=wrong-import-position
from matplotlib import colors as mcolors
from matplotlib import dates as mdates
from matplotlib import font_manager
from matplotlib import ft2font
from matplotlib import lines as mlines
from matplotlib import patches as mpatches
from matplotlib import path as mpath
from matplotlib import pyplot as plt
from matplotlib import ticker
import numpy as np
from PIL import Image

from dashboard import data as dashboard_data
# pylint: enable=wrong-import-position

CITY = "guangzhou"
SITES = (("motianlun", "摩天轮"), ("piaoniu", "票牛"))

# Every piece of text drawn on the dashboard.
TEXT = {
    "title": "孙燕姿《就在日落以后》广州站 · 二手票价",
    "span": "{start} 至 {end} · 每 {minutes} 分钟取中位数",
    "stat": "{session} 场 · 当前最低价",
    "first": "首日 {price}",
    "lowest": "最低价 · {session} 场",
    "tiers": "{session} 场各票档最低价 · {site}",
    "listings": "在售挂单数 · {site}",
    "face": "票面 {face}",
    "session": "{session} 场",
    "min": "最低",
    "max": "最高",
    "last": "最新",
}

# Palette steps checked for colour-vision separation and contrast against
# each panel surface.
THEMES = {
    "dark": {
        "canvas": "#111217", "panel": "#181b1f", "border": "#2c3035",
        "ink": "#ccccdc", "ink2": "#9fa1ae", "muted": "#7b7d8a",
        "grid": "#24272c", "up": "#d03b3b", "down": "#0ca30c",
        "series": ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181"],
    },
    "light": {
        "canvas": "#f4f5f5", "panel": "#ffffff", "border": "#e1e4e6",
        "ink": "#24292e", "ink2": "#52575f", "muted": "#7d828a",
        "grid": "#eceef0", "up": "#d03b3b", "down": "#006300",
        "series": ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4"],
    },
}

# Layout in inches.
_WIDTH = 16.0
_MARGIN = 0.22
_GAP = 0.16
_STAT_H = 1.45
_PLOT_H = 2.25
_ROW_H = 0.23
_DPI = 110
_GAP_S = 3600  # A pause longer than this breaks a line.


def font_family() -> str:
    """Registers and returns a font family that can draw Chinese text.

    DASHBOARD_FONT may name a font file; otherwise an installed font that
    covers every character in TEXT and SITES is used, preferring sans-serif
    regular faces.

    Returns:
        The font family name.

    Raises:
        RuntimeError: No installed font can draw Chinese text.
    """
    path = os.environ.get("DASHBOARD_FONT")
    if not path:
        words = list(TEXT.values()) + [label for _, label in SITES]
        needed = {ord(c) for c in "".join(words) if not c.isascii()}
        found = []
        for candidate in font_manager.findSystemFonts():
            try:
                face = ft2font.FT2Font(candidate)
            except (OSError, RuntimeError):
                continue
            if all(face.get_char_index(c) for c in needed):
                family, style = face.family_name, face.style_name.lower()
                rank = ("sans" not in family.lower(), style != "regular",
                        family)
                found.append((rank, candidate))
        if not found:
            raise RuntimeError("No installed font covers the dashboard text.")
        path = min(found)[1]
    font_manager.fontManager.addfont(path)
    return ft2font.FT2Font(path).family_name


class _Board:
    """A figure laid out in inches from the top, like a dashboard grid."""

    def __init__(self, theme, height):
        self.theme = theme
        self.height = height
        self.fig = plt.figure(figsize=(_WIDTH, height), dpi=_DPI)
        self.fig.patch.set_facecolor(theme["canvas"])

    def rect(self, x, top, w, h):
        return [x / _WIDTH, 1 - (top + h) / self.height, w / _WIDTH,
                h / self.height]

    def text(self, x, top, s, **kwargs):
        self.fig.text(x / _WIDTH, 1 - top / self.height, s, **kwargs)

    def panel(self, x, top, w, h, title):
        r = self.rect(x, top, w, h)
        self.fig.patches.append(mpatches.FancyBboxPatch(
            (r[0], r[1]), r[2], r[3],
            boxstyle="round,pad=0,rounding_size=0.003",
            transform=self.fig.transFigure, facecolor=self.theme["panel"],
            edgecolor=self.theme["border"], linewidth=0.8, zorder=-1))
        self.text(x + 0.14, top + 0.13, title, fontsize=10.5,
                  color=self.theme["ink"], va="top", weight="bold")

    def axes(self, x, top, w, h):
        ax = self.fig.add_axes(self.rect(x, top, w, h))
        ax.set_facecolor(self.theme["panel"])
        return ax


def _gapped(times, values):
    """Inserts a break wherever collection paused."""
    xs, ys = [times[0]], [values[0]]
    for (t0, t1), v in zip(zip(times, times[1:]), values[1:]):
        if (t1 - t0).total_seconds() > _GAP_S:
            xs.append(t0 + (t1 - t0) / 2)
            ys.append(float("nan"))
        xs.append(t1)
        ys.append(v)
    return xs, ys


def _gradient_fill(ax, xs, ys, color, top_alpha):
    """Fills under a line with an opacity gradient."""
    x = mdates.date2num(xs)
    y = np.array(ys, dtype=float)
    y0, y1 = ax.get_ylim()
    x0, x1 = ax.get_xlim()
    rgba = np.zeros((128, 1, 4))
    rgba[:, :, :3] = mcolors.to_rgb(color)
    rgba[:, 0, 3] = np.linspace(0, top_alpha, 128)
    image = ax.imshow(rgba, aspect="auto", extent=[x0, x1, y0, np.nanmax(y)],
                      origin="lower", zorder=1, interpolation="bilinear")
    area = ax.fill_between(x, y, y0, facecolor="none", edgecolor="none")
    image.set_clip_path(
        mpath.Path.make_compound_path(*area.get_paths()), ax.transData)
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)


def _money(value):
    return f"¥{value:,.0f}"


def _count(value):
    return f"{value:,.0f}"


def _series_height(rows):
    return 0.42 + _PLOT_H + 0.32 + _ROW_H * (rows + 1) + 0.12


def _time_series(board, x, top, w, title, rows, fmt, span, legend_rows):
    """Draws a time-series panel whose legend lists min, max and last."""
    th = board.theme
    board.panel(x, top, w, _series_height(legend_rows), title)
    ax = board.axes(x + 0.72, top + 0.42, w - 0.9, _PLOT_H)
    values = [v for _, _, (_, ys) in rows for v in ys]
    low, high = min(values), max(values)
    pad = (high - low) * 0.12 or high * 0.05
    ax.set_xlim(*mdates.date2num(span))
    ax.set_ylim(low - pad, high + pad)
    for _, color, (times, ys) in rows:
        xs, gy = _gapped(times, ys)
        ax.plot(xs, gy, color=color, linewidth=1.5, zorder=3,
                solid_capstyle="round")
        _gradient_fill(ax, xs, gy, color, 0.22)
    ax.grid(True, color=th["grid"], linewidth=0.8)
    ax.set_axisbelow(True)
    for spine in ax.spines.values():
        spine.set_visible(False)
    ax.tick_params(colors=th["muted"], labelsize=8, length=0, pad=5)
    ax.xaxis.set_major_locator(mdates.HourLocator(byhour=[0, 12]))
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d %H:%M"))
    ax.yaxis.set_major_locator(ticker.MaxNLocator(5))
    ax.yaxis.set_major_formatter(ticker.FuncFormatter(lambda v, _: fmt(v)))
    legend_top = top + 0.42 + _PLOT_H + 0.34
    columns = ((TEXT["min"], w - 2.25), (TEXT["max"], w - 1.35),
               (TEXT["last"], w - 0.45))
    for label, cx in columns:
        board.text(x + cx, legend_top, label, fontsize=8.5, color=th["muted"],
                   ha="right", va="top")
    for i, (name, color, (_, ys)) in enumerate(rows):
        row_top = legend_top + _ROW_H * (i + 1)
        r = board.rect(x + 0.2, row_top + 0.085, 0.22, 0)
        board.fig.lines.append(mlines.Line2D(
            [r[0], r[0] + r[2]], [r[1], r[1]],
            transform=board.fig.transFigure, color=color, linewidth=2.4,
            solid_capstyle="round"))
        board.text(x + 0.52, row_top, name, fontsize=9, color=th["ink"],
                   va="top")
        for value, (_, cx) in zip((min(ys), max(ys), ys[-1]), columns):
            board.text(x + cx, row_top, fmt(value), fontsize=9,
                       color=th["ink"], ha="right", va="top")


def _stat(board, x, top, w, session, series):
    """Draws a stat panel: lowest price now, change since the first day."""
    th = board.theme
    times, values = series
    board.panel(x, top, w, _STAT_H, TEXT["stat"].format(session=session))
    board.text(x + 0.18, top + 0.48, _money(values[-1]), fontsize=27,
               color=th["ink"], va="top", weight="bold")
    change = (values[-1] - values[0]) / values[0] * 100
    rising = change >= 0
    arrow = "▲" if rising else "▼"
    board.text(x + 0.2, top + 1.0, f"{arrow} {abs(change):.1f}%", fontsize=10,
               color=th["up" if rising else "down"], va="top", weight="bold")
    board.text(x + 1.05, top + 1.0,
               TEXT["first"].format(price=_money(values[0])), fontsize=9,
               color=th["ink2"], va="top")
    ax = board.axes(x + w * 0.56, top + 0.42, w * 0.4, _STAT_H - 0.6)
    ax.axis("off")
    pad = (max(values) - min(values)) * 0.15 or values[0] * 0.02
    ax.set_xlim(mdates.date2num(times[0]), mdates.date2num(times[-1]))
    ax.set_ylim(min(values) - pad, max(values) + pad)
    xs, ys = _gapped(times, values)
    ax.plot(xs, ys, color=th["series"][0], linewidth=1.4, zorder=3)
    _gradient_fill(ax, xs, ys, th["series"][0], 0.3)


def render(data, theme_name, path):
    """Draws the dashboard for one theme and saves it as a PNG.

    Args:
        data: Aggregated series.
        theme_name: Key of THEMES.
        path: Output file.
    """
    th = THEMES[theme_name]
    span = (data.start, data.end)
    faces = sorted({face for _, face in data.tiers})
    halves = (len(data.sessions) + 1) // 2
    height = (2 * _MARGIN + 0.75 + _STAT_H + _GAP
              + halves * (_series_height(len(SITES)) + _GAP)
              + _series_height(len(faces)) + _GAP
              + _series_height(len(data.sessions)))
    board = _Board(th, height)
    board.text(_MARGIN, _MARGIN + 0.05, TEXT["title"],
               fontsize=17, color=th["ink"], weight="bold", va="top")
    board.text(_WIDTH - _MARGIN, _MARGIN + 0.08,
               TEXT["span"].format(
                   start=f"{data.start:%m-%d %H:%M}",
                   end=f"{data.end:%m-%d %H:%M}",
                   minutes=dashboard_data.BUCKET_MINUTES),
               fontsize=10, color=th["ink2"], va="top", ha="right")
    inner = _WIDTH - 2 * _MARGIN
    top = _MARGIN + 0.75
    stat_w = (inner - (len(data.sessions) - 1) * _GAP) / len(data.sessions)
    for i, session in enumerate(data.sessions):
        _stat(board, _MARGIN + i * (stat_w + _GAP), top, stat_w, session,
              data.best[session])
    top += _STAT_H + _GAP
    half_w = (inner - _GAP) / 2
    for i, session in enumerate(data.sessions):
        rows = [(label, th["series"][j], data.lowest[(site, session)])
                for j, (site, label) in enumerate(SITES)
                if (site, session) in data.lowest]
        _time_series(board, _MARGIN + (i % 2) * (half_w + _GAP), top, half_w,
                     TEXT["lowest"].format(session=session), rows, _money,
                     span, len(SITES))
        if i % 2 or i == len(data.sessions) - 1:
            top += _series_height(len(SITES)) + _GAP
    for i, (site, label) in enumerate(SITES):
        rows = [(TEXT["face"].format(face=face), th["series"][j],
                 data.tiers[(site, face)])
                for j, face in enumerate(faces) if (site, face) in data.tiers]
        _time_series(board, _MARGIN + i * (half_w + _GAP), top, half_w,
                     TEXT["tiers"].format(session=data.tier_session,
                                          site=label),
                     rows, _money, span, len(faces))
    top += _series_height(len(faces)) + _GAP
    for i, (site, label) in enumerate(SITES):
        rows = [(TEXT["session"].format(session=session), th["series"][j],
                 data.listings[(site, session)])
                for j, session in enumerate(data.sessions)
                if (site, session) in data.listings]
        _time_series(board, _MARGIN + i * (half_w + _GAP), top, half_w,
                     TEXT["listings"].format(site=label), rows, _count, span,
                     len(data.sessions))
    board.fig.savefig(path, facecolor=th["canvas"])
    plt.close(board.fig)
    _compress(path, th)


def _compress(path, theme):
    """Re-encodes a PNG with a 256-colour palette that keeps theme colours."""
    image = Image.open(path).convert("RGB")
    exact = [theme[k] for k in ("canvas", "panel", "ink", "up", "down")]
    exact += theme["series"]
    learned = image.quantize(colors=256 - len(exact),
                             method=Image.Quantize.MEDIANCUT)
    palette = learned.getpalette()[:3 * (256 - len(exact))]
    for color in exact:
        palette += [round(c * 255) for c in mcolors.to_rgb(color)]
    reference = Image.new("P", (1, 1))
    reference.putpalette(palette)
    image.quantize(palette=reference, dither=Image.Dither.NONE).save(
        path, optimize=True)


def main() -> int:
    """Renders both themes from DATA_DIR into OUT_DIR."""
    plt.rcParams["font.family"] = font_family()
    data = dashboard_data.load(os.environ.get("DATA_DIR", "data"), CITY)
    out = os.environ.get("OUT_DIR", "assets")
    os.makedirs(out, exist_ok=True)
    for theme_name in THEMES:
        path = os.path.join(out, f"dashboard-{theme_name}.png")
        render(data, theme_name, path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
