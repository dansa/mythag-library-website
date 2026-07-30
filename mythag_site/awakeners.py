"""Validate Awakener pages and prepare their generated Zensical inputs."""

from __future__ import annotations

import difflib
import json
import re
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
GUIDES_ROOT = ROOT / "lib" / "handbook" / "awakeners"
SOURCE_IMAGES = ROOT / "lib" / "images"
CONTENT_ROOT = ROOT / "content"
SOURCE_CONFIG = ROOT / "zensical.toml"
GENERATED_CONFIG = ROOT / ".zensical.generated.toml"
NAV_MARKER = "@mythag-awakener-nav"
TEMPLATE_NAME = "awakeners/awakener.html"

REALM_FAMILIES: tuple[tuple[str, tuple[tuple[str, str | None], ...]], ...] = (
    ("Chaos", (("chaos", None),)),
    (
        "Aequor",
        (("aequor", None), ("benthos-aequor", "Benthos Aequor")),
    ),
    ("Caro", (("caro", None), ("propagation-caro", "Propagation Caro"))),
    ("Ultra", (("ultra", None), ("singularity-ultra", "Singularity Ultra"))),
)
KNOWN_REALMS = {
    realm for _, realms in REALM_FAMILIES for realm, _ in realms
}
ALLOWED_AWAKENER_FIELDS = {
    "tagline",
    "roles",
    "ranks",
    "stopping_points",
    "builds",
    "suggested_posses",
    "suggested_posses_note",
    "works_well_with",
    "works_well_with_note",
}
TIER_STYLE_NAMES = {
    "S": "s",
    "A": "a",
    "B+": "b-plus",
    "B": "b",
    "C+": "c-plus",
    "C": "c",
    "D": "d",
    "F": "f",
}
ALLOWED_TIERS = set(TIER_STYLE_NAMES)
CONTENT_CATEGORIES = ("awakeners", "covenants", "wheels", "posses")
CONTENT_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(?P<yaml>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)
LAYOUT_MARKUP = re.compile(
    r"{{|{%|<!--|<![A-Za-z]|</?[A-Za-z][A-Za-z0-9-]*(?:\s[^<>]*|/?)>"
)


@dataclass(frozen=True)
class ValidationIssue:
    path: Path
    field: str
    message: str

    def __str__(self) -> str:
        location = self.path.as_posix()
        if self.field:
            location = f"{location}: {self.field}"
        return f"{location}: {self.message}"


@dataclass(frozen=True)
class Rank:
    tier: str
    note: str | None


@dataclass(frozen=True)
class Recommendation:
    content_id: str
    note: str | None


@dataclass(frozen=True)
class WheelGroups:
    early_game: tuple[Recommendation, ...]
    astral_reign: tuple[Recommendation, ...]


@dataclass(frozen=True)
class Build:
    name: str
    covenants: tuple[str, ...]
    covenants_note: str | None
    wheels: WheelGroups


@dataclass(frozen=True)
class Awakener:
    tagline: str
    roles: tuple[str, ...]
    dps_ranks: tuple[Rank, ...]
    support_ranks: tuple[Rank, ...]
    stopping_points: tuple[str, ...]
    builds: tuple[Build, ...]
    suggested_posses: tuple[Recommendation, ...]
    suggested_posses_note: str | None
    works_well_with: tuple[str, ...]
    works_well_with_note: str | None


@dataclass(frozen=True)
class Guide:
    path: Path
    title: str
    slug: str
    realm: str
    awakener: Awakener


class AwakenerValidationError(Exception):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        message = "Awakener validation failed:\n" + "\n".join(
            f"- {issue}" for issue in issues
        )
        super().__init__(message)


def _issue(
    issues: list[ValidationIssue], path: Path, field: str, message: str
) -> None:
    issues.append(ValidationIssue(path, field, message))


def _non_empty_string(
    value: Any,
    issues: list[ValidationIssue],
    path: Path,
    field: str,
) -> str | None:
    if not isinstance(value, str) or not value.strip():
        _issue(issues, path, field, "expected a non-empty string")
        return None
    return value.strip()


def _content_id(
    value: Any,
    issues: list[ValidationIssue],
    path: Path,
    field: str,
) -> str | None:
    content_id = _non_empty_string(value, issues, path, field)
    if content_id is not None and CONTENT_ID.fullmatch(content_id) is None:
        _issue(
            issues,
            path,
            field,
            "expected a lowercase kebab-case content ID such as burial-grounds-sighs",
        )
        return None
    return content_id


def _parsed_string_list(
    value: Any,
    issues: list[ValidationIssue],
    path: Path,
    field: str,
    parser: Callable[[Any, list[ValidationIssue], Path, str], str | None],
    *,
    required: bool = True,
) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or (required and not value):
        expectation = "a non-empty list" if required else "a list"
        _issue(issues, path, field, f"expected {expectation}")
        return []

    result: list[str] = []
    for index, item in enumerate(value):
        parsed = parser(item, issues, path, f"{field}[{index}]")
        if parsed is not None:
            result.append(parsed)
    return result


def _string_list(
    value: Any,
    issues: list[ValidationIssue],
    path: Path,
    field: str,
    *,
    required: bool = True,
) -> list[str]:
    return _parsed_string_list(
        value, issues, path, field, _non_empty_string, required=required
    )


def _content_id_list(
    value: Any,
    issues: list[ValidationIssue],
    path: Path,
    field: str,
    *,
    required: bool = True,
) -> list[str]:
    return _parsed_string_list(
        value, issues, path, field, _content_id, required=required
    )


def _validate_rank_entries(
    value: Any,
    issues: list[ValidationIssue],
    path: Path,
    field: str,
) -> list[Rank]:
    if not isinstance(value, list) or not value:
        _issue(issues, path, field, "expected a non-empty list")
        return []
    ranks: list[Rank] = []
    for index, item in enumerate(value):
        item_field = f"{field}[{index}]"
        if not isinstance(item, dict):
            _issue(issues, path, item_field, "expected a mapping")
            continue
        unknown = set(item) - {"tier", "note"}
        for key in sorted(unknown):
            _issue(issues, path, f"{item_field}.{key}", "unknown field")
        tier = _non_empty_string(item.get("tier"), issues, path, f"{item_field}.tier")
        if tier is not None and tier not in ALLOWED_TIERS:
            _issue(
                issues,
                path,
                f"{item_field}.tier",
                f"expected one of {', '.join(sorted(ALLOWED_TIERS))}",
            )
        note = None
        if "note" in item:
            note = _non_empty_string(item["note"], issues, path, f"{item_field}.note")
        if tier is not None:
            ranks.append(Rank(tier, note))
    return ranks


def _validate_content_recommendations(
    value: Any,
    issues: list[ValidationIssue],
    path: Path,
    field: str,
    *,
    required: bool = True,
) -> list[Recommendation]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or (required and not value):
        expectation = "a non-empty list" if required else "a list"
        _issue(issues, path, field, f"expected {expectation}")
        return []

    recommendations: list[Recommendation] = []
    for index, item in enumerate(value):
        item_field = f"{field}[{index}]"
        if not isinstance(item, dict):
            _issue(issues, path, item_field, "expected a mapping with an id")
            continue
        unknown = set(item) - {"id", "note"}
        for key in sorted(unknown):
            _issue(issues, path, f"{item_field}.{key}", "unknown field")
        content_id = _content_id(item.get("id"), issues, path, f"{item_field}.id")
        note = None
        if "note" in item:
            note = _non_empty_string(item["note"], issues, path, f"{item_field}.note")
        if content_id is not None:
            recommendations.append(Recommendation(content_id, note))
    return recommendations


def _validate_builds(
    value: Any,
    issues: list[ValidationIssue],
    path: Path,
) -> list[Build]:
    if value is None:
        return []
    if not isinstance(value, list):
        _issue(issues, path, "awakener.builds", "expected a list")
        return []

    builds: list[Build] = []
    for index, build in enumerate(value):
        field = f"awakener.builds[{index}]"
        if not isinstance(build, dict):
            _issue(issues, path, field, "expected a mapping")
            continue
        unknown = set(build) - {"name", "covenants", "covenants_note", "wheels"}
        for key in sorted(unknown):
            _issue(issues, path, f"{field}.{key}", "unknown field")
        name = _non_empty_string(build.get("name"), issues, path, f"{field}.name")
        covenants = _content_id_list(
            build.get("covenants"), issues, path, f"{field}.covenants"
        )
        covenants_note = None
        if "covenants_note" in build:
            covenants_note = _non_empty_string(
                build["covenants_note"], issues, path, f"{field}.covenants_note"
            )

        wheel_groups = build.get("wheels")
        if not isinstance(wheel_groups, dict):
            _issue(issues, path, f"{field}.wheels", "expected a mapping")
            wheel_groups = {}
        unknown_groups = set(wheel_groups) - {"early_game", "astral_reign"}
        for key in sorted(unknown_groups):
            _issue(issues, path, f"{field}.wheels.{key}", "unknown field")
        early_game = _validate_content_recommendations(
            wheel_groups.get("early_game"),
            issues,
            path,
            f"{field}.wheels.early_game",
        )
        astral_reign = _validate_content_recommendations(
            wheel_groups.get("astral_reign"),
            issues,
            path,
            f"{field}.wheels.astral_reign",
        )
        if name is not None:
            builds.append(
                Build(
                    name,
                    tuple(covenants),
                    covenants_note,
                    WheelGroups(tuple(early_game), tuple(astral_reign)),
                )
            )
    return builds


def _parse_guide(path: Path, issues: list[ValidationIssue]) -> Guide | None:
    relative = path.relative_to(ROOT)
    text = path.read_text(encoding="utf-8")
    match = FRONT_MATTER.match(text)
    if match is None:
        _issue(issues, relative, "", "missing leading YAML front matter")
        return None
    try:
        meta = yaml.safe_load(match.group("yaml"))
    except yaml.MarkedYAMLError as error:
        mark = error.problem_mark
        location = "front matter"
        if mark is not None:
            location = f"front matter line {mark.line + 2}, column {mark.column + 1}"
        _issue(issues, relative, location, error.problem or "invalid YAML")
        return None
    if not isinstance(meta, dict):
        _issue(issues, relative, "front matter", "expected a mapping")
        return None

    body = text[match.end() :]
    if LAYOUT_MARKUP.search(body):
        _issue(
            issues,
            relative,
            "content",
            "use ordinary Markdown; HTML and template expressions are not allowed",
        )

    title = _non_empty_string(meta.get("title"), issues, relative, "title")
    _non_empty_string(meta.get("description"), issues, relative, "description")
    template = _non_empty_string(meta.get("template"), issues, relative, "template")
    if template is not None and template != TEMPLATE_NAME:
        _issue(issues, relative, "template", f"expected {TEMPLATE_NAME!r}")

    awakener = meta.get("awakener")
    if not isinstance(awakener, dict):
        _issue(issues, relative, "awakener", "expected a mapping")
        return None
    for key in sorted(set(awakener) - ALLOWED_AWAKENER_FIELDS):
        _issue(issues, relative, f"awakener.{key}", "unknown field")

    tagline = _non_empty_string(
        awakener.get("tagline"), issues, relative, "awakener.tagline"
    )
    roles = _string_list(awakener.get("roles"), issues, relative, "awakener.roles")

    ranks = awakener.get("ranks")
    dps_ranks: list[Rank] = []
    support_ranks: list[Rank] = []
    if not isinstance(ranks, dict) or not ranks:
        _issue(issues, relative, "awakener.ranks", "expected a non-empty mapping")
    else:
        for key in sorted(set(ranks) - {"dps", "support"}):
            _issue(issues, relative, f"awakener.ranks.{key}", "unknown rank")
        for key in ("dps", "support"):
            if key in ranks:
                parsed_ranks = _validate_rank_entries(
                    ranks[key], issues, relative, f"awakener.ranks.{key}"
                )
                if key == "dps":
                    dps_ranks = parsed_ranks
                else:
                    support_ranks = parsed_ranks

    stopping_points = _string_list(
        awakener.get("stopping_points"),
        issues,
        relative,
        "awakener.stopping_points",
    )
    builds = _validate_builds(awakener.get("builds"), issues, relative)
    suggested_posses = _validate_content_recommendations(
        awakener.get("suggested_posses"),
        issues,
        relative,
        "awakener.suggested_posses",
        required=False,
    )
    suggested_posses_note = None
    if "suggested_posses_note" in awakener:
        suggested_posses_note = _non_empty_string(
            awakener["suggested_posses_note"],
            issues,
            relative,
            "awakener.suggested_posses_note",
        )
    works_well_with = _content_id_list(
        awakener.get("works_well_with"),
        issues,
        relative,
        "awakener.works_well_with",
        required=False,
    )
    works_well_with_note = None
    if "works_well_with_note" in awakener:
        works_well_with_note = _non_empty_string(
            awakener["works_well_with_note"],
            issues,
            relative,
            "awakener.works_well_with_note",
        )

    realm = path.parent.name
    if realm not in KNOWN_REALMS:
        _issue(issues, relative, "", f"unknown realm directory {realm!r}")
    if title is None:
        return None
    slug = path.stem
    if CONTENT_ID.fullmatch(slug) is None:
        _issue(
            issues,
            relative,
            "",
            "expected a lowercase kebab-case filename",
        )
    return Guide(
        relative,
        title,
        slug,
        realm,
        Awakener(
            tagline or "",
            tuple(roles),
            tuple(dps_ranks),
            tuple(support_ranks),
            tuple(stopping_points),
            tuple(builds),
            tuple(suggested_posses),
            suggested_posses_note,
            tuple(works_well_with),
            works_well_with_note,
        ),
    )


def load_guides() -> tuple[list[Guide], list[ValidationIssue]]:
    issues: list[ValidationIssue] = []
    guides = [
        guide
        for path in sorted(GUIDES_ROOT.glob("*/*.md"))
        if (guide := _parse_guide(path, issues)) is not None
    ]

    by_title: dict[str, Guide] = {}
    by_slug: dict[str, Guide] = {}
    for guide in guides:
        for key, index, label in (
            (guide.title.casefold(), by_title, "title"),
            (guide.slug, by_slug, "slug"),
        ):
            if key in index:
                _issue(
                    issues,
                    guide.path,
                    "title",
                    f"duplicate {label}; first used by {index[key].path.as_posix()}",
                )
            else:
                index[key] = guide
    return guides, issues


def load_content_catalog(
    issues: list[ValidationIssue],
) -> dict[str, dict[str, str]]:
    catalog = {category: {} for category in CONTENT_CATEGORIES}
    for category in CONTENT_CATEGORIES:
        path = CONTENT_ROOT / f"{category}.yaml"
        relative = path.relative_to(ROOT)
        if not path.is_file():
            _issue(issues, relative, "", "missing content catalog")
            continue
        try:
            entries = yaml.safe_load(path.read_text(encoding="utf-8"))
        except yaml.MarkedYAMLError as error:
            mark = error.problem_mark
            field = ""
            if mark is not None:
                field = f"line {mark.line + 1}, column {mark.column + 1}"
            _issue(issues, relative, field, error.problem or "invalid YAML")
            continue
        if not isinstance(entries, dict):
            _issue(issues, relative, "", "expected a mapping of IDs to labels")
            continue
        for content_id, raw_label in entries.items():
            field = str(content_id)
            parsed_id = _content_id(content_id, issues, relative, field)
            label = _non_empty_string(raw_label, issues, relative, field)
            if parsed_id is not None and label is not None:
                catalog[category][parsed_id] = label
    return catalog


def _catalog_label(
    content_catalog: dict[str, dict[str, str]],
    category: str,
    content_id: str,
    issues: list[ValidationIssue],
    source_path: Path,
    field: str,
) -> str | None:
    label = content_catalog[category].get(content_id)
    if label is not None:
        return label

    message = f"unknown {category.removesuffix('s')} ID {content_id!r}"
    suggestions = difflib.get_close_matches(
        content_id, content_catalog[category], n=1, cutoff=0.6
    )
    if suggestions:
        message += f"; did you mean {suggestions[0]!r}?"
    _issue(issues, source_path, field, message)
    return None


def _find_unique_asset(
    pattern: str,
    issues: list[ValidationIssue],
    source_path: Path,
    field: str,
) -> Path | None:
    matches = sorted(SOURCE_IMAGES.glob(pattern))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        _issue(issues, source_path, field, f"no asset matched {pattern!r}")
    else:
        _issue(issues, source_path, field, f"multiple assets matched {pattern!r}")
    return None


def _site_url(path: Path) -> str:
    return "/" + path.relative_to(ROOT / "lib").as_posix()


def validate_guide_titles(
    guides: list[Guide],
    content_catalog: dict[str, dict[str, str]],
    issues: list[ValidationIssue],
) -> None:
    for guide in guides:
        label = _catalog_label(
            content_catalog, "awakeners", guide.slug, issues, guide.path, "title"
        )
        if label is not None and guide.title != label:
            _issue(issues, guide.path, "title", f"expected catalog label {label!r}")


def build_asset_catalog(
    guides: list[Guide],
    content_catalog: dict[str, dict[str, str]],
    issues: list[ValidationIssue],
) -> dict[str, dict[str, dict[str, str]]]:
    catalog: dict[str, dict[str, dict[str, str]]] = {
        "portraits": {},
        "awakeners": {},
        "covenants": {},
        "wheels": {},
        "posses": {},
    }
    standalone = {guide.slug: guide for guide in guides}

    def find_awakener_assets(
        guide: Guide, content_id: str, field: str
    ) -> tuple[Path, Path] | None:
        full = _find_unique_asset(
            f"awakeners/*/{content_id}.png", issues, guide.path, field
        )
        mini = _find_unique_asset(
            f"awakeners/*/{content_id}--mini.png", issues, guide.path, field
        )
        if full is None or mini is None:
            return None
        return full, mini

    def add_portrait(guide: Guide) -> None:
        assets = find_awakener_assets(guide, guide.slug, "title")
        if assets is None:
            return
        full, mini = assets
        catalog["portraits"][guide.title] = {
            "image": _site_url(full),
            "mini": _site_url(mini),
        }

    def add_awakeners(guide: Guide, content_ids: list[str], field: str) -> None:
        for content_id in content_ids:
            if content_id in catalog["awakeners"]:
                continue
            label = _catalog_label(
                content_catalog,
                "awakeners",
                content_id,
                issues,
                guide.path,
                field,
            )
            if label is None:
                continue
            assets = find_awakener_assets(guide, content_id, field)
            if assets is None:
                continue
            full, mini = assets
            target = standalone.get(content_id)
            url = (
                f"/handbook/awakeners/{target.realm}/{target.slug}/"
                if target is not None
                else f"/handbook/awakeners/#{content_id}"
            )
            catalog["awakeners"][content_id] = {
                "label": label,
                "image": _site_url(full),
                "mini": _site_url(mini),
                "url": url,
            }

    for guide in guides:
        awakener = guide.awakener
        add_portrait(guide)
        add_awakeners(
            guide,
            list(awakener.works_well_with),
            "awakener.works_well_with",
        )

        for build_index, build in enumerate(awakener.builds):
            for covenant in build.covenants:
                if covenant in catalog["covenants"]:
                    continue
                field = f"awakener.builds[{build_index}].covenants"
                label = _catalog_label(
                    content_catalog, "covenants", covenant, issues, guide.path, field
                )
                if label is None:
                    continue
                full = SOURCE_IMAGES / "covenants" / f"{covenant}.png"
                icon = SOURCE_IMAGES / "covenants" / f"{covenant}--icon.png"
                if not full.is_file():
                    _issue(issues, guide.path, field, f"missing {full.relative_to(ROOT)}")
                if not icon.is_file():
                    _issue(issues, guide.path, field, f"missing {icon.relative_to(ROOT)}")
                if full.is_file() and icon.is_file():
                    catalog["covenants"][covenant] = {
                        "label": label,
                        "image": _site_url(full),
                        "icon": _site_url(icon),
                        "url": f"/handbook/team#{covenant}",
                    }
            for group, recommendations in (
                ("early_game", build.wheels.early_game),
                ("astral_reign", build.wheels.astral_reign),
            ):
                for item_index, recommendation in enumerate(recommendations):
                    content_id = recommendation.content_id
                    if content_id in catalog["wheels"]:
                        continue
                    field = (
                        f"awakener.builds[{build_index}].wheels.{group}"
                        f"[{item_index}].id"
                    )
                    label = _catalog_label(
                        content_catalog,
                        "wheels",
                        content_id,
                        issues,
                        guide.path,
                        field,
                    )
                    if label is None:
                        continue
                    image = SOURCE_IMAGES / "wheels" / f"{content_id}.png"
                    if not image.is_file():
                        _issue(issues, guide.path, field, f"missing {image.relative_to(ROOT)}")
                    else:
                        catalog["wheels"][content_id] = {
                            "label": label,
                            "image": _site_url(image),
                        }

        for posse_index, posse in enumerate(awakener.suggested_posses):
            content_id = posse.content_id
            if content_id in catalog["posses"]:
                continue
            field = f"awakener.suggested_posses[{posse_index}].id"
            label = _catalog_label(
                content_catalog,
                "posses",
                content_id,
                issues,
                guide.path,
                field,
            )
            if label is None:
                continue
            image = SOURCE_IMAGES / "posses" / f"{content_id}.png"
            if not image.is_file():
                _issue(issues, guide.path, field, f"missing {image.relative_to(ROOT)}")
            else:
                catalog["posses"][content_id] = {
                    "label": label,
                    "image": _site_url(image),
                }
    return catalog


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_nav(guides: list[Guide], indent: str) -> str:
    grouped = {realm: [] for realm in KNOWN_REALMS}
    for guide in guides:
        grouped.setdefault(guide.realm, []).append(guide)

    lines = [f'{indent}{{ "Awakener Guides" = [', f'{indent}  "handbook/awakeners.md",']
    for family_name, realms in REALM_FAMILIES:
        if not any(grouped.get(realm) for realm, _ in realms):
            continue
        lines.append(f'{indent}  {{ {_toml_string(family_name)} = [')
        for realm, subgroup_name in realms:
            realm_guides = sorted(
                grouped.get(realm, []),
                key=lambda guide: (guide.title.casefold(), guide.slug),
            )
            if not realm_guides:
                continue
            if subgroup_name is not None:
                lines.append(f'{indent}    {{ {_toml_string(subgroup_name)} = [')
            guide_indent = indent + ("      " if subgroup_name is not None else "    ")
            lines.extend(
                f'{guide_indent}{_toml_string(guide.path.relative_to("lib").as_posix())},'
                for guide in realm_guides
            )
            if subgroup_name is not None:
                lines.append(f"{indent}    ]}},")
        lines.append(f"{indent}  ]}},")
    lines.append(f"{indent}]}},")
    return "\n".join(lines)


def _render_catalog(catalog: dict[str, dict[str, dict[str, str]]]) -> str:
    lines = ["", "# Generated by mythag_site.awakeners; do not edit this file."]
    for category in ("portraits", "awakeners", "covenants", "wheels", "posses"):
        lines.append(f"[project.extra.awakener_assets.{category}]")
        for name, values in sorted(catalog[category].items(), key=lambda item: item[0].casefold()):
            rendered = ", ".join(
                f"{key} = {_toml_string(value)}" for key, value in values.items()
            )
            lines.append(f"{_toml_string(name)} = {{ {rendered} }}")
        lines.append("")
    return "\n".join(lines)


def prepare_awakeners(*, write_config: bool = True) -> list[Guide]:
    guides, issues = load_guides()
    content_catalog = load_content_catalog(issues)
    validate_guide_titles(guides, content_catalog, issues)
    catalog = build_asset_catalog(guides, content_catalog, issues)
    if issues:
        raise AwakenerValidationError(issues)

    if write_config:
        source = SOURCE_CONFIG.read_text(encoding="utf-8")
        marker = re.compile(
            rf"^(?P<indent>[ \t]*).*{re.escape(NAV_MARKER)}.*$", re.MULTILINE
        )
        match = marker.search(source)
        if match is None:
            raise AwakenerValidationError(
                [ValidationIssue(SOURCE_CONFIG.relative_to(ROOT), "", f"missing {NAV_MARKER}")]
            )
        generated = marker.sub(
            _render_nav(guides, match.group("indent")), source, count=1
        ).rstrip()
        generated += "\n" + _render_catalog(catalog)
        temporary = GENERATED_CONFIG.with_suffix(".tmp.toml")
        temporary.write_text(generated, encoding="utf-8", newline="\n")
        temporary.replace(GENERATED_CONFIG)
    return guides


def check_main() -> None:
    try:
        guides = prepare_awakeners(write_config=False)
    except AwakenerValidationError as error:
        raise SystemExit(str(error)) from error
    print(f"Awakener content: {len(guides)} guides valid")


def serve_main() -> None:
    try:
        guides = prepare_awakeners()
    except AwakenerValidationError as error:
        raise SystemExit(str(error)) from error
    zensical = shutil.which("zensical")
    if zensical is None:
        raise SystemExit("zensical must be available on PATH")
    print(
        f"Awakener content: {len(guides)} guides valid; "
        "restart preview after adding, renaming, or removing a guide"
    )
    subprocess.run(
        [zensical, "serve", "--config-file", str(GENERATED_CONFIG)],
        cwd=ROOT,
        check=True,
    )


if __name__ == "__main__":
    check_main()
