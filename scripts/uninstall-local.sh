#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
VALIDATOR="$SCRIPT_DIR/validate.py"

rename_path() {
  python3 -c 'import os, sys; os.rename(sys.argv[1], sys.argv[2])' "$1" "$2"
}

# Make a path comparable as a string without touching the filesystem.
normalize_path() {
  local path=$1
  if [[ "$path" != /* ]]; then
    path="$PWD/$path"
  fi
  while [[ "$path" == *//* ]]; do
    path=${path//\/\//\/}
  done
  if [[ "$path" != "/" ]]; then
    path=${path%/}
  fi
  printf '%s' "$path"
}

# Compare install paths by physical location when it exists, so a config dir
# reached through a symlinked prefix such as /tmp still matches the resolved
# target parent. Falls back to lexical normalization.
resolve_path() {
  local path=$1
  if [[ -d "$path" ]]; then
    (cd "$path" && pwd -P)
    return 0
  fi
  normalize_path "$path"
}

# An install tree keeps its skills directory directly inside a Claude
# configuration directory: $CLAUDE_CONFIG_DIR, or a user- or project-level
# .claude. A goal-workflow source checkout also has skills/goal-workflow, but
# that copy is the release artifact, not an installation, so removing it would
# destroy the source this project ships.
is_claude_config_dir() {
  local candidate
  candidate="$(normalize_path "$1")"
  if [[ "$(basename "$candidate")" == ".claude" ]]; then
    return 0
  fi
  if [[ -n "${CLAUDE_CONFIG_DIR:-}" ]]; then
    local configured
    configured="$(normalize_path "$CLAUDE_CONFIG_DIR")"
    if [[ "$candidate" == "$configured" ]]; then
      return 0
    fi
    if [[ "$(resolve_path "$1")" == "$(resolve_path "$CLAUDE_CONFIG_DIR")" ]]; then
      return 0
    fi
  fi
  return 1
}

# Recognize a goal-workflow source checkout by its own repository markers, so a
# .claude path that resolves into a checkout is still refused.
is_source_checkout() {
  local candidate=$1
  if [[ -f "$candidate/VERSION" && -f "$candidate/CLAUDE.md" && -f "$candidate/scripts/install-local.sh" ]]; then
    return 0
  fi
  if [[ -e "$candidate/.git" && -d "$candidate/skills/goal-workflow" && -f "$candidate/scripts/uninstall-local.sh" ]]; then
    return 0
  fi
  return 1
}

default_install_hint() {
  if [[ -n "${CLAUDE_CONFIG_DIR:-}" ]]; then
    printf '%s/skills/goal-workflow' "$CLAUDE_CONFIG_DIR"
  elif [[ -n "${HOME:-}" ]]; then
    printf '%s/.claude/skills/goal-workflow' "$HOME"
  else
    printf '%s' '${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/goal-workflow'
  fi
}

usage() {
  printf '%s\n' \
    "Usage: scripts/uninstall-local.sh [--dest PATH] [--dry-run] [--purge-backups]" \
    "" \
    "Remove a validated goal-workflow installation." \
    "" \
    "Options:" \
    "  --dest PATH       Destination (default: \${CLAUDE_CONFIG_DIR:-\$HOME/.claude}/skills/goal-workflow)" \
    "  --dry-run         Validate and print the target without deleting it" \
    "  --purge-backups   Also remove goal-workflow.backup.* siblings left by --replace" \
    "  -h, --help        Show this help" \
    "" \
    "The target must be a goal-workflow directory directly inside a skills" \
    "directory of a Claude configuration directory, never a source checkout." \
    "Backups are reported but retained unless --purge-backups is given."
}

DEST=""
DEST_GIVEN=0
DRY_RUN=0
PURGE_BACKUPS=0
while (($#)); do
  case "$1" in
    --dest)
      if (($# < 2)); then
        printf 'ERROR: --dest requires a path\n' >&2
        exit 2
      fi
      if [[ -z "$2" ]]; then
        printf 'ERROR: --dest was given an empty path; pass an explicit target such as %s\n' \
          "$(default_install_hint)" >&2
        exit 2
      fi
      DEST=$2
      DEST_GIVEN=1
      shift 2
      ;;
    --dry-run)
      DRY_RUN=1
      shift
      ;;
    --purge-backups)
      PURGE_BACKUPS=1
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

if [[ $DEST_GIVEN -eq 0 ]]; then
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
DEST_ROOT="$(dirname "$(dirname "$DEST")")"
if ! is_claude_config_dir "$DEST_ROOT" || is_source_checkout "$DEST_ROOT"; then
  printf 'ERROR: uninstall target is not inside a Claude configuration directory: %s\n' "$DEST" >&2
  printf 'HINT: pass the real install path, for example %s\n' "$(default_install_hint)" >&2
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
DEST_ROOT="$(dirname "$DEST_PARENT")"
if ! is_claude_config_dir "$DEST_ROOT" || is_source_checkout "$DEST_ROOT"; then
  printf 'ERROR: resolved uninstall target is not inside a Claude configuration directory: %s\n' "$DEST" >&2
  printf 'HINT: pass the real install path, for example %s\n' "$(default_install_hint)" >&2
  exit 2
fi
if [[ -L "$DEST" || ! -d "$DEST" ]]; then
  printf 'ERROR: resolved uninstall target is unsafe or missing: %s\n' "$DEST" >&2
  exit 1
fi

python3 "$VALIDATOR" --skill-dir "$DEST" --identity-only >/dev/null

# A backup left by install-local.sh --replace is a complete working skill copy,
# so uninstalling without addressing it leaves the old behavior live.
shopt -s nullglob
BACKUPS=("$DEST_PARENT"/goal-workflow.backup.*)
shopt -u nullglob

report_backups() {
  local verb=$1
  local backup
  for backup in ${BACKUPS[@]+"${BACKUPS[@]}"}; do
    printf '%s backup %s\n' "$verb" "$backup"
  done
  if [[ ${#BACKUPS[@]} -gt 0 && $PURGE_BACKUPS -eq 0 ]]; then
    printf 'Retained %d backup(s); rerun with --purge-backups to remove them\n' "${#BACKUPS[@]}"
  fi
}

if [[ $DRY_RUN -eq 1 ]]; then
  printf 'Would uninstall validated goal-workflow at %s\n' "$DEST"
  if [[ $PURGE_BACKUPS -eq 1 ]]; then
    report_backups "Would remove"
  else
    report_backups "Found"
  fi
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

if [[ $PURGE_BACKUPS -eq 1 ]]; then
  for backup in ${BACKUPS[@]+"${BACKUPS[@]}"}; do
    if [[ -L "$backup" || ! -d "$backup" ]]; then
      printf 'Kept unexpected non-directory backup entry %s\n' "$backup"
      continue
    fi
    if rm -rf "$backup"; then
      printf 'Removed backup %s\n' "$backup"
    else
      printf 'ERROR: could not remove backup %s\n' "$backup" >&2
      exit 1
    fi
  done
else
  report_backups "Found"
fi
