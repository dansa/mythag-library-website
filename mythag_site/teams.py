"""Render validated inline team builds in Markdown pages."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from yaml.nodes import MappingNode, Node, SequenceNode

from mythag_site.content import (
    ROOT,
    AssetCatalog,
    UniqueKeyLoader,
    ValidationIssue,
    parse_content_id,
    parse_non_empty_string,
    unknown_content_id_message,
)


TEMPLATE_ROOT = ROOT / "overrides"
TEMPLATE_NAME = "teams/team.html"


@dataclass(frozen=True)
class TeamMemberSpec:
    awakener_id: str
    covenant_id: str
    wheel_ids: tuple[str, str]


@dataclass(frozen=True)
class TeamSpec:
    name: str
    posse_id: str
    members: tuple[TeamMemberSpec, ...]


@dataclass(frozen=True)
class TeamAsset:
    label: str
    image: str


@dataclass(frozen=True)
class LinkedTeamAsset:
    label: str
    image: str
    url: str


@dataclass(frozen=True)
class TeamMemberView:
    awakener: LinkedTeamAsset
    covenant: LinkedTeamAsset
    wheels: tuple[TeamAsset, TeamAsset]


@dataclass(frozen=True)
class TeamView:
    name: str
    posse: TeamAsset
    members: tuple[TeamMemberView, ...]


@dataclass(frozen=True)
class TeamFence:
    source: str
    opening_line: int


class TeamValidationError(Exception):
    def __init__(self, issues: list[ValidationIssue]):
        self.issues = issues
        super().__init__(
            "Team validation failed:\n" + "\n".join(f"- {issue}" for issue in issues)
        )


class _MarkedUniqueLoader(UniqueKeyLoader):
    """Shared strict YAML loader with source-mark access."""


def _collect_marks(node: Node, path: str, marks: dict[str, yaml.Mark]) -> None:
    marks[path] = node.start_mark
    if isinstance(node, MappingNode):
        for key_node, value_node in node.value:
            key = key_node.value
            child = f"{path}.{key}" if path else key
            marks[f"{child}#key"] = key_node.start_mark
            _collect_marks(value_node, child, marks)
    elif isinstance(node, SequenceNode):
        for index, value_node in enumerate(node.value):
            _collect_marks(value_node, f"{path}[{index}]", marks)


def _parse_yaml(source: str) -> tuple[Any, dict[str, yaml.Mark]]:
    loader = _MarkedUniqueLoader(source)
    try:
        node = loader.get_single_node()
        if node is None:
            return None, {}
        marks: dict[str, yaml.Mark] = {}
        _collect_marks(node, "", marks)
        return loader.construct_document(node), marks
    finally:
        loader.dispose()


def _mark_for(marks: dict[str, yaml.Mark], field: str) -> yaml.Mark | None:
    if field in marks:
        return marks[field]
    if "." in field:
        return marks.get(field.rsplit(".", 1)[0])
    return marks.get("")


def _issue(
    issues: list[ValidationIssue],
    path: Path,
    field: str,
    message: str,
    *,
    fence: TeamFence,
    mark: yaml.Mark | None = None,
) -> None:
    yaml_line = mark.line if mark is not None else 0
    yaml_column = mark.column if mark is not None else 0
    issues.append(
        ValidationIssue(
            path,
            field,
            message,
            line=fence.opening_line + 1 + yaml_line,
            column=yaml_column + 1,
        )
    )


def _mapping(
    value: Any,
    issues: list[ValidationIssue],
    path: Path,
    field: str,
    *,
    fence: TeamFence,
    marks: dict[str, yaml.Mark],
) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        _issue(
            issues, path, field, "expected a mapping", fence=fence, mark=_mark_for(marks, field)
        )
        return None
    return value


def _string(
    value: Any,
    issues: list[ValidationIssue],
    path: Path,
    field: str,
    *,
    fence: TeamFence,
    marks: dict[str, yaml.Mark],
) -> str | None:
    parsed, error = parse_non_empty_string(value, single_line=True)
    if error is not None:
        _issue(
            issues,
            path,
            field,
            error,
            fence=fence,
            mark=_mark_for(marks, field),
        )
        return None
    return parsed


def _content_id(
    value: Any,
    assets: dict[str, dict[str, str]],
    category: str,
    issues: list[ValidationIssue],
    path: Path,
    field: str,
    *,
    fence: TeamFence,
    marks: dict[str, yaml.Mark],
) -> str | None:
    content_id, error = parse_content_id(value)
    if error is not None:
        _issue(
            issues,
            path,
            field,
            error,
            fence=fence,
            mark=_mark_for(marks, field),
        )
        return None
    assert content_id is not None
    if content_id not in assets:
        _issue(
            issues,
            path,
            field,
            unknown_content_id_message(category, content_id, assets),
            fence=fence,
            mark=_mark_for(marks, field),
        )
        return None
    return content_id


def _fields(
    value: dict[str, Any],
    allowed: set[str],
    issues: list[ValidationIssue],
    path: Path,
    field: str,
    *,
    fence: TeamFence,
    marks: dict[str, yaml.Mark],
) -> None:
    for key in value:
        child = f"{field}.{key}" if field else str(key)
        if key not in allowed:
            _issue(
                issues,
                path,
                child,
                "unknown field",
                fence=fence,
                mark=marks.get(f"{child}#key"),
            )
    for key in allowed - value.keys():
        child = f"{field}.{key}" if field else key
        _issue(
            issues,
            path,
            child,
            "missing required field",
            fence=fence,
            mark=_mark_for(marks, field),
        )


def parse_team(fence: TeamFence, path: Path, assets: AssetCatalog) -> TeamSpec:
    issues: list[ValidationIssue] = []
    try:
        raw, marks = _parse_yaml(fence.source)
    except yaml.MarkedYAMLError as error:
        _issue(
            issues,
            path,
            "team",
            error.problem or "invalid YAML",
            fence=fence,
            mark=error.problem_mark,
        )
        raise TeamValidationError(issues) from error

    root = _mapping(raw, issues, path, "team", fence=fence, marks=marks)
    if root is None:
        raise TeamValidationError(issues)
    _fields(
        root,
        {"name", "posse", "members"},
        issues,
        path,
        "",
        fence=fence,
        marks=marks,
    )
    name = _string(root.get("name"), issues, path, "name", fence=fence, marks=marks)
    posse_id = _content_id(
        root.get("posse"),
        assets["posses"],
        "posses",
        issues,
        path,
        "posse",
        fence=fence,
        marks=marks,
    )

    members_raw = root.get("members")
    if not isinstance(members_raw, list):
        _issue(
            issues,
            path,
            "members",
            "expected a list of exactly four members",
            fence=fence,
            mark=_mark_for(marks, "members"),
        )
        members_raw = []
    elif len(members_raw) != 4:
        _issue(
            issues,
            path,
            "members",
            "expected exactly four members",
            fence=fence,
            mark=_mark_for(marks, "members"),
        )

    members: list[TeamMemberSpec] = []
    for index, member_raw in enumerate(members_raw):
        prefix = f"members[{index}]"
        member = _mapping(member_raw, issues, path, prefix, fence=fence, marks=marks)
        if member is None:
            continue
        _fields(
            member,
            {"awakener", "covenant", "wheels"},
            issues,
            path,
            prefix,
            fence=fence,
            marks=marks,
        )
        awakener_id = _content_id(
            member.get("awakener"),
            assets["awakeners"],
            "awakeners",
            issues,
            path,
            f"{prefix}.awakener",
            fence=fence,
            marks=marks,
        )
        covenant_id = _content_id(
            member.get("covenant"),
            assets["covenants"],
            "covenants",
            issues,
            path,
            f"{prefix}.covenant",
            fence=fence,
            marks=marks,
        )
        wheels_raw = member.get("wheels")
        wheel_ids: list[str] = []
        if not isinstance(wheels_raw, list) or len(wheels_raw) != 2:
            _issue(
                issues,
                path,
                f"{prefix}.wheels",
                "expected exactly two wheel IDs",
                fence=fence,
                mark=_mark_for(marks, f"{prefix}.wheels"),
            )
        else:
            for wheel_index, wheel_raw in enumerate(wheels_raw):
                wheel_id = _content_id(
                    wheel_raw,
                    assets["wheels"],
                    "wheels",
                    issues,
                    path,
                    f"{prefix}.wheels[{wheel_index}]",
                    fence=fence,
                    marks=marks,
                )
                if wheel_id is not None:
                    wheel_ids.append(wheel_id)
        if awakener_id and covenant_id and len(wheel_ids) == 2:
            members.append(
                TeamMemberSpec(awakener_id, covenant_id, (wheel_ids[0], wheel_ids[1]))
            )

    if issues or name is None or posse_id is None or len(members) != 4:
        raise TeamValidationError(issues)
    return TeamSpec(name, posse_id, tuple(members))


def _asset(
    assets: AssetCatalog,
    category: str,
    content_id: str,
) -> TeamAsset:
    item = assets[category][content_id]
    return TeamAsset(item["label"], item["image"])


def _linked_asset(
    assets: AssetCatalog,
    category: str,
    content_id: str,
) -> LinkedTeamAsset:
    item = assets[category][content_id]
    return LinkedTeamAsset(item["label"], item["image"], item["url"])


def resolve_team(spec: TeamSpec, assets: AssetCatalog) -> TeamView:
    members = tuple(
        TeamMemberView(
            awakener=_linked_asset(assets, "awakeners", member.awakener_id),
            covenant=_linked_asset(assets, "covenants", member.covenant_id),
            wheels=(
                _asset(assets, "wheels", member.wheel_ids[0]),
                _asset(assets, "wheels", member.wheel_ids[1]),
            ),
        )
        for member in spec.members
    )
    return TeamView(spec.name, _asset(assets, "posses", spec.posse_id), members)


_TEMPLATES = Environment(
    loader=FileSystemLoader(TEMPLATE_ROOT),
    autoescape=select_autoescape(("html",)),
    undefined=StrictUndefined,
    auto_reload=True,
)


def render_team(team: TeamView) -> str:
    return _TEMPLATES.get_template(TEMPLATE_NAME).render(team=team)
