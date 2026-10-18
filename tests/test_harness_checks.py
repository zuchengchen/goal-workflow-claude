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
import signal
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
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
        # Staging decoration may also precede the destination name. A live run
        # staged exactly this and gate 2 called it a pre-approval project write.
        "/work/.goalstage_2026-07-26-api-cleanup.md.tmp",
        "/work/.2026-07-26-api-cleanup.md.stage",
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

    # Two consecutive live runs echoed a declared behavior back with a backtick
    # dropped, then with a filename inserted inside the quoted command. Each time
    # the whole verdict was discarded as ungradable, correctly graded behaviors
    # and all. Recognising the label has to survive that drift.
    long_declared = (
        "Replace the bare negated `! rg '^!'` heuristic with producer-native status "
        "or a TeX-aware calibrated check rather than treating every bang-prefixed "
        "line as an error."
    )
    drift_case = {
        "expected": {
            "required_behaviors": [long_declared, "Keep asking one question at a time."],
            "forbidden_actions": ["never_this"],
            "tool_order": [{"before": "a", "after": "b", "reason": "because"}],
            "terminal_state": "goal_started",
        }
    }
    for label, echoed in (
        ("a dropped backtick", long_declared.replace("'^!'`", "'^!'")),
        ("an inserted filename", long_declared.replace("'^!'`", "'^!' appendix.log`")),
    ):
        drifted = complete_verdict()
        drifted["required_behaviors"] = [
            {"behavior": echoed, "satisfied": True, "evidence": "turn 1"},
            {
                "behavior": "Keep asking one question at a time.",
                "satisfied": True,
                "evidence": "turn 2",
            },
        ]
        try:
            failures = judge_failures(drift_case, drifted)
            ok, detail = not failures, f"graded with {failures}"
        except HarnessError as exc:
            ok, detail = False, f"rejected a recognisable verdict: {exc}"
        expect_true(f"a verdict echoing a behavior with {label} is still graded", ok, detail)

    # Tolerating drift must not tolerate a behavior nobody declared.
    substituted = complete_verdict()
    substituted["required_behaviors"] = [
        {"behavior": long_declared, "satisfied": True, "evidence": "turn 1"},
        {
            "behavior": "Delete the appendix and skip verification entirely.",
            "satisfied": True,
            "evidence": "turn 2",
        },
    ]
    try:
        judge_failures(drift_case, substituted)
        swapped_ok, swapped_detail = False, "a substituted behavior was accepted as declared"
    except HarnessError:
        swapped_ok, swapped_detail = True, ""
    expect_true(
        "a verdict swapping in a different behavior is still a harness error",
        swapped_ok,
        swapped_detail,
    )

    no_terminal = complete_verdict()
    del no_terminal["terminal_state"]
    judge_outcome("verdict without terminal_state is a harness error", no_terminal,
                  expect_harness=True)

    # The interview depth was declared by every case and graded by nothing. A
    # declared mode is now part of what the verdict has to answer.
    mode_case = {"expected": {**JUDGE_CASE["expected"], "mode": "exhaustive"}}

    def with_mode(observed: str, matches: bool) -> dict:
        verdict = complete_verdict()
        verdict["mode"] = {"expected": "exhaustive", "observed": observed,
                           "matches": matches, "evidence": "turn 0"}
        return verdict

    for label, verdict, expected_failure in (
        ("a matching interview depth passes", with_mode("exhaustive", True), None),
        ("a different interview depth fails the case", with_mode("standard", False),
         "interview depth mismatch"),
    ):
        try:
            failures = judge_failures(mode_case, verdict)
            ok = (not failures) if expected_failure is None else any(
                expected_failure in item for item in failures
            )
            detail = f"got {failures}"
        except HarnessError as exc:
            ok, detail = False, f"unexpected harness error: {exc}"
        expect_true(label, ok, detail)

    for label, case_under_test, verdict in (
        ("a declared mode left ungraded is a harness error", mode_case, complete_verdict()),
        ("a mode graded where none is declared is a harness error", JUDGE_CASE,
         with_mode("exhaustive", True)),
    ):
        try:
            judge_failures(case_under_test, verdict)
            ok, detail = False, "graded cleanly"
        except HarnessError:
            ok, detail = True, ""
        expect_true(label, ok, detail)

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

    # Once the operator interrupts the run, nothing is retried: a retry would
    # start a fresh claude call while the harness is trying to stop them all.
    run_evals.STOP.set()
    try:
        attempts = retry_attempts(
            raiser(HarnessError("overloaded", transient=True)), sleepless=True
        )
    finally:
        run_evals.STOP.clear()
    expect_true(
        "an interrupted run retries nothing",
        attempts == 1,
        f"a transient error was attempted {attempts} times after the interrupt",
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

    original = run_evals.retry_pause
    if sleepless:
        run_evals.retry_pause = lambda _seconds: False
    try:
        run_evals.with_retry(counted, 3, "test")
    except HarnessError:
        pass
    finally:
        run_evals.retry_pause = original
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


@contextmanager
def fake_cli(stdout_lines: list[str]):
    """Put a stand-in `claude` first on PATH that prints fixed output.

    It records its argv, so a test can also check how the harness invoked it.
    """
    with tempfile.TemporaryDirectory() as raw:
        root = Path(raw)
        bin_dir = root / "bin"
        bin_dir.mkdir()
        argv_log = root / "argv.json"
        script = bin_dir / "claude"
        script.write_text(
            "#!/usr/bin/env python3\nimport json, sys\n"
            f"open({str(argv_log)!r}, 'w').write(json.dumps(sys.argv[1:]))\n"
            f"sys.stdout.write({chr(10).join(stdout_lines) + chr(10)!r})\n",
            encoding="utf-8",
        )
        script.chmod(0o755)
        (root / "scratch").mkdir()
        (root / "config").mkdir()
        saved = os.environ.get("PATH", "")
        os.environ["PATH"] = f"{bin_dir}{os.pathsep}{saved}"
        try:
            yield root, argv_log
        finally:
            os.environ["PATH"] = saved


def check_cli_error_results() -> None:
    """A CLI error result is a transport failure, never a model's reply.

    The judge and simulator path parsed the envelope's text without looking at
    `is_error`, and an upstream overload body is itself a JSON object: it was
    returned as though the model had written it, so the error was never
    retried and surfaced as an incomplete judge verdict.
    """
    overload = json.dumps({
        "type": "result",
        "is_error": True,
        "api_error_status": 529,
        "result": 'API Error: 529 {"type":"error","error":{"type":"overloaded_error"}}',
    })
    with fake_cli([overload]) as (root, argv_log):
        try:
            run_evals.ask_json("grade", None, 30, root / "scratch", root / "config")
            outcome = "returned the API error body as a reply"
        except HarnessError as exc:
            outcome = "transient" if exc.transient else f"non-transient: {exc}"
        argv = json.loads(argv_log.read_text(encoding="utf-8"))
    expect_true(
        "a judge/simulator API error is a retryable harness error",
        outcome == "transient",
        outcome,
    )
    tools = argv.index("--tools") if "--tools" in argv else -1
    expect_true(
        "graders run with no tools at all",
        tools >= 0 and argv[tools + 1] == "" and "--disallowed-tools" not in argv,
        f"argv was {argv}",
    )

    reply = json.dumps({"type": "result", "is_error": False, "result": '{"ok": true}'})
    with fake_cli([reply]) as (root, _argv_log):
        parsed = run_evals.ask_json("ping", None, 30, root / "scratch", root / "config")
    expect_true("a normal envelope still parses", parsed == {"ok": True}, f"got {parsed!r}")

    # A turn that already called a tool must not be retried: the retry would
    # resend the user's message and drop the call from the evidence.
    acted = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "s-1"}),
        json.dumps({
            "type": "assistant",
            "parent_tool_use_id": None,
            "message": {"content": [
                {"type": "tool_use", "name": "Write", "input": {"file_path": "/work/cli.py"}}
            ]},
        }),
        json.dumps({"type": "result", "is_error": True, "session_id": "s-1",
                    "result": "API Error: 529 overloaded"}),
    ]
    with fake_cli(acted) as (root, _argv_log):
        try:
            run_evals.run_claude_turn(
                "y", None, root / "scratch", root / "config", None, "bypassPermissions", 30
            )
            caught = None
        except HarnessError as exc:
            caught = exc
    expect_true(
        "a turn that acted before failing is recorded, not retried",
        isinstance(caught, run_evals.InterruptedTurn)
        and not caught.transient
        and [call["input"]["file_path"] for call in caught.tool_calls] == ["/work/cli.py"],
        f"got {caught!r}",
    )

    # One that failed before doing anything is a plain upstream hiccup.
    idle = [
        json.dumps({"type": "system", "subtype": "init", "session_id": "s-1"}),
        json.dumps({"type": "result", "is_error": True, "session_id": "s-1",
                    "api_error_status": 529, "result": "API Error: 529"}),
    ]
    with fake_cli(idle) as (root, _argv_log):
        try:
            run_evals.run_claude_turn(
                "y", None, root / "scratch", root / "config", None, "bypassPermissions", 30
            )
            caught = None
        except HarnessError as exc:
            caught = exc
    expect_true(
        "a turn that failed before acting stays retryable",
        caught is not None
        and caught.transient
        and not isinstance(caught, run_evals.InterruptedTurn),
        f"got {caught!r}",
    )


def check_interrupted_turn_evidence() -> None:
    """The partial turn's calls feed the gate checks before anything is concluded."""
    stub_case = {
        "id": "interrupted",
        "title": "fails mid-turn",
        "prompt": "do the thing",
        "user_language": "en-US",
        "setup": {},
        "checkpoint_replies": [{"at": "save_approval", "content": "y"}],
        "expected": {"forbidden_actions": []},
    }

    def turn_writing_goal(message, session_id, work, *rest):
        raise run_evals.InterruptedTurn(
            "claude returned an error result: API Error: 529",
            session_id="s-1",
            assistant_text="",
            tool_calls=[{"name": "Write", "agent": None,
                         "input": {"file_path": str(Path(work) / "2026-07-26-early.md")}}],
        )

    def quiet_failure(message, session_id, work, *rest):
        raise run_evals.InterruptedTurn(
            "claude returned an error result: API Error: 529",
            session_id="s-1",
            assistant_text="Let me look at the project first.",
            tool_calls=[],
        )

    original_turn = run_evals.run_claude_turn
    original_config = run_evals.build_config_dir
    run_evals.build_config_dir = lambda base: base / "config"
    try:
        run_evals.run_claude_turn = turn_writing_goal
        violating = run_case(stub_case, {}, run_case_args(max_turns=5))
        run_evals.run_claude_turn = quiet_failure
        clean = run_case(stub_case, {}, run_case_args(max_turns=5))
    finally:
        run_evals.run_claude_turn = original_turn
        run_evals.build_config_dir = original_config

    expect_true(
        "a pre-approval write in a failed turn is still a gate failure",
        violating.harness_error is None
        and any("gate 1 violated" in f for f in violating.failures)
        and any("turn 0 failed" in f for f in violating.failures),
        f"harness_error={violating.harness_error!r} failures={violating.failures}",
    )
    expect_true(
        "a clean failed turn is a harness error that keeps its transcript",
        clean.harness_error is not None
        and "partial turn" in clean.harness_error
        and len(clean.turns) == 1
        and clean.turns[0].assistant_text.startswith("Let me look"),
        f"harness_error={clean.harness_error!r} turns={len(clean.turns)}",
    )


def check_last_turn_completion_is_judged() -> None:
    """A script finished on the final allowed turn is complete, not unsettled."""
    stub_case = {
        "id": "exact-fit",
        "title": "uses every turn",
        "prompt": "do the thing",
        "user_language": "en-US",
        "setup": {},
        "checkpoint_replies": [{"at": "coverage_summary", "content": "draft it"}],
        "expected": {"forbidden_actions": [], "required_behaviors": [], "tool_order": [],
                     "terminal_state": "awaiting_save_approval"},
    }
    originals = (run_evals.run_claude_turn, run_evals.build_config_dir,
                 run_evals.simulate_user, run_evals.judge_case)
    run_evals.run_claude_turn = lambda *a, **k: ("s-1", "summary shown", [])
    run_evals.build_config_dir = lambda base: base / "config"
    run_evals.simulate_user = lambda *a, **k: {
        "state": "checkpoint", "checkpoint": "coverage_summary", "reply": "draft it"
    }
    run_evals.judge_case = lambda *a, **k: {
        "required_behaviors": [], "forbidden_actions": [], "tool_order": [],
        "terminal_state": {"expected": "x", "observed": "x", "matches": True, "evidence": "-"},
    }
    try:
        result = run_case(stub_case, {}, run_case_args(max_turns=2))
    finally:
        (run_evals.run_claude_turn, run_evals.build_config_dir,
         run_evals.simulate_user, run_evals.judge_case) = originals
    expect_true(
        "a scenario completed on the last allowed turn is judged",
        result.harness_error is None and result.passed,
        f"harness_error={result.harness_error!r} failures={result.failures}",
    )


def check_interrupts_are_honoured() -> None:
    """Ctrl-C and SIGTERM stop a run, and an interrupted run still reports.

    collect_result caught every BaseException, including the operator's Ctrl-C
    arriving while it waited, so the run carried on. SIGTERM, which the
    documented `timeout` wrapper sends, killed the interpreter outright: no
    report, and every running sandbox kept its copy of the credentials.
    """
    release = threading.Event()
    with ThreadPoolExecutor(max_workers=1) as pool:
        future = pool.submit(release.wait, 30)
        timer = threading.Timer(0.3, lambda: os.kill(os.getpid(), signal.SIGINT))
        timer.start()
        try:
            collect_result({"id": "waiting", "title": "still running"}, future)
            propagated = False
        except KeyboardInterrupt:
            propagated = True
        finally:
            timer.cancel()
            release.set()
    expect_true(
        "Ctrl-C while waiting on a case propagates",
        propagated,
        "the interrupt was swallowed and reported as a dead worker",
    )

    with tempfile.TemporaryDirectory() as raw:
        out = Path(raw) / "out"
        sandboxes: list[Path] = []

        def fake_config(base: Path) -> Path:
            sandboxes.append(base)
            config = base / "config"
            config.mkdir(parents=True, exist_ok=True)
            (config / ".credentials.json").write_text('{"fake": true}', encoding="utf-8")
            return config

        def child_until_killed(*args, **kwargs):
            # Stands in for a claude child that runs until the interrupt kills it.
            run_evals.STOP.wait(30)
            raise run_evals.ProcessKilled(
                "the session under test was stopped: the run was interrupted", stdout=""
            )

        originals = (run_evals.build_config_dir, run_evals.run_claude_turn,
                     run_evals.preflight, run_evals.cli_version, run_evals.shutil.which)
        run_evals.build_config_dir = fake_config
        run_evals.run_claude_turn = child_until_killed
        run_evals.preflight = lambda args: None
        run_evals.cli_version = lambda: None
        run_evals.shutil.which = lambda name: "/usr/bin/claude"
        ids = ["narrow-fast-path", "reject-save-gate", "reject-start-gate"]
        timer = threading.Timer(1.0, lambda: os.kill(os.getpid(), signal.SIGTERM))
        started = time.monotonic()
        timer.start()
        try:
            code = run_evals.main(
                ["--jobs", "2", "--output", str(out)] + [arg for i in ids for arg in ("--case", i)]
            )
        finally:
            timer.cancel()
            (run_evals.build_config_dir, run_evals.run_claude_turn, run_evals.preflight,
             run_evals.cli_version, run_evals.shutil.which) = originals
            run_evals.STOP.clear()
        elapsed = time.monotonic() - started

        latest = out / "evals-report.json"
        stamped = sorted(out.glob("evals-report-*.json"))
        document = json.loads(latest.read_text(encoding="utf-8")) if latest.exists() else {}
        cases = document.get("cases", [])
        leftovers = [str(base) for base in sandboxes if (base / "config").exists()]

    expect_true(
        "SIGTERM stops the run promptly with exit status 143",
        code == 143 and elapsed < 20,
        f"exit={code} after {elapsed:.1f}s",
    )
    expect_true(
        "an interrupted run writes a timestamped report accounting for every case",
        len(stamped) == 1
        and document.get("meta", {}).get("interrupted") == "SIGTERM"
        and [case["id"] for case in cases] == ids
        and all(case["harness_error"] for case in cases),
        f"stamped={[p.name for p in stamped]} meta={document.get('meta')} "
        f"cases={[(c['id'], c['harness_error']) for c in cases]}",
    )
    expect_true(
        "no interrupted sandbox is left behind with its credentials",
        bool(sandboxes) and not leftovers,
        f"sandboxes={len(sandboxes)} leftovers={leftovers}",
    )


def check_preflight_covers_every_model() -> None:
    """The judge is first called after a whole conversation, so check it up front."""
    calls: list[str | None] = []

    def fake_ask(prompt, model, timeout, scratch, config_dir):
        calls.append(model)
        if model == "missing-judge":
            raise HarnessError("model not found")
        return {"ok": True}

    original_ask = run_evals.ask_json
    original_config = run_evals.build_config_dir
    run_evals.ask_json = fake_ask
    run_evals.build_config_dir = lambda base: base / "config"
    try:
        run_evals.preflight(argparse.Namespace(
            simulator_model="sim", judge_model="judge", model=None, timeout=5, retries=1
        ))
        checked = list(calls)
        calls.clear()
        try:
            run_evals.preflight(argparse.Namespace(
                simulator_model="sim", judge_model="missing-judge", model="sim",
                timeout=5, retries=1,
            ))
            message = ""
        except HarnessError as exc:
            message = str(exc)
    finally:
        run_evals.ask_json = original_ask
        run_evals.build_config_dir = original_config

    expect_true(
        "preflight checks the simulator, judge, and session models",
        checked == ["sim", "judge", None],
        f"checked {checked}",
    )
    expect_true(
        "a failing judge model is named, and a shared model is checked once",
        "judge model missing-judge" in message and calls == ["sim", "missing-judge"],
        f"message={message!r} calls={calls}",
    )


GOAL_WITH_CONDITION = (
    "# Goal: Fix the help typo\n\n"
    "## Goal Directive\n\nFollow the saved goal file at `/w/2026-07-26-typo.md` and complete it.\n\n"
    "## Goal Condition\n\n"
    "Every required verification item in `/w/2026-07-26-typo.md` has passed\n"
    "and its result has been reported, or execution has stopped at one of that file's\n"
    "stop conditions and asked the user how to proceed.\n\n"
    "## Full Prompt\n\n### Objective\n\nFix the typo.\n"
)
TYPED_CONDITION = (
    "Every required verification item in `/w/2026-07-26-typo.md` has passed and its result "
    "has been reported, or execution has stopped at one of that file's stop conditions and "
    "asked the user how to proceed."
)


def check_native_goal_reply() -> None:
    """A scripted `/goal` start reply opens gate 2 and is typed from the saved file."""
    check(
        "/goal at the start gate opens execution",
        deterministic_failures(
            case("y", "/goal"),
            [
                turn(0),
                turn(1, checkpoint="save_approval", goal_files=["g.md"], tools=[write(GOAL)]),
                turn(2, checkpoint="start_approval", goal_files=["g.md"], tools=[write(SRC)]),
            ],
            [],
        ),
        expect=None,
    )

    with tempfile.TemporaryDirectory() as raw:
        work = Path(raw)
        (work / "2026-07-26-typo.md").write_text(GOAL_WITH_CONDITION, encoding="utf-8")
        found = run_evals.saved_goal_condition(work, {})
    expect_true(
        "the saved Goal Condition is read as one typed line",
        found == TYPED_CONDITION,
        f"got {found!r}",
    )

    def run_with_saved(body: str) -> tuple[run_evals.CaseResult, list[str]]:
        stub_case = {
            "id": "native-goal",
            "title": "starts with /goal",
            "prompt": "do the thing",
            "user_language": "en-US",
            "setup": {},
            "checkpoint_replies": [
                {"at": "save_approval", "content": "y"},
                {"at": "start_approval", "content": "/goal"},
            ],
            "expected": {"forbidden_actions": [], "required_behaviors": [], "tool_order": [],
                         "terminal_state": "goal_started"},
        }
        sent: list[str] = []
        checkpoints = iter(["save_approval", "start_approval"])

        def fake_turn(message, session_id, work, *rest):
            sent.append(message)
            if message == "y":
                target = Path(work) / "2026-07-26-typo.md"
                target.write_text(body, encoding="utf-8")
                return "s-1", "Saved.", [write(str(target))]
            return "s-1", "Working.", []

        originals = (run_evals.run_claude_turn, run_evals.build_config_dir,
                     run_evals.simulate_user, run_evals.judge_case)
        run_evals.run_claude_turn = fake_turn
        run_evals.build_config_dir = lambda base: base / "config"
        run_evals.simulate_user = lambda *a, **k: {
            "state": "checkpoint", "checkpoint": next(checkpoints), "reply": "-"
        }
        run_evals.judge_case = lambda *a, **k: {
            "required_behaviors": [], "forbidden_actions": [], "tool_order": [],
            "terminal_state": {"expected": "x", "observed": "x", "matches": True,
                               "evidence": "-"},
        }
        try:
            return run_case(stub_case, {}, run_case_args(max_turns=6)), sent
        finally:
            (run_evals.run_claude_turn, run_evals.build_config_dir,
             run_evals.simulate_user, run_evals.judge_case) = originals

    result, sent = run_with_saved(GOAL_WITH_CONDITION)
    expect_true(
        "the start reply is sent as /goal with the saved condition",
        result.passed and sent[-1] == f"/goal {TYPED_CONDITION}",
        f"passed={result.passed} harness={result.harness_error!r} sent={sent}",
    )

    result, sent = run_with_saved("# Goal: no condition\n\n## Goal Directive\n\nFollow it.\n")
    expect_true(
        "a saved file without a Goal Condition fails the case instead of guessing one",
        not result.passed
        and result.harness_error is None
        and any("Goal Condition" in failure for failure in result.failures)
        and not any(message.startswith("/goal") for message in sent),
        f"harness={result.harness_error!r} failures={result.failures} sent={sent}",
    )


def check_spawn_env_keeps_auth() -> None:
    saved = dict(os.environ)
    os.environ.update({
        "CLAUDE_CODE_OAUTH_TOKEN": "token",
        "CLAUDE_CODE_USE_BEDROCK": "1",
        "ANTHROPIC_API_KEY": "key",
        "CLAUDE_CODE_SESSION_ID": "outer",
        "CLAUDECODE": "1",
        "CLAUDE_EFFORT": "max",
    })
    try:
        env = run_evals.spawn_env(Path("/tmp/isolated-config"))
    finally:
        os.environ.clear()
        os.environ.update(saved)
    expect_true(
        "auth and provider variables reach spawned sessions",
        env.get("CLAUDE_CODE_OAUTH_TOKEN") == "token"
        and env.get("CLAUDE_CODE_USE_BEDROCK") == "1"
        and env.get("ANTHROPIC_API_KEY") == "key",
        "an auth variable was stripped",
    )
    expect_true(
        "the outer session's identity does not",
        not {"CLAUDE_CODE_SESSION_ID", "CLAUDECODE", "CLAUDE_EFFORT"} & set(env)
        and env.get("CLAUDE_CONFIG_DIR") == "/tmp/isolated-config",
        f"leaked {sorted({'CLAUDE_CODE_SESSION_ID', 'CLAUDECODE', 'CLAUDE_EFFORT'} & set(env))}",
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
    check_cli_error_results()
    check_interrupted_turn_evidence()
    check_last_turn_completion_is_judged()
    check_interrupts_are_honoured()
    check_preflight_covers_every_model()
    check_spawn_env_keeps_auth()
    check_native_goal_reply()

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
