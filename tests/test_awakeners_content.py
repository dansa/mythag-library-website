import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
GUIDE_HEADING = re.compile(
    r"^### .+? \{#(?P<anchor>[a-z0-9-]+) \.tier \.text-center\}$",
    re.MULTILINE,
)
REALM_HEADING = re.compile(
    r"^## .+? \{#(?P<anchor>[a-z0-9-]+)\}$",
    re.MULTILINE,
)
LEGACY_LINK = re.compile(
    r'^- \[[^]]+\]\(/handbook/awakeners/(?P<realm>[a-z-]+)/(?P<slug>[a-z0-9-]+)/\)'
    r'\{#(?P<anchor>[a-z0-9-]+)\}$',
    re.MULTILINE,
)
RAW_TAGS = ("div", "figure", "p", "section")


def awakener_sources() -> list[Path]:
    sources = [ROOT / "lib" / "handbook" / "awakeners.md"]
    include_root = ROOT / "includes" / "handbook" / "awakeners"
    if include_root.exists():
        sources.extend(sorted(include_root.rglob("*.md")))
    return sources


class AwakenerContentTests(unittest.TestCase):
    def test_guide_and_realm_anchors_are_explicit_and_unique(self) -> None:
        main = awakener_sources()[0].read_text(encoding="utf-8")
        realm_anchors = REALM_HEADING.findall(main)
        self.assertEqual(len(realm_anchors), len(set(realm_anchors)))

        guide_anchors: list[str] = []
        for source in awakener_sources():
            text = source.read_text(encoding="utf-8")
            tier_headings = re.findall(
                r"^### .+? \{[^}]*\.tier \.text-center\}$",
                text,
                re.MULTILINE,
            )
            explicit_headings = list(GUIDE_HEADING.finditer(text))
            self.assertEqual(
                len(tier_headings),
                len(explicit_headings),
                f"Every guide heading in {source.relative_to(ROOT)} needs an explicit anchor",
            )
            guide_anchors.extend(match.group("anchor") for match in explicit_headings)

        legacy_anchors = [match.group("anchor") for match in LEGACY_LINK.finditer(main)]
        guide_anchors.extend(legacy_anchors)

        self.assertTrue(guide_anchors)
        self.assertEqual(len(guide_anchors), len(set(guide_anchors)))

    def test_legacy_indexes_cover_every_standalone_guide(self) -> None:
        main = awakener_sources()[0].read_text(encoding="utf-8")
        guide_root = ROOT / "lib" / "handbook" / "awakeners"
        for realm_directory in guide_root.iterdir():
            if not realm_directory.is_dir():
                continue
            indexed = {
                match.group("slug")
                for match in LEGACY_LINK.finditer(main)
                if match.group("realm") == realm_directory.name
            }
            standalone = {path.stem for path in realm_directory.glob("*.md")}

            with self.subTest(realm=realm_directory.name):
                self.assertEqual(indexed, standalone)

    def test_raw_html_is_balanced_within_each_guide(self) -> None:
        for source in awakener_sources():
            text = source.read_text(encoding="utf-8")
            headings = list(GUIDE_HEADING.finditer(text))
            for index, heading in enumerate(headings):
                end = headings[index + 1].start() if index + 1 < len(headings) else len(text)
                guide = text[heading.start() : end]
                for tag in RAW_TAGS:
                    openings = len(re.findall(rf"<{tag}(?:\s|>)", guide))
                    closings = guide.count(f"</{tag}>")
                    self.assertEqual(
                        openings,
                        closings,
                        f"Unbalanced <{tag}> in {source.relative_to(ROOT)} at "
                        f"#{heading.group('anchor')}",
                    )
