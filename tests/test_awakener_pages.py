from __future__ import annotations

import tempfile
import textwrap
import unittest
from pathlib import Path
from unittest.mock import patch

from mythag_site import awakeners


VALID_GUIDE = """\
---
title: Example
description: Example guide.
template: awakeners/awakener.html
awakener:
  tagline: Example tagline
  roles:
    - Support
  ranks:
    support:
      - tier: B
        note: Decent
  stopping_points:
    - E0
  builds: []
  suggested_posses: []
  works_well_with: []
---

Ordinary **Markdown** prose.
"""


class AwakenerPreparationTests(unittest.TestCase):
    def project(self, temporary: str) -> tuple[Path, Path]:
        root = Path(temporary)
        guides = root / "lib" / "handbook" / "awakeners"
        images = root / "lib" / "images"
        guide = guides / "chaos" / "example.md"
        guide.parent.mkdir(parents=True)
        guide.write_text(VALID_GUIDE, encoding="utf-8")
        portrait = images / "awakeners" / "chaos" / "example.png"
        portrait.parent.mkdir(parents=True)
        portrait.write_bytes(b"png")
        portrait.with_name("example--mini.png").write_bytes(b"png")
        config = root / "zensical.toml"
        config.write_text(
            textwrap.dedent(
                """\
                [project]
                site_name = "Test"
                nav = [
                  { "Awakener Guides" = "handbook/awakeners.md" }, # @mythag-awakener-nav
                ]
                """
            ),
            encoding="utf-8",
        )
        return root, config

    def patches(self, root: Path, config: Path):
        return (
            patch.object(awakeners, "ROOT", root),
            patch.object(
                awakeners,
                "GUIDES_ROOT",
                root / "lib" / "handbook" / "awakeners",
            ),
            patch.object(awakeners, "SOURCE_IMAGES", root / "lib" / "images"),
            patch.object(awakeners, "SOURCE_CONFIG", config),
            patch.object(
                awakeners, "GENERATED_CONFIG", root / ".zensical.generated.toml"
            ),
        )

    def test_prepares_navigation_and_assets_without_rewriting_source_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            original = config.read_text(encoding="utf-8")
            patches = self.patches(root, config)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                guides = awakeners.prepare_awakeners()

            self.assertEqual([guide.title for guide in guides], ["Example"])
            self.assertEqual(config.read_text(encoding="utf-8"), original)
            generated = (root / ".zensical.generated.toml").read_text(encoding="utf-8")
            self.assertIn('{ "Chaos" = [', generated)
            self.assertIn('"handbook/awakeners/chaos/example.md"', generated)
            self.assertIn('[project.extra.awakener_assets.awakeners]', generated)
            self.assertIn(
                '"Example" = { image = "/images/awakeners/chaos/example.png"',
                generated,
            )

    def test_reports_multiple_schema_errors_with_field_paths(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            guide = root / "lib" / "handbook" / "awakeners" / "chaos" / "example.md"
            guide.write_text(
                VALID_GUIDE.replace("    - Support", "    - ''").replace(
                    "      - tier: B", "      - tier: Z\n        surprise: true"
                ),
                encoding="utf-8",
            )
            patches = self.patches(root, config)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                _, issues = awakeners.load_guides()

            rendered = "\n".join(str(issue) for issue in issues)
            self.assertIn("awakener.roles[0]: expected a non-empty string", rendered)
            self.assertIn("awakener.ranks.support[0].tier: expected one of", rendered)
            self.assertIn("awakener.ranks.support[0].surprise: unknown field", rendered)

    def test_rejects_layout_markup_in_guide_prose(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            guide = root / "lib" / "handbook" / "awakeners" / "chaos" / "example.md"
            guide.write_text(
                VALID_GUIDE.replace(
                    "Ordinary **Markdown** prose.", '<div class="layout">Nope</div>'
                ),
                encoding="utf-8",
            )
            patches = self.patches(root, config)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                _, issues = awakeners.load_guides()

            rendered = "\n".join(str(issue) for issue in issues)
            self.assertIn("content: use ordinary Markdown", rendered)

    def test_allows_markdown_autolinks_in_guide_prose(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            guide = root / "lib" / "handbook" / "awakeners" / "chaos" / "example.md"
            guide.write_text(
                VALID_GUIDE.replace(
                    "Ordinary **Markdown** prose.",
                    "See <https://example.com> or contact <name@example.com>.",
                ),
                encoding="utf-8",
            )
            patches = self.patches(root, config)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                _, issues = awakeners.load_guides()

            self.assertNotIn("content", {issue.field for issue in issues})

    def test_missing_asset_stops_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            (root / "lib" / "images" / "awakeners" / "chaos" / "example--mini.png").unlink()
            patches = self.patches(root, config)
            with patches[0], patches[1], patches[2], patches[3], patches[4]:
                with self.assertRaises(awakeners.AwakenerValidationError) as context:
                    awakeners.prepare_awakeners()

            self.assertIn("no asset matched 'awakeners/*/example--mini.png'", str(context.exception))


if __name__ == "__main__":
    unittest.main()
