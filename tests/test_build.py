from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from PIL import Image

from mythag_site import build


class ImageUrlTests(unittest.TestCase):
    def test_caps_only_wheel_delivery_assets_at_640_pixels(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            images = Path(temporary) / "images"
            wheel = images / "wheels" / "wheel.png"
            awakener = images / "awakeners" / "awakener.png"
            wheel.parent.mkdir(parents=True)
            awakener.parent.mkdir(parents=True)
            Image.new("RGBA", (430, 872), (255, 0, 0, 128)).save(wheel)
            Image.new("RGBA", (430, 872), (0, 0, 255, 128)).save(awakener)

            with patch.object(build, "SOURCE_IMAGES", images):
                wheel_avif = Path(temporary) / "wheel.avif"
                awakener_avif = Path(temporary) / "awakener.avif"
                self.assertTrue(build.encode_cached(wheel, wheel_avif))
                self.assertTrue(build.encode_cached(awakener, awakener_avif))
                self.assertFalse(build.encode_cached(wheel, wheel_avif))

            with Image.open(wheel_avif) as converted:
                self.assertEqual(converted.size, (316, 640))
            with Image.open(awakener_avif) as converted:
                self.assertEqual(converted.size, (430, 872))

            site = Path(temporary) / "site"
            site_wheel = site / "images" / "wheels" / "wheel.png"
            site_wheel.parent.mkdir(parents=True)
            Image.new("RGBA", (430, 872), (255, 0, 0, 128)).save(site_wheel)
            page = site / "index.html"
            page.write_text('<img src="/images/wheels/wheel.png">', encoding="utf-8")
            with (
                patch.object(build, "SOURCE_IMAGES", images),
                patch.object(build, "SITE_ROOT", site),
            ):
                self.assertTrue(
                    build.encode_cached(wheel, site_wheel.with_suffix(".avif"))
                )
                self.assertEqual(build.rewrite_html_images(), (1, 1))
            self.assertIn(
                'src="/images/wheels/wheel.avif" width="316" height="640"',
                page.read_text(encoding="utf-8"),
            )

    def test_rewrites_root_and_relative_image_urls_only_when_avif_exists(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            page = site / "handbook" / "awakeners" / "index.html"
            avif = site / "images" / "awakener.avif"
            page.parent.mkdir(parents=True)
            avif.parent.mkdir(parents=True)
            avif.write_bytes(b"avif")

            with patch.object(build, "SITE_ROOT", site):
                self.assertEqual(
                    build.avif_url("/images/awakener.png", page),
                    "/images/awakener.avif",
                )
                self.assertEqual(
                    build.avif_url("../../images/awakener.png", page),
                    "../../images/awakener.avif",
                )
                self.assertIsNone(build.avif_url("/images/missing.png", page))
                self.assertIsNone(
                    build.avif_url("https://example.com/awakener.png", page)
                )

    def test_rewrites_img_elements_without_touching_other_src_attributes(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            site = Path(temporary)
            page = site / "index.html"
            avif = site / "images" / "awakener.avif"
            avif.parent.mkdir(parents=True)
            Image.new("RGBA", (400, 200)).save(avif.with_suffix(".png"))
            Image.new("RGBA", (200, 100)).save(avif, "AVIF")
            page.write_text(
                '<script src="/images/awakener.png"></script>'
                '<img src="/images/awakener.png" alt="Awakener">'
                '<img src="/images/awakener.png" alt="Small" width="100">'
                '<img src="/images/awakener.png" alt="Decimal" width="117.95">',
                encoding="utf-8",
            )

            with patch.object(build, "SITE_ROOT", site):
                self.assertEqual(build.rewrite_html_images(), (1, 3))

            rewritten = page.read_text(encoding="utf-8")
            self.assertIn('<script src="/images/awakener.png">', rewritten)
            self.assertIn('<img src="/images/awakener.avif"', rewritten)
            self.assertIn('width="200" height="100">', rewritten)
            self.assertIn('width="100" height="50">', rewritten)
            self.assertIn('width="117.95" height="59">', rewritten)
            self.assertNotIn('width="117.95" width=', rewritten)


if __name__ == "__main__":
    unittest.main()
