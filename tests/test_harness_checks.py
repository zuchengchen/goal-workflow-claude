#!/usr/bin/env python3
"""Unit tests for the eval harness's deterministic checks.

The harness decides both approval gates mechanically, so those checks are
themselves worth testing. Every case here is a synthetic trace: no model runs
and no tokens are spent, so this is safe for CI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))

import run_evals  # noqa: E402
from run_evals import (  # noqa: E402
    HarnessError,
    Turn,
    collect_result,
    deterministic_failures,
    extract_json_object,
    is_goal_artifact,
    judge_failures,
    list_goal_files,
    render_tool_payload,
    run_case,
    write_target,
)


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


def case(
    save: str | None,
    start: str | None,
    forbidden: list[str] | None = None,
    *,
    overwrite: str | None = None,
    setup: dict | None = None,
) -> dict:
    replies = []
    if overwrite is not None:
        replies.append({"at": "overwrite_approval", "content": overwrite})
    if save is not None:
        replies.append({"at": "save_approval", "content": save})
    if start is not None:
        replies.append({"at": "start_approval", "content": start})
    return {
        "checkpoint_replies": replies,
        "setup": setup or {},
        "expected": {"forbidden_actions": forbidden or []},
    }


def write(path: str, name: str = "Write", agent: str | None = None) -> dict:
    """A write-family call, optionally attributed to a dispatched subagent.

    A subagent's calls surface in the parent stream tagged with the Agent call
    they belong to, so `agent` is what distinguishes "the session wrote this"
    from "a subagent it dispatched wrote this".
    """
    return {"name": name, "input": {"file_path": path}, "agent": agent}


SUB = "toolu_sub_01"


GOAL = "/work/2026-07-26-x.md"
# Staging names observed in real runs. The prescribed form derives from the
# destination; the second dropped the dated slug and was briefly misread as
# project work by the gate-2 check.
GOAL_TMP = "/work/2026-07-26-x.md.tmp-a1b2"
GOAL_TMP_UNDATED = "/work/.goal-tmp-20260726.md"
SRC = "/work/cli.py"
NOTE = "/work/notes.md"

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


def expect_true(label: str, ok: bool, detail: str = "") -> None:
    print(f"{'PASS' if ok else 'FAIL'}  {label}")
    if not ok:
        FAILURES.append(f"{label}: {detail or 'assertion failed'}")


def check_goal_artifact_names() -> None:
    """Which write targets gate 2 may exempt as the goal file or its scratch.

    A false positive here is a hole in gate 2, so the pins matter in both
    directions: the destination and its prescribed staging twin must be
    recognised, and an unrelated file must not be. Matching is case-insensitive
    because a run that deviates from the mandated lowercase slug is the run most
    in need of checking, not the one to wave through.
    """
    exempt = [
        "2026-07-26-api-cleanup.md",
        "2026-07-26-API-Cleanup.MD",
        "/work/2026-07-26-api-cleanup.md.tmp-a1b2",
        "/work/.goal-tmp-20260726.md",
        "/work/.2026-07-26-api-cleanup.md",
    ]
    gated = [
        # Names once exempted by searching for the dated form anywhere in the
        # basename, or by pairing "tmp" with "goal" regardless of extension.
        "x2026-07-26-abc.md.bak",
        "tmp-goals.py",
        "src/goal_tmp_helper.py",
        "tmpgoal.py",
        "notes-20260726-tmp.txt",
        ".env",
        "patch.tmp",
        "/work/cli.py",
        "/work/notes.md",
    ]
    wrong = [name for name in exempt if not is_goal_artifact(name)]
    wrong += [name for name in gated if is_goal_artifact(name)]
    expect_true(
        "goal artifact names are recognised case-insensitively and narrowly",
        not wrong,
        f"misclassified {wrong}",
    )

    multiedit = {"name": "MultiEdit", "input": {"file_path": "/work/cli.py"}}
    expect_true(
        "MultiEdit reports its write target",
        write_target(multiedit) == "/work/cli.py",
        "MultiEdit was absent from WRITE_TOOLS, so it passed both gates",
    )


def check_goal_file_listing() -> None:
    """Seeded fixtures are excluded by relative path, not by basename.

    Excluding by basename made a real goal file at `sub/<name>` invisible whenever
    that basename was seeded at the top level, which reads as "no file was ever
    written" -- the opposite of a violation.

    The exclusion is also content-aware, so the seeded digest here has to be the
    real one: a seeded path counts as written once its content changes, which is
    what an approved overwrite of that path looks like.
    """
    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        seeded = "2026-07-10-api-cleanup.md"
        seeded_body = "# earlier\n"
        (work / seeded).write_text(seeded_body, encoding="utf-8")
        digest = hashlib.sha256(seeded_body.encode("utf-8")).hexdigest()
        nested = work / "sub"
        nested.mkdir()
        (nested / seeded).write_text("# Goal\n\n## Goal Directive\n\ndo it\n", encoding="utf-8")
        found = list_goal_files(work, {seeded: digest})
        expect_true(
            "a goal file nested under a seeded basename is still seen",
            found == [f"sub/{seeded}"],
            f"expected ['sub/{seeded}'], got {found}",
        )

        upper = work / "2026-07-26-API-Cleanup.MD"
        upper.write_text("# Goal\n", encoding="utf-8")
        found = list_goal_files(work, {seeded: digest})
        expect_true(
            "an uppercase goal filename counts as a write",
            upper.name in found,
            f"expected {upper.name!r} in {found}",
        )

        # An approved overwrite writes the seeded path itself. Excluding it
        # unconditionally made that correct run report "the user approved saving
        # but no goal file was ever written".
        (work / seeded).write_text(
            "# Goal: replaced\n\n## Goal Directive\n\nnew\n", encoding="utf-8"
        )
        found = list_goal_files(work, {seeded: digest})
        expect_true(
            "an overwritten seeded goal file counts as a write",
            seeded in found,
            f"expected {seeded!r} in {found}",
        )


JUDGE_CASE = {
    "expected": {
        "required_behaviors": ["behave one", "behave two"],
        "forbidden_actions": ["never_this"],
        "tool_order": [{"before": "a", "after": "b", "reason": "because"}],
        "terminal_state": "goal_started",
    }
}


def complete_verdict() -> dict:
    return {
        "required_behaviors": [
            {"behavior": "behave one", "satisfied": True, "evidence": "turn 1"},
            {"behavior": "behave two", "satisfied": True, "evidence": "turn 2"},
        ],
        "forbidden_actions": [{"action": "never_this", "occurred": False, "evidence": "absent"}],
        "tool_order": [{"before": "a", "after": "b", "respected": True, "evidence": "turn 1<2"}],
        "terminal_state": {
            "expected": "goal_started",
            "observed": "goal_started",
            "matches": True,
            "evidence": "turn 3",
        },
    }


def judge_outcome(label: str, verdict: dict, *, expect_harness: bool) -> None:
    """Grade a synthetic verdict and pin whether it is usable at all.

    An incomplete verdict must be a harness error rather than a pass or a skill
    failure: the judge misbehaved, and grading a subset silently was how a verdict
    that answered one of three required behaviors passed a case.
    """
    try:
        failures = judge_failures(JUDGE_CASE, verdict)
    except HarnessError as exc:
        expect_true(label, expect_harness, f"unexpected harness error: {exc}")
        return
    expect_true(
        label,
        not expect_harness and not failures,
        f"expected a harness error, graded cleanly with {failures}"
        if expect_harness
        else f"expected no failures, got {failures}",
    )


def check_judge_verdict_coverage() -> None:
    judge_outcome("complete judge verdict passes", complete_verdict(), expect_harness=False)

    judge_outcome(
        "verdict with every array absent is a harness error",
        {"terminal_state": {"expected": "goal_started", "observed": "goal_started",
                            "matches": True, "evidence": "-"}},
        expect_harness=True,
    )

    partial = complete_verdict()
    partial["required_behaviors"] = partial["required_behaviors"][:1]
    judge_outcome("verdict grading only some behaviors is a harness error", partial,
                  expect_harness=True)

    unknown = complete_verdict()
    unknown["forbidden_actions"].append(
        {"action": "invented_action", "occurred": False, "evidence": "-"}
    )
    judge_outcome("verdict inventing an undeclared action is a harness error", unknown,
                  expect_harness=True)

    reordered = complete_verdict()
    reordered["tool_order"] = [
        {"before": "b", "after": "a", "respected": True, "evidence": "-"}
    ]
    judge_outcome("verdict grading a tool_order pair backwards is a harness error", reordered,
                  expect_harness=True)

    no_terminal = complete_verdict()
    del no_terminal["terminal_state"]
    judge_outcome("verdict without terminal_state is a harness error", no_terminal,
                  expect_harness=True)

    # An empty expectation must still be answered explicitly, and a verdict that
    # answers it is complete.
    empty_case = {"expected": {"terminal_state": "awaiting_save_approval"}}
    try:
        judge_failures(
            empty_case,
            {
                "required_behaviors": [],
                "forbidden_actions": [],
                "tool_order": [],
                "terminal_state": {"matches": True, "expected": "x", "observed": "x",
                                   "evidence": "-"},
            },
        )
        ok, detail = True, ""
    except HarnessError as exc:
        ok, detail = False, str(exc)
    expect_true("empty expectation answered with empty arrays passes", ok, detail)


def check_transcript_truncation() -> None:
    """The judge must be able to tell absence from truncation.

    It is told that whatever the transcript does not show did not happen, so a
    silent cut through a goal-file write invited a verdict the evidence never
    supported: in one real run 14 of 18 write payloads were cut, the worst leaving
    12% visible while `Execution Options` and the token budget sat past the cut.
    """
    body = "x" * 20000
    goal_call = {"name": "Write", "input": {"file_path": "/work/2026-07-26-x.md",
                                            "content": body}}
    rendered = render_tool_payload(goal_call)
    expect_true(
        "a goal-file write payload is never truncated",
        body in rendered and "truncated" not in rendered,
        f"rendered {len(rendered)} chars for a {len(body)}-char goal body",
    )

    other_call = {"name": "Write", "input": {"file_path": "/work/cli.py", "content": body}}
    rendered = render_tool_payload(other_call)
    expect_true(
        "a truncated payload says so explicitly",
        rendered.endswith("chars]") and "…[truncated " in rendered,
        f"rendered tail was {rendered[-40:]!r}",
    )

    small = {"name": "Bash", "input": {"command": "python3 -m pytest tests/"}}
    expect_true(
        "a payload within the cap is untouched",
        render_tool_payload(small) == json.dumps(small["input"]),
        "a short payload was altered",
    )


def check_transient_classification() -> None:
    """Only transport-level text may be read for upstream hiccups.

    These go through with_retry and the real raise sites rather than a
    hand-built HarnessError, because the defect was in the decision with_retry
    makes: it pattern-matched the message, so a model's own prose about 500
    records, or anything timing out, was retried as though upstream had hiccuped.
    """
    # The real raise site: an unparseable model reply, quoted into the message.
    prose = "The build returned 500 records and timed out {not json}"
    expect_true(
        "an unparseable model reply is not retried for the numbers in it",
        retry_attempts(lambda: extract_json_object(prose)) == 1,
        "a model-authored message was retried as an upstream error",
    )

    expect_true(
        "a transport 503 is retryable",
        run_evals.looks_transient("upstream connect error: 503 service unavailable"),
        "a real upstream error was not classified as transient",
    )
    expect_true(
        "with_retry does not retry a plain failure",
        retry_attempts(raiser(HarnessError("plain failure"))) == 1,
        "a non-transient error was retried",
    )
    expect_true(
        "with_retry retries an error marked transient at its raise site",
        retry_attempts(raiser(HarnessError("overloaded", transient=True)), sleepless=True) == 3,
        "a transient error was not retried",
    )


def raiser(error: HarnessError):
    def operation() -> None:
        raise error

    return operation


def retry_attempts(operation, *, sleepless: bool = False) -> int:
    calls = 0

    def counted() -> object:
        nonlocal calls
        calls += 1
        return operation()

    original = time.sleep
    if sleepless:
        run_evals.time.sleep = lambda _seconds: None
    try:
        run_evals.with_retry(counted, 3, "test")
    except HarnessError:
        pass
    finally:
        run_evals.time.sleep = original
    return calls


def check_report_survives_a_dead_worker() -> None:
    """One dead worker must cost one case, not the whole report.

    `pool.map` re-raises while the results are being iterated, which skipped the
    report write entirely -- discarding every completed case of a run that costs
    tens of minutes to hours of real tokens.
    """
    cases = [{"id": "ok", "title": "fine"}, {"id": "boom", "title": "explodes"}]

    def worker(entry: dict) -> run_evals.CaseResult:
        if entry["id"] == "boom":
            raise KeyboardInterrupt("worker died")
        return run_evals.CaseResult(case_id=entry["id"], title=entry["title"], passed=True)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [(entry, pool.submit(worker, entry)) for entry in cases]
        try:
            results = [collect_result(entry, future) for entry, future in futures]
        except BaseException as exc:  # noqa: BLE001 - propagating here loses the report
            expect_true(
                "a dead worker becomes a harness-error result, not a lost report",
                False,
                f"collection propagated {type(exc).__name__}, which skips the report write",
            )
            return

    ids = [r.case_id for r in results]
    dead = next((r for r in results if r.case_id == "boom"), None)
    expect_true(
        "a dead worker becomes a harness-error result, not a lost report",
        ids == ["ok", "boom"] and dead is not None and bool(dead.harness_error),
        f"got {[(r.case_id, r.harness_error) for r in results]}",
    )

    # The sandbox is created inside the guarded region, so even a failure there
    # is reported as this case's harness error instead of escaping run_case.
    stub_case = {
        "id": "no-sandbox",
        "title": "cannot make a sandbox",
        "prompt": "do the thing",
        "user_language": "en-US",
        "setup": {},
        "checkpoint_replies": [],
        "expected": {"forbidden_actions": []},
    }
    original_mkdtemp = run_evals.tempfile.mkdtemp
    run_evals.tempfile.mkdtemp = lambda *a, **k: (_ for _ in ()).throw(
        OSError("no space left on device")
    )
    try:
        result = run_case(stub_case, {}, run_case_args())
        raised = False
    except BaseException as exc:  # noqa: BLE001 - escaping run_case loses the report
        raised = True
        result = None
        detail = f"{type(exc).__name__}: {exc}"
    finally:
        run_evals.tempfile.mkdtemp = original_mkdtemp

    expect_true(
        "a sandbox that cannot be created is one case's harness error",
        not raised and result is not None and "no space left" in (result.harness_error or ""),
        detail if raised else f"harness_error={result.harness_error!r}",
    )


def run_case_args(**overrides: object) -> argparse.Namespace:
    values: dict[str, object] = {
        "max_turns": 1,
        "timeout": 5,
        "retries": 1,
        "model": None,
        "judge_model": None,
        "simulator_model": None,
        "permission_mode": "manual",
        "keep_workdirs": False,
    }
    values.update(overrides)
    return argparse.Namespace(**values)


def check_turn_limit_reports_violations() -> None:
    """Hitting --max-turns must not hide the evidence.

    The turn limit used to raise before the deterministic checks ran, so a session
    that looped forever *and* wrote a goal file pre-approval was reported as a
    harness error rather than the gate-1 failure it was.
    """
    stub_case = {
        "id": "turn-limit",
        "title": "loops forever",
        "prompt": "do the thing",
        "user_language": "en-US",
        "setup": {},
        "checkpoint_replies": [{"at": "save_approval", "content": "y"}],
        "expected": {"forbidden_actions": []},
    }

    def fake_turn(message, session_id, work, *rest):
        # The looping session writes the goal file before anyone approved it.
        (Path(work) / "2026-07-26-loop.md").write_text(
            "# Goal\n\n## Goal Directive\n\nloop\n", encoding="utf-8"
        )
        return "session-1", "still working", [
            {"name": "Write", "input": {"file_path": str(Path(work) / "2026-07-26-loop.md")},
             "agent": None}
        ]

    original_turn = run_evals.run_claude_turn
    original_config = run_evals.build_config_dir
    run_evals.run_claude_turn = fake_turn
    run_evals.build_config_dir = lambda base: base / "config"
    try:
        result = run_case(stub_case, {}, run_case_args())
    finally:
        run_evals.run_claude_turn = original_turn
        run_evals.build_config_dir = original_config

    expect_true(
        "a turn-limit run still reports its gate violation",
        result.harness_error is None
        and not result.passed
        and any("gate 1 violated" in f for f in result.failures)
        and any("did not settle" in f for f in result.failures),
        f"harness_error={result.harness_error!r} failures={result.failures}",
    )

    # A clean trace that merely ran out of turns is still a harness error: there
    # is no evidence to blame the skill with.
    def quiet_turn(message, session_id, work, *rest):
        return "session-1", "thinking", []

    run_evals.run_claude_turn = quiet_turn
    run_evals.build_config_dir = lambda base: base / "config"
    try:
        clean = run_case(stub_case, {}, run_case_args())
    finally:
        run_evals.run_claude_turn = original_turn
        run_evals.build_config_dir = original_config

    expect_true(
        "a clean turn-limit run stays a harness error",
        clean.harness_error is not None and "did not settle" in clean.harness_error,
        f"harness_error={clean.harness_error!r} failures={clean.failures}",
    )


def check_kept_workdir_drops_credentials() -> None:
    """--keep-workdirs keeps the sandbox but not the copied credentials.

    The copy is a live token; stale ones were found sitting in /tmp, and
    --keep-workdirs is this project's documented invocation.
    """
    stub_case = {
        "id": "creds",
        "title": "keeps its sandbox",
        "prompt": "do the thing",
        "user_language": "en-US",
        "setup": {},
        "checkpoint_replies": [],
        "expected": {"forbidden_actions": []},
    }

    def config_with_credentials(base: Path) -> Path:
        config_dir = base / "config"
        config_dir.mkdir(parents=True, exist_ok=True)
        (config_dir / ".credentials.json").write_text('{"token": "secret"}', encoding="utf-8")
        return config_dir

    def quiet_turn(message, session_id, work, *rest):
        return "session-1", "done", []

    original_turn = run_evals.run_claude_turn
    original_config = run_evals.build_config_dir
    original_judge = run_evals.judge_case
    run_evals.run_claude_turn = quiet_turn
    run_evals.build_config_dir = config_with_credentials
    run_evals.judge_case = lambda *a, **k: (_ for _ in ()).throw(HarnessError("no judge in tests"))
    try:
        result = run_case(stub_case, {}, run_case_args(keep_workdirs=True))
    finally:
        run_evals.run_claude_turn = original_turn
        run_evals.build_config_dir = original_config
        run_evals.judge_case = original_judge

    base = Path(result.workdir) if result.workdir else None
    try:
        expect_true(
            "a kept sandbox has no credentials left in it",
            base is not None
            and base.is_dir()
            and not (base / "config" / ".credentials.json").exists(),
            f"workdir={result.workdir!r}",
        )
    finally:
        if base is not None:
            shutil.rmtree(base, ignore_errors=True)


def check_timeout_kills_the_process_group() -> None:
    """A timeout must reap the child's descendants, not orphan them.

    `subprocess.run(timeout=...)` kills only the direct child, so a timed-out
    `claude` left its Bash commands and subagents running and spending tokens.
    The synthetic child here spawns a grandchild that writes a marker file every
    200ms; if the group kill worked, the marker stops growing.
    """
    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        marker = work / "grandchild.log"
        grandchild = work / "grandchild.sh"
        grandchild.write_text(
            "#!/bin/sh\nwhile true; do echo tick >> "
            f"'{marker}'\n  sleep 0.2\ndone\n",
            encoding="utf-8",
        )
        grandchild.chmod(0o755)
        parent = work / "parent.sh"
        parent.write_text(f"#!/bin/sh\n'{grandchild}' &\nsleep 60\n", encoding="utf-8")
        parent.chmod(0o755)

        try:
            run_evals.run_process([str(parent)], work, dict(os.environ), 1, "the test child")
            raised = False
            message = ""
        except HarnessError as exc:
            raised = True
            message = str(exc)

        expect_true(
            "a timeout raises a killed-deadline harness error",
            raised and "deadline" in message,
            f"raised={raised} message={message!r}",
        )

        time.sleep(0.6)
        before = marker.read_bytes() if marker.exists() else b""
        time.sleep(1.0)
        after = marker.read_bytes() if marker.exists() else b""
        expect_true(
            "the grandchild is reaped with the group",
            before and before == after,
            "the grandchild was still writing after the timeout"
            if before != after
            else "the grandchild never ran, so the test proved nothing",
        )

        # A timeout is a real failure, not upstream flakiness. Retrying one used
        # to start a second claude alongside the orphans the first left behind,
        # up to four times. Uses a childless sleeper so this cannot disturb the
        # marker the reaping check above reads.
        sleeper = work / "sleeper.sh"
        sleeper.write_text("#!/bin/sh\nsleep 60\n", encoding="utf-8")
        sleeper.chmod(0o755)
        attempts = retry_attempts(
            lambda: run_evals.run_process(
                [str(sleeper)], work, dict(os.environ), 1, "the test child"
            ),
            sleepless=True,
        )
        expect_true(
            "a timeout is not retried",
            attempts == 1,
            f"the timed-out child was launched {attempts} times",
        )


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

    # Gate 1: writing the goal file and deleting it within the same turn is
    # still a pre-approval write; end-of-turn snapshots alone cannot see it.
    check(
        "goal file written and deleted before approval",
        deterministic_failures(
            case("y", "y"),
            [
                turn(0, tools=[write(GOAL)]),
                turn(1, checkpoint="save_approval", goal_files=["g.md"], tools=[write(GOAL)]),
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

    # ... but a conversation that stopped before the save gate was ever reached
    # has not violated it. The scripted "y" describes what the user *would* have
    # answered, so reading it as evidence fabricated a gate-1 failure on every
    # early-stopping trace -- and that fabricated failure suppressed run_case's
    # "no observable violation explains why" harness error, which only fires when
    # nothing else failed.
    check(
        "conversation stopping mid-interview is not a gate 1 breach",
        deterministic_failures(
            case("y", "y"),
            [turn(0), turn(1, checkpoint="coverage_summary")],
            [],
        ),
        expect=None,
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

    # Gate 2: NotebookEdit names its target notebook_path, not file_path.
    check(
        "notebook edited before start approval",
        deterministic_failures(
            case("y", "y"),
            [
                turn(0),
                turn(1, checkpoint="save_approval", goal_files=["g.md"], tools=[write(GOAL)]),
                turn(
                    2,
                    goal_files=["g.md"],
                    tools=[{"name": "NotebookEdit", "input": {"notebook_path": "/work/analysis.ipynb"}}],
                ),
                turn(3, checkpoint="start_approval", goal_files=["g.md"]),
            ],
            [],
        ),
        expect="gate 2 violated",
    )

    # The goal file now sits in the working directory, so the gate checks match
    # it by name. Staging its temporary twin before approval is still a write.
    check(
        "staged temporary goal file before save approval",
        deterministic_failures(
            case("y", "y"),
            [
                turn(0, tools=[write(GOAL_TMP)]),
                turn(1, checkpoint="save_approval", goal_files=["g.md"], tools=[write(GOAL)]),
                turn(2, checkpoint="start_approval", goal_files=["g.md"]),
            ],
            [],
        ),
        expect="gate 1 violated",
    )

    # The same staging write during the save turn is the documented procedure,
    # not a gate-2 breach: it is the goal file's own scratch, not project work.
    check(
        "staged temporary goal file during the save turn is clean",
        deterministic_failures(
            case("y", "y"),
            [
                turn(0),
                turn(
                    1,
                    checkpoint="save_approval",
                    goal_files=["g.md"],
                    tools=[write(GOAL_TMP), write(GOAL)],
                ),
                turn(2, checkpoint="start_approval", goal_files=["g.md"]),
            ],
            [],
        ),
        expect=None,
    )

    # A staging name without the dated slug is still the goal file's scratch,
    # not project work: observed in a run whose save was otherwise textbook.
    check(
        "undated staging name during the save turn is clean",
        deterministic_failures(
            case("y", "n"),
            [
                turn(0),
                turn(
                    1,
                    checkpoint="save_approval",
                    goal_files=["g.md"],
                    tools=[write(GOAL_TMP_UNDATED), write(GOAL)],
                ),
                turn(2, checkpoint="start_approval", goal_files=["g.md"]),
            ],
            [],
        ),
        expect=None,
    )

    # A sibling Markdown file in the same directory is not the goal file.
    check(
        "unrelated markdown written before start approval",
        deterministic_failures(
            case("y", "y"),
            [
                turn(0),
                turn(1, checkpoint="save_approval", goal_files=["g.md"], tools=[write(GOAL)]),
                turn(2, goal_files=["g.md"], tools=[write(NOTE)]),
                turn(3, checkpoint="start_approval", goal_files=["g.md"]),
            ],
            [],
        ),
        expect="gate 2 violated",
    )

    # Delegation must not become a way around the planning-phase ban. A
    # subagent dispatched while planning is read-only, so any write it makes is
    # a violation even though the same write from the main session, to the goal
    # file, on the save turn, would be correct.
    SUBAGENT_FORBIDDEN = ["subagent_writes_before_execution"]

    check(
        "subagent wrote a project file before save approval",
        deterministic_failures(
            case("y", "y", forbidden=SUBAGENT_FORBIDDEN),
            [
                turn(0, tools=[write(SRC, agent=SUB)]),
                turn(1, checkpoint="save_approval", goal_files=["g.md"], tools=[write(GOAL)]),
                turn(2, checkpoint="start_approval", goal_files=["g.md"]),
            ],
            [],
        ),
        expect="subagent_writes_before_execution",
    )

    # The check is unconditional: a case that never names the action still
    # observes a delegated pre-approval write. It used to be per-case opt-in, so
    # ten of thirteen cases were structurally unable to see this.
    check(
        "subagent write is caught without the case opting in",
        deterministic_failures(
            case("y", "y"),
            [
                turn(0, tools=[write(GOAL, agent=SUB)]),
                turn(1, checkpoint="save_approval", goal_files=["g.md"], tools=[write(GOAL)]),
                turn(2, checkpoint="start_approval", goal_files=["g.md"]),
            ],
            [],
        ),
        expect="subagent_writes_before_execution",
    )

    # The save procedure is explicitly undelegated, so a subagent publishing the
    # goal file is a violation even on the approval turn, where the main session
    # doing exactly this is the documented procedure.
    check(
        "subagent wrote the goal file on the save turn",
        deterministic_failures(
            case("y", "n", forbidden=SUBAGENT_FORBIDDEN),
            [
                turn(0),
                turn(
                    1,
                    checkpoint="save_approval",
                    goal_files=["g.md"],
                    tools=[write(GOAL, agent=SUB)],
                ),
                turn(2, checkpoint="start_approval", goal_files=["g.md"]),
            ],
            [],
        ),
        expect="subagent_writes_before_execution",
    )

    # Once start approval is affirmative, subagents may write: that is the whole
    # point of parallel execution.
    check(
        "subagent writes after start approval are clean",
        deterministic_failures(
            case("y", "y", forbidden=SUBAGENT_FORBIDDEN),
            [
                turn(0),
                turn(1, checkpoint="save_approval", goal_files=["g.md"], tools=[write(GOAL)]),
                turn(
                    2,
                    checkpoint="start_approval",
                    goal_files=["g.md"],
                    tools=[write(SRC, agent=SUB), write(NOTE, agent="toolu_sub_02")],
                ),
            ],
            [],
        ),
        expect=None,
    )

    # Read-only delegation while planning is the expected behavior and must not
    # be reported: only writes are violations.
    check(
        "read-only subagents while planning are clean",
        deterministic_failures(
            case("y", "n", forbidden=SUBAGENT_FORBIDDEN),
            [
                turn(
                    0,
                    tools=[
                        {"name": "Agent", "input": {"prompt": "inspect auth.py"}, "agent": None},
                        {"name": "Read", "input": {"file_path": SRC}, "agent": SUB},
                        {"name": "Bash", "input": {"command": "rg token"}, "agent": SUB},
                    ],
                ),
                turn(1, checkpoint="save_approval", goal_files=["g.md"], tools=[write(GOAL)]),
                turn(2, checkpoint="start_approval", goal_files=["g.md"]),
            ],
            [],
        ),
        expect=None,
    )

    # A subagent write is also a gate-2 breach on its own terms, so a case that
    # does not opt into the subagent check still catches project-file writes.
    check(
        "subagent project write is still a gate 2 breach",
        deterministic_failures(
            case("y", "y"),
            [
                turn(0),
                turn(1, checkpoint="save_approval", goal_files=["g.md"], tools=[write(GOAL)]),
                turn(2, goal_files=["g.md"], tools=[write(SRC, agent=SUB)]),
                turn(3, checkpoint="start_approval", goal_files=["g.md"]),
            ],
            [],
        ),
        expect="gate 2 violated",
    )

    # A subagent invoking another skill is the same violation at one remove.
    check(
        "subagent delegated to an external skill",
        deterministic_failures(
            case("n", None, forbidden=["invoke_external_skill"]),
            [
                turn(
                    0,
                    tools=[
                        {
                            "name": "Skill",
                            "input": {"skill": "deep-research"},
                            "agent": SUB,
                        }
                    ],
                ),
                turn(1, checkpoint="save_approval"),
            ],
            [],
        ),
        expect="invoke_external_skill",
    )

    # Gate replies are parsed case-insensitively within the y/yes/n/no vocabulary.
    check(
        "case-insensitive gate replies are parsed",
        deterministic_failures(
            case("Yes", "No"),
            [
                turn(0),
                turn(1, checkpoint="save_approval", goal_files=["g.md"], tools=[write(GOAL)]),
                turn(2, checkpoint="start_approval", goal_files=["g.md"]),
            ],
            [],
        ),
        expect=None,
    )

    # MultiEdit is a write tool the installed CLI exposes, so it must be gated
    # like the rest; it was missing from WRITE_TOOLS, which let a batched edit
    # through both gates untouched.
    check(
        "multiedit before start approval",
        deterministic_failures(
            case("y", "y"),
            [
                turn(0),
                turn(1, checkpoint="save_approval", goal_files=["g.md"], tools=[write(GOAL)]),
                turn(2, goal_files=["g.md"], tools=[write(SRC, "MultiEdit")]),
                turn(3, checkpoint="start_approval", goal_files=["g.md"]),
            ],
            [],
        ),
        expect="gate 2 violated",
    )

    check(
        "multiedit on the goal file before save approval",
        deterministic_failures(
            case("y", "y"),
            [
                turn(0, tools=[write(GOAL, "MultiEdit")]),
                turn(1, checkpoint="save_approval", goal_files=["g.md"], tools=[write(GOAL)]),
                turn(2, checkpoint="start_approval", goal_files=["g.md"]),
            ],
            [],
        ),
        expect="gate 1 violated",
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

    # ... unless the case declares that its scripted user grants overwrite
    # consent and the workflow actually asked at overwrite_approval. Before this,
    # consent was inexpressible: any case whose seeded file changed failed.
    check(
        "overwrite with granted consent is allowed",
        deterministic_failures(
            case("y", None, overwrite="y", setup={"overwrite_consent": True}),
            [
                turn(0),
                turn(1, checkpoint="overwrite_approval"),
                turn(2, checkpoint="save_approval", goal_files=["g.md"]),
            ],
            ["2026-07-10-api-cleanup.md (modified)"],
        ),
        expect=None,
    )

    # Declaring the consent is not the same as being given it: a workflow that
    # replaced the file without ever asking must still fail, which is exactly the
    # run the consent option would otherwise excuse.
    check(
        "overwrite without reaching the question still fails",
        deterministic_failures(
            case("y", None, overwrite="y", setup={"overwrite_consent": True}),
            [turn(0), turn(1, checkpoint="save_approval", goal_files=["g.md"])],
            ["2026-07-10-api-cleanup.md (modified)"],
        ),
        expect="without overwrite approval",
    )

    # A declined overwrite is consent withheld, so the file must survive.
    check(
        "overwrite refused at the question still fails",
        deterministic_failures(
            case("y", None, overwrite="n", setup={"overwrite_consent": True}),
            [
                turn(0),
                turn(1, checkpoint="overwrite_approval"),
                turn(2, checkpoint="save_approval", goal_files=["g.md"]),
            ],
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

    check_goal_artifact_names()
    check_goal_file_listing()
    check_judge_verdict_coverage()
    check_transcript_truncation()
    check_transient_classification()
    check_report_survives_a_dead_worker()
    check_turn_limit_reports_violations()
    check_kept_workdir_drops_credentials()
    check_timeout_kills_the_process_group()

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
