from __future__ import annotations

import tempfile
import textwrap
import unittest
from contextlib import ExitStack, contextmanager
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
  suggested_posses_note: Any
  works_well_with: []
  works_well_with_note: Anyone
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
        (root / "awakener-content.yaml").write_text(
            "covenants: {}\nwheels: {}\nposses: {}\n",
            encoding="utf-8",
        )
        return root, config

    def add_catalog_references(self, root: Path, covenant_id: str) -> None:
        guide = root / "lib" / "handbook" / "awakeners" / "chaos" / "example.md"
        guide.write_text(
            VALID_GUIDE.replace(
                "  builds: []",
                "  builds:\n"
                "    - name: Example Build\n"
                "      covenants:\n"
                f"        - {covenant_id}\n"
                "      wheels:\n"
                "        early_game:\n"
                "          - id: wheel-unseen\n"
                "        astral_reign:\n"
                "          - id: wheel-unseen",
            ),
            encoding="utf-8",
        )
        (root / "awakener-content.yaml").write_text(
            "covenants:\n  burial-grounds-sighs: Burial Ground's Sighs\n"
            "wheels:\n  wheel-unseen: Wheel Unseen\nposses: {}\n",
            encoding="utf-8",
        )
        wheels = root / "lib" / "images" / "wheels"
        wheels.mkdir()
        (wheels / "wheel-unseen.png").write_bytes(b"png")

    @contextmanager
    def patches(self, root: Path, config: Path):
        with ExitStack() as stack:
            stack.enter_context(patch.object(awakeners, "ROOT", root))
            stack.enter_context(
                patch.object(
                    awakeners,
                    "GUIDES_ROOT",
                    root / "lib" / "handbook" / "awakeners",
                )
            )
            stack.enter_context(
                patch.object(
                    awakeners,
                    "CONTENT_CATALOG",
                    root / "awakener-content.yaml",
                )
            )
            stack.enter_context(
                patch.object(awakeners, "SOURCE_IMAGES", root / "lib" / "images")
            )
            stack.enter_context(patch.object(awakeners, "SOURCE_CONFIG", config))
            stack.enter_context(
                patch.object(
                    awakeners,
                    "GENERATED_CONFIG",
                    root / ".zensical.generated.toml",
                )
            )
            yield

    def test_prepares_navigation_and_assets_without_rewriting_source_config(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            original = config.read_text(encoding="utf-8")
            with self.patches(root, config):
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
            with self.patches(root, config):
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
            with self.patches(root, config):
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
            with self.patches(root, config):
                _, issues = awakeners.load_guides()

            self.assertNotIn("content", {issue.field for issue in issues})

    def test_every_allowed_tier_has_a_css_selector_and_color(self) -> None:
        rank_styles = (awakeners.ROOT / "lib" / "styles" / "awakeners.css").read_text(
            encoding="utf-8"
        )
        color_variables = (awakeners.ROOT / "lib" / "styles" / "extra.css").read_text(
            encoding="utf-8"
        )

        for tier, style_name in awakeners.TIER_STYLE_NAMES.items():
            with self.subTest(tier=tier):
                self.assertIn(f'.awakener-rank[data-tier="{tier}"]', rank_styles)
                self.assertIn(f"--md-tier-{style_name}:", color_variables)

    def test_missing_asset_stops_preparation(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            (root / "lib" / "images" / "awakeners" / "chaos" / "example--mini.png").unlink()
            with self.patches(root, config):
                with self.assertRaises(awakeners.AwakenerValidationError) as context:
                    awakeners.prepare_awakeners()

            self.assertIn("no asset matched 'awakeners/*/example--mini.png'", str(context.exception))

    def test_rejects_unknown_content_id_with_suggestion(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            self.add_catalog_references(root, "burial-ground-sighs")

            with self.patches(root, config):
                with self.assertRaises(awakeners.AwakenerValidationError) as context:
                    awakeners.prepare_awakeners()

            self.assertIn(
                "unknown covenant ID 'burial-ground-sighs'; "
                "did you mean 'burial-grounds-sighs'?",
                str(context.exception),
            )

    def test_resolves_content_id_to_catalog_label_and_assets(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            self.add_catalog_references(root, "burial-grounds-sighs")
            covenants = root / "lib" / "images" / "covenants"
            covenants.mkdir()
            (covenants / "burial-grounds-sighs.png").write_bytes(b"png")
            (covenants / "burial-grounds-sighs--icon.png").write_bytes(b"png")

            with self.patches(root, config):
                awakeners.prepare_awakeners()

            generated = (root / ".zensical.generated.toml").read_text(encoding="utf-8")
            self.assertIn(
                '"burial-grounds-sighs" = { label = "Burial Ground\'s Sighs"',
                generated,
            )


if __name__ == "__main__":
    unittest.main()
