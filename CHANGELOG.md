# Changelog

本项目的显著变更记录在此文件中，版本号遵循 Semantic Versioning。

## [Unreleased]

### Added

- README 与 INSTALL 新增「方法零：在 Claude Code 中一句话安装」——把 `安装 skill https://github.com/zuchengchen/goal-workflow-claude.git` 发给 Claude Code，由它按 INSTALL.md 声明的步骤 clone 并执行 `scripts/install-local.sh` 完成安装。

## [0.5.0]

### Added

- SKILL.md frontmatter 增加 `version` 字段，安装副本可追溯到发布版本。校验器要求 canonical bundle 的该字段与 VERSION 一致；已安装 bundle 中该字段可选，0.5.0 之前的安装仍能通过 `--replace` 与卸载流程的身份校验。
- 新增 `matching_goal` eval case：已有同内容 active goal 时必须给出 continue / revised successor / cancel 编号选项，不得创建重复 goal，也不得声称新目标的状态或审批已经走过。`setup.active_goal: "matching"` 由无人引用的死词汇变为被覆盖的分支。
- eval harness 增加 preflight：正式跑 case 之前先在隔离 `CLAUDE_CONFIG_DIR` 里做一次最小模型调用。认证或环境问题现在一次性报清并以退出码 2 结束，不再产出一整份全是相同 harness error 的报告（0.4.0 留下的最后一份报告即因未登录而 11 个 case 全灭）。
- eval 报告新增 `meta`（skill 版本、被测/模拟/评审模型、起止时间），作为前向测试证据留痕。
- INSTALL.md 排错新增「保存 goal 文件时出现权限提示」与安装副本版本识别方法。

### Changed

- conflicting 与 matching 场景现在种入内容不同的 active goal：conflicting 种「存储层冻结」目标，与新请求真实冲突。此前两者种同一个「替换存储层」文件，conflicting case 的盘上证据实际是 matching，正确读取文件证据的 skill 反而可能被判偏航。
- `tests/evals.json` 升级到 schema_version 3：save/start 两道闸门的脚本化回复限定为 y/yes/n/no（大小写不敏感），与 harness 确定性判定共用同一词表，由校验器和 harness 双侧强制；coverage 与 direction checkpoint 的回复由裸 "y"/"n" 改为无歧义的自然语言，避免对「继续调查还是起草」这类二选一问题的误读。
- japanese case 的 required_behaviors 仅保留单轮对话内可观察的行为；此前的「Localize approval prompts」在零 checkpoint 的对话里无法展示，judge 按「未展示即未发生」会误判。token_budget case 的「Apply」改为可从 transcript 判定的表述；ambiguous case 的 prompt 改为 fixture 中真实存在的鉴权模块，此前指向 fixture 里不存在的「目标工作流」。
- judge transcript 中 tool 输入截断从 200 字符放宽到 1500，长验证命令与写入的 goal 内容不再因截断而被 judge 当作未发生。
- harness 生成的 claude 子进程剥离外层会话环境变量（`CLAUDECODE`、`CLAUDE_CODE_*`、`CLAUDE_EFFORT`），嵌套会话身份与 effort 不再泄漏进被测会话与评审模型。

### Changed (评测驱动的收紧)

- 首轮 0.5.0 全量评测（6 过 6 挂，0 harness error）暴露出真实偏航，SKILL.md 相应收紧：两道闸门规则明确「写入后删除仍算写入、草稿只以消息文本展示」；追加调查后必须重新展示覆盖摘要并再次征求同意；用户显式指定的文件名发生碰撞时，改名或覆盖必须作为独立问题先问；第二道闸门在用户已声明交接偏好时改问「是否现在准备交接」，不得把执行/交接当作新选择重新抛出；带 `token_budget` 的 goal 开始执行时必须声明按该预算执行；校准或验证未实际运行前不得表述为已完成。
- eval 契约随之校准：japanese case 首问允许是 discovery 或 direction 问题（语言才是被测点）；collision case 删去在其终态下无法展示的「写前复查」行为；`recheck_path -> write_goal_file` 的排序理由明确「先暂存临时文件、后复查再发布目标文件」不算违序；`start_approval` checkpoint 描述覆盖交接偏好下的二元问法。
- 第三轮评测（11 过 1 挂，0 harness error）后的收紧：语言不变量由列举式（问题、选项、摘要、审批、交接）扩为一切用户可见输出，包括工具调用之间的过程叙述与状态说明——此前中文会话中夹带英文过程叙述不在禁止范围内。
- 次轮评测（9 过 2 挂 1 瞬态网络错误）后的第二次收紧：访谈不变量禁止用 "and" 串联可独立回答的问题；碰撞专问改为「发现碰撞时立即提出」。collision case 重设计——碰撞决策成为脚本化 checkpoint（新增 `collision_decision`，模拟用户不再对该决定自由发挥），对话经覆盖摘要走到 save 闸门收尾，终态由 `path_collision_prompt` 改为 `awaiting_save_approval`。judge 对 `goal_started` 的语义明确为「至少已开始」：执行在会话内继续推进乃至完成仍然匹配，其余终态仍须严格吻合。harness 的瞬态错误识别补充 `connection closed` / `mid-response`。

### Fixed

- gate-1 确定性检查同时审查写入 `.claude/goals/` 的工具调用：轮内「写入后又删除」此前只靠轮末磁盘快照无法察觉（首轮评测实录），现在会被判定为审批前写入。
- gate-2 确定性检查读取 NotebookEdit 的 `notebook_path`；此前统一取 `file_path`，对 NotebookEdit 恒为空串，start 审批前的笔记本写入永远不会被判定。
- 文本卫生检查改按原始字节检测回车行尾；此前文本模式读取的换行翻译吞掉 `\r`，CRLF 文件本地放行而 CI 的 `git diff --check` 拒绝，「本地检查是 CI 超集」的注释不成立。
- 为已发布的 0.4.0 补打 `v0.4.0` tag。此前 INSTALL.md 推荐固定安装的 tag 在远端并不存在，按文档执行的可复现安装直接失败。

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
