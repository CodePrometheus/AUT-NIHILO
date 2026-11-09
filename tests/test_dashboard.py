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
    """Writes six rounds; the later session rises, collection pauses once."""
    sessions = {
        site: [
            models.Session(config.Show(site, "guangzhou", "show"), sid,
                           datetime.datetime(2026, 11, day, 19, 0,
                                             tzinfo=config.TZ))
            for sid, day in (("a", 27), ("b", 29))
        ]
        for site in ("motianlun", "piaoniu")
    }
    offsets = (0, 30, 60, 240, 270, 300)  # Minutes; a 3-hour pause.
    for step, minutes in enumerate(offsets):
        fetched_at = _START + datetime.timedelta(minutes=minutes)
        results = []
        for site, (early, late) in sessions.items():
            results.append(models.SessionResult(early, (
                _listing(f"{site}-a1", 480.0, 2500.0),
                _listing(f"{site}-a2", 1680.0, 4000.0),
            )))
            results.append(models.SessionResult(late, (
                _listing(f"{site}-b1", 480.0, 3000.0 + 100 * step),
                _listing(f"{site}-b2", 1680.0, 5000.0 + 200 * step),
            )))
        store.write_prices(results, fetched_at, root)


@unittest.skipIf(render is None, "dashboard dependencies are not installed")
class DashboardTest(unittest.TestCase):

    def setUp(self):
        super().setUp()
        self.root = tempfile.mkdtemp()
        _write_rounds(self.root)

    def test_aggregates_sessions_and_picks_the_fastest_rising(self):
        data = dashboard_data.load(self.root, "guangzhou")

        self.assertEqual(data.sessions, ["11-27", "11-29"])
        self.assertEqual(data.tier_session, "11-29")
        self.assertEqual(data.best["11-27"][1], [2500.0] * 6)
        self.assertEqual(data.best["11-29"][1][0], 3000.0)
        self.assertEqual(sorted(data.tiers), [
            ("motianlun", 480), ("motianlun", 1680),
            ("piaoniu", 480), ("piaoniu", 1680),
        ])
        self.assertEqual(data.listings[("piaoniu", "11-27")][1], [2.0] * 6)

    def test_rejects_city_without_data(self):
        with self.assertRaises(ValueError):
            dashboard_data.load(self.root, "changsha")

    def test_renders_compressed_images_for_both_themes(self):
        data = dashboard_data.load(self.root, "guangzhou")
        out = tempfile.mkdtemp()
        for theme in render.THEMES:
            path = os.path.join(out, f"dashboard-{theme}.png")
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")  # Missing CJK glyphs.
                render.render(data, theme, path)
            with Image.open(path) as image:
                self.assertEqual(image.mode, "P")
                rgb = image.convert("RGB")
                colors = {c for _, c in rgb.getcolors(1 << 24)}
            self.assertIn((0xd0, 0x3b, 0x3b), colors)


if __name__ == "__main__":
    unittest.main()
