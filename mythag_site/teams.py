"""Render validated inline team builds in Markdown pages."""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Hashable

import yaml
from jinja2 import Environment, FileSystemLoader, StrictUndefined, select_autoescape
from markdown import Extension, Markdown
from markdown.preprocessors import Preprocessor
from yaml.nodes import MappingNode, Node, SequenceNode

from mythag_site.awakeners import AssetCatalog, ROOT, ValidationIssue
from zensical.extensions.context import ContextPreprocessor


TEAM_OPEN = "```team"
TEAM_CLOSE = "```"
CONTENT_ID = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+)*\Z")
FENCE_OPEN = re.compile(r"^(?P<fence>`{3,}|~{3,})(?:[^`~].*)?$")
FRONT_MATTER = re.compile(
    r"^-{3}[ \r\t]*?\n(.*?\r?\n)(?:\.{3}|-{3})[ \r\t]*\n",
    re.UNICODE | re.DOTALL,
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
    content_id: str
    label: str
    image: str
    url: str | None = None


@dataclass(frozen=True)
class TeamMemberView:
    awakener: TeamAsset
    covenant: TeamAsset
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


class _MarkedUniqueLoader(yaml.SafeLoader):
    """Safe YAML loader that rejects duplicate mapping keys."""


def _construct_unique_mapping(
    loader: yaml.SafeLoader, node: MappingNode, deep: bool = False
) -> dict[Any, Any]:
    loader.flatten_mapping(node)
    mapping: dict[Any, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, Hashable):
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                "found an unhashable key",
                key_node.start_mark,
            )
        if key in mapping:
            raise yaml.constructor.ConstructorError(
                "while constructing a mapping",
                node.start_mark,
                f"found duplicate key {key!r}",
                key_node.start_mark,
            )
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_MarkedUniqueLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
    _construct_unique_mapping,
)


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
    if not isinstance(value, str) or not value.strip():
        _issue(
            issues,
            path,
            field,
            "expected a non-empty string",
            fence=fence,
            mark=_mark_for(marks, field),
        )
        return None
    if value != value.strip() or "\n" in value:
        _issue(
            issues,
            path,
            field,
            "must be a trimmed single-line string",
            fence=fence,
            mark=_mark_for(marks, field),
        )
        return None
    return value


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
    content_id = _string(value, issues, path, field, fence=fence, marks=marks)
    if content_id is None:
        return None
    if CONTENT_ID.fullmatch(content_id) is None:
        _issue(
            issues,
            path,
            field,
            "expected a lowercase kebab-case ID",
            fence=fence,
            mark=_mark_for(marks, field),
        )
        return None
    if content_id not in assets:
        singular = category.removesuffix("s")
        message = f"unknown {singular} ID {content_id!r}"
        suggestions = difflib.get_close_matches(content_id, assets, n=1, cutoff=0.6)
        if suggestions:
            message += f"; did you mean {suggestions[0]!r}?"
        _issue(
            issues,
            path,
            field,
            message,
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
    *,
    image_key: str = "image",
) -> TeamAsset:
    item = assets[category][content_id]
    return TeamAsset(content_id, item["label"], item[image_key], item.get("url"))


def resolve_team(spec: TeamSpec, assets: AssetCatalog) -> TeamView:
    members = tuple(
        TeamMemberView(
            awakener=_asset(assets, "awakeners", member.awakener_id),
            covenant=_asset(assets, "covenants", member.covenant_id),
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


def _closing_fence(line: str, fence: str) -> bool:
    return re.fullmatch(rf"{re.escape(fence[0])}{{{len(fence)},}}[ \t]*", line) is not None


def scan_team_fences(lines: list[str], path: Path) -> list[str | TeamFence]:
    output: list[str | TeamFence] = []
    outer_fence: str | None = None
    index = 0
    while index < len(lines):
        line = lines[index]
        if outer_fence is not None:
            output.append(line)
            if _closing_fence(line, outer_fence):
                outer_fence = None
            index += 1
            continue
        if line == TEAM_OPEN:
            closing = index + 1
            while closing < len(lines) and lines[closing] != TEAM_CLOSE:
                closing += 1
            if closing == len(lines):
                raise TeamValidationError(
                    [ValidationIssue(path, "team", "missing closing ``` fence", index + 1, 1)]
                )
            output.append(TeamFence("\n".join(lines[index + 1 : closing]), index + 1))
            output.extend("" for _ in range(closing - index))
            index = closing + 1
            continue
        match = FENCE_OPEN.fullmatch(line)
        if match is not None:
            outer_fence = match.group("fence")
        output.append(line)
        index += 1
    return output


def validate_team_document(path: Path, assets: AssetCatalog) -> list[ValidationIssue]:
    relative = path.relative_to(ROOT)
    try:
        lines = path.read_text(encoding="utf-8").replace("\r\n", "\n").split("\n")
        for segment in scan_team_fences(lines, relative):
            if isinstance(segment, TeamFence):
                parse_team(segment, relative, assets)
    except TeamValidationError as error:
        return error.issues
    return []


def _source_context(md: Markdown, lines: list[str]) -> tuple[Path, int]:
    context = ContextPreprocessor.from_markdown(md)
    if context is None:
        raise TeamValidationError(
            [ValidationIssue(Path("<markdown>"), "team", "missing Zensical page context")]
        )
    page_path = Path(context.page.path)
    candidates = (
        [page_path]
        if page_path.is_absolute()
        else [ROOT / page_path, ROOT / "lib" / page_path]
    )
    source_path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if source_path is None:
        raise TeamValidationError(
            [ValidationIssue(page_path, "team", "could not locate source page")]
        )
    source = source_path.read_text(encoding="utf-8").replace("\r\n", "\n")
    body = source
    start_line = 1
    if match := FRONT_MATTER.match(source):
        body = source[match.end() :]
        start_line = source[: match.end()].count("\n") + 1
        stripped = len(body) - len(body.lstrip("\n"))
        body = body.lstrip("\n")
        start_line += stripped
    normalized = (body + "\n\n").expandtabs(md.tab_length)
    normalized = re.sub(r"(?<=\n) +\n", "\n", normalized)
    expected_lines = normalized.split("\n")
    if lines[: len(expected_lines)] != expected_lines:
        relative = source_path.relative_to(ROOT)
        mismatch = next(
            (
                index
                for index, (expected, actual) in enumerate(zip(expected_lines, lines))
                if expected != actual
            ),
            min(len(expected_lines), len(lines)),
        )
        raise TeamValidationError(
            [
                ValidationIssue(
                    relative,
                    "team",
                    "could not align rendered Markdown with its source "
                    f"(first mismatch at body line {mismatch + 1}; "
                    f"expected at least {len(expected_lines)} lines, received {len(lines)})",
                )
            ]
        )
    return source_path.relative_to(ROOT), start_line


class TeamPreprocessor(Preprocessor):
    def run(self, lines: list[str]) -> list[str]:
        if TEAM_OPEN not in lines:
            return lines
        context = ContextPreprocessor.from_markdown(self.md)
        if context is None:
            raise TeamValidationError(
                [ValidationIssue(Path("<markdown>"), "team", "missing Zensical page context")]
            )
        path, start_line = _source_context(self.md, lines)
        assets = context.config["extra"]["content_assets"]
        output: list[str] = []
        for segment in scan_team_fences(lines, path):
            if isinstance(segment, str):
                output.append(segment)
                continue
            fence = TeamFence(segment.source, segment.opening_line + start_line - 1)
            team = resolve_team(parse_team(fence, path, assets), assets)
            output.append(render_team(team).replace("\n", ""))
        return output


class TeamExtension(Extension):
    def extendMarkdown(self, md: Markdown) -> None:
        md.registerExtension(self)
        md.preprocessors.register(TeamPreprocessor(md), "mythag_team", 29)


def makeExtension(**kwargs: Any) -> TeamExtension:
    return TeamExtension(**kwargs)
