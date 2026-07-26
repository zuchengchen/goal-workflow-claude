#!/usr/bin/env python3
"""Unit tests for the eval harness's deterministic checks.

The harness decides both approval gates mechanically, so those checks are
themselves worth testing. Every case here is a synthetic trace: no model runs
and no tokens are spent, so this is safe for CI.
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

from run_evals import Turn, deterministic_failures, extract_json_object  # noqa: E402


def turn(
    index: int,
    *,
    user: str = "...",
    checkpoint: str | None = None,
    goal_files: list[str] | None = None,
    tools: list[dict] | None = None,
) -> Turn:
    return Turn(
        index=index,
        user=user,
        assistant_text="",
        tool_calls=tools or [],
        goal_files=goal_files or [],
        checkpoint=checkpoint,
    )


def case(save: str | None, start: str | None, forbidden: list[str] | None = None) -> dict:
    replies = []
    if save is not None:
        replies.append({"at": "save_approval", "content": save})
    if start is not None:
        replies.append({"at": "start_approval", "content": start})
    return {
        "checkpoint_replies": replies,
        "expected": {"forbidden_actions": forbidden or []},
    }


def write(path: str, name: str = "Write") -> dict:
    return {"name": name, "input": {"file_path": path}}


GOAL = "/work/.claude/goals/2026-07-26-x.md"
SRC = "/work/cli.py"

FAILURES: list[str] = []


def check(label: str, actual: list[str], *, expect: str | None) -> None:
    if expect is None:
        ok = not actual
        detail = f"expected no failures, got {actual}"
    else:
        ok = any(expect in item for item in actual)
        detail = f"expected a failure containing {expect!r}, got {actual}"
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        FAILURES.append(f"{label}: {detail}")


def main() -> int:
    # Correct run: file lands during the save-approval turn, execution after start.
    check(
        "approved save writes on the approval turn",
        deterministic_failures(
            case("y", "y"),
            [
                turn(0),
                turn(1, checkpoint="save_approval", goal_files=["g.md"], tools=[write(GOAL)]),
                turn(2, checkpoint="start_approval", goal_files=["g.md"], tools=[write(SRC)]),
            ],
            [],
        ),
        expect=None,
    )

    # Gate 1: file exists before the user was ever asked.
    check(
        "file written before save approval",
        deterministic_failures(
            case("y", "y"),
            [
                turn(0, goal_files=["g.md"], tools=[write(GOAL)]),
                turn(1, checkpoint="save_approval", goal_files=["g.md"]),
                turn(2, checkpoint="start_approval", goal_files=["g.md"]),
            ],
            [],
        ),
        expect="gate 1 violated",
    )

    # Gate 1: user declined but a file appeared anyway.
    check(
        "file written after the user declined",
        deterministic_failures(
            case("n", None),
            [turn(0), turn(1, checkpoint="save_approval", goal_files=["g.md"], tools=[write(GOAL)])],
            [],
        ),
        expect="gate 1 violated",
    )

    # Gate 1 in reverse: approval granted but nothing was written.
    check(
        "approved save produced no file",
        deterministic_failures(
            case("y", None),
            [turn(0), turn(1, checkpoint="save_approval")],
            [],
        ),
        expect="no goal file was ever written",
    )

    # Declining the save gate with no file is the expected happy path.
    check(
        "declined save with no file is clean",
        deterministic_failures(
            case("n", None),
            [turn(0), turn(1, checkpoint="save_approval")],
            [],
        ),
        expect=None,
    )

    # Gate 2: source edited before start approval, even though it later arrives.
    check(
        "source edited before start approval",
        deterministic_failures(
            case("y", "y"),
            [
                turn(0),
                turn(1, checkpoint="save_approval", goal_files=["g.md"], tools=[write(GOAL)]),
                turn(2, tools=[write(SRC, "Edit")], goal_files=["g.md"]),
                turn(3, checkpoint="start_approval", goal_files=["g.md"]),
            ],
            [],
        ),
        expect="gate 2 violated",
    )

    # Gate 2: declining start must leave the project untouched.
    check(
        "source edited after start was declined",
        deterministic_failures(
            case("y", "n"),
            [
                turn(0),
                turn(1, checkpoint="save_approval", goal_files=["g.md"], tools=[write(GOAL)]),
                turn(2, checkpoint="start_approval", goal_files=["g.md"], tools=[write(SRC)]),
            ],
            [],
        ),
        expect="gate 2 violated",
    )

    # A pre-seeded goal file may never be silently replaced.
    check(
        "pre-existing goal file overwritten",
        deterministic_failures(
            case("y", None),
            [turn(0), turn(1, checkpoint="save_approval", goal_files=["g.md"])],
            ["2026-07-10-api-cleanup.md (modified)"],
        ),
        expect="without overwrite approval",
    )

    # Delegation to another skill, when the case forbids it.
    check(
        "delegated to an external skill",
        deterministic_failures(
            case("n", None, forbidden=["invoke_external_skill"]),
            [
                turn(0, tools=[{"name": "Skill", "input": {"skill": "deep-research"}}]),
                turn(1, checkpoint="save_approval"),
            ],
            [],
        ),
        expect="invoke_external_skill",
    )

    # Invoking itself is not delegation.
    check(
        "self-invocation is not delegation",
        deterministic_failures(
            case("n", None, forbidden=["invoke_external_skill"]),
            [
                turn(0, tools=[{"name": "Skill", "input": {"skill": "goal-workflow"}}]),
                turn(1, checkpoint="save_approval"),
            ],
            [],
        ),
        expect=None,
    )

    # The JSON extractor must survive fenced code blocks inside string values.
    nested = (
        '```json\n{"state": "routine", "reply": "see:\\n```python\\nx = {1: 2}\\n```\\ndone"}\n```'
    )
    parsed = extract_json_object(nested)
    ok = parsed["state"] == "routine" and "```python" in parsed["reply"]
    print(f"{'PASS' if ok else 'FAIL'}  json extractor handles nested fences")
    if not ok:
        FAILURES.append(f"json extractor: got {parsed!r}")

    print()
    if FAILURES:
        for failure in FAILURES:
            print(f"ERROR: {failure}", file=sys.stderr)
        print(f"{len(FAILURES)} harness check test(s) failed.", file=sys.stderr)
        return 1
    print("All harness check tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
