"""Tests for dashboard aggregation and rendering.

Skipped when the dashboard dependencies are not installed, as in the
collection step that only installs the collector's requirements.
"""

import datetime
import os
import tempfile
import unittest
import warnings

from collector import config
from collector import models
from collector import store

try:
    from PIL import Image
    from dashboard import data as dashboard_data
    from dashboard import render
except ImportError:
    Image = dashboard_data = render = None

_START = datetime.datetime(2026, 11, 7, 10, 0, tzinfo=config.TZ)


def _listing(listing_id, face, price):
    return models.Listing(
        tier_id=str(face), tier_name=None, face_value=face, source="tickets",
        buy_type=None, listing_id=listing_id, price=price, area=None, row=None,
        stock=1, qty_options="1",
    )


def _write_rounds(root):
    """Writes rounds across both months, with changing coverage and prices."""
    sessions = {
        site: [
            models.Session(config.Show(site, "guangzhou", "show"), sid,
                           datetime.datetime(2026, month, day, 19, 0,
                                             tzinfo=config.TZ))
            for sid, month, day in (("a", 11, 27), ("b", 11, 29),
                                    ("c", 12, 4), ("d", 12, 6))
        ]
        for site in ("motianlun", "piaoniu")
    }
    offsets = (0, 30, 60, 240, 270, 300, 305)  # Last two share a bucket.
    for step, minutes in enumerate(offsets):
        fetched_at = _START + datetime.timedelta(minutes=minutes)
        results = []
        for site, shows in sessions.items():
            prices = (2500.0, 3000.0 + 100 * step, 4000.0, 4500.0)
            for i, (show, price) in enumerate(zip(shows, prices)):
                if i == 2 and site == "motianlun" and step >= 4:
                    continue
                if i in (1, 2) and site == "piaoniu":
                    price += 100
                if i == 1 and minutes == 305:
                    price = 3800.0 if site == "motianlun" else 3200.0
                results.append(models.SessionResult(show, (
                    _listing(f"{site}-{i}-1", 480.0, price),
                    _listing(f"{site}-{i}-2", 1680.0, price + 1500),
                )))
        store.write_prices(results, fetched_at, root)


@unittest.skipIf(render is None, "dashboard dependencies are not installed")
class DashboardTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp()
        _write_rounds(self.root)

    def test_aggregates_tiers_and_attributes_the_latest_bucket(self):
        data = dashboard_data.load(self.root, "guangzhou")

        self.assertEqual(data.sessions, ["11-27", "11-29", "12-04", "12-06"])
        self.assertEqual(data.best["11-27"][1], [2500.0] * 6)
        self.assertEqual(data.best["11-29"][1][0], 3000.0)
        self.assertEqual(data.best_sites["11-27"], ("motianlun", "piaoniu"))
        self.assertEqual(data.best["11-29"][1][-1], 3400.0)
        self.assertEqual(data.best_sites["11-29"], ("piaoniu",))
        self.assertEqual(data.best["12-04"][1][-1], 4100.0)
        self.assertEqual(data.best_sites["12-04"], ("piaoniu",))
        self.assertEqual(set(data.tiers), {
            (site, session, face)
            for site in ("motianlun", "piaoniu")
            for session in data.sessions
            for face in (480, 1680)
        })
        self.assertEqual(data.tiers[("motianlun", "11-29", 1680)][1][-1],
                         5150.0)
        self.assertEqual(data.listings[("piaoniu", "11-27")][1], [2.0] * 6)

    def test_rejects_city_without_data(self):
        with self.assertRaises(ValueError):
            dashboard_data.load(self.root, "changsha")

    def test_renders_compressed_images_for_both_themes(self):
        data = dashboard_data.load(self.root, "guangzhou")
        out = tempfile.mkdtemp()
        for theme in render.THEMES:
            summary, november, december, listings = (
                os.path.join(out, f"dashboard-{name}-{theme}.png")
                for name in ("summary", "11", "12", "listings")
            )
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # Missing CJK glyphs.
                render.render_summary(data, theme, summary)
                render.render_tiers(data, "11", theme, november)
                render.render_tiers(data, "12", theme, december)
                render.render_listings(data, theme, listings)
            for path in (summary, november, december, listings):
                with Image.open(path) as image:
                    self.assertEqual(image.mode, "P")
            with Image.open(summary) as image:
                rgb = image.convert("RGB")
                colors = {c for _, c in rgb.getcolors(1 << 24)}
                self.assertIn((0xd0, 0x3b, 0x3b), colors)

    def test_long_date_labels_remain_readable(self):
        start = datetime.datetime(2026, 11, 6, 15, 30)
        end = start + datetime.timedelta(days=19, hours=3)
        series = ([start, end], [2500.0, 3100.0])
        board = render._Board(render.THEMES["light"], 5)
        self.addCleanup(render.plt.close, board.fig)
        render._time_series(board, 0, 0, 15, "Prices",
                            [("Platform", "#2a78d6", series)], render._money,
                            (start, end), 1)
        with warnings.catch_warnings():
            warnings.filterwarnings("ignore", message="Glyph .* missing")
            board.fig.canvas.draw()
        ax, = board.fig.axes
        labels = ax.get_xticklabels()
        bounds = [label.get_window_extent() for label in labels]
        self.assertGreater(len(labels), 1)
        self.assertTrue(all(":" not in label.get_text() for label in labels))
        self.assertTrue(all(a.x1 < b.x0 for a, b in zip(bounds, bounds[1:])))


if __name__ == "__main__":
    unittest.main()
