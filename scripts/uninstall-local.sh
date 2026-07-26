#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
VALIDATOR="$SCRIPT_DIR/validate.py"

rename_path() {
  python3 -c 'import os, sys; os.rename(sys.argv[1], sys.argv[2])' "$1" "$2"
}

usage() {
  printf '%s\n' \
    "Usage: scripts/uninstall-local.sh [--dest PATH] [--dry-run]" \
    "" \
    "Remove a validated goal-workflow installation." \
    "" \
    "Options:" \
    "  --dest PATH  Destination (default: \${CLAUDE_CONFIG_DIR:-\$HOME/.claude}/skills/goal-workflow)" \
    "  --dry-run    Validate and print the target without deleting it" \
    "  -h, --help   Show this help"
}

DEST=""
DRY_RUN=0
while (($#)); do
  case "$1" in
    --dest)
      if (($# < 2)); then
        printf 'ERROR: --dest requires a path\n' >&2
        exit 2
      fi
      DEST=$2
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf 'ERROR: unknown argument: %s\n' "$1" >&2
      usage >&2
      exit 2
      ;;
  esac
done

if [[ -z "$DEST" ]]; then
  if [[ -n "${CLAUDE_CONFIG_DIR:-}" ]]; then
    DEST="$CLAUDE_CONFIG_DIR/skills/goal-workflow"
  elif [[ -n "${HOME:-}" ]]; then
    DEST="$HOME/.claude/skills/goal-workflow"
  else
    printf 'ERROR: neither CLAUDE_CONFIG_DIR nor HOME is set; use --dest\n' >&2
    exit 2
  fi
fi

if [[ -z "$DEST" || "$DEST" == "/" ]]; then
  printf 'ERROR: refusing unsafe uninstall target: %s\n' "$DEST" >&2
  exit 2
fi
if [[ "$(basename "$DEST")" != "goal-workflow" ]]; then
  printf 'ERROR: uninstall target must be named goal-workflow: %s\n' "$DEST" >&2
  exit 2
fi
if [[ "$(basename "$(dirname "$DEST")")" != "skills" ]]; then
  printf 'ERROR: uninstall target must be directly inside a skills directory: %s\n' "$DEST" >&2
  exit 2
fi
if [[ -L "$DEST" ]]; then
  printf 'ERROR: refusing to uninstall a symbolic link: %s\n' "$DEST" >&2
  exit 1
fi
if [[ ! -d "$DEST" ]]; then
  printf 'ERROR: uninstall target is not an existing directory: %s\n' "$DEST" >&2
  exit 1
fi

DEST_PARENT="$(cd "$(dirname "$DEST")" && pwd -P)"
DEST="$DEST_PARENT/goal-workflow"
if [[ "$DEST_PARENT" == "/" || "$(basename "$DEST_PARENT")" != "skills" ]]; then
  printf 'ERROR: resolved uninstall target is outside a non-root skills directory: %s\n' "$DEST" >&2
  exit 2
fi
if [[ -L "$DEST" || ! -d "$DEST" ]]; then
  printf 'ERROR: resolved uninstall target is unsafe or missing: %s\n' "$DEST" >&2
  exit 1
fi

python3 "$VALIDATOR" --skill-dir "$DEST" --identity-only >/dev/null

if [[ $DRY_RUN -eq 1 ]]; then
  printf 'Would uninstall validated goal-workflow at %s\n' "$DEST"
  exit 0
fi

timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
QUARANTINE="$DEST_PARENT/.goal-workflow.uninstall.$timestamp.$$"
suffix=0
while [[ -e "$QUARANTINE" || -L "$QUARANTINE" ]]; do
  suffix=$((suffix + 1))
  QUARANTINE="$DEST_PARENT/.goal-workflow.uninstall.$timestamp.$$.${suffix}"
done

rename_path "$DEST" "$QUARANTINE"
if ! rm -rf "$QUARANTINE"; then
  printf 'ERROR: uninstall quarantined the skill but could not remove it; inspect or recover from %s\n' "$QUARANTINE" >&2
  exit 1
fi
printf 'Uninstalled goal-workflow from %s\n' "$DEST"
