from __future__ import annotations

import unittest
from tempfile import TemporaryDirectory
from pathlib import Path
from unittest.mock import patch

from markdown import Markdown

from mythag_site import teams
from mythag_site.teams import (
    TeamExtension,
    TeamFence,
    TeamValidationError,
    parse_team,
    render_team,
    resolve_team,
    scan_team_fences,
)
from zensical.extensions.context import ContextExtension, Page


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
    covenant: steppenwolf
    wheels: [gift-of-decay, cursed-binding]
  - awakener: nymphaea
    covenant: life-drain
    wheels: [merciful-nurturing, moment-of-reunion]
  - awakener: gdoll
    covenant: dream-of-medicine
    wheels: [manikin-of-oblivion, elevated-focus]
  - awakener: faint
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
        self.assertIn('/images/steppenwolf.png', rendered)
        self.assertEqual(rendered.count('class="mythag-team__member"'), 4)

    def test_unknown_id_reports_physical_location_and_suggestion(self) -> None:
        source = VALID_TEAM.replace("cursed-binding", "cursed-bindng")
        with self.assertRaises(TeamValidationError) as caught:
            parse_team(TeamFence(source, 310), Path("guide.md"), ASSETS)

        self.assertEqual(
            str(caught.exception.issues[0]),
            "guide.md:316:29: members[0].wheels[1]: "
            "unknown wheel ID 'cursed-bindng'; did you mean 'cursed-binding'?",
        )

    def test_scanner_ignores_examples_and_rejects_unclosed_teams(self) -> None:
        example = ["````markdown", "```team", VALID_TEAM, "```", "````"]
        self.assertFalse(
            any(
                isinstance(segment, TeamFence)
                for segment in scan_team_fences(example, Path("guide.md"))
            )
        )

        with self.assertRaisesRegex(TeamValidationError, "missing closing"):
            scan_team_fences(["before", "```team", VALID_TEAM], Path("guide.md"))

    def test_extension_renders_at_the_authored_position_in_page_prose(self) -> None:
        with TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "lib" / "handbook" / "awakeners" / "example.md"
            source.parent.mkdir(parents=True)
            body = f"Before.\n\n```team\n{VALID_TEAM}```\n\nAfter.\n"
            source.write_text(body, encoding="utf-8")
            config = {"extra": {"content_assets": ASSETS}}
            page = Page(url="/example/", path="handbook/awakeners/example.md")
            markdown = Markdown(
                extensions=[
                    ContextExtension(page=page, config=config),
                    TeamExtension(),
                ]
            )

            with patch.object(teams, "ROOT", root):
                rendered = markdown.convert(body)

        self.assertLess(rendered.index("Before."), rendered.index("mythag-team"))
        self.assertLess(rendered.index("mythag-team"), rendered.index("After."))


if __name__ == "__main__":
    unittest.main()
