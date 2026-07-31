"""Zensical transport for inline team fences."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from markdown import Extension, Markdown
from markdown.preprocessors import Preprocessor
from zensical.extensions.context import ContextPreprocessor

from mythag_site.content import ROOT, AssetCatalog, ValidationIssue
from mythag_site.teams import (
    TeamFence,
    TeamValidationError,
    parse_team,
    render_team,
    resolve_team,
)


TEAM_OPEN = "```team"
TEAM_CLOSE = "```"
FENCE_OPEN = re.compile(r"^(?P<fence>`{3,}|~{3,})(?:[^`~].*)?$")
FRONT_MATTER = re.compile(
    r"^-{3}[ \r\t]*?\n(.*?\r?\n)(?:\.{3}|-{3})[ \r\t]*\n",
    re.UNICODE | re.DOTALL,
)


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
