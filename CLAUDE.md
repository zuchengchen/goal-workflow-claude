# Repository Guidelines

## Project Structure & Module Organization

The installable skill lives in `skills/goal-workflow/`. Its `SKILL.md` is the canonical release artifact and the single source of truth; there are no compatibility mirrors.

Repository tooling is under `scripts/`: `validate.py` enforces repository and bundle *structure*, `run_evals.py` exercises skill *behavior* against a live model, and the shell scripts cover validation, the eval harness, and local install, update, and uninstall flows. Declarative scenarios live in `tests/evals.json`; `tests/README.md` explains how they are decided and what they cannot prove. CI is defined in `.github/workflows/validate.yml`.

`validate.py` deliberately makes no assertion about the wording of `SKILL.md`. Behavioral claims belong in `tests/evals.json`, not in a regex over the prose.

## Build, Test, and Development Commands

This project has no compilation or package-install step.

- `./scripts/validate.sh`: validate skill metadata and structure, eval schema, documentation, and repository hygiene.
- `./scripts/smoke-install.sh`: exercise install, replacement, backup, dry-run, and uninstall behavior in an isolated `CLAUDE_CONFIG_DIR`.
- `python3 tests/test_harness_checks.py`: unit-test the eval harness's deterministic gate checks against synthetic traces. Model-free, so CI runs it.
- `./scripts/run-evals.sh`: run the behavioral evals against a live model. Spends tokens; never run by CI. Use `--case <id>` while iterating.
- `python3 scripts/validate.py --skill-dir skills/goal-workflow --installed-only`: validate the canonical bundle as an installed skill.
- `git diff --check`: detect whitespace errors before committing.

## Coding Style & Naming Conventions

Use four-space indentation and type hints in Python. Keep Bash scripts strict with `set -euo pipefail`, quote expansions, and use uppercase names for environment variables and constants. Format JSON and YAML with two-space indentation. Markdown should be concise, use descriptive headings, end with a newline, and contain no trailing whitespace.

Use lowercase kebab-case for skill names, goal filenames, and eval case IDs, for example `verification-integrity` or `2026-07-10-api-cleanup.md`.

## Testing Guidelines

Run both validation scripts for behavior or installer changes. Add or update a focused case in `tests/evals.json` when changing workflow behavior, approval gates, or verification rules; more than one case per category is allowed.

Any change to `SKILL.md` must also be forward-tested with `./scripts/run-evals.sh` against the affected cases, because nothing in `validate.sh` checks what the skill actually does. When a case fails, read its transcript in `tests/results/evals-report.json` before changing the skill: the judge is a model and can be wrong, and a harness error is not a skill failure.

## Commit & Pull Request Guidelines

Use short imperative commit subjects such as `Expand goal workflow discovery interview` or `Add installation documentation`. Keep each commit focused.

Pull requests should explain the behavioral change, list affected skill or tooling files, report exact verification commands and results, and link relevant issues. Update `CHANGELOG.md` for user-visible changes. Screenshots are unnecessary unless a future change adds UI behavior.

## Security & Repository Hygiene

Do not commit credentials, tokens, local goal files, caches, or generated install artifacts. `.claude/goals/`, `__pycache__/`, and `tests/results/` are intentionally ignored. Preserve unrelated worktree changes, and do not add extra files to the canonical skill bundle unless the validator and installation contract are updated deliberately.
