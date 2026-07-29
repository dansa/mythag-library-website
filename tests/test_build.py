from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from mythag_site import build


class ImageUrlTests(unittest.TestCase):
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
            avif.write_bytes(b"avif")
            page.write_text(
                '<script src="/images/awakener.png"></script>'
                '<img src="/images/awakener.png" alt="Awakener">',
                encoding="utf-8",
            )

            with patch.object(build, "SITE_ROOT", site):
                self.assertEqual(build.rewrite_html_images(), (1, 1))

            rewritten = page.read_text(encoding="utf-8")
            self.assertIn('<script src="/images/awakener.png">', rewritten)
            self.assertIn('<img src="/images/awakener.avif"', rewritten)


if __name__ == "__main__":
    unittest.main()
