# Goal Workflow

`goal-workflow` 是一个自包含的 Claude Code skill：它通过逐步访谈把粗略任务整理成可执行、可验证的 goal，并在保存和开始执行前分别取得确认。

```text
/goal-workflow 重构这个项目的认证模块
```

它适合目标模糊、存在多种方案，或需要明确范围、风险、验证、发布与停止条件的任务。skill 自带目标质量检查和方案探索流程，不依赖任何外部 skill，也没有 npm、pip 等包管理器依赖。

## 安装

canonical moving-source 地址（分支随 `master` 更新）：

```text
https://github.com/zuchengchen/goal-workflow-claude/tree/master/skills/goal-workflow
```

最简单的方式是把 canonical skill 复制到用户级 skills 目录：

```bash
scripts/install-local.sh
```

安装器写入 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/goal-workflow`，遇到同名目录会停止，不会覆盖。手动安装、项目级复制、版本固定、验证、更新、卸载和同名冲突处理统一见 [INSTALL.md](INSTALL.md)。

## 工作流摘要

- 根据任务复杂度选择适当的访谈深度，一次只问一个问题。
- 在需要时检查项目上下文、比较 2-3 个方案并确认方向。
- 覆盖目标、范围、约束、兼容性、安全、测试、发布、回滚和停止条件。
- 将每项自动验证视为需要校准的判定器，保留生产命令退出码、使用当前运行证据，并防止文本扫描的假阳性和假阴性。
- 起草后先确认是否保存，保存后再确认是否开始执行。
- 默认将 goal 文件保存到项目根的 `.claude/goals/`；无法确定项目根时使用当前工作目录下的 `.claude/goals/`。
- 两次确认后，默认在当前会话按已保存的 goal 文件执行；也可交接——给出可粘贴到新 Claude Code 会话的启动语，稍后再执行。

是否提交 `.claude/goals/` 由项目决定：个人 goal 通常应加入 `.gitignore`，团队共享的 goal 可以显式纳入版本控制。

## 要求

- 支持 Agent Skills 的 Claude Code。
- 仅在 clone、更新或检出固定版本时需要 Git。

## 仓库布局

```text
goal-workflow/
├── skills/goal-workflow/   # canonical 可安装 skill
├── scripts/                # 安装、卸载、烟测、结构验证和行为 eval
├── tests/                  # 行为契约与 eval 报告
├── .github/workflows/      # CI 验证
├── INSTALL.md              # 完整安装说明
├── CHANGELOG.md
├── VERSION
└── LICENSE
```

新安装应始终使用 `skills/goal-workflow/`。

## 开发

- `scripts/validate.sh` 检查结构：skill 元数据、eval schema、文档一致性和仓库卫生。它不对 SKILL.md 的措辞做任何断言。
- `scripts/run-evals.sh` 检查行为：为 `tests/evals.json` 的每个 case 驱动一次真实 `claude -p` 会话，用确定性检查判定两道审批闸门，再由 judge 模型评估其余预期。会消耗 token，默认不进入 CI。详见 [tests/README.md](tests/README.md)。

发布历史见 [CHANGELOG.md](CHANGELOG.md)。
