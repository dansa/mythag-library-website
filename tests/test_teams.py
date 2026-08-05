from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from mythag_site import team_extension
from mythag_site.awakeners import GENERATED_CONFIG, prepare_awakeners
from mythag_site.team_extension import scan_team_fences
from mythag_site.teams import (
    TeamFence,
    TeamValidationError,
    parse_team,
    render_team,
    resolve_team,
)


def asset(label: str, image: str, url: str | None = None) -> dict[str, str]:
    value = {"label": label, "image": image}
    if url is not None:
        value["url"] = url
    return value


ASSETS = {
    "portraits": {},
    "awakeners": {
        "xu": asset("Xu", "/images/xu.png", "/awakeners/xu/"),
        "nymphaea": asset("Nymphaea", "/images/nymphaea.png", "/awakeners/nymphaea/"),
        "gdoll": asset("GDoll", "/images/gdoll.png", "/awakeners/gdoll/"),
        "faint": asset("Faint", "/images/faint.png", "/awakeners/faint/"),
    },
    "covenants": {
        covenant: {
            **asset(covenant.replace("-", " ").title(), f"/images/{covenant}.png", f"/team#{covenant}"),
            "icon": f"/images/{covenant}--icon.png",
        }
        for covenant in (
            "steppenwolf",
            "life-drain",
            "dream-of-medicine",
            "burial-grounds-sighs",
        )
    },
    "wheels": {
        wheel: asset(wheel.replace("-", " ").title(), f"/images/{wheel}.png")
        for wheel in (
            "gift-of-decay",
            "cursed-binding",
            "merciful-nurturing",
            "moment-of-reunion",
            "manikin-of-oblivion",
            "elevated-focus",
            "dusk-and-dawn",
            "cloaked-in-the-night",
        )
    },
    "posses": {
        "plague-of-illusions": asset(
            "Plague of Illusions", "/images/plague-of-illusions.png"
        )
    },
}

VALID_TEAM = """\
name: Xu Poison
posse: plague-of-illusions
members:
  - awakener: xu
    archetype: dps
    covenant: steppenwolf
    wheels: [gift-of-decay, cursed-binding]
  - awakener: nymphaea
    archetype: support
    covenant: life-drain
    wheels: [merciful-nurturing, moment-of-reunion]
  - awakener: gdoll
    archetype: support
    covenant: dream-of-medicine
    wheels: [manikin-of-oblivion, elevated-focus]
  - awakener: faint
    archetype: tank
    covenant: burial-grounds-sighs
    wheels: [dusk-and-dawn, cloaked-in-the-night]
"""


class TeamTests(unittest.TestCase):
    def test_valid_team_resolves_catalog_data_and_escapes_author_text(self) -> None:
        spec = parse_team(
            TeamFence(VALID_TEAM.replace("Xu Poison", "Xu & Friends"), 10),
            Path("guide.md"),
            ASSETS,
        )
        rendered = render_team(resolve_team(spec, ASSETS))

        self.assertIn("Xu &amp; Friends", rendered)
        self.assertIn('/awakeners/xu/', rendered)
        self.assertIn('/images/steppenwolf--icon.png', rendered)
        self.assertIn('title="Xu"', rendered)
        self.assertIn('title="Steppenwolf"', rendered)
        self.assertIn('title="Gift Of Decay"', rendered)
        self.assertEqual(rendered.count('title="Gift Of Decay"'), 1)
        self.assertEqual(rendered.count('<li class="mythag-team__member'), 4)

    def test_accepts_optional_team_narrative_fields(self) -> None:
        source = (
            VALID_TEAM.replace(
                "posse: plague-of-illusions",
                "summary: A poison team\nposse: plague-of-illusions",
            )
            .replace(
                "    archetype: dps\n",
                "    archetype: dps\n    role: Poison / DPS\n    note: Applies poison\n",
            )
        )

        spec = parse_team(TeamFence(source, 10), Path("guide.md"), ASSETS)
        rendered = render_team(resolve_team(spec, ASSETS))

        self.assertEqual(spec.summary, "A poison team")
        self.assertEqual(spec.members[0].archetype, "dps")
        self.assertEqual(spec.members[0].role, "Poison / DPS")
        self.assertEqual(spec.members[0].note, "Applies poison")
        self.assertIn("A poison team", rendered)
        self.assertIn('data-archetype="dps"', rendered)
        self.assertIn("Poison / DPS", rendered)
        self.assertIn("Applies poison", rendered)

    def test_rejects_unknown_team_archetype(self) -> None:
        source = VALID_TEAM.replace(
            "    archetype: dps\n",
            "    archetype: striker\n",
        )

        with self.assertRaises(TeamValidationError) as caught:
            parse_team(TeamFence(source, 10), Path("guide.md"), ASSETS)

        self.assertEqual(
            str(caught.exception.issues[0]),
            "guide.md:15:16: members[0].archetype: "
            "expected one of: dps, support, tank",
        )

    def test_rejects_missing_team_archetype(self) -> None:
        source = VALID_TEAM.replace("    archetype: dps\n", "", 1)

        with self.assertRaises(TeamValidationError) as caught:
            parse_team(TeamFence(source, 10), Path("guide.md"), ASSETS)

        self.assertEqual(
            str(caught.exception.issues[0]),
            "guide.md:14:5: members[0].archetype: missing required field",
        )

    def test_unknown_id_reports_physical_location_and_suggestion(self) -> None:
        source = VALID_TEAM.replace("cursed-binding", "cursed-bindng")
        with self.assertRaises(TeamValidationError) as caught:
            parse_team(TeamFence(source, 310), Path("guide.md"), ASSETS)

        self.assertEqual(
            str(caught.exception.issues[0]),
            "guide.md:317:29: members[0].wheels[1]: "
            "unknown wheel ID 'cursed-bindng'; did you mean 'cursed-binding'?",
        )

    def test_scanner_ignores_examples_and_rejects_unclosed_teams(self) -> None:
        example = ["  ````markdown", "  ```team", VALID_TEAM, "  ```", "  ````"]
        self.assertFalse(
            any(
                isinstance(segment, TeamFence)
                for segment in scan_team_fences(example, Path("guide.md"))
            )
        )

        with self.assertRaises(TeamValidationError) as caught:
            scan_team_fences(
                ["before", "```team", VALID_TEAM],
                Path("guide.md"),
                line_offset=4,
            )
        self.assertEqual(caught.exception.issues[0].line, 6)

    def test_scanner_accepts_team_fence_trailing_whitespace(self) -> None:
        segments = scan_team_fences(
            ["   ```team   ", *VALID_TEAM.splitlines(), "   ```   "],
            Path("guide.md"),
        )

        team_segments = [segment for segment in segments if isinstance(segment, TeamFence)]
        self.assertEqual(len(team_segments), 1)
        self.assertIn("name: Xu Poison", team_segments[0].source)

    def test_scanner_rejects_nested_team_fence(self) -> None:
        for opener in ("    ```team", "\t```team"):
            with self.subTest(opener=repr(opener)):
                with self.assertRaises(TeamValidationError) as caught:
                    scan_team_fences(
                        [opener, *VALID_TEAM.splitlines(), "    ```"],
                        Path("guide.md"),
                    )

                self.assertEqual(
                    str(caught.exception.issues[0]),
                    "guide.md:1:1: team: team blocks must be standalone top-level Markdown; "
                    "nested lists, blockquotes, admonitions, and tabs are not supported",
                )

    def test_zensical_renders_frontmatter_page_at_the_authored_position(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "lib" / "handbook" / "awakeners" / "example.md"
            source.parent.mkdir(parents=True)
            document = (
                "---\ntitle: Example team\n---\n\n"
                f"Before.\n\n```team   \n{VALID_TEAM}```   \n\nAfter.\n"
            )
            source.write_text(document, encoding="utf-8")

            import zensical.config as zensical_config
            from zensical.markdown.render import render

            previous_config = zensical_config._CONFIG
            try:
                prepare_awakeners()
                zensical_config.parse_zensical_config(str(GENERATED_CONFIG))
                with patch.object(team_extension, "ROOT", root):
                    rendered = render(
                        document,
                        "handbook/awakeners/example.md",
                        "/handbook/awakeners/example/",
                    )["content"]
            finally:
                zensical_config._CONFIG = previous_config

        self.assertLess(rendered.index("Before."), rendered.index("mythag-team"))
        self.assertLess(rendered.index("mythag-team"), rendered.index("After."))


if __name__ == "__main__":
    unittest.main()
