# Changelog

本项目的显著变更记录在此文件中，版本号遵循 Semantic Versioning。

## [Unreleased]

## [0.4.0]

### Added

- `scripts/run-evals.sh`：真正执行 `tests/evals.json` 的行为测试工具。每个 case 在隔离的临时工作目录和临时 `CLAUDE_CONFIG_DIR` 中驱动一次真实 `claude -p` 会话，由模拟用户按 setup 回答常规问题、在声明的 checkpoint 给出脚本化回复，最后由确定性检查和 judge 模型共同判定。消耗真实 token，默认不进入 CI。
- 两道审批闸门改由确定性检查判定：goal 文件是否在 save 审批之前落盘、用户拒绝后是否仍落盘、批准后是否确实落盘、start 审批前是否写了 `.claude/goals/` 之外的文件、预置 goal 文件是否被静默覆盖、是否委派给了其他 skill。这些都是可观测事实，不依赖模型判断。
- `tests/evals.json` 新增 `checkpoints` 词汇表（schema_version 2），描述每个 checkpoint 的到达条件，供 harness 路由回复使用。

### Changed

- SKILL.md 重排：新增开篇的 **The Two Gates** 段落，把两道审批闸门提到最显眼位置；Verification Integrity 由五段密集散文压缩为六条编号规则，语义不变。
- 校验器不再对 SKILL.md 正文做正则断言。此前 `validate.py` 用从 SKILL.md 逐字抄出的正则去 grep SKILL.md：保持语义的改写会失败，而删掉两道审批闸门却能通过。行为断言现由 `tests/evals.json` 和 harness 承担，校验器只检查结构。
- `tests/evals.json` 由「每个 category 恰好一个 case」改为「至少一个」，补充覆盖不再是 CI 失败。
- 校验器不再硬编码某次 LaTeX 事故的字符串（`latexmk -xelatex`、`!\left` 等）；该场景仍作为一个普通 case 保留。
- 安装 URL 检查不再硬编码 owner/repo，改为要求 README 与 INSTALL 指向同一个仓库，fork 与改名不再失败；同时校验 INSTALL 中的 tag 固定示例与当前 VERSION 一致。

### Removed

- SKILL.md 中作为通用规则示例的 XeLaTeX 轶事。
- `tests/evals.json` 中无任何 case 引用的 `execute_goal` action；校验器现在会主动拒绝这类无人引用的死词汇。

### Fixed

- `.gitignore` 补上 `__pycache__/`、`*.pyc` 和 `tests/results/`，并删除已产生的 `scripts/__pycache__`。

## [0.3.0]

### Added

- 面向 Claude Code 的自包含 `goal-workflow` skill：通过自适应头脑风暴和逐题访谈，把粗略任务整理成可执行、可验证的 goal，并在保存和开始执行前分别取得确认。
- 版本文件、变更记录和 MIT License。
- 自动化结构检查、场景化行为契约、隔离安装烟测和 CI 发布验证。
- 为安装、更新、卸载、同名冲突提供可执行说明。

### Behavior

- canonical 可安装 skill 位于 `skills/goal-workflow/`，仅包含运行所需的 `SKILL.md`，没有兼容镜像或额外元数据文件。
- 安装地址明确固定 ref 和 path；moving ref 为 `master`，path 为 `skills/goal-workflow`。可复现安装默认建议使用版本 tag 或完整 commit SHA。
- 用户级安装写入 `${CLAUDE_CONFIG_DIR:-$HOME/.claude}/skills/goal-workflow`；项目级安装写入 `<project>/.claude/skills/goal-workflow`。
- 通过两次审批后，默认在当前会话按已保存的 goal 文件直接执行；也支持交接——给出可粘贴到新 Claude Code 会话的启动语。
- 新 goal 文件默认保存到项目根 `.claude/goals/`；无法确定项目根时保存到当前工作目录下的 `.claude/goals/`。
- skill 完全自包含，不调用任何外部 skill。
- 用户显式请求的 `token_budget` 记录在已保存 goal 的 `Execution Options` 段，恢复或交接执行时可从中还原。

### Verification

- goal 中的自动验证必须定义可靠的判定语义：优先使用生产工具退出码或结构化报告，证明预期工作确实执行，并只接受可追溯到当前输入和目标的完整证据。
- 防止宽泛日志前缀或关键词匹配把换行续行、源码回显、`0 errors` 和允许的 warning 误判为失败；自定义匹配器必须用真实失败与良性碰撞样本校准。
- 防止管道、`tee`、裸 `! grep` / `! rg`、`|| true`、陈旧产物或缺失日志吞掉真实失败或制造空洞成功；不可判定的验证结果不再计为通过。
