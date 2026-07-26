# Behavioral Eval Contract

`evals.json` is a declarative behavior contract for the goal-workflow skill. Each case fixes the initial state, decisive checkpoint replies, required behaviors, forbidden actions, and observable action ordering for a high-risk workflow branch.

## Running the evals

```bash
scripts/run-evals.sh                          # every case
scripts/run-evals.sh --case reject-save-gate  # one case
scripts/run-evals.sh --category reject_save --keep-workdirs
```

`scripts/run-evals.sh` drives a real `claude -p` conversation per case, inside a temporary working directory containing a small fixture project and against a temporary `CLAUDE_CONFIG_DIR` holding only the canonical skill bundle. It spends tokens against a live model, so it is never run by CI; invoke it deliberately. Useful flags: `--model`, `--judge-model`, `--simulator-model`, `--jobs`, `--max-turns`, `--retries`. The run writes `tests/results/evals-report.json` with the full transcript, tool calls, and judge verdict for every case.

The session under test runs with `--permission-mode bypassPermissions`. Weaker modes trip the sensitive-file guard on `.claude/goals/`, which is the one directory the skill exists to write to; the resulting failures are environment artifacts rather than skill defects. Each case is confined to its own throwaway temporary directory, so that is the blast radius. Override with `--permission-mode` if your environment differs.

## How a case is decided

A simulated user answers routine discovery questions from the declared `setup` and replies with the declared content at each declared checkpoint. `checkpoint_replies` is not a complete transcript: the simulator fills in the routine turns, but it never invents a design, save, overwrite, or start approval.

Two kinds of assertion then run against the trace.

**Deterministic checks** read the filesystem and the recorded tool calls. Both approval gates are checked this way, because whether a goal file was on disk before the user approved saving is an observable fact rather than a judgement. These checks cover: a goal file appearing at or before the save approval, a goal file appearing after the user declined, an approved save that never produced a file, writes outside `.claude/goals/` before an affirmative start approval, a modified or deleted pre-seeded goal file, and delegation to another skill.

**A judge model** grades the remaining `required_behaviors`, `forbidden_actions`, `tool_order`, and `terminal_state` entries, which are stated in prose and cannot be checked mechanically. An ordering constraint applies only when both named actions occur; a terminal state may intentionally stop before the later action.

The two are ordered deliberately. Deterministic checks run first, because a skill that skips a gate also derails the script; when the conversation ends before every checkpoint is reached, observable violations decide whether that was the skill's fault or the simulator stopping early. With no such evidence the run is reported as a harness error, not as a skill failure.

Upstream capacity errors are retried and, if they persist, reported as a harness error. So is a session blocked by a permission denial or a missing tool: a verifier failure is not a product failure, and the simulated user is forbidden from talking the assistant through a workaround.

## Scope and limits

The judge is a model and can be wrong in both directions, so a failing case deserves a look at the recorded transcript in `tests/results/evals-report.json` before the skill is changed. Results are not bit-reproducible: model behavior varies between runs, and a case that passes once is evidence, not proof.

`scripts/validate.sh` checks only the schema of this file — case structure, the action and checkpoint vocabularies, category coverage, and ordering references. It does not run a model and never claims that behavior passed. Adding more cases per category is allowed and encouraged.
