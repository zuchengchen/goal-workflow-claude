# 让 goal-workflow 尽量并行、尽量使用 subagent

## 目标

在 `skills/goal-workflow/SKILL.md` 中把「独立工作默认并行、默认派发给 subagent」写成不变量，覆盖规划阶段（只读探查）和执行阶段（子任务与验证），并让 eval 契约与 harness 能真正判定它——同时不放松现有的两道审批闸门。

## 关键前提（已在本环境实测，决定了设计）

我用隔离 `CLAUDE_CONFIG_DIR` 起了真实 `claude -p` 会话验证过三件事：

1. 会话可用的委派工具叫 `Agent`（不是 `Task`）；写入工具在 stream-json 事件里仍报规范名 `Write`/`Edit`，所以 harness 现有 `{"Write","Edit","NotebookEdit"}` 判定没有失效。
2. **subagent 的工具调用会出现在父会话的 stream-json 流里**，带 `parent_tool_use_id` 和 `subagent_type` 字段。实测让父会话「自己绝不写文件、只派 subagent 写」，父流里照样出现了 `Write /tmp/.../sub2.md`。
3. 但 `run_evals.py:226-232` 只读 `block["name"]` 和 `block["input"]`，**丢掉了 `parent_tool_use_id`**。

第 2 条是好消息：subagent 的写入不会绕过两道闸门的确定性判定，闸门天然仍然有效。第 3 条是必须补的：记录了归属，才能在报告和 judge transcript 里分清「主会话自己做的」和「subagent 做的」，也才能判定「该并行的地方真的派了 subagent」。

## 改动清单

### 1. `skills/goal-workflow/SKILL.md`（发布物，版本升到 0.7.0）

新增一节 **Parallelism And Delegation**（放在 `Invariants` 之后、`State Machine` 之前），并在 `Invariants`、`Brainstorm`、`Discovery`、`Execute` 里各加一条钩子。核心规则：

- **默认必须**：彼此独立的探查、子任务和验证项默认并行派发给 subagent；只有存在真实依赖、需要共享改动同一文件、或涉及闸门决策时才串行，且要说明原因。
- **规划阶段（`discovering` 之前到 `saved`）的 subagent 严格只读**：可以读文件、搜索、跑只读命令，不得写入、改动或删除任何文件，也不得代表用户做出任何审批。闸门只能由主会话面对用户开启。这条直接封住「用 subagent 绕过闸门」的后门。
- **执行阶段（`active`）**：独立子任务与独立验证项并行派发；验证仍按 `Verification Integrity` 判定，subagent 报告的结果不算证据，除非命令真的跑过且退出码与产物可核。
- **一次问一个问题的不变量不受影响**：并行探查是工具层面的，不是把多个问题一次抛给用户。
- 语言不变量沿用：subagent 的派发说明属于用户可见叙述，仍用用户语言。

### 2. `tests/evals.json`（行为契约，schema_version 保持 3）

- `actions` 新增：`delegate_parallel_subagents`、`parallel_independent_verification`、`serialize_independent_work`、`subagent_writes_before_save_approval`、`subagent_opens_gate`。

  > 0.8.0 后记：该 action 已更名为 `subagent_writes_before_execution`（禁止窗口止于 start 审批而非 save 审批），且不再按 case opt-in，改为对所有 case 无条件执行。
- 新增一个 case `parallel-subagent-delegation`（category 复用 `exhaustive`，校验器允许一个 category 多个 case）：要求规划期并行只读探查、执行期并行子任务，禁止 subagent 写文件或代开闸门。
- 在 `narrow-fast-path` 与 `verification-integrity` 上各加一条与并行相关的 `required_behaviors`/`forbidden_actions`，让既有 case 也覆盖新规则。

`validate.py` 的「死词汇」检查要求每个新 action 都被引用，所以新增的 action 必须全部落到 case 里。

### 3. `scripts/run_evals.py`（harness）

- `run_claude_turn` 记录每个 tool_use 的 `parent_tool_use_id` / `subagent_type`，`Turn.tool_calls` 里带上 `agent` 归属字段；报告与 judge transcript 标注 `(subagent)`。
- 新增确定性检查 `subagent_writes_before_save_approval`：规划阶段任何带 `parent_tool_use_id` 的写入调用，只要目标不是 goal 文件及其暂存，一律判违规——即使闸门后来开了。这条比现有 gate 2 更严，因为它不给「subagent 替我先动手」留空间。

  > 0.8.0 后记：这条最终比计划更严——连写 goal 文件本身也算违规（保存流程不得委派），因此它与 gate 2 的真正差别只在「goal 产物豁免」，两者共用同一个 start 审批边界。
- 判定并行程度：统计同一轮内 `Agent` 调用数，供 judge 参考。

### 4. `tests/test_harness_checks.py`（模型无关，CI 会跑）

补 4 个合成 trace 用例：subagent 写 goal 文件的暂存在保存轮内是干净的；subagent 在保存审批前写项目文件要判违规；subagent 在 start 审批前写项目文件要判违规；父会话自己的写入判定不受新字段影响（回归）。

### 5. 文档

- `tests/README.md`：补「subagent 归属如何记录、规划期只读如何判定」，以及这条判定的边界（Bash 间接写入仍是盲区）。
- `README.md` 的「工作流摘要」补一条并行/subagent 行为。
- `CHANGELOG.md` 新增 `## [0.7.0]`，说明这是行为变更并记录实测依据。
- `VERSION` 与 SKILL.md frontmatter 同步为 `0.7.0`；`INSTALL.md` 里 4 处 `v0.6.0` 固定示例同步为 `v0.7.0`（`validate.py` 会强制这一致性）。

## 验证

1. `./scripts/validate.sh`
2. `python3 tests/test_harness_checks.py`
3. `./scripts/smoke-install.sh`
4. `git diff --check`
5. 前向测试（按你的选择只跑受影响 case）：
   `timeout 10800 ./scripts/run-evals.sh --model claude-sonnet-5 --simulator-model claude-haiku-4-5-20251001 --judge-model claude-sonnet-5 --jobs 3 --max-turns 40 --keep-workdirs --case parallel-subagent-delegation --case narrow-fast-path --case verification-integrity`
   这一步会花真实 token、耗时数十分钟。失败时先读 `tests/results/evals-report.json` 的 transcript 再改 skill。

## 明确不做

- 不改 `validate.py` 的遍历为多线程，不动 `--jobs` 默认值（你已选「只改 skill 行为」）。
- 不引入 `Workflow` 工具编排：它需要用户显式 opt-in，写进 skill 会让每次 `/goal-workflow` 都想 fan out 几十个 agent。
- 不放松两道闸门，不改 `AFFIRMATIVE_GATE_REPLIES` 词表。
