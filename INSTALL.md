# 安装、更新与卸载

本文是 `goal-workflow` 唯一的详细安装说明。README 只提供快速入口；安装路径、版本固定、卸载和排错以本文为准。

## 安装标识

| 项目 | 值 |
| --- | --- |
| 仓库 | `https://github.com/zuchengchen/goal-workflow-claude` |
| canonical URL | `https://github.com/zuchengchen/goal-workflow-claude/tree/master/skills/goal-workflow` |
| moving ref | `master` |
| 仓库内精确 path | `skills/goal-workflow` |
| skill 名称 | `goal-workflow` |
| 用户级默认目标 | `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/goal-workflow` |
| 项目级目标 | `<project>/.claude/skills/goal-workflow` |

canonical URL 同时编码了精确 ref 和 path。canonical 安装路径不是仓库根，而是 `skills/goal-workflow`；本仓库的 moving ref 是 `master`。

为保证可复现，正式环境和团队配置应优先把 URL 中的 `master` 替换为已发布 tag（当前 source 版本 `0.6.0` 发布后对应 `v0.6.0`）或完整 commit SHA：

```text
https://github.com/zuchengchen/goal-workflow-claude/tree/v0.6.0/skills/goal-workflow
https://github.com/zuchengchen/goal-workflow-claude/tree/<full-commit-sha>/skills/goal-workflow
```

只有确实希望自动跟随最新提交时才使用 `master`。版本 tag 必须已经发布；发布前请固定到实际存在的完整 commit SHA。

## 前置条件

需要支持 Agent Skills 的 Claude Code。skill 本身是自包含的，不依赖任何外部 skill，也不依赖 npm、pip、Go、Rust 包。

只有手动 clone、检出固定版本或从 Git 更新源仓库时才需要 Git：

```bash
git --version
```

Claude Code 会在启动时从下列位置发现 skill：

- 用户级：`${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/`
- 项目级：从启动目录或其父目录向上查找的 `.claude/skills/`

安装后启动新的 Claude Code 会话；已打开的会话可能仍保留旧的 skill 上下文。

## 方法零：在 Claude Code 中一句话安装

在任意 Claude Code 会话中输入：

```text
安装 skill https://github.com/zuchengchen/goal-workflow-claude.git
```

这句话的预期语义、也是执行安装的 Claude 应当采用的步骤：

```bash
tmp_dir="$(mktemp -d)"
git clone --depth 1 https://github.com/zuchengchen/goal-workflow-claude.git "$tmp_dir"
"$tmp_dir/scripts/install-local.sh"
rm -rf -- "$tmp_dir"
```

安装脚本会先校验 skill 结构，目标已存在时停止且不覆盖；更新已有安装改用 `"$tmp_dir/scripts/install-local.sh" --replace`（保留备份，见「更新」）。需要固定版本时，把 clone 命令改为 `git clone --branch v0.6.0 --depth 1 …` 再执行安装脚本。安装完成后启动新的 Claude Code 会话，确认 `/goal-workflow` 出现。

## 方法一：从本地仓库直接复制

如果当前工作目录就是本仓库根，可安装到用户目录：

```bash
scripts/install-local.sh
```

复制到另一个项目：

```bash
scripts/install-local.sh --dest "/path/to/target-project/.claude/skills/goal-workflow"
```

脚本只接受名为 `goal-workflow` 且直接位于非根 `skills` 目录中的目标，避免写入错误位置。目标已存在时默认失败；需要更新时使用后文的 `--replace` 流程。脚本会验证 source 和目标路径、默认拒绝覆盖，并通过临时目录发布完整安装。

## 方法二：手动 clone 后复制到用户目录

下面的命令默认固定到当前 release tag。tag 尚未发布时，把 `ref` 改为实际存在的完整 commit SHA；只有确实希望跟随最新提交时才设为 `master`。

```bash
install_root="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills"
dest="$install_root/goal-workflow"
ref="v0.6.0"
tmp_dir="$(mktemp -d)"
source_dir="$tmp_dir/goal-workflow"

cleanup() {
  rm -rf -- "$tmp_dir"
}
trap cleanup EXIT

test ! -e "$dest" || {
  echo "目标已存在：$dest" >&2
  echo "请先检查、备份或按更新步骤处理。" >&2
  exit 1
}

git clone https://github.com/zuchengchen/goal-workflow-claude.git "$source_dir"
git -C "$source_dir" checkout --detach "$ref"
"$source_dir/scripts/install-local.sh" --dest "$dest"
```

固定到 commit 时必须使用完整 SHA：

```bash
ref="0123456789abcdef0123456789abcdef01234567"
```

在主命令块执行前，把示例 SHA 替换为准备安装的真实 40 位 commit SHA。若要保留一个正常 clone 作为后续更新源，请使用固定路径代替临时目录并省略清理命令。安装目录本身只包含 canonical skill 文件，不应包含仓库根 README、测试或 `.git`。

## 方法三：复制到项目仓库

项目级安装适合只在某个仓库中启用或由团队共同维护。先 clone 并检出所需版本：

```bash
git clone https://github.com/zuchengchen/goal-workflow-claude.git /path/to/goal-workflow-source
git -C /path/to/goal-workflow-source checkout --detach v0.6.0
```

然后用仓库自带的安全安装脚本把 canonical skill 复制到目标项目：

```bash
target_project="/path/to/target-project"
dest="$target_project/.claude/skills/goal-workflow"

/path/to/goal-workflow-source/scripts/install-local.sh --dest "$dest"
```

不要把整个 `goal-workflow` 仓库直接 clone 或作为 submodule 放到 `.claude/skills/goal-workflow`；新安装应只复制 `skills/goal-workflow/`，让安装目录仅包含运行所需内容。

## 验证安装

用户级安装：

```bash
dest="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/goal-workflow"
test -f "$dest/SKILL.md"
grep -q '^name: goal-workflow$' "$dest/SKILL.md"
```

项目级安装（从目标项目根执行）：

```bash
test -f .claude/skills/goal-workflow/SKILL.md
grep -q '^name: goal-workflow$' .claude/skills/goal-workflow/SKILL.md
```

随后启动一个新的 Claude Code 会话，输入 `/` 确认列表中存在 `goal-workflow`，或直接运行：

```text
/goal-workflow 把这个任务整理成可执行 goal
```

仓库中的 `tests/` 和 CI 会验证 canonical skill 的结构、行为不变量和场景契约 schema；模型级前向测试仍需由人工或 agent harness 执行。开发测试不需要复制到安装目录。

## 同名冲突

安装器发现目标目录已存在时会停止。不要直接覆盖或把两个版本合并到同一目录，否则可能留下已经删除的旧文件。

先确认现有目录来源：

```bash
dest="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/goal-workflow"
find "$dest" -maxdepth 2 -type f -print
git -C "$dest" remote -v 2>/dev/null || true
```

需要保留时先重命名备份：

```bash
dest="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/goal-workflow"
backup="${dest}.backup.$(date +%Y%m%d%H%M%S)"
mv "$dest" "$backup"
```

然后重新安装并验证。确认新版本工作正常后，再由你决定是否删除备份。项目级 `.claude/skills/goal-workflow` 采用同样策略。若用户级和项目级同时存在同名 skill，优先保留单一、明确的来源，避免不同版本随启动目录变化。

## 更新

保留 source checkout 时，优先使用 transactional replace。脚本会验证现有 skill 身份、把新版本安装到 staging、保留旧目录备份，并在发布失败时尝试恢复：

```bash
dest="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/goal-workflow"
/path/to/goal-workflow-source/scripts/install-local.sh --dest "$dest" --replace
```

脚本会打印保留的备份路径。验证通过后重启 Claude Code，再由你决定是否删除备份。项目级安装同理，只需把 `dest` 改为：

```bash
dest="/path/to/target-project/.claude/skills/goal-workflow"
```

### 更新 source clone

如果保留了独立的 source clone：

```bash
git -C /path/to/goal-workflow-source fetch --tags origin
git -C /path/to/goal-workflow-source checkout --detach v0.6.0
```

然后按复制步骤更新安装目录。跟随 moving ref 时可以改为：

```bash
git -C /path/to/goal-workflow-source checkout master
git -C /path/to/goal-workflow-source pull --ff-only origin master
```

### Goal 文件位置

新 goal 文件默认保存到当前工作目录，文件名为 `<YYYY-MM-DD>-<slug>.md`，例如 `2026-07-28-api-cleanup.md`。除用户在对话中显式指定其他目录外，skill 不会自行改放位置。是否纳入版本控制由项目决定：

- 个人工作用 goal：通常在 `.gitignore` 中忽略该文件名模式，例如 `/20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]-*.md`。
- 团队共享 goal：不要忽略，审阅后显式提交所需文件。

## 卸载

有 source checkout 时，先 dry-run，再使用经过路径和 skill 身份校验的卸载脚本：

```bash
/path/to/goal-workflow-source/scripts/uninstall-local.sh --dry-run
/path/to/goal-workflow-source/scripts/uninstall-local.sh
```

项目级安装传入明确目标：

```bash
dest="/path/to/target-project/.claude/skills/goal-workflow"
/path/to/goal-workflow-source/scripts/uninstall-local.sh --dest "$dest" --dry-run
/path/to/goal-workflow-source/scripts/uninstall-local.sh --dest "$dest"
```

没有 source checkout 时，用户级手工回退为（仅在你确认其中是本 skill 后执行）：

```bash
dest="${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/goal-workflow"
test -f "$dest/SKILL.md"
grep -q '^name: goal-workflow$' "$dest/SKILL.md"
rm -rf -- "$dest"
```

项目级安装（从目标项目根执行）：

```bash
dest=".claude/skills/goal-workflow"
test -f "$dest/SKILL.md"
grep -q '^name: goal-workflow$' "$dest/SKILL.md"
rm -rf -- "$dest"
```

卸载或更新后重启 Claude Code。本文不假定或宣称任何系统级 skills 目录；管理员部署应以所用 Claude Code 版本和组织配置的明确文档为准。

## 排错

### `/goal-workflow` 没有出现

确认安装目录顶层存在 `SKILL.md`，其 frontmatter 的 `name` 为 `goal-workflow`，然后启动新 Claude Code 会话。项目级安装要求从目标项目或其子目录启动 Claude Code。

### 更新后仍看到旧行为

检查是否同时存在用户级 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/goal-workflow` 和项目级 `.claude/skills/goal-workflow`。移除或备份重复来源，并启动新会话。

`SKILL.md` frontmatter 中的 `version` 字段标识安装副本对应的发布版本（0.5.0 之前的安装没有该字段）：

```bash
grep '^version:' "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/goal-workflow/SKILL.md"
```

### 保存 goal 文件时出现权限提示

goal 文件写入当前工作目录。在默认权限模式下，Claude Code 的写入工具本身需要确认，因此第一道审批通过、skill 实际写盘时会再出现一次工具权限确认——这是 Claude Code 的防护行为，不是 skill 故障，放行即可。若希望在某个项目中长期免提示，可在该项目 `.claude/settings.json` 的 `permissions.allow` 中加入形如 `Write(20[0-9][0-9]-[0-9][0-9]-[0-9][0-9]-*.md)` 的规则，具体语法以所用 Claude Code 版本的权限文档为准。无头或自动化环境需要显式选择合适的权限模式（本仓库的 eval harness 因此以 `--permission-mode bypassPermissions` 在一次性临时目录中运行，见 tests/README.md）。

### 是否需要安装其他 skill

不需要。`goal-workflow` 自包含目标质量标准和完整工作流，不读取或调用任何外部 skill。
