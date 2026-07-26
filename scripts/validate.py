#!/usr/bin/env python3
"""Validate the goal-workflow repository or an installed skill bundle.

This validator checks *structure*, not prose. It deliberately does not assert
that SKILL.md contains particular sentences: an earlier version grepped the
skill for phrases copied verbatim out of the skill itself, which failed on
meaning-preserving rewordings and passed when both approval gates were deleted.
Behavioral claims about the skill belong in tests/evals.json and are exercised
by scripts/run-evals.sh against a live model.
"""

from __future__ import annotations

import argparse
import json
import re
import stat
import sys
from pathlib import Path
from typing import Any


SKILL_NAME = "goal-workflow"

# Canonical install URLs are checked for internal consistency rather than
# against a hardcoded owner, so a fork or rename does not fail validation.
CANONICAL_URL_RE = re.compile(
    r"https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+)"
    r"/tree/(?P<ref>[^/\s`]+)/skills/goal-workflow"
)
CLONE_URL_RE = re.compile(
    r"https://github\.com/(?P<owner>[A-Za-z0-9_.-]+)/(?P<repo>[A-Za-z0-9_.-]+?)(?:\.git)?(?=[\s`\"']|$)"
)
MOVING_REF = "master"

REQUIRED_EVAL_CATEGORIES = {
    "narrow",
    "ambiguous",
    "exhaustive",
    "reject_save",
    "reject_start",
    "matching_goal",
    "conflicting_goal",
    "handoff",
    "path_collision",
    "non_chinese",
    "token_budget",
    "verification_integrity",
}

# The harness's deterministic gate checks parse the scripted replies at these
# checkpoints mechanically (see AFFIRMATIVE_GATE_REPLIES in run_evals.py). A
# reply outside this vocabulary would be misread as a refusal, so the schema
# forbids it.
GATE_CHECKPOINTS = {"save_approval", "start_approval"}
GATE_REPLIES = {"y", "yes", "n", "no"}

SEMVER = (
    r"(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)\.(?:0|[1-9]\d*)"
    r"(?:-[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?"
)


class Checks:
    def __init__(self) -> None:
        self.errors: list[str] = []

    def require(self, condition: bool, message: str) -> None:
        if not condition:
            self.errors.append(message)

    def read_text(self, path: Path) -> str | None:
        try:
            return path.read_text(encoding="utf-8")
        except FileNotFoundError:
            self.errors.append(f"missing file: {path}")
        except UnicodeDecodeError as exc:
            self.errors.append(f"file is not valid UTF-8: {path}: {exc}")
        except OSError as exc:
            self.errors.append(f"cannot read {path}: {exc}")
        return None


def decode_yaml_scalar(raw: str, path: Path, checks: Checks) -> str | None:
    value = raw.strip()
    if not value:
        checks.errors.append(f"empty YAML scalar in {path}")
        return None
    if value.startswith('"'):
        try:
            parsed = json.loads(value)
        except json.JSONDecodeError as exc:
            checks.errors.append(f"invalid double-quoted scalar in {path}: {exc}")
            return None
        if not isinstance(parsed, str):
            checks.errors.append(f"non-string YAML scalar in {path}: {value}")
            return None
        return parsed
    if value.startswith("'"):
        if len(value) < 2 or not value.endswith("'"):
            checks.errors.append(f"invalid single-quoted scalar in {path}: {value}")
            return None
        return value[1:-1].replace("''", "'")
    return value


def parse_frontmatter(text: str, path: Path, checks: Checks) -> tuple[dict[str, str], str]:
    lines = text.splitlines()
    if not lines or lines[0] != "---":
        checks.errors.append(f"{path} must start with a YAML frontmatter delimiter")
        return {}, text

    try:
        closing = lines.index("---", 1)
    except ValueError:
        checks.errors.append(f"{path} has no closing YAML frontmatter delimiter")
        return {}, text

    metadata: dict[str, str] = {}
    for line_number, line in enumerate(lines[1:closing], start=2):
        if not line.strip():
            continue
        match = re.fullmatch(r"([a-z][a-z0-9_-]*):\s*(.+)", line)
        if not match:
            checks.errors.append(
                f"unsupported frontmatter syntax in {path}:{line_number}: {line!r}"
            )
            continue
        key, raw_value = match.groups()
        if key in metadata:
            checks.errors.append(f"duplicate frontmatter key in {path}: {key}")
            continue
        value = decode_yaml_scalar(raw_value, path, checks)
        if value is not None:
            metadata[key] = value

    body = "\n".join(lines[closing + 1 :])
    return metadata, body


def validate_frontmatter(
    skill_dir: Path,
    text: str,
    skill_path: Path,
    checks: Checks,
    expected_version: str | None = None,
) -> str:
    metadata, body = parse_frontmatter(text, skill_path, checks)
    # version is optional so that bundles installed before it existed still
    # pass the identity checks that the replace and uninstall flows run.
    checks.require(
        {"name", "description"} <= set(metadata) <= {"name", "description", "version"},
        f"{skill_path} frontmatter must contain name and description, "
        f"plus at most version",
    )
    checks.require(
        metadata.get("name") == SKILL_NAME,
        f"{skill_path} frontmatter name must be {SKILL_NAME!r}",
    )
    checks.require(
        skill_dir.name == metadata.get("name"),
        f"skill directory {skill_dir.name!r} must match its frontmatter name",
    )
    description = metadata.get("description", "")
    checks.require(
        1 <= len(description) <= 1024,
        f"{skill_path} description must be between 1 and 1024 characters",
    )
    checks.require(
        "/goal-workflow" in description,
        f"{skill_path} description must include the /goal-workflow trigger",
    )
    version = metadata.get("version")
    if version is not None:
        checks.require(
            re.fullmatch(SEMVER, version) is not None,
            f"{skill_path} frontmatter version must be a semantic version; got {version!r}",
        )
    if expected_version is not None:
        checks.require(
            version == expected_version,
            f"{skill_path} frontmatter version must equal VERSION "
            f"{expected_version!r}; got {version!r}",
        )
    return body


def validate_skill_structure(body: str, path: Path, checks: Checks) -> None:
    """Check meaning-independent structure only.

    These are properties that are wrong regardless of how the workflow is
    worded: an empty body, an unterminated fenced block (which silently
    swallows the rest of the document when the skill is rendered), or a
    missing top-level heading.
    """
    checks.require(bool(body.strip()), f"{path} must have a non-empty body")

    fence_lines = [
        number
        for number, line in enumerate(body.splitlines(), start=1)
        if line.strip().startswith("```")
    ]
    checks.require(
        len(fence_lines) % 2 == 0,
        f"{path} has an unterminated fenced code block; "
        f"fences open/close at body lines {fence_lines}",
    )

    depth = 0
    headings: list[str] = []
    for line in body.splitlines():
        if line.strip().startswith("```"):
            depth = 1 - depth
            continue
        if depth == 0 and line.startswith("#"):
            headings.append(line)
    checks.require(
        any(line.startswith("# ") for line in headings),
        f"{path} must contain a top-level heading outside a code fence",
    )


def validate_skill_bundle(
    skill_dir: Path,
    checks: Checks,
    identity_only: bool = False,
    expected_version: str | None = None,
) -> None:
    checks.require(skill_dir.name == SKILL_NAME, f"skill directory must be named {SKILL_NAME}")
    checks.require(skill_dir.is_dir(), f"skill directory does not exist: {skill_dir}")
    if not skill_dir.is_dir():
        return

    skill_path = skill_dir / "SKILL.md"
    text = checks.read_text(skill_path)
    if text is None:
        return
    body = validate_frontmatter(
        skill_dir, text, skill_path, checks, expected_version=expected_version
    )
    if identity_only:
        return
    validate_skill_structure(body, skill_path, checks)


def require_exact_keys(
    value: dict[str, Any], expected: set[str], label: str, checks: Checks
) -> None:
    actual = set(value)
    checks.require(
        actual == expected,
        f"{label} keys must be exactly {sorted(expected)}; got {sorted(actual)}",
    )


def validate_string_list(
    value: Any, label: str, checks: Checks, *, nonempty: bool = True
) -> list[str]:
    if not isinstance(value, list):
        checks.errors.append(f"{label} must be an array")
        return []
    if nonempty and not value:
        checks.errors.append(f"{label} must not be empty")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            checks.errors.append(f"{label}[{index}] must be a non-empty string")
        else:
            result.append(item)
    checks.require(len(result) == len(set(result)), f"{label} must not contain duplicates")
    return result


def validate_eval_case(
    case: Any,
    index: int,
    actions: set[str],
    checkpoints: set[str],
    used_actions: set[str],
    used_checkpoints: set[str],
    checks: Checks,
) -> str | None:
    label = f"tests/evals.json cases[{index}]"
    if not isinstance(case, dict):
        checks.errors.append(f"{label} must be an object")
        return None
    require_exact_keys(
        case,
        {"id", "title", "category", "user_language", "prompt", "checkpoint_replies", "setup", "expected"},
        label,
        checks,
    )
    case_id = case.get("id")
    checks.require(
        isinstance(case_id, str) and re.fullmatch(r"[a-z0-9]+(?:-[a-z0-9]+)*", case_id or "") is not None,
        f"{label}.id must be a lowercase kebab-case string",
    )
    for key in ("title", "user_language", "prompt"):
        checks.require(
            isinstance(case.get(key), str) and bool(case.get(key, "").strip()),
            f"{label}.{key} must be a non-empty string",
        )
    category = case.get("category")
    checks.require(category in REQUIRED_EVAL_CATEGORIES, f"{label}.category is not recognized")

    replies = case.get("checkpoint_replies")
    if not isinstance(replies, list):
        checks.errors.append(f"{label}.checkpoint_replies must be an array")
    else:
        for reply_index, reply in enumerate(replies):
            reply_label = f"{label}.checkpoint_replies[{reply_index}]"
            if not isinstance(reply, dict):
                checks.errors.append(f"{reply_label} must be an object")
                continue
            require_exact_keys(reply, {"at", "content"}, reply_label, checks)
            for key in ("at", "content"):
                checks.require(
                    isinstance(reply.get(key), str) and bool(reply.get(key, "").strip()),
                    f"{reply_label}.{key} must be a non-empty string",
                )
            at = reply.get("at")
            if isinstance(at, str):
                checks.require(
                    at in checkpoints,
                    f"{reply_label}.at is not a declared checkpoint: {at!r}",
                )
                used_checkpoints.add(at)
            content = reply.get("content")
            if at in GATE_CHECKPOINTS and isinstance(content, str):
                checks.require(
                    content.strip().lower() in GATE_REPLIES,
                    f"{reply_label}.content at {at!r} must be one of y/yes/n/no "
                    f"(case-insensitive) so the deterministic gate checks can read it",
                )

    setup = case.get("setup")
    setup_keys = {
        "requested_depth",
        "start_mode",
        "active_goal",
        "goal_file",
        "explicit_token_budget",
    }
    if not isinstance(setup, dict):
        checks.errors.append(f"{label}.setup must be an object")
    else:
        require_exact_keys(setup, setup_keys, f"{label}.setup", checks)
        checks.require(
            setup.get("requested_depth") in {"auto", "fast", "standard", "exhaustive"},
            f"{label}.setup.requested_depth is invalid",
        )
        checks.require(
            setup.get("start_mode") in {"in_session", "handoff"},
            f"{label}.setup.start_mode is invalid",
        )
        checks.require(
            setup.get("active_goal") in {"none", "matching", "conflicting"},
            f"{label}.setup.active_goal is invalid",
        )
        checks.require(
            setup.get("goal_file") in {"absent", "exists"},
            f"{label}.setup.goal_file is invalid",
        )
        budget = setup.get("explicit_token_budget")
        checks.require(
            budget is None or (isinstance(budget, int) and not isinstance(budget, bool) and budget > 0),
            f"{label}.setup.explicit_token_budget must be null or a positive integer",
        )
        if category == "token_budget":
            checks.require(isinstance(budget, int), f"{label} must provide an explicit token budget")

    expected = case.get("expected")
    expected_keys = {
        "mode",
        "required_behaviors",
        "forbidden_actions",
        "tool_order",
        "terminal_state",
    }
    if not isinstance(expected, dict):
        checks.errors.append(f"{label}.expected must be an object")
        return category if isinstance(category, str) else None
    require_exact_keys(expected, expected_keys, f"{label}.expected", checks)
    checks.require(
        expected.get("mode") in {"fast", "standard", "exhaustive"},
        f"{label}.expected.mode is invalid",
    )
    validate_string_list(
        expected.get("required_behaviors"), f"{label}.expected.required_behaviors", checks
    )
    forbidden = validate_string_list(
        expected.get("forbidden_actions"), f"{label}.expected.forbidden_actions", checks
    )
    for action in forbidden:
        checks.require(action in actions, f"{label} uses unknown forbidden action {action!r}")
    used_actions.update(forbidden)

    tool_order = expected.get("tool_order")
    if not isinstance(tool_order, list) or not tool_order:
        checks.errors.append(f"{label}.expected.tool_order must be a non-empty array")
    else:
        for order_index, order in enumerate(tool_order):
            order_label = f"{label}.expected.tool_order[{order_index}]"
            if not isinstance(order, dict):
                checks.errors.append(f"{order_label} must be an object")
                continue
            require_exact_keys(order, {"before", "after", "reason"}, order_label, checks)
            before = order.get("before")
            after = order.get("after")
            checks.require(before in actions, f"{order_label}.before is not a known action")
            checks.require(after in actions, f"{order_label}.after is not a known action")
            checks.require(before != after, f"{order_label} must order two different actions")
            checks.require(
                isinstance(order.get("reason"), str) and bool(order.get("reason", "").strip()),
                f"{order_label}.reason must be a non-empty string",
            )
            used_actions.update({a for a in (before, after) if isinstance(a, str)})

    terminal_states = {
        "awaiting_question",
        "awaiting_revision",
        "awaiting_save_approval",
        "saved_not_started",
        "goal_started",
        "waiting_on_existing_goal",
        "waiting_on_conflict",
        "handoff_prepared",
    }
    checks.require(
        expected.get("terminal_state") in terminal_states,
        f"{label}.expected.terminal_state is invalid",
    )
    if category == "non_chinese":
        language = case.get("user_language", "").lower()
        checks.require(not language.startswith("zh"), f"{label} must use a non-Chinese language")
    return category if isinstance(category, str) else None


def validate_evals(path: Path, checks: Checks) -> None:
    text = checks.read_text(path)
    if text is None:
        return
    try:
        document = json.loads(text)
    except json.JSONDecodeError as exc:
        checks.errors.append(f"invalid JSON in {path}: {exc}")
        return
    if not isinstance(document, dict):
        checks.errors.append(f"{path} must contain a JSON object")
        return
    require_exact_keys(
        document,
        {"schema_version", "description", "actions", "checkpoints", "cases"},
        str(path),
        checks,
    )
    checks.require(document.get("schema_version") == 3, f"{path} schema_version must be 3")
    checks.require(
        isinstance(document.get("description"), str) and bool(document.get("description", "").strip()),
        f"{path} description must be a non-empty string",
    )
    action_list = validate_string_list(document.get("actions"), f"{path}.actions", checks)
    actions = set(action_list)

    raw_checkpoints = document.get("checkpoints")
    checkpoints: set[str] = set()
    if not isinstance(raw_checkpoints, dict) or not raw_checkpoints:
        checks.errors.append(f"{path}.checkpoints must be a non-empty object")
    else:
        for name, description in raw_checkpoints.items():
            checks.require(
                re.fullmatch(r"[a-z0-9]+(?:_[a-z0-9]+)*", name) is not None,
                f"{path}.checkpoints key {name!r} must be lowercase snake_case",
            )
            checks.require(
                isinstance(description, str) and bool(description.strip()),
                f"{path}.checkpoints[{name!r}] must describe when the checkpoint is reached",
            )
        checkpoints = set(raw_checkpoints)

    cases = document.get("cases")
    if not isinstance(cases, list) or not cases:
        checks.errors.append(f"{path}.cases must be a non-empty array")
        return

    categories: set[str] = set()
    ids: list[str] = []
    used_actions: set[str] = set()
    used_checkpoints: set[str] = set()
    for index, case in enumerate(cases):
        category = validate_eval_case(
            case, index, actions, checkpoints, used_actions, used_checkpoints, checks
        )
        if category is not None:
            categories.add(category)
        if isinstance(case, dict) and isinstance(case.get("id"), str):
            ids.append(case["id"])

    checks.require(len(ids) == len(set(ids)), f"{path} case ids must be unique")

    # At least one case per category. Additional cases per category are allowed
    # and encouraged; the schema must never make added coverage a failure.
    missing = REQUIRED_EVAL_CATEGORIES - categories
    checks.require(
        not missing,
        f"{path} must contain at least one case for each required category; "
        f"missing: {sorted(missing)}",
    )
    unknown = categories - REQUIRED_EVAL_CATEGORIES
    checks.require(not unknown, f"{path} has unrecognized categories: {sorted(unknown)}")

    # Dead vocabulary silently rots: an action or checkpoint no case references
    # looks like coverage that does not exist.
    checks.require(
        not (actions - used_actions),
        f"{path}.actions declares names no case references: {sorted(actions - used_actions)}",
    )
    checks.require(
        not (checkpoints - used_checkpoints),
        f"{path}.checkpoints declares names no case references: "
        f"{sorted(checkpoints - used_checkpoints)}",
    )


def validate_text_hygiene(root: Path, checks: Checks) -> None:
    text_suffixes = {".json", ".md", ".py", ".sh", ".yaml", ".yml"}
    text_names = {".gitignore", "LICENSE", "VERSION"}
    # tests/results holds generated eval reports: ignored by git, so not subject
    # to the hygiene rules that mirror `git diff --check`.
    skip_parts = {".git", ".claude", "__pycache__"}
    results_dir = root / "tests" / "results"
    for path in root.rglob("*"):
        if skip_parts & set(path.parts) or not path.is_file() or path.is_symlink():
            continue
        if results_dir in path.parents:
            continue
        if path.suffix not in text_suffixes and path.name not in text_names:
            continue
        try:
            data = path.read_bytes()
        except OSError as exc:
            checks.errors.append(f"cannot read {path}: {exc}")
            continue
        # Git's default rules also reject a carriage return at end of line,
        # but a text-mode read hides CRLF behind newline translation, so the
        # carriage-return check must look at the raw bytes.
        if b"\r" in data:
            line_number = data[: data.index(b"\r")].count(b"\n") + 1
            checks.errors.append(f"carriage return in {path}:{line_number}")
        try:
            text = data.decode("utf-8")
        except UnicodeDecodeError as exc:
            checks.errors.append(f"file is not valid UTF-8: {path}: {exc}")
            continue
        # Mirror the whitespace rules that Git's default core.whitespace
        # enforces via `git diff --check` in CI, so this local check is a
        # superset and never passes text that CI would reject:
        # blank-at-eol, space-before-tab, blank-at-eof, and the CR check above.
        for line_number, line in enumerate(text.splitlines(), start=1):
            # blank-at-eol: no trailing spaces or tabs.
            checks.require(
                not line.endswith((" ", "\t")),
                f"trailing whitespace in {path}:{line_number}",
            )
            # space-before-tab: a space immediately followed by a tab in the
            # leading indent is ambiguous under tab expansion.
            indent = line[: len(line) - len(line.lstrip(" \t"))]
            checks.require(
                " \t" not in indent,
                f"space before tab in indent in {path}:{line_number}",
            )
        checks.require(text.endswith("\n"), f"text file must end with a newline: {path}")
        # blank-at-eof: a final "\n\n" leaves a trailing empty line.
        checks.require(
            not text.endswith("\n\n"),
            f"text file must not end with a blank line: {path}",
        )


def validate_install_urls(root: Path, version: str, checks: Checks) -> None:
    """Require the install docs to agree on one repository.

    The owner and repository name are not hardcoded, so a fork or a rename
    stays valid as long as every document points at the same place.
    """
    docs = {}
    for doc_name in ("README.md", "INSTALL.md"):
        text = checks.read_text(root / doc_name)
        if text is not None:
            docs[doc_name] = text
    if not docs:
        return

    repos: set[str] = set()
    for doc_name, text in docs.items():
        canonical = list(CANONICAL_URL_RE.finditer(text))
        checks.require(
            bool(canonical),
            f"{root / doc_name} must include a canonical install URL of the form "
            f"https://github.com/<owner>/<repo>/tree/<ref>/skills/{SKILL_NAME}",
        )
        checks.require(
            any(match.group("ref") == MOVING_REF for match in canonical),
            f"{root / doc_name} must include the moving-source URL pinned to {MOVING_REF!r}",
        )
        repos.update(f"{m.group('owner')}/{m.group('repo')}" for m in canonical)
        repos.update(
            f"{m.group('owner')}/{m.group('repo')}" for m in CLONE_URL_RE.finditer(text)
        )

    checks.require(
        len(repos) <= 1,
        f"install documentation must reference exactly one repository; got {sorted(repos)}",
    )

    # A pinned example that names a release must name the current release,
    # otherwise the documented reproducible install is silently stale.
    install_text = docs.get("INSTALL.md", "")
    pinned = {
        match.group("ref")
        for match in CANONICAL_URL_RE.finditer(install_text)
        if re.fullmatch(rf"v{SEMVER}", match.group("ref"))
    }
    stale = {ref for ref in pinned if ref != f"v{version}"}
    checks.require(
        not stale,
        f"{root / 'INSTALL.md'} pins release {sorted(stale)} but VERSION is {version}",
    )


def validate_repository(root: Path, checks: Checks) -> None:
    version_path = root / "VERSION"
    version_text = checks.read_text(version_path)
    version = version_text.strip() if version_text is not None else ""
    version_ok = re.fullmatch(SEMVER, version) is not None
    checks.require(
        version_ok,
        f"{version_path} must contain exactly one semantic version",
    )

    canonical = root / "skills" / SKILL_NAME
    # The canonical bundle must carry the release version so an installed
    # copy can be traced back to it; installed bundles are allowed to omit it.
    validate_skill_bundle(
        canonical, checks, expected_version=version if version_ok else None
    )
    validate_text_hygiene(root, checks)

    expected_bundle_files = {"SKILL.md"}
    actual_bundle_files = {
        str(path.relative_to(canonical))
        for path in canonical.rglob("*")
        if path.is_file()
    } if canonical.is_dir() else set()
    checks.require(
        actual_bundle_files == expected_bundle_files,
        "canonical skill bundle files must be exactly "
        f"{sorted(expected_bundle_files)}; got {sorted(actual_bundle_files)}",
    )

    forbidden_bundle_entries = []
    if canonical.is_dir():
        for path in canonical.rglob("*"):
            relative = path.relative_to(canonical)
            if (
                path.name in {"README.md", "INSTALL.md"}
                or "history" in relative.parts
                or re.fullmatch(r"20\d{2}-\d{2}-\d{2}-.+\.md", path.name)
            ):
                forbidden_bundle_entries.append(str(relative))
    checks.require(
        not forbidden_bundle_entries,
        "canonical skill bundle must not contain repository docs or goal history: "
        f"{sorted(forbidden_bundle_entries)}",
    )

    root_goal_files = [
        path.name
        for path in root.iterdir()
        if path.is_file() and re.fullmatch(r"20\d{2}-\d{2}-\d{2}-.+\.md", path.name)
    ]
    checks.require(
        not root_goal_files,
        f"historical goal files must not live at repository root: {sorted(root_goal_files)}",
    )
    validate_evals(root / "tests" / "evals.json", checks)
    checks.require((root / "tests" / "README.md").is_file(), "missing tests/README.md")

    validate_install_urls(root, version, checks)
    changelog_path = root / "CHANGELOG.md"
    changelog = checks.read_text(changelog_path)
    if changelog is not None and version:
        checks.require(
            re.search(rf"(?<![0-9A-Za-z]){re.escape(version)}(?![0-9A-Za-z])", changelog)
            is not None,
            f"{changelog_path} must contain the current VERSION {version}",
        )
    license_path = root / "LICENSE"
    license_text = checks.read_text(license_path)
    if license_text is not None:
        checks.require(bool(license_text.strip()), f"{license_path} must not be empty")
    gitignore_path = root / ".gitignore"
    gitignore = checks.read_text(gitignore_path)
    if gitignore is not None:
        ignore_lines = {
            line.strip()
            for line in gitignore.splitlines()
            if line.strip() and not line.lstrip().startswith("#")
        }
        checks.require(
            ".claude/goals/" in ignore_lines,
            f"{gitignore_path} must ignore .claude/goals/",
        )
        checks.require(
            "__pycache__/" in ignore_lines,
            f"{gitignore_path} must ignore __pycache__/",
        )

    scripts = (
        "validate.sh",
        "install-local.sh",
        "uninstall-local.sh",
        "smoke-install.sh",
        "run-evals.sh",
    )
    for script_name in scripts:
        path = root / "scripts" / script_name
        try:
            mode = path.stat().st_mode
        except FileNotFoundError:
            checks.errors.append(f"missing script: {path}")
            continue
        checks.require(
            bool(mode & (stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)),
            f"script must be executable: {path}",
        )
    workflow_path = root / ".github" / "workflows" / "validate.yml"
    workflow = checks.read_text(workflow_path)
    if workflow is not None:
        for command in (
            "scripts/validate.sh",
            "scripts/smoke-install.sh",
            "git diff --check",
        ):
            checks.require(
                command in workflow,
                f"{workflow_path} must run or conditionally handle {command}",
            )
        # The eval harness spends tokens against a live model; CI must not run
        # it implicitly. Match executable lines only: a YAML comment explaining
        # why the harness is excluded is not an invocation of it.
        executable = [
            line
            for line in workflow.splitlines()
            if not line.lstrip().startswith("#")
        ]
        checks.require(
            not any("run-evals.sh" in line or "run_evals.py" in line for line in executable),
            f"{workflow_path} must not run the live-model eval harness in CI",
        )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--root",
        type=Path,
        default=Path(__file__).resolve().parents[1],
        help="repository root (default: parent of scripts/)",
    )
    parser.add_argument("--skill-dir", type=Path, help="skill bundle to validate")
    parser.add_argument(
        "--installed-only",
        action="store_true",
        help="validate only --skill-dir as an installed bundle",
    )
    parser.add_argument(
        "--identity-only",
        action="store_true",
        help="validate only the skill directory name and SKILL.md frontmatter identity",
    )
    args = parser.parse_args()
    if args.identity_only and args.skill_dir is None:
        parser.error("--identity-only requires --skill-dir")
    if args.installed_only and args.skill_dir is None:
        parser.error("--installed-only requires --skill-dir")
    if args.identity_only and args.installed_only:
        parser.error("use only one of --identity-only and --installed-only")
    return args


def main() -> int:
    args = parse_args()
    checks = Checks()
    if args.skill_dir is not None:
        validate_skill_bundle(args.skill_dir.resolve(), checks, identity_only=args.identity_only)
        target = args.skill_dir.resolve()
    else:
        root = args.root.resolve()
        validate_repository(root, checks)
        target = root

    if checks.errors:
        for error in checks.errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"Validation failed with {len(checks.errors)} error(s).", file=sys.stderr)
        return 1
    print(f"Validation passed: {target}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
