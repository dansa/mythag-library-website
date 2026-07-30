from __future__ import annotations

import tempfile
import textwrap
import tomllib
import unittest
from contextlib import ExitStack, contextmanager
from pathlib import Path
from unittest.mock import patch, sentinel

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
        content = root / "content"
        content.mkdir()
        (content / "awakeners.yaml").write_text(
            "example: Example\n", encoding="utf-8"
        )
        for category in ("covenants", "wheels", "posses"):
            (content / f"{category}.yaml").write_text("{}\n", encoding="utf-8")
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
        (root / "content" / "covenants.yaml").write_text(
            "burial-grounds-sighs: Burial Ground's Sighs\n",
            encoding="utf-8",
        )
        (root / "content" / "wheels.yaml").write_text(
            "wheel-unseen: Wheel Unseen\n",
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
                    "CONTENT_ROOT",
                    root / "content",
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
            self.assertIn('[project.extra.awakener_assets.portraits]', generated)
            self.assertIn(
                '"Example" = { image = "/images/awakeners/chaos/example.png"',
                generated,
            )
            config_data = tomllib.loads(generated)
            index = config_data["project"]["extra"]["awakener_index"]
            self.assertEqual(index["family_order"], ["chaos"])
            self.assertEqual(index["family"]["chaos"]["groups"], ["chaos"])
            self.assertEqual(index["group"]["chaos"]["guides"], ["example"])
            self.assertEqual(
                index["guide"]["example"],
                {
                    "label": "Example",
                    "image": "/images/awakeners/chaos/example--mini.png",
                    "url": "/handbook/awakeners/chaos/example/",
                },
            )

    def test_nests_subrealm_guides_under_their_realm_family(self) -> None:
        guides = [
            awakeners.Guide(
                Path("lib/handbook/awakeners/aequor/aurita.md"),
                "Aurita",
                sentinel.awakener,
            ),
            awakeners.Guide(
                Path("lib/handbook/awakeners/benthos-aequor/pontos.md"),
                "Pontos",
                sentinel.awakener,
            ),
        ]

        rendered = awakeners._render_nav(guides, "  ")

        self.assertEqual(rendered.count('{ "Aequor" = ['), 1)
        self.assertIn('    "handbook/awakeners/aequor/aurita.md",', rendered)
        self.assertIn('    { "Benthos Aequor" = [', rendered)
        self.assertIn('      "handbook/awakeners/benthos-aequor/pontos.md",', rendered)

    def test_derives_guide_identity_and_url_from_source_path(self) -> None:
        guide = awakeners.Guide(
            Path("lib/handbook/awakeners/benthos-aequor/pontos.md"),
            "Pontos",
            sentinel.awakener,
        )

        self.assertEqual(guide.slug, "pontos")
        self.assertEqual(guide.realm, "benthos-aequor")
        self.assertEqual(guide.url, "/handbook/awakeners/benthos-aequor/pontos/")

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

    def test_rejects_surrounding_whitespace_in_rendered_strings(self) -> None:
        cases = (
            ("title", "title: Example", "title: ' Example '"),
            (
                "template",
                "template: awakeners/awakener.html",
                "template: ' awakeners/awakener.html '",
            ),
            ("awakener.roles[0]", "    - Support", "    - ' Support '"),
            (
                "awakener.ranks.support[0].tier",
                "      - tier: B",
                "      - tier: ' B '",
            ),
        )

        for field, original, replacement in cases:
            with self.subTest(field=field), tempfile.TemporaryDirectory() as temporary:
                root, config = self.project(temporary)
                guide = (
                    root
                    / "lib"
                    / "handbook"
                    / "awakeners"
                    / "chaos"
                    / "example.md"
                )
                guide.write_text(
                    guide.read_text(encoding="utf-8").replace(original, replacement, 1),
                    encoding="utf-8",
                )

                with self.patches(root, config):
                    _, issues = awakeners.load_guides()

                rendered = "\n".join(str(issue) for issue in issues)
                self.assertIn(
                    f"{field}: must not have leading or trailing whitespace",
                    rendered,
                )

    def test_rejects_duplicate_yaml_keys_in_guides_and_catalogs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            guide = (
                root / "lib" / "handbook" / "awakeners" / "chaos" / "example.md"
            )
            guide.write_text(
                guide.read_text(encoding="utf-8").replace(
                    "  tagline: Example tagline",
                    "  tagline: First\n  tagline: Second",
                    1,
                ),
                encoding="utf-8",
            )

            with self.patches(root, config):
                _, guide_issues = awakeners.load_guides()

            self.assertIn(
                "found duplicate key 'tagline'",
                "\n".join(str(issue) for issue in guide_issues),
            )

        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            (root / "content" / "awakeners.yaml").write_text(
                "example: First\nexample: Second\n",
                encoding="utf-8",
            )
            issues: list[awakeners.ValidationIssue] = []

            with self.patches(root, config):
                awakeners.load_content_catalog(issues)

            self.assertIn(
                "found duplicate key 'example'",
                "\n".join(str(issue) for issue in issues),
            )

    def test_reports_mixed_type_unknown_fields_without_crashing(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            guide = (
                root / "lib" / "handbook" / "awakeners" / "chaos" / "example.md"
            )
            guide.write_text(
                guide.read_text(encoding="utf-8").replace(
                    "  tagline: Example tagline",
                    "  1: malformed\n  surprise: true\n  tagline: Example tagline",
                    1,
                ),
                encoding="utf-8",
            )

            with self.patches(root, config):
                _, issues = awakeners.load_guides()

            rendered = "\n".join(str(issue) for issue in issues)
            self.assertIn("awakener.1: expected a string field name", rendered)
            self.assertIn("awakener.surprise: unknown field", rendered)

    def test_reports_duplicate_filename_as_slug_issue(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            source = (
                root / "lib" / "handbook" / "awakeners" / "chaos" / "example.md"
            )
            duplicate = (
                root / "lib" / "handbook" / "awakeners" / "aequor" / "example.md"
            )
            duplicate.parent.mkdir()
            duplicate.write_text(
                source.read_text(encoding="utf-8").replace(
                    "title: Example", "title: Other", 1
                ),
                encoding="utf-8",
            )

            with self.patches(root, config):
                _, issues = awakeners.load_guides()

            duplicate_issues = [
                issue for issue in issues if "duplicate slug" in issue.message
            ]
            self.assertEqual(len(duplicate_issues), 1)
            self.assertEqual(duplicate_issues[0].field, "slug")

    def test_rejects_guide_title_that_disagrees_with_awakener_catalog(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            (root / "content" / "awakeners.yaml").write_text(
                "example: Different Name\n", encoding="utf-8"
            )

            with self.patches(root, config):
                with self.assertRaises(awakeners.AwakenerValidationError) as context:
                    awakeners.prepare_awakeners()

            self.assertIn(
                "title: expected catalog label 'Different Name'",
                str(context.exception),
            )

    def test_rejects_awakener_catalog_entry_without_a_guide(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            (root / "content" / "awakeners.yaml").write_text(
                "example: Example\nmissing: Missing\n", encoding="utf-8"
            )

            with self.patches(root, config):
                with self.assertRaises(awakeners.AwakenerValidationError) as context:
                    awakeners.prepare_awakeners()

            self.assertIn(
                "content/awakeners.yaml: missing: does not have a standalone guide",
                str(context.exception),
            )

    def test_rejects_related_awakener_without_a_guide(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            guide = root / "lib" / "handbook" / "awakeners" / "chaos" / "example.md"
            guide.write_text(
                guide.read_text(encoding="utf-8").replace(
                    "  works_well_with: []", "  works_well_with:\n    - missing"
                ),
                encoding="utf-8",
            )
            (root / "content" / "awakeners.yaml").write_text(
                "example: Example\nmissing: Missing\n", encoding="utf-8"
            )

            with self.patches(root, config):
                with self.assertRaises(awakeners.AwakenerValidationError) as context:
                    awakeners.prepare_awakeners()

            self.assertIn(
                "awakener.works_well_with: Awakener ID 'missing' does not have a "
                "standalone guide",
                str(context.exception),
            )

    def test_filename_is_stable_id_when_title_punctuation_differs(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            chaos = root / "lib" / "handbook" / "awakeners" / "chaos"
            guide = chaos / "example.md"
            guide.replace(chaos / "gdoll.md")
            renamed = chaos / "gdoll.md"
            renamed.write_text(
                renamed.read_text(encoding="utf-8").replace(
                    "title: Example", "title: G-Doll", 1
                ),
                encoding="utf-8",
            )
            portraits = root / "lib" / "images" / "awakeners" / "chaos"
            (portraits / "example.png").replace(portraits / "gdoll.png")
            (portraits / "example--mini.png").replace(portraits / "gdoll--mini.png")
            (root / "content" / "awakeners.yaml").write_text(
                "gdoll: G-Doll\n", encoding="utf-8"
            )

            with self.patches(root, config):
                guides = awakeners.prepare_awakeners()

            self.assertEqual((guides[0].slug, guides[0].title), ("gdoll", "G-Doll"))

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

    def test_rejects_content_id_with_surrounding_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            self.add_catalog_references(root, '" burial-grounds-sighs "')

            with self.patches(root, config):
                with self.assertRaises(awakeners.AwakenerValidationError) as context:
                    awakeners.prepare_awakeners()

            self.assertIn(
                "must not have leading or trailing whitespace",
                str(context.exception),
            )

    def test_rejects_catalog_label_with_surrounding_whitespace(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            (root / "content" / "awakeners.yaml").write_text(
                "example: ' Example '\n",
                encoding="utf-8",
            )
            issues: list[awakeners.ValidationIssue] = []

            with self.patches(root, config):
                awakeners.load_content_catalog(issues)

            rendered = "\n".join(str(issue) for issue in issues)
            self.assertIn(
                "content/awakeners.yaml: example: "
                "must not have leading or trailing whitespace",
                rendered,
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

    def test_accepts_optional_build_covenant_guidance(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root, config = self.project(temporary)
            self.add_catalog_references(root, "burial-grounds-sighs")
            guide = root / "lib" / "handbook" / "awakeners" / "chaos" / "example.md"
            guide.write_text(
                guide.read_text(encoding="utf-8").replace(
                    "      wheels:", "      covenants_note: Any support\n      wheels:"
                ),
                encoding="utf-8",
            )
            covenants = root / "lib" / "images" / "covenants"
            covenants.mkdir()
            (covenants / "burial-grounds-sighs.png").write_bytes(b"png")
            (covenants / "burial-grounds-sighs--icon.png").write_bytes(b"png")

            with self.patches(root, config):
                guides = awakeners.prepare_awakeners()

            self.assertEqual(
                guides[0].awakener.builds[0].covenants_note, "Any support"
            )


if __name__ == "__main__":
    unittest.main()
