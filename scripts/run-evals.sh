#!/usr/bin/env bash
# Run the behavioral evals in tests/evals.json against a live Claude Code
# session. This spends tokens and is never run by CI; invoke it deliberately.
#
#   scripts/run-evals.sh                        # every case
#   scripts/run-evals.sh --case reject-save-gate
#   scripts/run-evals.sh --category reject_save --keep-workdirs
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
exec python3 "$SCRIPT_DIR/run_evals.py" "$@"
