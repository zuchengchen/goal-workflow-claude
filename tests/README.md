# Behavioral Eval Contract

`evals.json` is a declarative behavior contract for the goal-workflow skill. Each case fixes the initial state, decisive checkpoint replies, required behaviors, forbidden actions, and observable action ordering for a high-risk workflow branch.

## Running the evals

```bash
scripts/run-evals.sh                          # every case
scripts/run-evals.sh --case reject-save-gate  # one case
scripts/run-evals.sh --category reject_save --keep-workdirs
```

`scripts/run-evals.sh` drives a real `claude -p` conversation per case, inside a temporary working directory containing a small fixture project and against a temporary `CLAUDE_CONFIG_DIR` holding only the canonical skill bundle. It spends tokens against a live model, so it is never run by CI; invoke it deliberately. Useful flags: `--model`, `--judge-model`, `--simulator-model`, `--jobs`, `--max-turns`, `--retries`. The run writes `tests/results/evals-report.json` with a `meta` block (skill version, models, timestamps) plus the full transcript, tool calls, and judge verdict for every case; that report is the durable forward-test evidence for the current skill.

Before any case runs, a preflight makes one minimal model call in the same isolated setup. A broken environment — not logged in, no network, an unusable CLI — fails every case identically, so the harness reports it once, up front, with exit code 2, instead of writing a report full of identical harness errors.

The session under test runs with `--permission-mode bypassPermissions`. A scripted session cannot answer an interactive permission prompt, so every write and verification command has to be pre-authorized; in weaker modes the resulting failures are environment artifacts rather than skill defects. Each case is confined to its own throwaway temporary directory, so that is the blast radius. Override with `--permission-mode` if your environment differs.

## How a case is decided

A simulated user answers routine discovery questions from the declared `setup` and replies with the declared content at each declared checkpoint. `checkpoint_replies` is not a complete transcript: the simulator fills in the routine turns, but it never invents a design, save, overwrite, or start approval.

Two kinds of assertion then run against the trace.

**Deterministic checks** read the filesystem and the recorded tool calls. Both approval gates are checked this way, because whether a goal file was on disk before the user approved saving is an observable fact rather than a judgement. These checks cover: a goal file appearing at or before the save approval — including a write call naming the goal file whose file is deleted again within the same turn, which an end-of-turn disk snapshot alone would miss — a goal file appearing after the user declined, an approved save that never produced a file, writes to anything other than the goal file before an affirmative start approval, a modified or deleted pre-seeded goal file, and delegation to another skill.

Subagent work is attributed rather than inferred. A subagent's own tool calls surface in the parent session's stream tagged with the `Agent` call they belong to, so the harness records each call's `parent_tool_use_id` and `subagent_type`; the report, the progress log, and the judge transcript all distinguish `[main session]` from `[inside subagent <id>]`. Two consequences matter. Both gate checks see a delegated write exactly as they see a direct one, so delegation cannot slip a write past them. And a subagent's final text is a report to its parent rather than a user-visible message, so it is excluded from the graded assistant text; counting it would misjudge the language invariant and the one-question-at-a-time rule.

`subagent_writes_before_execution` runs for every case, not only the ones that name it, and is stricter than gate 2: before execution begins, *any* write from a subagent is a violation — including a write to the goal file, since the save procedure is undelegated, and including one made after an affirmative save approval. Gate 2 cannot express that, because it exempts goal artifacts. Both checks share the same start-approval boundary; the difference is the goal-artifact exemption, and the name records where the forbidden window ends.

Because the goal file is saved directly in the working directory, both gate checks identify it by name rather than by a containing directory: a basename carrying the skill's mandated `<YYYY-MM-DD>-<slug>.md` form, which also matches the temporary file the save procedure stages beside the destination. On disk, a `.md` file containing a `## Goal Directive` heading counts too, so a misnamed but real goal file does not read as "no file was written".

Three boundaries of the deterministic checks are deliberate. Scripted replies at `save_approval`, `start_approval`, and `overwrite_approval` must be y/yes/n/no (case-insensitive) — the schema and the harness both enforce this — because the checks parse those replies mechanically. The write checks read `file_path`/`notebook_path` from Write, Edit, MultiEdit, and NotebookEdit calls only; a write smuggled through Bash is invisible to them, whether it came from the main session or from a subagent. And a staging filename that dropped the dated part would be misread as project work. Both gaps are left to the judge's reading of `start_before_second_approval`.

A pre-seeded goal file changing is a violation unless the case declares `overwrite_consent` in its setup *and* its scripted user granted that consent at `overwrite_approval`. That is what makes an overwrite-approved run expressible at all: before it, the fixture-integrity check failed any case whose seeded file changed, so the skill's third consent decision could not be tested.

**A judge model** grades the remaining `required_behaviors`, `forbidden_actions`, `tool_order`, and `terminal_state` entries, which are stated in prose and cannot be checked mechanically. An ordering constraint applies only when both named actions occur; a terminal state may intentionally stop before the later action.

A write to the goal file is never truncated in the judge's transcript, because the judge is told that what the transcript does not show did not happen — and a truncated goal file reads as a goal file missing its later sections. Other tool inputs are capped at 8000 characters, and a cap that actually bites is marked in place so the judge can tell an absent thing from an elided one. The verdict is then cross-checked against the case: a judge that grades only some of the declared expectations, or invents an entry the case never declared, is a harness error rather than a passing case.

The two are ordered deliberately. Deterministic checks run first, because a skill that skips a gate also derails the script; when the conversation ends before every checkpoint is reached, observable violations decide whether that was the skill's fault or the simulator stopping early. With no such evidence the run is reported as a harness error, not as a skill failure.

Upstream capacity errors are retried and, if they persist, reported as a harness error. So is a session blocked by a permission denial or a missing tool: a verifier failure is not a product failure, and the simulated user is forbidden from talking the assistant through a workaround.

## Scope and limits

The judge is a model and can be wrong in both directions, so a failing case deserves a look at the recorded transcript in `tests/results/evals-report.json` before the skill is changed. Results are not bit-reproducible: model behavior varies between runs, and a case that passes once is evidence, not proof.

Three structural limits shape what any case can assert. The harness records tool *calls*, never tool *results*, so no expectation may depend on what a command returned. The driver ends the conversation once the scripted replies run out, so a terminal state that names an early stopping point is partly an artifact of the script's length, and a forbidden action only becomes falsifiable if the script runs long enough to give it an opportunity. And the fixture is seeded once before the session starts, so a destination that appears midway through a run — the save-failure and readback-mismatch paths — cannot be induced at all.

`scripts/validate.sh` checks only the schema of this file — case structure, the action and checkpoint vocabularies, category coverage, and ordering references. It does not run a model and never claims that behavior passed. Adding more cases per category is allowed and encouraged.
