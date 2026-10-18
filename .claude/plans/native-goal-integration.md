# 接入 Claude Code 原生 `/goal`（0.9.0）

## 目标

2026-10-17 的项目审查发现：skill 使用 clear、mark complete、blocked、`token_budget` 等 goal 生命周期用语，却没有对应任何 Claude Code 机制，而当前 CLI 已经有原生的 `/goal`。用户选择接入原生 `/goal`：开始执行或交接时给出 `/goal <完成条件>`，让 Claude Code 在条件满足前持续工作。

## 关键前提（读 CLI 2.1.302 自带实现得到，未花 token）

1. `/goal <condition>` 注册一个会话级 `Stop` hook（`type: prompt`）：会话每次要停下时，由模型判定条件是否成立，不成立就继续工作；成立后 hook 自动清除。
2. `/goal clear`（stop、off、reset、none、cancel 同义）提前清除；条件最长 4000 字符。
3. 只有用户能输入这条命令。设置成功后 CLI 追加一条 meta 消息，要求 Claude 立即开始朝条件工作、不要停下来问用户该做什么。
4. 非交互（`-p`）版本可用：`isEnabled` 在非交互模式下为真，工作区信任检查在 `-p` 下跳过。设置了 `disableAllHooks`、`allowManagedHooksOnly`，或组织策略尚未读到时会拒绝。
5. 没有 token 预算：goal 达成时只报告用掉的 token 数。

同一轮审查中另外两项测量影响了相关修复：

- `skills/` 下任何含 `SKILL.md` 的目录都会被加载为 skill，`goal-workflow.backup.*` 和隐藏的 `.goal-workflow.backup.*` 都不例外，所以安装器的备份改放到 `skills/` 之外。
- init 事件把 subagent 派发工具列为 `Task`，而 0.7.0 计划实测为 `Agent`，所以 harness 两个名字都认。`tool_use` 块里实际是哪个名字未能验证：嵌套的 bypassPermissions 会话被 auto 模式分类器拦下。

## 设计决定

- 模板新增 `## Goal Condition`：一句声明式条件，写明绝对路径；成立条件是「所有必需验证项已通过并报告，**或**执行已在某个停止条件处停下并询问用户」。第二个分句是必需的，否则 Stop hook 会把执行推过停止条件，正是 skill 禁止的猜测。
- 第二道闸门：在当前会话执行的路径上，同时提供 `/goal <Goal Condition>` 作为另一种肯定答复。只有用户能输入它，而且它点名已保存的文件，所以它就是明确批准；CLI 随即要求立即开始，与闸门语义一致。启动复查把这个原生 goal 视为本 goal 自己的约束，否则「已有匹配 goal」规则会把刚保存的文件误当成多余的继任者。
- 交接：给出 `/goal <Goal Condition>`，附 `Goal Directive` 作为 `/goal` 不可用时的备用。
- 生命周期：完成与受阻只在对话中报告并附证据；原生 goal 生效期间，暂停或放弃需要用户输入 `/goal clear`。
- `token_budget`：继续记录并在开始时声明，同时说明没有东西强制执行它。

## 明确不做

- 不让 skill 自己执行 `/goal`：模型无法调用内置命令，只能请用户输入。
- 不去掉 `Goal Directive`：`/goal` 在受限环境里不可用，需要备用启动语。
- schema_version 不再单独升级：`/goal` 回复与 mode 评判同属尚未发布的 schema 5。

## 验证

1. `./scripts/validate.sh`、`python3 tests/test_harness_checks.py`、`./scripts/smoke-install.sh`、`git diff --check`。
2. 新用例 `native-goal-start`（类别 `native_goal`）：harness 把 start 闸门的脚本回复 `/goal` 组装成 `/goal <已保存文件里的 Goal Condition>` 发出，跑起来时也就验证了原生 `/goal` 在 `-p` 下能用。
3. 全量前向测试（17 例）：`timeout 10800 ./scripts/run-evals.sh --model claude-sonnet-5-5 --simulator-model claude-haiku-5-5 --judge-model claude-sonnet-5-5 --jobs 3 --max-turns 40 --keep-workdirs`。以往证据所用的 simulator `claude-haiku-4-5-20251001` 已不可用，所以用户选择全部换成当前模型，结果与 0.8.0 及更早的记录不能直接比较。

**第 2、3 步没有执行。** 本机经 API 中转站访问 Claude，中转站对所有无头 `claude -p` 调用都返回 403，与模型、配置、工具集都无关，每次都在 preflight 被拦下。维护者决定不做前向测试、直接发布 0.9.0，CHANGELOG 的 Verification 一节记录了这一例外和未验证的各项。能运行无头调用后，先执行第 3 步，再依赖这些改动。
