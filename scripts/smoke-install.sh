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
BACKUP_DIR="$CLAUDE_CONFIG_DIR/goal-workflow-backups"

# Claude Code loads every directory under skills/ that holds a SKILL.md, so a
# backup there is a live second copy of the skill. Fails if any is present.
assert_no_backup_in_skills() {
  local skills_dir=$1 context=$2
  local stray=()
  shopt -s nullglob
  stray=("$skills_dir"/goal-workflow.backup.* "$skills_dir"/.goal-workflow.backup.*)
  shopt -u nullglob
  if ((${#stray[@]} != 0)); then
    printf 'ERROR: %s left %d backup(s) inside the skills directory, where they load as skills\n' \
      "$context" "${#stray[@]}" >&2
    exit 1
  fi
}

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

# A wrong basename must be refused, not silently rewritten to goal-workflow.
# The config directory is fresh so nothing else can refuse this for us.
FRESH_CONFIG="$TMP_ROOT/fresh/.claude"
WRONG_NAME_DEST="$FRESH_CONFIG/skills/goal-workflow-copy"
if "$INSTALLER" --dest "$WRONG_NAME_DEST" >/dev/null 2>&1; then
  printf 'ERROR: installer accepted a destination not named goal-workflow\n' >&2
  exit 1
fi
test ! -e "$WRONG_NAME_DEST"
test ! -e "$FRESH_CONFIG/skills/goal-workflow"

# An explicitly empty --dest must fail on its own terms, never fall back to the
# default destination.
empty_dest_error="$("$INSTALLER" --dest "" 2>&1 >/dev/null || true)"
if "$INSTALLER" --dest "" >/dev/null 2>&1; then
  printf 'ERROR: installer accepted an empty --dest\n' >&2
  exit 1
fi
if ! printf '%s\n' "$empty_dest_error" | grep -q 'ERROR: --dest was given an empty path'; then
  printf 'ERROR: installer did not reject an empty --dest as an empty path\n' >&2
  exit 1
fi
test -f "$DEST/SKILL.md"

# A destination outside a Claude configuration directory must be refused before
# the installer creates any directories on the way to it.
PLAIN_DEST="$TMP_ROOT/plain-not-config/skills/goal-workflow"
if "$INSTALLER" --dest "$PLAIN_DEST" >/dev/null 2>&1; then
  printf 'ERROR: installer accepted a destination outside a Claude configuration directory\n' >&2
  exit 1
fi
test ! -e "$TMP_ROOT/plain-not-config"

INSTALL_SYMLINK_DEST="$TMP_ROOT/install-symlink/skills/goal-workflow"
mkdir -p "$TMP_ROOT/install-symlink/skills" "$TMP_ROOT/install-symlink-victim"
ln -s "$TMP_ROOT/install-symlink-victim" "$INSTALL_SYMLINK_DEST"
if "$INSTALLER" --dest "$INSTALL_SYMLINK_DEST" --replace >/dev/null 2>&1; then
  printf 'ERROR: installer accepted a symbolic-link destination\n' >&2
  exit 1
fi
test -L "$INSTALL_SYMLINK_DEST"
test ! -e "$INSTALL_SYMLINK_DEST/SKILL.md"

CONFIG_SYMLINK_DEST="$TMP_ROOT/config-symlink/.claude/skills/goal-workflow"
mkdir -p "$(dirname "$CONFIG_SYMLINK_DEST")" "$TMP_ROOT/config-symlink-victim"
ln -s "$TMP_ROOT/config-symlink-victim" "$CONFIG_SYMLINK_DEST"
if "$INSTALLER" --dest "$CONFIG_SYMLINK_DEST" --replace >/dev/null 2>&1; then
  printf 'ERROR: installer accepted a symbolic-link destination inside a .claude directory\n' >&2
  exit 1
fi
test -L "$CONFIG_SYMLINK_DEST"
test ! -e "$CONFIG_SYMLINK_DEST/SKILL.md"

# A .claude name is not enough: when the path resolves out of the configuration
# directory the destination must still be refused, checked after resolution.
RESOLVED_ESCAPE_ROOT="$TMP_ROOT/resolved-escape"
mkdir -p "$RESOLVED_ESCAPE_ROOT/plain-target"
mkdir -p "$RESOLVED_ESCAPE_ROOT/home"
ln -s "$RESOLVED_ESCAPE_ROOT/plain-target" "$RESOLVED_ESCAPE_ROOT/home/.claude"
RESOLVED_ESCAPE_DEST="$RESOLVED_ESCAPE_ROOT/home/.claude/skills/goal-workflow"
if "$INSTALLER" --dest "$RESOLVED_ESCAPE_DEST" >/dev/null 2>&1; then
  printf 'ERROR: installer accepted a destination that resolves outside a Claude configuration directory\n' >&2
  exit 1
fi
test ! -e "$RESOLVED_ESCAPE_ROOT/plain-target/skills/goal-workflow"

# A source checkout's own skills/goal-workflow is the release artifact, not an
# installation. Both scripts must refuse it even when it is reached through a
# path that otherwise looks like a Claude configuration directory, so the
# checkout markers are the only thing that can save it here.
SOURCE_CHECKOUT="$TMP_ROOT/source-checkout/.claude"
mkdir -p "$SOURCE_CHECKOUT/skills" "$SOURCE_CHECKOUT/scripts"
cp -R "$CANONICAL" "$SOURCE_CHECKOUT/skills/goal-workflow"
cp "$REPO_ROOT/VERSION" "$SOURCE_CHECKOUT/VERSION"
cp "$REPO_ROOT/CLAUDE.md" "$SOURCE_CHECKOUT/CLAUDE.md"
cp "$INSTALLER" "$UNINSTALLER" "$SOURCE_CHECKOUT/scripts/"
if "$INSTALLER" --dest "$SOURCE_CHECKOUT/skills/goal-workflow" --replace >/dev/null 2>&1; then
  printf 'ERROR: installer replaced the canonical bundle of a source checkout\n' >&2
  exit 1
fi
test -f "$SOURCE_CHECKOUT/skills/goal-workflow/SKILL.md"
if "$UNINSTALLER" --dest "$SOURCE_CHECKOUT/skills/goal-workflow" >/dev/null 2>&1; then
  printf 'ERROR: uninstaller deleted the canonical bundle of a source checkout\n' >&2
  exit 1
fi
test -f "$SOURCE_CHECKOUT/skills/goal-workflow/SKILL.md"

# The same protection must hold for a checkout identified by a .git directory
# alongside skills/, which is what a plain `git clone` of this project looks
# like before anything is installed from it.
GIT_CHECKOUT="$TMP_ROOT/git-checkout/.claude"
mkdir -p "$GIT_CHECKOUT/.git" "$GIT_CHECKOUT/skills" "$GIT_CHECKOUT/scripts"
cp -R "$CANONICAL" "$GIT_CHECKOUT/skills/goal-workflow"
cp "$UNINSTALLER" "$GIT_CHECKOUT/scripts/"
if "$UNINSTALLER" --dest "$GIT_CHECKOUT/skills/goal-workflow" >/dev/null 2>&1; then
  printf 'ERROR: uninstaller deleted the canonical bundle of a git source checkout\n' >&2
  exit 1
fi
test -f "$GIT_CHECKOUT/skills/goal-workflow/SKILL.md"

# The repository this smoke test runs from is itself a source checkout: the
# real-world shape of the reported defect. Its canonical bundle must survive.
if "$UNINSTALLER" --dest "$CANONICAL" >/dev/null 2>&1; then
  printf 'ERROR: uninstaller deleted the canonical bundle of this checkout\n' >&2
  exit 1
fi
test -f "$CANONICAL/SKILL.md"
if "$INSTALLER" --dest "$CANONICAL" --replace >/dev/null 2>&1; then
  printf 'ERROR: installer replaced the canonical bundle of this checkout\n' >&2
  exit 1
fi
test -f "$CANONICAL/SKILL.md"

NON_SKILLS_PARENT_DEST="$TMP_ROOT/non-skills-parent/.claude/plugins/goal-workflow"
mkdir -p "$(dirname "$NON_SKILLS_PARENT_DEST")"
if "$INSTALLER" --dest "$NON_SKILLS_PARENT_DEST" >/dev/null 2>&1; then
  printf 'ERROR: installer accepted a destination whose parent is not skills\n' >&2
  exit 1
fi
test ! -e "$NON_SKILLS_PARENT_DEST"

WRONG_SKILL_DEST="$TMP_ROOT/wrong-skill/.claude/skills/goal-workflow"
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
backups=("$BACKUP_DIR"/goal-workflow.backup.*)
shopt -u nullglob
if [[ ${#backups[@]} -ne 1 || ! -f "${backups[0]}/local-update-marker" ]]; then
  printf 'ERROR: --replace did not retain the previous installation as a backup in %s\n' "$BACKUP_DIR" >&2
  exit 1
fi
assert_no_backup_in_skills "$CLAUDE_CONFIG_DIR/skills" "--replace"

# A sibling copy under a different name is a valid skill in a valid skills
# directory, so only the basename check stands between it and deletion.
WRONG_NAME_TARGET="$CLAUDE_CONFIG_DIR/skills/goal-workflow-stale"
cp -R "$DEST" "$WRONG_NAME_TARGET"
if "$UNINSTALLER" --dest "$WRONG_NAME_TARGET" >/dev/null 2>&1; then
  printf 'ERROR: uninstaller accepted a target not named goal-workflow\n' >&2
  exit 1
fi
test -f "$WRONG_NAME_TARGET/SKILL.md"
rm -rf "$WRONG_NAME_TARGET"

# Correct name, valid identity, inside the configuration directory, but the
# parent is not skills/: the parent check is the only guard that applies.
NON_SKILLS_TARGET="$CLAUDE_CONFIG_DIR/plugins/goal-workflow"
mkdir -p "$(dirname "$NON_SKILLS_TARGET")"
cp -R "$DEST" "$NON_SKILLS_TARGET"
non_skills_error="$("$UNINSTALLER" --dest "$NON_SKILLS_TARGET" 2>&1 >/dev/null || true)"
if "$UNINSTALLER" --dest "$NON_SKILLS_TARGET" >/dev/null 2>&1; then
  printf 'ERROR: uninstaller accepted a target whose parent is not skills\n' >&2
  exit 1
fi
# The refusal must come from the parent check itself, before any resolution, so
# the operator sees the path they typed rather than a resolved variant of it.
if ! printf '%s\n' "$non_skills_error" | grep -q '^ERROR: uninstall target must be directly inside a skills directory'; then
  printf 'ERROR: uninstaller refused a non-skills parent with the wrong diagnostic\n' >&2
  exit 1
fi
test -f "$NON_SKILLS_TARGET/SKILL.md"
rm -rf "$(dirname "$NON_SKILLS_TARGET")"

# The reported data-loss shape: an empty --dest must never silently retarget the
# default installation and delete it.
empty_dest_error="$("$UNINSTALLER" --dest "" 2>&1 >/dev/null || true)"
if "$UNINSTALLER" --dest "" >/dev/null 2>&1; then
  printf 'ERROR: uninstaller accepted an empty --dest\n' >&2
  exit 1
fi
if ! printf '%s\n' "$empty_dest_error" | grep -q 'ERROR: --dest was given an empty path'; then
  printf 'ERROR: uninstaller did not reject an empty --dest as an empty path\n' >&2
  exit 1
fi
test -f "$DEST/SKILL.md"

# A valid installed skill outside any Claude configuration directory must be
# refused: nothing else in the uninstaller distinguishes it.
OUTSIDE_TARGET="$TMP_ROOT/outside-config/skills/goal-workflow"
mkdir -p "$(dirname "$OUTSIDE_TARGET")"
cp -R "$DEST" "$OUTSIDE_TARGET"
outside_error="$("$UNINSTALLER" --dest "$OUTSIDE_TARGET" 2>&1 >/dev/null || true)"
if "$UNINSTALLER" --dest "$OUTSIDE_TARGET" >/dev/null 2>&1; then
  printf 'ERROR: uninstaller accepted a target outside a Claude configuration directory\n' >&2
  exit 1
fi
# Refused before resolution, and the message must name the real install path so
# an operator who guessed wrong knows what to pass instead.
if ! printf '%s\n' "$outside_error" | grep -q '^ERROR: uninstall target is not inside a Claude configuration directory'; then
  printf 'ERROR: uninstaller refused a non-configuration target with the wrong diagnostic\n' >&2
  exit 1
fi
if ! printf '%s\n' "$outside_error" | grep -qF "HINT: pass the real install path, for example $DEST"; then
  printf 'ERROR: uninstaller did not name the real install path when refusing\n' >&2
  exit 1
fi
test -f "$OUTSIDE_TARGET/SKILL.md"

# Refusal must survive path resolution: a .claude symlink pointing out of the
# configuration directory is only caught after the parent is resolved.
UNINSTALL_ESCAPE_ROOT="$TMP_ROOT/uninstall-escape"
mkdir -p "$UNINSTALL_ESCAPE_ROOT/plain-target/skills" "$UNINSTALL_ESCAPE_ROOT/home"
cp -R "$DEST" "$UNINSTALL_ESCAPE_ROOT/plain-target/skills/goal-workflow"
ln -s "$UNINSTALL_ESCAPE_ROOT/plain-target" "$UNINSTALL_ESCAPE_ROOT/home/.claude"
if "$UNINSTALLER" --dest "$UNINSTALL_ESCAPE_ROOT/home/.claude/skills/goal-workflow" >/dev/null 2>&1; then
  printf 'ERROR: uninstaller accepted a target that resolves outside a Claude configuration directory\n' >&2
  exit 1
fi
test -f "$UNINSTALL_ESCAPE_ROOT/plain-target/skills/goal-workflow/SKILL.md"

# A skills directory that is itself a symlink resolves to a non-skills parent,
# which only the post-resolution parent check rejects.
RESOLVED_PARENT_ROOT="$TMP_ROOT/resolved-parent/.claude"
mkdir -p "$RESOLVED_PARENT_ROOT/plugins"
cp -R "$DEST" "$RESOLVED_PARENT_ROOT/plugins/goal-workflow"
ln -s "$RESOLVED_PARENT_ROOT/plugins" "$RESOLVED_PARENT_ROOT/skills"
if "$UNINSTALLER" --dest "$RESOLVED_PARENT_ROOT/skills/goal-workflow" >/dev/null 2>&1; then
  printf 'ERROR: uninstaller accepted a target whose resolved parent is not skills\n' >&2
  exit 1
fi
test -f "$RESOLVED_PARENT_ROOT/plugins/goal-workflow/SKILL.md"

"$UNINSTALLER" --dry-run >/dev/null
test -d "$DEST"

# The backup from the --replace above is a complete working skill copy, so a
# default uninstall must report it and keep it, and --purge-backups must clear
# it. Dry-run must not remove anything either way.
"$UNINSTALLER" --dry-run --purge-backups >/dev/null
test -d "$DEST"
test -d "${backups[0]}"

uninstall_output="$("$UNINSTALLER")"
test ! -e "$DEST"
if ! printf '%s\n' "$uninstall_output" | grep -qF "Found backup ${backups[0]}"; then
  printf 'ERROR: uninstaller did not report the retained backup\n' >&2
  exit 1
fi
if [[ ! -f "${backups[0]}/SKILL.md" ]]; then
  printf 'ERROR: uninstaller removed a backup without --purge-backups\n' >&2
  exit 1
fi

"$INSTALLER" >/dev/null
purge_output="$("$UNINSTALLER" --purge-backups)"
test ! -e "$DEST"
if ! printf '%s\n' "$purge_output" | grep -qF "Removed backup ${backups[0]}"; then
  printf 'ERROR: --purge-backups did not report removing the backup\n' >&2
  exit 1
fi
test ! -e "${backups[0]}"

# Backups must not accumulate without bound: repeated --replace keeps the most
# recent few and reports every prune.
"$INSTALLER" >/dev/null
for _ in 1 2 3 4 5; do
  "$INSTALLER" --replace >/dev/null
done
shopt -s nullglob
retained=("$BACKUP_DIR"/goal-workflow.backup.*)
shopt -u nullglob
if ((${#retained[@]} > 3)); then
  printf 'ERROR: repeated --replace accumulated %d backups without pruning\n' "${#retained[@]}" >&2
  exit 1
fi
prune_output="$("$INSTALLER" --replace)"
if ! printf '%s\n' "$prune_output" | grep -q '^Pruned old backup '; then
  printf 'ERROR: --replace did not report pruning an old backup\n' >&2
  exit 1
fi
"$UNINSTALLER" --purge-backups >/dev/null
test ! -e "$DEST"
shopt -s nullglob
leftover=("$BACKUP_DIR"/goal-workflow.backup.*)
shopt -u nullglob
if ((${#leftover[@]} != 0)); then
  printf 'ERROR: --purge-backups left %d backups behind\n' "${#leftover[@]}" >&2
  exit 1
fi
test ! -e "$BACKUP_DIR"
"$INSTALLER" >/dev/null
"$UNINSTALLER" >/dev/null
test ! -e "$DEST"

# Versions before 0.9.0 kept backups inside skills/, where they still load as
# skills. Installing moves them out rather than deleting them.
LEGACY_NAME="goal-workflow.backup.20260101T000000Z.4242"
cp -R "$CANONICAL" "$CLAUDE_CONFIG_DIR/skills/$LEGACY_NAME"
install_output="$("$INSTALLER")"
assert_no_backup_in_skills "$CLAUDE_CONFIG_DIR/skills" "install"
test -f "$BACKUP_DIR/$LEGACY_NAME/SKILL.md"
if ! printf '%s\n' "$install_output" | grep -qF "Moved legacy backup $CLAUDE_CONFIG_DIR/skills/$LEGACY_NAME"; then
  printf 'ERROR: install did not report moving a legacy backup out of the skills directory\n' >&2
  exit 1
fi

# Uninstalling must not leave a legacy backup loaded as the skill it removes.
# A dry run reports the move without making it.
cp -R "$CANONICAL" "$CLAUDE_CONFIG_DIR/skills/$LEGACY_NAME"
dry_output="$("$UNINSTALLER" --dry-run)"
test -d "$CLAUDE_CONFIG_DIR/skills/$LEGACY_NAME"
if ! printf '%s\n' "$dry_output" | grep -qF "Would move legacy backup $CLAUDE_CONFIG_DIR/skills/$LEGACY_NAME"; then
  printf 'ERROR: uninstall --dry-run did not report the legacy backup it would move\n' >&2
  exit 1
fi
uninstall_output="$("$UNINSTALLER")"
test ! -e "$DEST"
assert_no_backup_in_skills "$CLAUDE_CONFIG_DIR/skills" "uninstall"
test -f "$BACKUP_DIR/$LEGACY_NAME.1/SKILL.md"
if ! printf '%s\n' "$uninstall_output" | grep -qF "Found backup $BACKUP_DIR/$LEGACY_NAME.1"; then
  printf 'ERROR: uninstall did not report the legacy backup it moved\n' >&2
  exit 1
fi

# --purge-backups clears legacy and current backups alike, then the empty
# backup directory.
"$INSTALLER" >/dev/null
cp -R "$CANONICAL" "$CLAUDE_CONFIG_DIR/skills/$LEGACY_NAME"
"$UNINSTALLER" --purge-backups >/dev/null
test ! -e "$DEST"
assert_no_backup_in_skills "$CLAUDE_CONFIG_DIR/skills" "uninstall --purge-backups"
test ! -e "$BACKUP_DIR"

# A backup location that is a symlink is refused before the installed skill is
# touched: one pointing back into skills/ would make every backup a live skill
# again, which is the defect the separate directory exists to prevent.
"$INSTALLER" >/dev/null
ln -s "$CLAUDE_CONFIG_DIR/skills" "$BACKUP_DIR"
symlinked_backup_error="$("$INSTALLER" --replace 2>&1 >/dev/null || true)"
if ! printf '%s\n' "$symlinked_backup_error" | grep -q '^ERROR: backup location is not a plain directory'; then
  printf 'ERROR: --replace did not refuse a symlinked backup location\n' >&2
  exit 1
fi
test -f "$DEST/SKILL.md"
assert_no_backup_in_skills "$CLAUDE_CONFIG_DIR/skills" "--replace with a symlinked backup location"
rm -f "$BACKUP_DIR"
"$UNINSTALLER" >/dev/null
test ! -e "$DEST"

EXPLICIT_DEST="$TMP_ROOT/explicit/.claude/skills/goal-workflow"
"$INSTALLER" --dest "$EXPLICIT_DEST" >/dev/null
"$UNINSTALLER" --dest "$EXPLICIT_DEST" --dry-run >/dev/null
test -d "$EXPLICIT_DEST"
"$UNINSTALLER" --dest "$EXPLICIT_DEST" >/dev/null
test ! -e "$EXPLICIT_DEST"

# A symlink whose target is a real installation elsewhere: following it would
# delete the target, so the link itself must be refused. The victim is a plain
# installed copy rather than this checkout, so only the symlink check applies.
SYMLINK_VICTIM="$TMP_ROOT/symlink-victim/.claude/skills/goal-workflow"
mkdir -p "$(dirname "$SYMLINK_VICTIM")"
cp -R "$CANONICAL" "$SYMLINK_VICTIM"
SYMLINK_DEST="$TMP_ROOT/symlink/.claude/skills/goal-workflow"
mkdir -p "$(dirname "$SYMLINK_DEST")"
ln -s "$SYMLINK_VICTIM" "$SYMLINK_DEST"
symlink_error="$("$UNINSTALLER" --dest "$SYMLINK_DEST" 2>&1 >/dev/null || true)"
if "$UNINSTALLER" --dest "$SYMLINK_DEST" >/dev/null 2>&1; then
  printf 'ERROR: uninstaller accepted a symbolic-link target\n' >&2
  exit 1
fi
# Refused as a symlink specifically: resolving it first would report the victim's
# path and make the link itself look acceptable.
if ! printf '%s\n' "$symlink_error" | grep -q '^ERROR: refusing to uninstall a symbolic link'; then
  printf 'ERROR: uninstaller refused a symbolic-link target with the wrong diagnostic\n' >&2
  exit 1
fi
test -L "$SYMLINK_DEST"
test -f "$SYMLINK_VICTIM/SKILL.md"
test -f "$CANONICAL/SKILL.md"

INVALID_DEST="$TMP_ROOT/invalid/.claude/skills/goal-workflow"
mkdir -p "$INVALID_DEST"
printf '%s\n' '---' 'name: another-skill' 'description: invalid identity' '---' >"$INVALID_DEST/SKILL.md"
if "$UNINSTALLER" --dest "$INVALID_DEST" >/dev/null 2>&1; then
  printf 'ERROR: uninstaller accepted a directory with the wrong skill identity\n' >&2
  exit 1
fi
test -d "$INVALID_DEST"

# A dangling backup symlink is invisible to -e, so the collision loop must also
# test -L or the rename dies and --replace is blocked for good. The shim plants
# the symlink on exactly the path the installer is about to choose: it runs as a
# child of the installer, so $PPID is the PID the installer interpolates.
DANGLING_ROOT="$TMP_ROOT/dangling/.claude/skills"
DANGLING_BACKUPS="$TMP_ROOT/dangling/.claude/goal-workflow-backups"
DANGLING_DEST="$DANGLING_ROOT/goal-workflow"
"$INSTALLER" --dest "$DANGLING_DEST" >/dev/null

SHIM_BIN="$TMP_ROOT/shim-bin"
mkdir -p "$SHIM_BIN"
REAL_DATE="$(command -v date)"
{
  printf '%s\n' '#!/usr/bin/env bash' 'set -euo pipefail'
  printf 'stamp="$(%q "$@")"\n' "$REAL_DATE"
  printf 'if [[ "${1-}" == "-u" ]]; then\n'
  printf '  ln -s %q "%s/goal-workflow.backup.$stamp.$PPID"\n' \
    "$TMP_ROOT/dangling-missing-target" "$DANGLING_BACKUPS"
  printf 'fi\n'
  printf '%s\n' 'printf "%s\n" "$stamp"'
} >"$SHIM_BIN/date"
chmod +x "$SHIM_BIN/date"

if ! PATH="$SHIM_BIN:$PATH" "$INSTALLER" --dest "$DANGLING_DEST" --replace >/dev/null 2>&1; then
  printf 'ERROR: --replace failed with a dangling backup symlink in the backup directory\n' >&2
  exit 1
fi
shopt -s nullglob
dangling_links=("$DANGLING_BACKUPS"/goal-workflow.backup.*)
shopt -u nullglob
dangling_seen=0
for entry in ${dangling_links[@]+"${dangling_links[@]}"}; do
  if [[ -L "$entry" ]]; then
    dangling_seen=1
  fi
done
if ((dangling_seen == 0)); then
  printf 'ERROR: the dangling backup symlink case did not plant a symlink to collide with\n' >&2
  exit 1
fi
python3 "$VALIDATOR" --skill-dir "$DANGLING_DEST" --installed-only >/dev/null
"$UNINSTALLER" --dest "$DANGLING_DEST" --purge-backups >/dev/null
test ! -e "$DANGLING_DEST"

printf 'Install/update/uninstall smoke test passed in isolated CLAUDE_CONFIG_DIR.\n'
