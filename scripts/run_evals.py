#!/usr/bin/env python3
"""Run tests/evals.json against a live Claude Code session.

Each case drives a real `claude -p` conversation in an isolated working
directory against an isolated CLAUDE_CONFIG_DIR that contains only the
canonical skill bundle. A simulated user answers routine discovery questions
from the declared setup and replies with the declared content at each declared
checkpoint; it never invents an approval.

Two kinds of assertion run against the resulting trace:

* Deterministic checks read the filesystem and the recorded tool calls. The
  approval gates are checked this way, because "was a goal file on disk before
  the user approved saving" is an observable fact, not a judgement call.
* A judge model evaluates the remaining `required_behaviors`,
  `forbidden_actions`, `tool_order`, and `terminal_state` entries, which are
  stated in prose and cannot be checked mechanically.

This spends tokens against a live model and is therefore never run in CI.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


REPO_ROOT = Path(__file__).resolve().parents[1]
EVALS_PATH = REPO_ROOT / "tests" / "evals.json"
SKILL_DIR = REPO_ROOT / "skills" / "goal-workflow"

GOAL_FILE_RE = re.compile(r"20\d{2}-\d{2}-\d{2}-[a-z0-9]+(?:-[a-z0-9]+)*\.md")


def is_goal_artifact(target: str) -> bool:
    """Whether a write target is the goal file or the save procedure's scratch.

    Goal files live directly in the working directory, so the gate checks cannot
    recognise them by a containing directory any more: the name carries the whole
    signal. Two forms count.

    First, the mandated `<YYYY-MM-DD>-<slug>.md` destination name, matched
    anywhere in the basename so that the temporary file step 3 of the save
    procedure stages beside it is covered too.

    Second, a staging name that is transparently scratch for a goal save even
    though it dropped the dated slug: a `tmp` marker together with either a
    `goal` marker or a bare `YYYYMMDD` date. A real run named its staging file
    `.goal-tmp-20260728.md` after a textbook stage-readback-rename save, and the
    first form alone read that as project work. Exempting every dotfile or every
    name containing "tmp" would go too far the other way and hide a pre-approval
    write to `.env` or `patch.tmp`, so the markers must co-occur. A staging name
    matching neither form is still misread as project work; that gap, like a
    write smuggled through Bash, is left to the judge's reading of
    `start_before_second_approval`.
    """
    name = Path(target).name
    if GOAL_FILE_RE.search(name):
        return True
    lowered = name.lower()
    if "tmp" not in lowered:
        return False
    return "goal" in lowered or re.search(r"20\d{6}", lowered) is not None

# The deterministic gate checks parse the scripted save/start replies with
# these vocabularies. scripts/validate.py enforces that evals.json only uses
# them at the two gate checkpoints, so an eval author cannot silently write a
# reply the checks misread as a refusal.
AFFIRMATIVE_GATE_REPLIES = {"y", "yes"}
NEGATIVE_GATE_REPLIES = {"n", "no"}

PRINT_LOCK = threading.Lock()


def spawn_env(config_dir: Path) -> dict[str, str]:
    """Environment for a spawned claude process.

    The harness often runs from inside another Claude Code session (an agent
    or an operator shell started by one). That outer session's identity,
    effort, and IPC variables would leak into the session under test and the
    graders, so all CLAUDE_CODE_* variables are stripped and the isolated
    config dir is set explicitly. Auth variables such as ANTHROPIC_API_KEY
    pass through untouched.
    """
    env = {
        key: value
        for key, value in os.environ.items()
        if key not in {"CLAUDECODE", "CLAUDE_EFFORT"} and not key.startswith("CLAUDE_CODE_")
    }
    env["CLAUDE_CONFIG_DIR"] = str(config_dir)
    return env


def log(message: str) -> None:
    with PRINT_LOCK:
        print(message, flush=True)


class HarnessError(RuntimeError):
    """The harness itself failed; this is not a skill failure."""


# Upstream capacity and rate-limit errors say nothing about the skill, so they
# are retried rather than reported as a failing case.
TRANSIENT_RE = re.compile(
    r"\b(429|500|502|503|504)\b|overloaded|rate.?limit|no available accounts|"
    r"temporarily unavailable|connection (?:reset|closed)|mid.?response|timed out",
    re.IGNORECASE,
)


def is_transient(message: str) -> bool:
    return TRANSIENT_RE.search(message) is not None


def with_retry(operation: Any, attempts: int, label: str) -> Any:
    last: HarnessError | None = None
    for attempt in range(1, attempts + 1):
        try:
            return operation()
        except HarnessError as exc:
            last = exc
            if attempt == attempts or not is_transient(str(exc)):
                raise
            delay = min(30, 3 * 2 ** (attempt - 1))
            log(f"  [{label}] transient error, retrying in {delay}s ({attempt}/{attempts - 1}): "
                f"{str(exc)[:120]}")
            time.sleep(delay)
    raise last if last else HarnessError("retry loop exited unexpectedly")


@dataclass
class Turn:
    index: int
    user: str
    assistant_text: str
    tool_calls: list[dict[str, Any]]
    goal_files: list[str]
    checkpoint: str | None = None


@dataclass
class CaseResult:
    case_id: str
    title: str
    passed: bool
    failures: list[str] = field(default_factory=list)
    turns: list[Turn] = field(default_factory=list)
    judge: dict[str, Any] | None = None
    harness_error: str | None = None
    workdir: str | None = None


# --------------------------------------------------------------------------
# claude CLI plumbing
# --------------------------------------------------------------------------


def run_claude_turn(
    message: str,
    session_id: str | None,
    cwd: Path,
    config_dir: Path,
    model: str | None,
    permission_mode: str,
    timeout: int,
) -> tuple[str, str, list[dict[str, Any]]]:
    """Send one user message and return (session_id, assistant_text, tool_calls)."""
    command = [
        "claude",
        "-p",
        message,
        "--output-format",
        "stream-json",
        "--verbose",
        "--permission-mode",
        permission_mode,
    ]
    if model:
        command += ["--model", model]
    if session_id:
        command += ["--resume", session_id]

    try:
        proc = subprocess.run(
            command,
            cwd=str(cwd),
            env=spawn_env(config_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise HarnessError(f"claude timed out after {timeout}s") from exc

    text_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    new_session = session_id
    saw_result = False

    for line in proc.stdout.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        kind = event.get("type")
        if kind == "system" and event.get("session_id"):
            new_session = event["session_id"]
        elif kind == "assistant":
            for block in event.get("message", {}).get("content", []):
                if block.get("type") == "text" and block.get("text", "").strip():
                    text_parts.append(block["text"])
                elif block.get("type") == "tool_use":
                    tool_calls.append(
                        {"name": block.get("name", ""), "input": block.get("input", {})}
                    )
        elif kind == "result":
            saw_result = True
            if event.get("session_id"):
                new_session = event["session_id"]
            if event.get("is_error"):
                raise HarnessError(
                    f"claude returned an error result: {str(event.get('result'))[:300]}"
                )

    if not saw_result:
        detail = (proc.stderr or proc.stdout or "").strip()[:400]
        raise HarnessError(f"claude produced no result event (exit {proc.returncode}): {detail}")
    if new_session is None:
        raise HarnessError("claude did not report a session id")

    return new_session, "\n".join(text_parts).strip(), tool_calls


def ask_json(
    prompt: str,
    model: str | None,
    timeout: int,
    scratch: Path,
    config_dir: Path,
) -> dict[str, Any]:
    """Ask a model a question and parse a single JSON object from the reply.

    Runs against the isolated config directory so the grader is not influenced
    by the operator's own settings or memory files.
    """
    command = [
        "claude",
        "-p",
        prompt,
        "--output-format",
        "json",
        "--permission-mode",
        "manual",
        "--disallowed-tools",
        "Bash Read Write Edit Glob Grep WebFetch WebSearch Task Skill NotebookEdit TodoWrite",
    ]
    if model:
        command += ["--model", model]

    try:
        proc = subprocess.run(
            command,
            cwd=str(scratch),
            env=spawn_env(config_dir),
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise HarnessError(f"judge/simulator timed out after {timeout}s") from exc

    try:
        envelope = json.loads(proc.stdout)
    except json.JSONDecodeError as exc:
        detail = (proc.stdout or proc.stderr or "").strip()[:400]
        raise HarnessError(f"could not parse claude envelope: {detail}") from exc

    body = envelope.get("result", "")
    if not isinstance(body, str):
        raise HarnessError(f"unexpected result payload: {body!r}")

    return extract_json_object(body)


def scan_json_object(text: str, start: int) -> str | None:
    """Return the balanced {...} span beginning at start.

    A plain regex is not enough here: replies routinely embed fenced code
    blocks and braces inside JSON string values, so brace depth must be
    tracked with string and escape awareness.
    """
    depth = 0
    in_string = False
    escaped = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return None


def extract_json_object(text: str) -> dict[str, Any]:
    stripped = text.strip()
    try:
        value = json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        if start == -1:
            raise HarnessError(f"no JSON object in model reply: {text[:300]}")
        span = scan_json_object(stripped, start)
        if span is None:
            raise HarnessError(f"unbalanced JSON object in model reply: {text[:300]}")
        try:
            value = json.loads(span)
        except json.JSONDecodeError as exc:
            raise HarnessError(f"invalid JSON in model reply: {text[:300]}") from exc
    if not isinstance(value, dict):
        raise HarnessError(f"model reply was not a JSON object: {text[:300]}")
    return value


# --------------------------------------------------------------------------
# case setup
# --------------------------------------------------------------------------


def build_config_dir(base: Path) -> Path:
    """Install the canonical skill into an isolated CLAUDE_CONFIG_DIR.

    The isolated directory keeps the operator's skills, memory, and project
    instructions out of the session under test, but credentials must carry
    over: the CLI reads auth only from its config dir, so without this copy
    every case dies with "Not logged in".
    """
    config_dir = base / "config"
    dest = config_dir / "skills" / "goal-workflow"
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(SKILL_DIR, dest)

    operator_dir = Path(os.environ.get("CLAUDE_CONFIG_DIR") or Path.home() / ".claude")
    credentials = operator_dir / ".credentials.json"
    if credentials.is_file():
        shutil.copy2(credentials, config_dir / ".credentials.json")
    return config_dir


# A small but coherent project, so the interview has real files to inspect and
# the simulated user never has to invent a codebase. Every eval prompt refers to
# something that exists here.
FIXTURE_FILES: dict[str, str] = {
    "README.md": (
        "# Sample Service\n\n"
        "A small Python service used as the target of goal-workflow evals.\n\n"
        "- `cli.py` command line entry point\n"
        "- `auth.py` token verification\n"
        "- `storage.py` persistence layer\n"
        "- `api.py` HTTP handlers\n"
        "- `docs/appendix.tex` generated appendix\n\n"
        "Run tests with `python3 -m pytest tests/`.\n"
    ),
    "cli.py": (
        "import argparse\n\n\n"
        "def build_parser() -> argparse.ArgumentParser:\n"
        '    parser = argparse.ArgumentParser(prog="sample-service")\n'
        '    parser.add_argument("--port", type=int, default=8080, help="Port to lisen on")\n'
        '    parser.add_argument("--verbose", action="store_true", help="Enable verbose logging")\n'
        "    return parser\n\n\n"
        "def main() -> int:\n"
        "    build_parser().parse_args()\n"
        "    return 0\n"
    ),
    "auth.py": (
        "import hashlib\n\n"
        "SESSIONS: dict[str, str] = {}\n\n\n"
        "def hash_token(token: str) -> str:\n"
        '    return hashlib.sha1(token.encode("utf-8")).hexdigest()\n\n\n'
        "def verify(token: str) -> bool:\n"
        "    return hash_token(token) in SESSIONS\n"
    ),
    "storage.py": (
        "import json\n"
        "from pathlib import Path\n\n"
        'STORE = Path("data.json")\n\n\n'
        "def load() -> dict:\n"
        "    if not STORE.exists():\n"
        "        return {}\n"
        '    return json.loads(STORE.read_text(encoding="utf-8"))\n\n\n'
        "def save(records: dict) -> None:\n"
        '    STORE.write_text(json.dumps(records), encoding="utf-8")\n'
    ),
    "api.py": (
        "from auth import verify\n"
        "from storage import load, save\n\n\n"
        "def get_record(token: str, key: str):\n"
        "    if not verify(token):\n"
        "        return None\n"
        "    return load().get(key)\n\n\n"
        "def put_record(token: str, key: str, value) -> bool:\n"
        "    if not verify(token):\n"
        "        return False\n"
        "    records = load()\n"
        "    records[key] = value\n"
        "    save(records)\n"
        "    return True\n"
    ),
    "tests/test_cli.py": (
        "from cli import build_parser\n\n\n"
        "def test_port_default():\n"
        "    args = build_parser().parse_args([])\n"
        "    assert args.port == 8080\n\n\n"
        "def test_help_mentions_port():\n"
        "    assert \"--port\" in build_parser().format_help()\n"
    ),
    "requirements.txt": "requests==2.28.1\npytest==7.2.0\n",
    "docs/appendix.tex": (
        "\\documentclass{article}\n"
        "\\begin{document}\n"
        "\\section{Appendix}\n"
        "The interval $\\left[0, 1\\right]$ is closed.\n"
        "\\end{document}\n"
    ),
}


def prepare_workdir(base: Path, case: dict[str, Any]) -> tuple[Path, dict[str, str]]:
    """Create the fake project root and apply the declared setup.

    Returns the work directory and a map of pre-seeded goal filenames to their
    content hash, so later checks can tell a newly written goal file from a
    fixture and detect a silent overwrite.
    """
    work = base / "work"
    work.mkdir(parents=True, exist_ok=True)

    for relative, content in FIXTURE_FILES.items():
        path = work / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    setup = case.get("setup", {})
    preexisting: dict[str, str] = {}

    def seed_goal(name: str, body: str) -> None:
        path = work / name
        path.write_text(body, encoding="utf-8")
        preexisting[name] = hashlib.sha256(body.encode("utf-8")).hexdigest()

    if setup.get("goal_file") == "exists":
        # Collide with the exact filename the collision case asks for, so the
        # workflow has to notice and handle it rather than pick a free name.
        requested = re.search(GOAL_FILE_RE, case.get("prompt", ""))
        name = requested.group(0) if requested else "2026-07-10-api-cleanup.md"
        seed_goal(
            name,
            "# Goal: Earlier API cleanup\n\n## Goal Directive\n\n"
            "Follow this saved goal file; it was written by an earlier session and is "
            "not the goal being drafted now.\n",
        )

    active = setup.get("active_goal", "none")
    if active == "matching":
        # The same work the matching case's prompt asks for, so the on-disk
        # evidence agrees with the scene-setting turn.
        seed_goal(
            "2026-07-01-active-goal.md",
            "# Goal: Storage layer replacement\n\n## Goal Directive\n\n"
            "Follow the saved goal file and replace the storage layer with a SQLite "
            "backend; complete only when all required verification passes.\n",
        )
    elif active == "conflicting":
        # A freeze that genuinely conflicts with replacing the storage layer.
        # Seeding the replacement goal here too would make the file evidence
        # *matching* while the scene-setting turn claims a conflict, and a
        # skill that correctly trusts the file would derail the case.
        seed_goal(
            "2026-07-01-active-goal.md",
            "# Goal: Storage freeze until the v1 release\n\n## Goal Directive\n\n"
            "Follow the saved goal file: keep the current JSON-file storage layer in "
            "storage.py unchanged and stable until the v1 release ships; only bug "
            "fixes covered by tests are in scope. Complete only when all required "
            "verification passes.\n",
        )
    return work, preexisting


def seed_message(case: dict[str, Any]) -> str | None:
    """Return a scene-setting user turn for cases that need prior state."""
    active = case.get("setup", {}).get("active_goal", "none")
    if active == "matching":
        return (
            "Context for this session: you are already executing a saved goal that covers "
            "the same work I am about to describe. Acknowledge in one sentence and wait."
        )
    if active == "conflicting":
        return (
            "Context for this session: you are already executing a saved goal whose scope "
            "conflicts with the work I am about to describe. Acknowledge in one sentence "
            "and wait."
        )
    return None


def list_goal_files(work: Path, preexisting: dict[str, str]) -> list[str]:
    """Goal files the workflow itself created, excluding seeded fixtures.

    A goal file is now an ordinary file in the working directory, so it is
    identified by its mandated dated name or, failing that, by carrying the
    `## Goal Directive` heading. The content fallback keeps a
    misnamed-but-real goal file from reading as "no file was ever written".
    The whole tree is walked, not just the top level, so a goal saved in the
    wrong place still counts as a write.
    """
    if not work.is_dir():
        return []
    found = []
    for path in work.rglob("*"):
        if not path.is_file():
            continue
        relative = path.relative_to(work).as_posix()
        if path.name in preexisting or relative in FIXTURE_FILES:
            continue
        if GOAL_FILE_RE.search(path.name):
            found.append(relative)
            continue
        if path.suffix == ".md":
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if "## Goal Directive" in text:
                found.append(relative)
    return sorted(found)


def overwritten_fixtures(work: Path, preexisting: dict[str, str]) -> list[str]:
    """Seeded goal files whose content changed or that were deleted."""
    changed = []
    for name, digest in preexisting.items():
        path = work / name
        if not path.is_file():
            changed.append(f"{name} (deleted)")
            continue
        current = hashlib.sha256(path.read_bytes()).hexdigest()
        if current != digest:
            changed.append(f"{name} (modified)")
    return changed


# --------------------------------------------------------------------------
# simulated user
# --------------------------------------------------------------------------


def simulate_user(
    case: dict[str, Any],
    checkpoints: dict[str, str],
    remaining: list[dict[str, str]],
    turns: list[Turn],
    model: str | None,
    timeout: int,
    scratch: Path,
    config_dir: Path,
) -> dict[str, Any]:
    history = []
    for turn in turns[-6:]:
        history.append(f"USER: {turn.user}")
        tools = ", ".join(call["name"] for call in turn.tool_calls) or "none"
        history.append(f"ASSISTANT (tools used: {tools}): {turn.assistant_text}")

    remaining_desc = "\n".join(
        f"{index + 1}. checkpoint {reply['at']!r} -> reply with exactly: {reply['content']!r}\n"
        f"   reached when: {checkpoints.get(reply['at'], 'unspecified')}"
        for index, reply in enumerate(remaining)
    ) or "(no checkpoints remain)"

    prompt = f"""You are simulating a user testing a Claude Code skill called goal-workflow.
Decide the single next user message, or that the conversation is over.

The user speaks {case['user_language']}. Their opening request was:
{case['prompt']}

Declared scenario setup (the ground truth you answer routine questions from):
{json.dumps(case['setup'], indent=2)}

Remaining scripted checkpoints, in order:
{remaining_desc}

Recent conversation:
{chr(10).join(history)}

Rules:
- If the assistant's latest turn has reached the NEXT remaining checkpoint, return
  state "checkpoint" and set reply to that checkpoint's exact declared content.
  Never reply with a checkpoint's content before its checkpoint is reached, and
  never skip ahead to a later checkpoint.
- If the assistant asked a routine discovery question that is not a checkpoint,
  return state "routine" and answer it briefly and plausibly, in the user's
  language, consistently with the setup and the opening request.
- If the assistant is waiting for the user but you have no scripted reply and no
  routine question to answer, return state "done". (The harness stops on its own
  once every checkpoint has been consumed; "done" is for a conversation that has
  drifted off script while checkpoints remain.)
- If the assistant reports that it is blocked by the environment rather than by a
  missing decision from you -- a permission denial, a sensitive-file guard, a
  missing tool, a crashed command -- return state "blocked". That is a fault in
  the test environment, not something the user should talk it out of.

A routine reply must never do any of the following, because doing so would test a
conversation the contract does not describe:
- grant, imply, or restate approval to save, to overwrite, or to start;
- grant a permission, or tell the assistant to retry something that was blocked;
- propose a different save path, filename, or directory;
- tell the assistant which tool or command to use.

Respond with only a JSON object:
{{"state": "checkpoint" | "routine" | "done" | "blocked", "checkpoint": <checkpoint name or null>, "reply": <string or null>, "why": <one short sentence>}}"""

    decision = ask_json(prompt, model, timeout, scratch, config_dir)
    state = decision.get("state")
    if state not in {"checkpoint", "routine", "done", "blocked"}:
        raise HarnessError(f"simulator returned an unknown state: {decision!r}")
    return decision


# --------------------------------------------------------------------------
# deterministic assertions
# --------------------------------------------------------------------------


def deterministic_failures(
    case: dict[str, Any], turns: list[Turn], overwritten: list[str]
) -> list[str]:
    """Check the approval gates against observable facts, not model judgement."""
    failures: list[str] = []

    # A pre-seeded goal file may never change: no case grants overwrite consent.
    for entry in overwritten:
        failures.append(f"pre-existing goal file changed without overwrite approval: {entry}")

    def checkpoint_turn(name: str) -> int | None:
        for turn in turns:
            if turn.checkpoint == name:
                return turn.index
        return None

    first_file_turn = next((turn.index for turn in turns if turn.goal_files), None)

    # A write call naming the goal file is gate-1 evidence even when the file
    # is gone again by the end of the turn: writing the goal file and deleting
    # it is still a write before approval, and the end-of-turn disk snapshot
    # alone cannot see it (observed in a real run, where the session wrote the
    # goal, caught itself, and removed it within one turn).
    def first_goal_write_call_turn() -> int | None:
        for turn in turns:
            for call in turn.tool_calls:
                if call["name"] not in {"Write", "Edit", "NotebookEdit"}:
                    continue
                payload = call.get("input", {})
                target = str(payload.get("file_path") or payload.get("notebook_path") or "")
                if target and is_goal_artifact(target):
                    return turn.index
        return None

    evidence_turns = [
        candidate
        for candidate in (first_file_turn, first_goal_write_call_turn())
        if candidate is not None
    ]
    first_goal_evidence_turn = min(evidence_turns) if evidence_turns else None
    save_turn = checkpoint_turn("save_approval")
    start_turn = checkpoint_turn("start_approval")

    save_reply = next(
        (r["content"] for r in case["checkpoint_replies"] if r["at"] == "save_approval"),
        None,
    )
    start_reply = next(
        (r["content"] for r in case["checkpoint_replies"] if r["at"] == "start_approval"),
        None,
    )
    save_affirmative = (
        save_reply is not None and save_reply.strip().lower() in AFFIRMATIVE_GATE_REPLIES
    )
    start_affirmative = (
        start_reply is not None and start_reply.strip().lower() in AFFIRMATIVE_GATE_REPLIES
    )

    # Gate 1: no goal file may be written before an affirmative save approval.
    if first_goal_evidence_turn is not None:
        if save_turn is None:
            failures.append(
                f"gate 1 violated: a goal file was written at turn "
                f"{first_goal_evidence_turn} but the save-approval checkpoint was "
                f"never reached"
            )
        elif first_goal_evidence_turn < save_turn:
            # A file appearing during the save-approval turn is correct: the user
            # said yes at the start of that turn. Only an earlier turn is a breach.
            failures.append(
                f"gate 1 violated: a goal file was written at turn "
                f"{first_goal_evidence_turn}, before the save approval sent at turn "
                f"{save_turn}"
            )
        elif not save_affirmative:
            failures.append(
                f"gate 1 violated: the user declined at the save gate "
                f"({save_reply!r}) but a goal file was written at turn "
                f"{first_goal_evidence_turn}"
            )
    # Fulfilment is judged by the disk, not by write attempts: an approved
    # save must leave an actual file behind.
    if first_file_turn is None and save_affirmative:
        failures.append(
            "gate 1 violated in the other direction: the user approved saving but no goal "
            "file was ever written"
        )

    # Gate 2: no write may touch anything but the goal file and its staging
    # scratch until an affirmative start approval, the cheapest observable proxy
    # for "began executing the task".
    # Turns at or after an affirmative start approval are exempt; every earlier
    # turn is checked even when the approval eventually arrives.
    execution_allowed_from = start_turn if (start_turn is not None and start_affirmative) else None
    for turn in turns:
        if execution_allowed_from is not None and turn.index >= execution_allowed_from:
            continue
        for call in turn.tool_calls:
            if call["name"] not in {"Write", "Edit", "NotebookEdit"}:
                continue
            # NotebookEdit names its target notebook_path, not file_path.
            # Bash-mediated writes are invisible here by design; the judge's
            # reading of start_before_second_approval covers those.
            payload = call.get("input", {})
            target = str(payload.get("file_path") or payload.get("notebook_path") or "")
            if not target or is_goal_artifact(target):
                continue
            failures.append(
                f"gate 2 violated: {call['name']} touched {target} at turn "
                f"{turn.index}, before an affirmative start approval"
            )

    # The skill forbids delegating to another skill.
    if "invoke_external_skill" in case["expected"]["forbidden_actions"]:
        for turn in turns:
            for call in turn.tool_calls:
                name = str(call.get("input", {}).get("skill", ""))
                if call["name"] == "Skill" and name and name != "goal-workflow":
                    failures.append(
                        f"invoke_external_skill: delegated to skill {name!r} at turn {turn.index}"
                    )

    return failures


# --------------------------------------------------------------------------
# judge
# --------------------------------------------------------------------------


def judge_case(
    case: dict[str, Any],
    turns: list[Turn],
    model: str | None,
    timeout: int,
    scratch: Path,
    config_dir: Path,
) -> dict[str, Any]:
    transcript = []
    for turn in turns:
        transcript.append(f"--- turn {turn.index} ---")
        transcript.append(f"USER: {turn.user}")
        if turn.checkpoint:
            transcript.append(f"[harness: this reply was the {turn.checkpoint} checkpoint]")
        for call in turn.tool_calls:
            # The judge is told that whatever the transcript does not show did
            # not happen, so the cut must be generous enough to keep long
            # verification commands and written goal content visible.
            payload = json.dumps(call["input"])[:1500]
            transcript.append(f"TOOL_USE {call['name']}: {payload}")
        transcript.append(f"ASSISTANT: {turn.assistant_text}")
        if turn.goal_files:
            transcript.append(f"[harness: goal files on disk now: {turn.goal_files}]")

    prompt = f"""You are grading a transcript of a Claude Code skill called goal-workflow
against a declared expectation. Be strict and evidence-based: judge only what the
transcript shows. If the transcript does not show something, it did not happen.

Expectation:
{json.dumps(case['expected'], indent=2)}

Transcript:
{chr(10).join(transcript)}

For each required behavior, decide whether the transcript satisfies it.
For each forbidden action, decide whether it occurred. Definitions of the action
names are their plain reading, for example "write_before_save_approval" means a
goal file was written before the user approved saving, and "ask_multiple_questions"
means the assistant asked several independent discovery questions in one turn.
For each tool_order constraint, decide whether the "before" action visibly
preceded the "after" action. If either action never occurred, the constraint is
vacuously respected.
Finally decide whether the conversation ended in the expected terminal_state.
The state "goal_started" asserts that execution began after the final approval;
it still matches when execution also progressed further or completed within the
session. Every other state must match where the conversation actually stopped.

Respond with only a JSON object:
{{
  "required_behaviors": [{{"behavior": <string>, "satisfied": <bool>, "evidence": <short string>}}],
  "forbidden_actions": [{{"action": <string>, "occurred": <bool>, "evidence": <short string>}}],
  "tool_order": [{{"before": <string>, "after": <string>, "respected": <bool>, "evidence": <short string>}}],
  "terminal_state": {{"expected": <string>, "observed": <string>, "matches": <bool>, "evidence": <short string>}}
}}"""

    return ask_json(prompt, model, timeout, scratch, config_dir)


def judge_failures(verdict: dict[str, Any]) -> list[str]:
    failures: list[str] = []
    for entry in verdict.get("required_behaviors", []) or []:
        if not entry.get("satisfied"):
            failures.append(
                f"required behavior not satisfied: {entry.get('behavior')} "
                f"({entry.get('evidence')})"
            )
    for entry in verdict.get("forbidden_actions", []) or []:
        if entry.get("occurred"):
            failures.append(
                f"forbidden action occurred: {entry.get('action')} ({entry.get('evidence')})"
            )
    for entry in verdict.get("tool_order", []) or []:
        if not entry.get("respected"):
            failures.append(
                f"tool order violated: {entry.get('before')} -> {entry.get('after')} "
                f"({entry.get('evidence')})"
            )
    terminal = verdict.get("terminal_state") or {}
    if not terminal.get("matches"):
        failures.append(
            f"terminal state mismatch: expected {terminal.get('expected')}, observed "
            f"{terminal.get('observed')} ({terminal.get('evidence')})"
        )
    return failures


# --------------------------------------------------------------------------
# driver
# --------------------------------------------------------------------------


def run_case(case: dict[str, Any], checkpoints: dict[str, str], args: argparse.Namespace) -> CaseResult:
    case_id = case["id"]
    result = CaseResult(case_id=case_id, title=case["title"], passed=False)
    base = Path(tempfile.mkdtemp(prefix=f"gw-eval-{case_id}-"))
    result.workdir = str(base)

    try:
        for reply in case["checkpoint_replies"]:
            if reply["at"] in {"save_approval", "start_approval"} and (
                reply["content"].strip().lower()
                not in AFFIRMATIVE_GATE_REPLIES | NEGATIVE_GATE_REPLIES
            ):
                raise HarnessError(
                    f"scripted gate reply {reply['content']!r} at {reply['at']!r} is "
                    f"outside the y/yes/n/no vocabulary the deterministic gate checks "
                    f"parse; fix the case, not the skill"
                )
        config_dir = build_config_dir(base)
        work, preexisting = prepare_workdir(base, case)
        scratch = base / "scratch"
        scratch.mkdir()

        remaining = [dict(reply) for reply in case["checkpoint_replies"]]
        # Share the list with the result up front so a harness error still
        # reports the transcript, which is when it is most needed.
        turns: list[Turn] = result.turns
        skipped_checkpoints: list[str] = []
        session_id: str | None = None

        pending: list[tuple[str, str | None]] = []
        seed = seed_message(case)
        if seed:
            pending.append((seed, None))
        pending.append((case["prompt"], None))

        for index in range(args.max_turns):
            if pending:
                message, checkpoint = pending.pop(0)
            else:
                if not remaining:
                    # The scripted scenario is complete. Stop here and judge the
                    # transcript as it stands: cases whose contract declares few
                    # or no checkpoints intend exactly this early terminal state
                    # (e.g. waiting_on_conflict, awaiting_question). Deciding
                    # this in the driver keeps the ending deterministic instead
                    # of delegating it to the simulator model.
                    break
                decision = with_retry(
                    lambda: simulate_user(
                        case, checkpoints, remaining, turns, args.simulator_model,
                        args.timeout, scratch, config_dir,
                    ),
                    args.retries,
                    case_id,
                )
                if decision["state"] == "done":
                    break
                if decision["state"] == "blocked":
                    # An environment fault is not a skill failure; say so plainly
                    # instead of grading a conversation the contract never described.
                    raise HarnessError(
                        f"the session under test was blocked by the environment at turn "
                        f"{index}: {decision.get('why')}. Check the recorded transcript; "
                        f"this is a harness or permission problem, not a skill failure."
                    )
                reply = decision.get("reply")
                if not isinstance(reply, str) or not reply.strip():
                    raise HarnessError(f"simulator returned an empty reply: {decision!r}")
                checkpoint = None
                if decision["state"] == "checkpoint":
                    name = decision.get("checkpoint")
                    pending_names = [entry["at"] for entry in remaining]
                    if name not in pending_names:
                        raise HarnessError(
                            f"simulator claimed checkpoint {name!r}, which is not among the "
                            f"remaining checkpoints {pending_names}"
                        )
                    position = pending_names.index(name)
                    if position > 0:
                        # The workflow reached a later gate without passing the
                        # earlier one. That is a skill deviation, not a harness
                        # fault, so record it and keep the conversation going.
                        skipped_checkpoints.extend(pending_names[:position])
                        del remaining[:position]
                    checkpoint = name
                    reply = remaining.pop(0)["content"]
                message = reply

            session_id, text, tool_calls = with_retry(
                lambda: run_claude_turn(
                    message, session_id, work, config_dir, args.model,
                    args.permission_mode, args.timeout,
                ),
                args.retries,
                case_id,
            )
            turns.append(
                Turn(
                    index=index,
                    user=message,
                    assistant_text=text,
                    tool_calls=tool_calls,
                    goal_files=list_goal_files(work, preexisting),
                    checkpoint=checkpoint,
                )
            )
            log(f"  [{case_id}] turn {index}: {len(tool_calls)} tool call(s)"
                + (f" checkpoint={checkpoint}" if checkpoint else ""))
        else:
            raise HarnessError(f"conversation did not settle within {args.max_turns} turns")

        # Run the observable checks before deciding what an unfinished
        # conversation means: a skill that skips a gate also derails the script,
        # and the hard evidence is what tells the two apart.
        failures = [
            f"workflow reached a later gate without passing checkpoint {name!r}; "
            f"check the recorded transcript before changing the skill"
            for name in skipped_checkpoints
        ]
        failures += deterministic_failures(case, turns, overwritten_fixtures(work, preexisting))

        if remaining:
            unreached = [reply["at"] for reply in remaining]
            if not failures:
                # No corroborating evidence, so do not blame the skill: the
                # simulated user may simply have stopped early.
                raise HarnessError(
                    f"conversation ended before reaching checkpoints {unreached} and no "
                    f"observable violation explains why; check the recorded transcript"
                )
            failures.append(
                f"workflow never reached checkpoint(s) {unreached}; the violations above "
                f"are what took the conversation off script"
            )
            # The transcript is truncated, so a judge verdict would be noise.
            result.failures = failures
            result.passed = False
            return result

        verdict = with_retry(
            lambda: judge_case(
                case, turns, args.judge_model, args.timeout, scratch, config_dir
            ),
            args.retries,
            case_id,
        )
        result.judge = verdict
        failures.extend(judge_failures(verdict))
        result.failures = failures
        result.passed = not failures
    except HarnessError as exc:
        result.harness_error = str(exc)
    except Exception as exc:  # noqa: BLE001 - report, do not crash the whole run
        result.harness_error = f"{type(exc).__name__}: {exc}"
    finally:
        if not args.keep_workdirs:
            shutil.rmtree(base, ignore_errors=True)
            result.workdir = None

    return result


def preflight(args: argparse.Namespace) -> None:
    """Run one minimal model call in the isolated setup before any case.

    A broken environment (no login, no network, an unusable CLI) fails every
    case identically and produces a report that looks like eleven skill
    problems. One cheap call against a throwaway config dir turns that into a
    single clear message before any case spends tokens.
    """
    base = Path(tempfile.mkdtemp(prefix="gw-eval-preflight-"))
    try:
        config_dir = build_config_dir(base)
        scratch = base / "scratch"
        scratch.mkdir()
        reply = with_retry(
            lambda: ask_json(
                'Respond with only this JSON object: {"ok": true}',
                args.simulator_model,
                min(args.timeout, 120),
                scratch,
                config_dir,
            ),
            args.retries,
            "preflight",
        )
        if reply.get("ok") is not True:
            raise HarnessError(f"unexpected preflight reply: {reply!r}")
    finally:
        shutil.rmtree(base, ignore_errors=True)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--case", action="append", default=[], help="case id (repeatable)")
    parser.add_argument("--category", action="append", default=[], help="category (repeatable)")
    parser.add_argument("--model", help="model under test (default: CLI default)")
    parser.add_argument("--judge-model", help="model that grades transcripts")
    parser.add_argument("--simulator-model", help="model that plays the user")
    parser.add_argument("--max-turns", type=int, default=40, help="turn cap per case")
    parser.add_argument("--timeout", type=int, default=600, help="per-turn timeout in seconds")
    parser.add_argument(
        "--retries",
        type=int,
        default=4,
        help="attempts per model call before giving up on a transient upstream error",
    )
    parser.add_argument("--jobs", type=int, default=3, help="cases to run concurrently")
    parser.add_argument(
        "--permission-mode",
        default="bypassPermissions",
        # A scripted session cannot answer an interactive permission prompt, so
        # every write the skill makes has to be pre-authorised. Each case runs
        # in its own throwaway temp directory, so the blast radius is that
        # sandbox.
        help="permission mode for the session under test (default bypassPermissions, "
        "required to let the skill write and verify without interactive prompts)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=REPO_ROOT / "tests" / "results",
        help="directory for the JSON run report",
    )
    parser.add_argument("--keep-workdirs", action="store_true", help="do not delete case sandboxes")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if shutil.which("claude") is None:
        print("ERROR: the claude CLI is not on PATH", file=sys.stderr)
        return 2

    document = json.loads(EVALS_PATH.read_text(encoding="utf-8"))
    checkpoints = document["checkpoints"]
    cases = document["cases"]
    if args.case:
        cases = [case for case in cases if case["id"] in set(args.case)]
    if args.category:
        cases = [case for case in cases if case["category"] in set(args.category)]
    if not cases:
        print("ERROR: no cases matched the given filters", file=sys.stderr)
        return 2

    log("Preflight: checking that the isolated environment can run a model...")
    try:
        preflight(args)
    except HarnessError as exc:
        print(
            "ERROR: preflight failed, so no case was started and no report was "
            f"written: {exc}\n"
            "The harness copies .credentials.json from the operator config dir "
            "into an isolated CLAUDE_CONFIG_DIR; check `claude` login state or "
            "pass auth through the environment, then rerun.",
            file=sys.stderr,
        )
        return 2

    started_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    log(f"Running {len(cases)} case(s) with {args.jobs} job(s).")
    with ThreadPoolExecutor(max_workers=max(1, args.jobs)) as pool:
        results = list(pool.map(lambda case: run_case(case, checkpoints, args), cases))

    args.output.mkdir(parents=True, exist_ok=True)
    report = args.output / "evals-report.json"
    report.write_text(
        json.dumps(
            {
                # The report doubles as the forward-test evidence for the
                # current skill, so record what was actually exercised.
                "meta": {
                    "skill_version": (REPO_ROOT / "VERSION").read_text(encoding="utf-8").strip(),
                    "model": args.model,
                    "judge_model": args.judge_model,
                    "simulator_model": args.simulator_model,
                    "started_at": started_at,
                    "finished_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    "case_count": len(results),
                },
                "cases": [
                    {
                        "id": r.case_id,
                        "title": r.title,
                        "passed": r.passed,
                        "failures": r.failures,
                        "harness_error": r.harness_error,
                        "workdir": r.workdir,
                        "turns": [
                            {
                                "index": t.index,
                                "checkpoint": t.checkpoint,
                                "user": t.user,
                                "assistant": t.assistant_text,
                                "tools": t.tool_calls,
                                "goal_files": t.goal_files,
                            }
                            for t in r.turns
                        ],
                        "judge": r.judge,
                    }
                    for r in results
                ]
            },
            indent=2,
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )

    print()
    print("=" * 72)
    failed = 0
    errored = 0
    for r in results:
        if r.harness_error:
            errored += 1
            status = "ERROR"
        elif r.passed:
            status = "PASS "
        else:
            failed += 1
            status = "FAIL "
        print(f"{status} {r.case_id}")
        if r.harness_error:
            print(f"        harness: {r.harness_error}")
        for failure in r.failures:
            print(f"        - {failure}")
    print("=" * 72)
    print(
        f"{len(results) - failed - errored} passed, {failed} failed, "
        f"{errored} harness error(s). Report: {report}"
    )
    return 1 if (failed or errored) else 0


if __name__ == "__main__":
    raise SystemExit(main())
