"""Validate Awakener pages and prepare their generated Zensical inputs."""

from __future__ import annotations

import json
import re
import shutil
import subprocess
import unicodedata
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


ROOT = Path(__file__).resolve().parents[1]
GUIDES_ROOT = ROOT / "lib" / "handbook" / "awakeners"
SOURCE_IMAGES = ROOT / "lib" / "images"
SOURCE_CONFIG = ROOT / "zensical.toml"
GENERATED_CONFIG = ROOT / ".zensical.generated.toml"
NAV_MARKER = "@mythag-awakener-nav"
TEMPLATE_NAME = "awakeners/awakener.html"

REALMS: tuple[tuple[str, str], ...] = (
    ("chaos", "Chaos"),
    ("aequor", "Aequor"),
    ("benthos-aequor", "Benthos: Aequor"),
    ("caro", "Caro"),
    ("propagation-caro", "Propagation: Caro"),
    ("ultra", "Ultra"),
    ("singularity-ultra", "Singularity: Ultra"),
)
REALM_NAMES = dict(REALMS)
ALLOWED_AWAKENER_FIELDS = {
    "tagline",
    "roles",
    "ranks",
    "stopping_points",
    "builds",
    "suggested_posses",
    "works_well_with",
}
ALLOWED_TIERS = {"S", "A", "B", "C", "D"}
FRONT_MATTER = re.compile(r"\A---[ \t]*\r?\n(?P<yaml>.*?)\r?\n---[ \t]*(?:\r?\n|\Z)", re.DOTALL)


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
class Guide:
    path: Path
    title: str
    slug: str
    realm: str
    meta: dict[str, Any]


class AwakenerValidationError(Exception):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        message = "Awakener validation failed:\n" + "\n".join(
            f"- {issue}" for issue in issues
        )
        super().__init__(message)


def slugify(value: str) -> str:
    normalized = unicodedata.normalize("NFKD", value).casefold()
    normalized = normalized.replace("'", "").replace("’", "")
    return re.sub(r"[^a-z0-9]+", "-", normalized).strip("-")


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


def _string_list(
    value: Any,
    issues: list[ValidationIssue],
    path: Path,
    field: str,
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
        parsed = _non_empty_string(item, issues, path, f"{field}[{index}]")
        if parsed is not None:
            result.append(parsed)
    return result


def _validate_rank_entries(
    value: Any,
    issues: list[ValidationIssue],
    path: Path,
    field: str,
) -> None:
    if not isinstance(value, list) or not value:
        _issue(issues, path, field, "expected a non-empty list")
        return
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
        if "note" in item:
            _non_empty_string(item["note"], issues, path, f"{item_field}.note")


def _validate_named_items(
    value: Any,
    issues: list[ValidationIssue],
    path: Path,
    field: str,
    *,
    required: bool = True,
) -> list[str]:
    if value is None and not required:
        return []
    if not isinstance(value, list) or (required and not value):
        expectation = "a non-empty list" if required else "a list"
        _issue(issues, path, field, f"expected {expectation}")
        return []

    names: list[str] = []
    for index, item in enumerate(value):
        item_field = f"{field}[{index}]"
        if not isinstance(item, dict):
            _issue(issues, path, item_field, "expected a mapping with a name")
            continue
        unknown = set(item) - {"name", "note"}
        for key in sorted(unknown):
            _issue(issues, path, f"{item_field}.{key}", "unknown field")
        name = _non_empty_string(item.get("name"), issues, path, f"{item_field}.name")
        if name is not None:
            names.append(name)
        if "note" in item:
            _non_empty_string(item["note"], issues, path, f"{item_field}.note")
    return names


def _validate_builds(
    value: Any,
    issues: list[ValidationIssue],
    path: Path,
) -> tuple[list[str], list[str]]:
    if value is None:
        return [], []
    if not isinstance(value, list):
        _issue(issues, path, "awakener.builds", "expected a list")
        return [], []

    covenants: list[str] = []
    wheels: list[str] = []
    for index, build in enumerate(value):
        field = f"awakener.builds[{index}]"
        if not isinstance(build, dict):
            _issue(issues, path, field, "expected a mapping")
            continue
        unknown = set(build) - {"name", "covenants", "wheels"}
        for key in sorted(unknown):
            _issue(issues, path, f"{field}.{key}", "unknown field")
        _non_empty_string(build.get("name"), issues, path, f"{field}.name")
        covenants.extend(
            _string_list(
                build.get("covenants"), issues, path, f"{field}.covenants"
            )
        )

        wheel_groups = build.get("wheels")
        if not isinstance(wheel_groups, dict):
            _issue(issues, path, f"{field}.wheels", "expected a mapping")
            continue
        unknown_groups = set(wheel_groups) - {"early_game", "astral_reign"}
        for key in sorted(unknown_groups):
            _issue(issues, path, f"{field}.wheels.{key}", "unknown field")
        for group in ("early_game", "astral_reign"):
            wheels.extend(
                _validate_named_items(
                    wheel_groups.get(group),
                    issues,
                    path,
                    f"{field}.wheels.{group}",
                )
            )
    return covenants, wheels


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
    if re.search(r"<[/!A-Za-z]|{%|{{", body):
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

    _non_empty_string(awakener.get("tagline"), issues, relative, "awakener.tagline")
    _string_list(awakener.get("roles"), issues, relative, "awakener.roles")

    ranks = awakener.get("ranks")
    if not isinstance(ranks, dict) or not ranks:
        _issue(issues, relative, "awakener.ranks", "expected a non-empty mapping")
    else:
        for key in sorted(set(ranks) - {"dps", "support"}):
            _issue(issues, relative, f"awakener.ranks.{key}", "unknown rank")
        for key in ("dps", "support"):
            if key in ranks:
                _validate_rank_entries(
                    ranks[key], issues, relative, f"awakener.ranks.{key}"
                )

    _string_list(
        awakener.get("stopping_points"),
        issues,
        relative,
        "awakener.stopping_points",
    )
    _validate_builds(awakener.get("builds"), issues, relative)
    _validate_named_items(
        awakener.get("suggested_posses"),
        issues,
        relative,
        "awakener.suggested_posses",
        required=False,
    )
    _string_list(
        awakener.get("works_well_with"),
        issues,
        relative,
        "awakener.works_well_with",
        required=False,
    )

    realm = path.parent.name
    if realm not in REALM_NAMES:
        _issue(issues, relative, "", f"unknown realm directory {realm!r}")
    if title is None:
        return None
    slug = slugify(title.strip('"'))
    if path.stem != slug:
        _issue(
            issues,
            relative,
            "title",
            f"expected filename {slug}.md for this title",
        )
    return Guide(relative, title, slug, realm, meta)


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


def build_asset_catalog(
    guides: list[Guide], issues: list[ValidationIssue]
) -> dict[str, dict[str, dict[str, str]]]:
    catalog: dict[str, dict[str, dict[str, str]]] = {
        "awakeners": {},
        "covenants": {},
        "wheels": {},
        "posses": {},
    }
    standalone = {guide.title.casefold(): guide for guide in guides}

    def add_awakeners(guide: Guide, names: list[str], field: str) -> None:
        for name in names:
            if name in catalog["awakeners"]:
                continue
            slug = slugify(name.strip('"'))
            full = _find_unique_asset(
                f"awakeners/*/{slug}.png", issues, guide.path, field
            )
            mini = _find_unique_asset(
                f"awakeners/*/{slug}--mini.png", issues, guide.path, field
            )
            if full is None or mini is None:
                continue
            target = standalone.get(name.casefold())
            url = (
                f"/handbook/awakeners/{target.realm}/{target.slug}/"
                if target is not None
                else f"/handbook/awakeners/#{slug}"
            )
            catalog["awakeners"][name] = {
                "image": _site_url(full),
                "mini": _site_url(mini),
                "url": url,
            }

    for guide in guides:
        awakener = guide.meta["awakener"]
        add_awakeners(guide, [guide.title], "title")
        related = awakener.get("works_well_with")
        if isinstance(related, list):
            add_awakeners(
                guide,
                [item for item in related if isinstance(item, str)],
                "awakener.works_well_with",
            )

        for build_index, build in enumerate(awakener.get("builds") or []):
            if not isinstance(build, dict):
                continue
            for covenant in build.get("covenants") or []:
                if not isinstance(covenant, str) or covenant in catalog["covenants"]:
                    continue
                slug = slugify(covenant)
                full = SOURCE_IMAGES / "covenants" / f"{slug}.png"
                icon = SOURCE_IMAGES / "covenants" / f"{slug}--icon.png"
                field = f"awakener.builds[{build_index}].covenants"
                if not full.is_file():
                    _issue(issues, guide.path, field, f"missing {full.relative_to(ROOT)}")
                if not icon.is_file():
                    _issue(issues, guide.path, field, f"missing {icon.relative_to(ROOT)}")
                if full.is_file() and icon.is_file():
                    catalog["covenants"][covenant] = {
                        "image": _site_url(full),
                        "icon": _site_url(icon),
                        "url": f"/handbook/team#{slug}",
                    }
            wheels = build.get("wheels")
            if not isinstance(wheels, dict):
                continue
            for group, recommendations in wheels.items():
                if not isinstance(recommendations, list):
                    continue
                for item_index, recommendation in enumerate(recommendations):
                    if not isinstance(recommendation, dict):
                        continue
                    name = recommendation.get("name")
                    if not isinstance(name, str) or name in catalog["wheels"]:
                        continue
                    slug = slugify(name)
                    image = SOURCE_IMAGES / "wheels" / f"{slug}.png"
                    if not image.is_file():
                        _issue(
                            issues,
                            guide.path,
                            f"awakener.builds[{build_index}].wheels.{group}[{item_index}].name",
                            f"missing {image.relative_to(ROOT)}",
                        )
                    else:
                        catalog["wheels"][name] = {"image": _site_url(image)}

        for posse_index, posse in enumerate(awakener.get("suggested_posses") or []):
            if not isinstance(posse, dict):
                continue
            name = posse.get("name")
            if not isinstance(name, str) or name in catalog["posses"]:
                continue
            slug = slugify(name)
            image = SOURCE_IMAGES / "posses" / f"{slug}.png"
            if not image.is_file():
                _issue(
                    issues,
                    guide.path,
                    f"awakener.suggested_posses[{posse_index}].name",
                    f"missing {image.relative_to(ROOT)}",
                )
            else:
                catalog["posses"][name] = {"image": _site_url(image)}
    return catalog


def _toml_string(value: str) -> str:
    return json.dumps(value, ensure_ascii=False)


def _render_nav(guides: list[Guide], indent: str) -> str:
    grouped = {realm: [] for realm, _ in REALMS}
    for guide in guides:
        grouped.setdefault(guide.realm, []).append(guide)

    lines = [f'{indent}{{ "Awakener Guides" = [', f'{indent}  "handbook/awakeners.md",']
    for realm, display_name in REALMS:
        realm_guides = sorted(
            grouped.get(realm, []), key=lambda guide: (guide.title.casefold(), guide.slug)
        )
        if not realm_guides:
            continue
        lines.append(f'{indent}  {{ {_toml_string(display_name)} = [')
        lines.extend(
            f'{indent}    {_toml_string(guide.path.relative_to("lib").as_posix())},'
            for guide in realm_guides
        )
        lines.append(f"{indent}  ]}},")
    lines.append(f"{indent}]}},")
    return "\n".join(lines)


def _render_catalog(catalog: dict[str, dict[str, dict[str, str]]]) -> str:
    lines = ["", "# Generated by mythag_site.awakeners; do not edit this file."]
    for category in ("awakeners", "covenants", "wheels", "posses"):
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
    catalog = build_asset_catalog(guides, issues)
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
