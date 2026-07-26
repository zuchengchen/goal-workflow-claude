#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd -P)"
INSTALLER="$SCRIPT_DIR/install-local.sh"
UNINSTALLER="$SCRIPT_DIR/uninstall-local.sh"
VALIDATOR="$SCRIPT_DIR/validate.py"
CANONICAL="$REPO_ROOT/skills/goal-workflow"

TMP_ROOT="$(mktemp -d)"
cleanup() {
  rm -rf "$TMP_ROOT"
}
trap cleanup EXIT
trap 'exit 130' HUP INT TERM

export CLAUDE_CONFIG_DIR="$TMP_ROOT/claude-config"
DEST="$CLAUDE_CONFIG_DIR/skills/goal-workflow"

"$INSTALLER" >/dev/null
test -f "$DEST/SKILL.md"
cmp "$CANONICAL/SKILL.md" "$DEST/SKILL.md"
python3 "$VALIDATOR" --skill-dir "$DEST" --installed-only >/dev/null

if "$INSTALLER" >/dev/null 2>&1; then
  printf 'ERROR: installer overwrote an existing destination without --replace\n' >&2
  exit 1
fi

UNSAFE_DEST="$TMP_ROOT/not-a-skills-directory/goal-workflow"
if "$INSTALLER" --dest "$UNSAFE_DEST" >/dev/null 2>&1; then
  printf 'ERROR: installer accepted a destination outside a skills directory\n' >&2
  exit 1
fi
test ! -e "$UNSAFE_DEST"
test ! -e "$(dirname "$UNSAFE_DEST")"

WRONG_SKILL_DEST="$TMP_ROOT/wrong-skill/skills/goal-workflow"
mkdir -p "$WRONG_SKILL_DEST"
printf '%s\n' '---' 'name: another-skill' 'description: invalid identity' '---' >"$WRONG_SKILL_DEST/SKILL.md"
if "$INSTALLER" --dest "$WRONG_SKILL_DEST" --replace >/dev/null 2>&1; then
  printf 'ERROR: installer replaced a same-named directory with the wrong skill identity\n' >&2
  exit 1
fi
test -f "$WRONG_SKILL_DEST/SKILL.md"
grep -q '^name: another-skill$' "$WRONG_SKILL_DEST/SKILL.md"

touch "$DEST/local-update-marker"
"$INSTALLER" --replace >/dev/null
test ! -e "$DEST/local-update-marker"
python3 "$VALIDATOR" --skill-dir "$DEST" --installed-only >/dev/null

shopt -s nullglob
backups=("$CLAUDE_CONFIG_DIR/skills"/goal-workflow.backup.*)
shopt -u nullglob
if [[ ${#backups[@]} -ne 1 || ! -f "${backups[0]}/local-update-marker" ]]; then
  printf 'ERROR: --replace did not retain the previous installation as a backup\n' >&2
  exit 1
fi

"$UNINSTALLER" --dry-run >/dev/null
test -d "$DEST"
"$UNINSTALLER" >/dev/null
test ! -e "$DEST"

EXPLICIT_DEST="$TMP_ROOT/explicit/skills/goal-workflow"
"$INSTALLER" --dest "$EXPLICIT_DEST" >/dev/null
"$UNINSTALLER" --dest "$EXPLICIT_DEST" --dry-run >/dev/null
test -d "$EXPLICIT_DEST"
"$UNINSTALLER" --dest "$EXPLICIT_DEST" >/dev/null
test ! -e "$EXPLICIT_DEST"

SYMLINK_DEST="$TMP_ROOT/symlink/skills/goal-workflow"
mkdir -p "$(dirname "$SYMLINK_DEST")"
ln -s "$CANONICAL" "$SYMLINK_DEST"
if "$UNINSTALLER" --dest "$SYMLINK_DEST" >/dev/null 2>&1; then
  printf 'ERROR: uninstaller accepted a symbolic-link target\n' >&2
  exit 1
fi
test -f "$CANONICAL/SKILL.md"

INVALID_DEST="$TMP_ROOT/invalid/skills/goal-workflow"
mkdir -p "$INVALID_DEST"
printf '%s\n' '---' 'name: another-skill' 'description: invalid identity' '---' >"$INVALID_DEST/SKILL.md"
if "$UNINSTALLER" --dest "$INVALID_DEST" >/dev/null 2>&1; then
  printf 'ERROR: uninstaller accepted a directory with the wrong skill identity\n' >&2
  exit 1
fi
test -d "$INVALID_DEST"

printf 'Install/update/uninstall smoke test passed in isolated CLAUDE_CONFIG_DIR.\n'
