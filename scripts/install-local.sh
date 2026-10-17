#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
SOURCE_DIR="$REPO_ROOT/skills/goal-workflow"
VALIDATOR="$SCRIPT_DIR/validate.py"

BACKUP_RETENTION=3

rename_path() {
  python3 -c 'import os, sys; os.rename(sys.argv[1], sys.argv[2])' "$1" "$2"
}

# Make a path comparable as a string without touching the filesystem: the
# destination may not exist yet, so symlink resolution is not available here.
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
# destination parent. Falls back to lexical normalization.
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
# .claude. Anything else is not an installation, most importantly a
# goal-workflow source checkout whose own skills/goal-workflow is the release
# artifact this script copies from.
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
    "Usage: scripts/install-local.sh [--dest PATH] [--replace]" \
    "" \
    "Install the canonical goal-workflow skill from this checkout." \
    "" \
    "Options:" \
    "  --dest PATH  Destination (default: \${CLAUDE_CONFIG_DIR:-\$HOME/.claude}/skills/goal-workflow)" \
    "  --replace    Transactionally replace an installation and retain a backup" \
    "  -h, --help   Show this help" \
    "" \
    "The destination must be a goal-workflow directory directly inside a skills" \
    "directory of a Claude configuration directory, never a source checkout." \
    "--replace keeps the $BACKUP_RETENTION most recent backups in goal-workflow-backups/" \
    "beside the skills directory and prunes older ones."
}

DEST=""
DEST_GIVEN=0
REPLACE=0
while (($#)); do
  case "$1" in
    --dest)
      if (($# < 2)); then
        printf 'ERROR: --dest requires a path\n' >&2
        exit 2
      fi
      if [[ -z "$2" ]]; then
        printf 'ERROR: --dest was given an empty path; pass an explicit destination such as %s\n' \
          "$(default_install_hint)" >&2
        exit 2
      fi
      DEST=$2
      DEST_GIVEN=1
      shift 2
      ;;
    --replace)
      REPLACE=1
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

if [[ -z "$DEST" ]]; then
  printf 'ERROR: refusing unsafe install destination: %s\n' "$DEST" >&2
  exit 2
fi
if [[ "$(basename "$DEST")" != "goal-workflow" ]]; then
  printf 'ERROR: destination directory must be named goal-workflow: %s\n' "$DEST" >&2
  exit 2
fi
if [[ ! -d "$SOURCE_DIR" ]]; then
  printf 'ERROR: canonical skill is missing: %s\n' "$SOURCE_DIR" >&2
  exit 1
fi
if [[ -L "$DEST" ]]; then
  printf 'ERROR: refusing to replace a symbolic-link destination: %s\n' "$DEST" >&2
  exit 1
fi

DEST_PARENT="$(dirname "$DEST")"
if [[ "$DEST_PARENT" == "/" || "$(basename "$DEST_PARENT")" != "skills" ]]; then
  printf 'ERROR: destination must be directly inside a non-root skills directory: %s\n' "$DEST" >&2
  exit 2
fi
DEST_ROOT="$(dirname "$DEST_PARENT")"
if ! is_claude_config_dir "$DEST_ROOT" || is_source_checkout "$DEST_ROOT"; then
  printf 'ERROR: destination is not inside a Claude configuration directory: %s\n' "$DEST" >&2
  printf 'HINT: install into a CLAUDE_CONFIG_DIR or .claude skills directory, for example %s\n' \
    "$(default_install_hint)" >&2
  exit 2
fi
python3 "$VALIDATOR" --skill-dir "$SOURCE_DIR" --installed-only >/dev/null

mkdir -p "$DEST_PARENT"
DEST_PARENT="$(cd "$DEST_PARENT" && pwd -P)"
DEST="$DEST_PARENT/goal-workflow"
if [[ "$DEST_PARENT" == "/" || "$(basename "$DEST_PARENT")" != "skills" ]]; then
  printf 'ERROR: destination must be directly inside a non-root skills directory: %s\n' "$DEST" >&2
  exit 2
fi
DEST_ROOT="$(dirname "$DEST_PARENT")"
if ! is_claude_config_dir "$DEST_ROOT" || is_source_checkout "$DEST_ROOT"; then
  printf 'ERROR: resolved destination is not inside a Claude configuration directory: %s\n' "$DEST" >&2
  printf 'HINT: install into a CLAUDE_CONFIG_DIR or .claude skills directory, for example %s\n' \
    "$(default_install_hint)" >&2
  exit 2
fi
if [[ -L "$DEST" ]]; then
  printf 'ERROR: refusing to replace a symbolic-link destination: %s\n' "$DEST" >&2
  exit 1
fi

if [[ -e "$DEST" && $REPLACE -eq 0 ]]; then
  printf 'ERROR: destination already exists; rerun with --replace: %s\n' "$DEST" >&2
  exit 1
fi
if [[ -e "$DEST" && ! -d "$DEST" ]]; then
  printf 'ERROR: destination exists but is not a directory: %s\n' "$DEST" >&2
  exit 1
fi
if [[ -e "$DEST" && $REPLACE -eq 1 ]]; then
  if ! python3 "$VALIDATOR" --skill-dir "$DEST" --identity-only >/dev/null; then
    printf 'ERROR: refusing to replace a directory that is not the goal-workflow skill: %s\n' "$DEST" >&2
    exit 1
  fi
fi

# Backups live beside the skills directory, never inside it. Claude Code loads
# every directory under skills/ that holds a SKILL.md, whatever its name --
# goal-workflow.backup.<stamp> and a hidden .goal-workflow.backup.<stamp> alike
# (measured with CLI 2.1.302) -- so a backup kept there stays a live second copy
# of the skill, and an update left the old behavior running beside the new.
BACKUP_DIR="$DEST_ROOT/goal-workflow-backups"
if [[ -L "$BACKUP_DIR" || ( -e "$BACKUP_DIR" && ! -d "$BACKUP_DIR" ) ]]; then
  printf 'ERROR: backup location is not a plain directory: %s\n' "$BACKUP_DIR" >&2
  exit 1
fi

# Before 0.9.0 backups were kept inside skills/, where they are still loaded.
# Moving them out keeps every copy while ending the duplicate skill; a failed
# move is reported but does not undo an install that already succeeded.
migrate_legacy_backups() {
  local legacy target suffix
  local legacy_entries=()
  shopt -s nullglob
  legacy_entries=("$DEST_PARENT"/goal-workflow.backup.*)
  shopt -u nullglob
  for legacy in ${legacy_entries[@]+"${legacy_entries[@]}"}; do
    if [[ -L "$legacy" || ! -d "$legacy" ]]; then
      printf 'Kept unexpected non-directory legacy backup entry %s\n' "$legacy"
      continue
    fi
    if ! mkdir -p "$BACKUP_DIR"; then
      printf 'WARNING: could not create %s; legacy backups remain in the skills directory\n' "$BACKUP_DIR" >&2
      return 0
    fi
    target="$BACKUP_DIR/$(basename "$legacy")"
    suffix=0
    while [[ -e "$target" || -L "$target" ]]; do
      suffix=$((suffix + 1))
      target="$BACKUP_DIR/$(basename "$legacy").$suffix"
    done
    if rename_path "$legacy" "$target"; then
      printf 'Moved legacy backup %s out of the skills directory to %s\n' "$legacy" "$target"
    else
      printf 'WARNING: could not move legacy backup %s out of the skills directory; it is still loaded as a skill\n' "$legacy" >&2
    fi
  done
}

STAGE_ROOT=""
BACKUP_PATH=""
OLD_MOVED=0
COMMITTED=0
cleanup() {
  local status=$?
  trap - EXIT
  if [[ $status -ne 0 && $OLD_MOVED -eq 1 && $COMMITTED -eq 0 ]]; then
    if [[ ! -e "$DEST" && -e "$BACKUP_PATH" ]]; then
      if ! rename_path "$BACKUP_PATH" "$DEST"; then
        printf 'ERROR: install failed and rollback also failed; recover from %s\n' "$BACKUP_PATH" >&2
      fi
    elif [[ -e "$BACKUP_PATH" ]]; then
      printf 'ERROR: install failed after backup; recover the previous installation from %s\n' "$BACKUP_PATH" >&2
    fi
  fi
  if [[ -n "$STAGE_ROOT" && -d "$STAGE_ROOT" ]]; then
    rm -rf "$STAGE_ROOT"
  fi
  exit "$status"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

STAGE_ROOT="$(mktemp -d "$DEST_PARENT/.goal-workflow.install.XXXXXX")"
STAGED_SKILL="$STAGE_ROOT/goal-workflow"
mkdir "$STAGED_SKILL"
cp -R "$SOURCE_DIR/." "$STAGED_SKILL/"
python3 "$VALIDATOR" --skill-dir "$STAGED_SKILL" --installed-only >/dev/null

if [[ -e "$DEST" ]]; then
  mkdir -p "$BACKUP_DIR"
  timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
  BACKUP_PATH="$BACKUP_DIR/goal-workflow.backup.$timestamp.$$"
  suffix=0
  while [[ -e "$BACKUP_PATH" || -L "$BACKUP_PATH" ]]; do
    suffix=$((suffix + 1))
    BACKUP_PATH="$BACKUP_DIR/goal-workflow.backup.$timestamp.$$.${suffix}"
  done
  rename_path "$DEST" "$BACKUP_PATH"
  OLD_MOVED=1
fi

rename_path "$STAGED_SKILL" "$DEST"
COMMITTED=1

printf 'Installed goal-workflow at %s\n' "$DEST"
if [[ -n "$BACKUP_PATH" ]]; then
  printf 'Previous installation retained at %s\n' "$BACKUP_PATH"
fi

migrate_legacy_backups

# Every --replace leaves a full copy of the previous installation behind. Keep
# the newest BACKUP_RETENTION and prune the rest, reporting each removal so
# nothing disappears silently.
shopt -s nullglob
BACKUPS=("$BACKUP_DIR"/goal-workflow.backup.*)
shopt -u nullglob
if ((${#BACKUPS[@]} > BACKUP_RETENTION)); then
  # The timestamp is fixed-width, so a lexical sort is chronological and the
  # newest entries land last. Slice rather than `head -n -N`, which is a GNU
  # extension.
  SORTED=()
  while IFS= read -r line; do
    SORTED+=("$line")
  done < <(printf '%s\n' "${BACKUPS[@]}" | sort)
  PRUNE_COUNT=$((${#SORTED[@]} - BACKUP_RETENTION))
  for backup in "${SORTED[@]:0:$PRUNE_COUNT}"; do
    # The backup this run just created is reported as retained; never prune it,
    # even if a same-second sibling sorts after it.
    if [[ -n "$BACKUP_PATH" && "$backup" == "$BACKUP_PATH" ]]; then
      continue
    fi
    if [[ -L "$backup" || ! -d "$backup" ]]; then
      printf 'Kept unexpected non-directory backup entry %s\n' "$backup"
      continue
    fi
    if rm -rf "$backup"; then
      printf 'Pruned old backup %s\n' "$backup"
    else
      printf 'WARNING: could not prune old backup %s\n' "$backup" >&2
    fi
  done
fi
