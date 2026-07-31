import re
import subprocess
import sys
import tempfile
import tomllib
import unittest
from collections import Counter
from html.parser import HTMLParser
from pathlib import Path
from unittest.mock import patch

from mythag_site import awakeners, build


ROOT = Path(__file__).resolve().parents[1]


class AwakenerIndexParser(HTMLParser):
    def __init__(self, guide_ids: set[str]) -> None:
        super().__init__()
        self.guide_ids = guide_ids
        self.cards: dict[str, dict[str, str]] = {}
        self.card_order: list[str] = []
        self.toc_hrefs: set[str] = set()
        self._active_card: str | None = None
        self._active_label: list[str] = []
        self._secondary_toc_depth = 0

    def handle_starttag(
        self, tag: str, attrs: list[tuple[str, str | None]]
    ) -> None:
        values = {key: value or "" for key, value in attrs}
        classes = set(values.get("class", "").split())
        if tag == "nav" and (
            self._secondary_toc_depth or "md-nav--secondary" in classes
        ):
            self._secondary_toc_depth += 1
        if tag == "a" and self._secondary_toc_depth and values.get("href", "").startswith("#"):
            self.toc_hrefs.add(values["href"])
        if tag == "a" and values.get("id") in self.guide_ids:
            self._active_card = values["id"]
            self._active_label = []
            self.card_order.append(self._active_card)
            self.cards[self._active_card] = {"href": values.get("href", "")}
        elif tag == "img" and self._active_card is not None:
            self.cards[self._active_card].update(values)

    def handle_data(self, data: str) -> None:
        if self._active_card is not None:
            self._active_label.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._active_card is not None:
            self.cards[self._active_card]["label"] = "".join(
                self._active_label
            ).strip()
            self._active_card = None
            self._active_label = []
        elif tag == "nav" and self._secondary_toc_depth:
            self._secondary_toc_depth -= 1


class AwakenerContentTests(unittest.TestCase):
    def test_preserves_support_build_covenant_guidance(self) -> None:
        guides, issues = awakeners.load_guides()
        self.assertEqual(issues, [])
        by_slug = {guide.slug: guide for guide in guides}

        for slug in ("24", "alva", "karen"):
            with self.subTest(guide=slug):
                self.assertEqual(
                    by_slug[slug].awakener.builds[0].covenants_note,
                    "Any support",
                )

    def test_index_inventory_is_generated(self) -> None:
        source = (ROOT / "lib" / "handbook" / "awakeners.md").read_text(
            encoding="utf-8"
        )
        template = (
            ROOT / "overrides" / "awakeners" / "index.html"
        ).read_text(encoding="utf-8")

        self.assertIn("template: awakeners/index.html", source)
        self.assertNotIn("/handbook/awakeners/chaos/", source)
        self.assertIn('id="{{ guide_id }}"', template)

    def test_rendered_index_covers_guides_fragments_and_delivery_contract(self) -> None:
        guides = awakeners.prepare_awakeners()
        subprocess.run(
            [
                sys.executable,
                "-m",
                "zensical",
                "build",
                "--clean",
                "--config-file",
                str(awakeners.GENERATED_CONFIG),
            ],
            cwd=ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
        config = tomllib.loads(
            awakeners.GENERATED_CONFIG.read_text(encoding="utf-8")
        )
        index = config["project"]["extra"]["awakener_index"]
        guide_ids = {guide.slug for guide in guides}

        with tempfile.TemporaryDirectory() as temporary:
            temporary_site = Path(temporary)
            rendered_index = temporary_site / "handbook" / "awakeners" / "index.html"
            rendered_index.parent.mkdir(parents=True)
            rendered_index.write_text(
                (ROOT / "site" / "handbook" / "awakeners" / "index.html").read_text(
                    encoding="utf-8"
                ),
                encoding="utf-8",
            )
            for guide in index["guide"].values():
                avif = (temporary_site / guide["image"].lstrip("/")).with_suffix(
                    ".avif"
                )
                avif.parent.mkdir(parents=True, exist_ok=True)
                avif.touch()

            with patch.object(build, "SITE_ROOT", temporary_site):
                build.rewrite_html_images()
                rendered_guide = (
                    temporary_site
                    / "handbook"
                    / "awakeners"
                    / "chaos"
                    / "24"
                    / "index.html"
                )
                rendered_guide.parent.mkdir(parents=True)
                rendered_guide.write_text(
                    (
                        ROOT
                        / "site"
                        / "handbook"
                        / "awakeners"
                        / "chaos"
                        / "24"
                        / "index.html"
                    ).read_text(encoding="utf-8"),
                    encoding="utf-8",
                )
                build.expand_html_abbreviations()

            html = rendered_index.read_text(encoding="utf-8")
            guide_html = rendered_guide.read_text(encoding="utf-8")

        parser = AwakenerIndexParser(guide_ids)
        parser.feed(html)
        self.assertEqual(set(parser.cards), guide_ids)
        expected_order = [
            guide_id
            for family_id in index["family_order"]
            for group_id in index["family"][family_id]["groups"]
            for guide_id in index["group"][group_id]["guides"]
        ]
        self.assertEqual(parser.card_order, expected_order)
        self.assertEqual(
            parser.toc_hrefs,
            {
                *(f"#{guide_id}" for guide_id in guide_ids),
                "#chaos",
                "#aequor",
                "#benthos-aequor",
                "#caro",
                "#propagation-caro",
                "#ultra",
                "#singularity-ultra",
            },
        )
        self.assertIn('class="awakener-index-grid grid-96"', html)
        for guide in guides:
            with self.subTest(guide=guide.slug):
                card = parser.cards[guide.slug]
                expected = index["guide"][guide.slug]
                self.assertEqual(
                    card["href"],
                    expected["url"],
                )
                self.assertEqual(card["label"], expected["label"])
                self.assertEqual(
                    card["src"], Path(expected["image"]).with_suffix(".avif").as_posix()
                )
                self.assertEqual(card["alt"], "")
                self.assertEqual(card["width"], "360")
                self.assertEqual(card["height"], "360")
                self.assertEqual(card["loading"], "lazy")
                self.assertEqual(card["decoding"], "async")

        rendered_terms = Counter(
            match.group("term")
            for match in re.finditer(
                r'<abbr\b[^>]*>(?P<term>.*?)</abbr>', guide_html, re.DOTALL
            )
        )
        legacy_terms = Counter(
            {
                "Spamming": 1,
                "DPS": 3,
                "E0": 1,
                "E2": 1,
                "E3": 1,
                "GDoll": 1,
            }
        )
        self.assertFalse(legacy_terms - rendered_terms)
        self.assertIn(
            '<ul class="awakener-roles" aria-label="Roles and mechanics">',
            guide_html,
        )
        self.assertIn('id="overview"', guide_html)
        self.assertRegex(
            guide_html,
            r'<img src="/images/covenants/scarlet-embrace\.png" alt=""',
        )
        self.assertRegex(
            guide_html,
            r'<img src="/images/awakeners/chaos/gdoll--mini\.png" alt=""',
        )

    def test_standalone_guides_do_not_link_back_to_legacy_fragments(self) -> None:
        guide_root = ROOT / "lib" / "handbook" / "awakeners"
        for guide in guide_root.glob("*/*.md"):
            with self.subTest(guide=guide.relative_to(ROOT)):
                self.assertNotIn(
                    "/handbook/awakeners/#", guide.read_text(encoding="utf-8")
                )
