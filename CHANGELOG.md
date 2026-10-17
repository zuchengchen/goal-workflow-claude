# Changelog

本项目的显著变更记录在此文件中，版本号遵循 Semantic Versioning。

## [Unreleased]

### Fixed（安装与更新）

- `--replace` 的备份不再放在 `skills/` 里。Claude Code 会把 `skills/` 下任何含 `SKILL.md` 的目录都加载为 skill（CLI 2.1.302 实测：`goal-workflow.backup.*` 和隐藏的 `.goal-workflow.backup.*` 都会出现在 skill 列表与 `/` 命令里），所以按文档更新后，最多 3 份旧版本与新版本同时生效——这正是排错一节「更新后仍看到旧行为」的成因。备份改放在 `skills/` 旁的 `goal-workflow-backups/`；安装与卸载脚本会把旧版本留在 `skills/` 内的备份移过去（只移动、不删除），`--purge-backups` 一并清除。备份位置若是符号链接或非目录则拒绝执行，避免备份经由符号链接落回 `skills/`。INSTALL.md 的手动备份示例同步改为移出 `skills/`。

### Fixed（校验器）

- 本地校验不再因被 git 忽略的文件失败。卫生检查与「仓库根不得有 goal 文件」检查改为只看 git 会纳入提交的文件（已跟踪的文件加未被忽略的未跟踪文件），正好是 CI 的 `git diff --check` 能看到的范围；没有 git 时退回目录遍历。此前在本仓库用一次 skill，它按设计写到仓库根、并已被 `.gitignore` 忽略的 goal 文件就会让 `validate.sh` 失败。
- frontmatter 的无引号值改按 YAML 规则检查：含 `: `（YAML 会解析成映射而报错）或 ` #`（被当作注释静默截断）的值，以及以 YAML 指示符开头的值，现在都会被拒绝并提示加引号。此前 `description: Use when: …` 能通过校验，YAML 解析器却会拒绝它。
- `.gitignore` 补上 `.claude/worktrees/`：此前 `git add -A` 会把临时 worktree 当作嵌入仓库加进来。

### Fixed（eval harness）

- Ctrl-C 不再被吞掉。`collect_result` 只转换 worker 自身的异常，主线程等待时收到的中断照常传播；此前中断被记成正在等待的那个 case「worker died」（它其实已经通过），运行照旧继续。SIGTERM（文档推荐的 `timeout` 包装发的就是它）与 SIGHUP 也按中断处理：取消未开始的 case，按进程组杀掉运行中的 `claude`，删除各沙箱连同凭据副本，写出带 `meta.interrupted` 的报告，退出码为 128 加信号值。此前 SIGTERM 直接杀死解释器：不写报告，运行中的沙箱都留着 `.credentials.json` 副本。
- 报告在每个 case 结束后原子重写，并且每次运行写入各自的 `evals-report-<UTC 时间戳>.json`（`evals-report.json` 镜像最近一次）；`meta` 新增 CLI 版本、过滤条件和所请求的 case 列表。此前每次 `--case` 迭代都覆盖唯一的报告，本地那份「durable forward-test evidence」只剩 1 个 case。未知的 `--case` id 改为报错。
- judge/simulator 的 CLI 错误结果不再被当作模型回复。此前 `ask_json` 不看 `is_error`，而上游过载的错误体本身就是 JSON 对象，被原样当作回复返回：不会重试，发生在 judge 阶段时还会在整段会话之后报成「verdict 不完整」。现在按传输层错误分类（含 `api_error_status` 与 529）并重试。
- 已经说过话或调用过工具的回合，在 CLI 失败或超时被杀后不再重试：重试会把同一条用户回复（例如 save 闸门的 `y`）再发一次，失败那次的工具调用也会从证据里消失。现在记录这个不完整的回合并交给确定性检查，有违规则判为 skill 失败，否则为 harness error。
- preflight 检查全部三个模型（simulator、judge、被测会话）。此前只查 simulator，`--judge-model` 写错要等每个 case 的完整会话跑完才暴露。
- 认证与 provider 变量（`CLAUDE_CODE_OAUTH_TOKEN`、`CLAUDE_CODE_USE_BEDROCK/VERTEX/FOUNDRY` 等）不再随外层会话变量一起被剥离，用这些方式认证的环境不再在 preflight 报「Not logged in」。
- judge 与 simulator 以 `--tools ""` 运行，没有任何工具；此前的黑名单仍漏掉 `Workflow`、`SendMessage`、`CronCreate`、`EnterWorktree` 等。
- subagent 派发工具同时识别 `Agent` 与 `Task`（CLI 2.1.302 的 init 事件报告为 `Task`），judge 提示同步说明。
- 最后一条脚本回复恰好用掉最后一个允许回合时，不再被误报为「对话未在回合上限内结束」。
- tests/README.md 不再声称 `bypassPermissions` 下的影响范围就是临时目录：它不是沙箱。

## [0.8.0]

### Fixed（发布与安装）

- 补打并推送缺失的 `v0.7.0` tag（指向 `bd9ecbe`）。0.7.0 发布时漏了 tag，而 INSTALL.md 六处让用户 pin `v0.7.0`，按文档执行的固定版本安装必然失败——与 0.5.0 为 v0.4.0 补 tag 是同一类事故。校验器新增 tag 存在性检查以防再犯：仅在「git 可用、仓库确实带 tag、pin 已随提交落地、且工作树干净」时报错，因此开发期的未提交版本号提升不会被拦，CI 的浅克隆（`actions/checkout` 默认不取 tag）也不会误报。
- **破坏性缺陷**：`scripts/uninstall-local.sh` 会删掉本仓库自己的 canonical 源码 bundle。`--dest <repo>/skills/goal-workflow` 能通过全部守卫（basename 是 `goal-workflow`、父目录叫 `skills`、身份校验通过——因为它就是真的 skill），实测删除了 `skills/goal-workflow/SKILL.md` 并返回 0。现在要求卸载目标位于 Claude 配置目录内，否则拒绝并提示真实安装路径。
- **破坏性缺陷**：`--dest ""` 静默改删默认的用户级安装并返回 0。空串被接受后又被 `-z` 回退覆盖成默认目标，使那条安全守卫成为死代码；而 INSTALL.md 文档里正是 `dest="..."` + `--dest "$dest"` 这个形状，变量未设值即误删。显式传入的空 `--dest` 现在是硬错误（退出码 2），与「未传 `--dest`」区分开。`install-local.sh` 同一处漏洞一并修复。
- `scripts/smoke-install.sh` 此前删掉六道安全守卫仍然全绿，而它是 CI 对安装器唯一的行为防线。补齐负面断言，使每道守卫都可被证伪。
- 卸载不再默默留下 `goal-workflow.backup.*`——那是完整可用的旧 skill 副本，会让「卸载以清除旧行为」失效。默认报告其路径，`--purge-backups` 才删除。
- 修复备份路径遇悬空符号链接时 `[[ -e ]]` 判断为假、导致安装以未捕获的 Python 异常永久失败的问题。
- INSTALL.md 与 README 补记 Python 3 是安装/卸载脚本的硬依赖（此前文档声称只需 Claude Code 与可选 Git，无 Python 的机器上「一句话安装」会以退出码 127 不明失败）。

### Changed (评测驱动的收紧)

- 密钥不变量此前读起来像「只管真实密钥」，实测中会话把 `.env` 里一个看起来无害的开发值原样抄进草稿与已保存的 goal 文件。现在明确：无论该值看起来多么无害——占位值、明显的假值、已过期的值、以及已在被检查文件里可见的值——都只按名称与获取方式引用。判断某个值「可以安全引用」不是会话的权限，且 goal 文件的存在时间长于写下它的会话。Draft 质量标准在泄漏实际发生的位置（Context 段）重复这条。
- gate-2 曾拒绝一次正确的保存：把带日期的文件名锚定在 basename 开头，导致 `.goalstage_<目标名>.md.tmp` 不被识别为暂存，教科书式的「暂存-回读-改名」被判成审批前改动项目文件。现在允许暂存装饰出现在目标文件名之前，前提是它自己表明是暂存产物，`x2026-07-26-abc.md.bak` 等无关文件仍被拦住。
- judge 覆盖检查连续丢弃了两份完整 verdict：一次是回显声明行为时少了一个反引号，一次是在被引用命令中插入了文件名，两次都被判为「未评分」，并把同一份 verdict 里已正确评分的条目一起作废。逐字与前缀匹配都挡不住句中漂移，因此长标签改按字符相似度识别，短标签仍要求完全一致；把声明外的行为偷换进来仍然是 harness error。
- eval fixture 的 `.env` 不再把值标注为假值。告诉被测会话「这不是真实凭据」等于把「该值可否引用」这个判断交还给它，而那正是本用例要测的禁区——fixture 反过来替 skill 辩护了。

### Changed（行为变更）

- **subagent 只读窗口收紧到 `active`**：此前只读状态列举为 `discovering`/`drafted`/`draft_approved`/`saved`，遗漏了 `start_approved`，而同一文件另一处又写「`active` 之后才可写」——两条规则在一个真实存在的窗口里给出相反答案（启动失败或冲突未解时工作流就停在 `start_approved`）。现在明确 `start_approved` 期间 subagent 仍然只读，写权限跟随 `active`。
- **「只读」有了可判定的定义**：不改动工作树（含 `__pycache__` 等附带产物）、不改版本控制状态、不装依赖；工作树之外的临时目录允许写。因此需要产出物才能校准的验证命令，在规划期只被「写明」，在 `active` 之后才「执行」。此前「可运行不改变项目的命令」与「不得创建、修改、删除、移动任何文件」在 `pytest`、`npm install`、重定向到临时文件等场景互相矛盾，且与 `verification-integrity` 用例直接冲突。
- **交接语统一为一句**：`Goal Directive` 成为唯一 canonical 启动句，交接路径原样复用，不再另造第二句措辞不同的「exact」文本。绝对路径永不本地化。
- 覆盖确认在 The Two Gates 中明确为独立于两道闸门的第三个决策，始终绑定唯一绝对路径、始终单独提问。

### Fixed（skill 规则的自相矛盾）

- 保存失败后的状态不再二义：内容与路径未变时留在 `draft_approved`（可重试而无需重新审批），任一变更则回到 `drafted`——因此以改名解决碰撞回到 `drafted`，以覆盖确认解决则留在 `draft_approved`。
- Resume 规则不再与碰撞规则死锁：重走保存闸门时先回读，内容逐字节一致即视为已保存，不再为自己刚写的文件索取覆盖确认。
- 明确工作目录里的旧 goal 文件不等于「正在进行的 goal」。0.6.0 把保存位置改到当前工作目录后，该目录会堆积历史 goal 文件，而此前没有任何规则说明如何对待「本会话并未在跟随」的那些文件。
- 启动前复查发现已有匹配 goal 时，刚保存的文件会被孤立：现在要求说明情况、给出两个绝对路径并询问保留或删除，绝不擅自删除刚写下的文件。
- 委派限制中「不得委派保存流程」的步骤枚举补上碰撞复查与创建父目录，与 Save 段实际步骤一致。

### Fixed（eval harness 可信度）

- harness 不再凭空造出 gate-1 失败。`save_affirmative` 取自脚本化回复而非「闸门是否真的到达」，因此对话中途停止时会报「用户批准保存但从未写入 goal 文件」，并同时废掉那条「无可观测违规则报 harness error」的安全阀——模拟用户提早停被报成看起来很真实的 skill 失败。
- judge 判定从「按返回内容评分」改为「按契约覆盖评分」：verdict 必须覆盖 case 声明的每一项，缺项、漏评、或凭空多出条目都算 harness error。此前空 verdict、只评 1/7 条、或杜撰不存在的 forbidden action 都会得到绿色结果。
- 写入 goal 文件的载荷不再截断（此前 1500 字符上限会切掉 6289 字符写入的后半段，而 judge 被要求「transcript 里没有就是没发生」）；其他工具载荷上限放宽到 8000 并在截断处显式标注，使 judge 能区分「不存在」与「被省略」。
- 单个 worker 抛异常不再丢掉整轮报告（`mkdtemp` 移入受保护区间，逃逸异常转为该 case 的 harness error）。一轮全量评测是几十分钟到约两小时的真实 token。
- 超时改为杀整个进程组（`start_new_session=True` + SIGTERM/宽限/SIGKILL），不再留下继续消耗 token 的孤儿 `claude` 子进程；超时也不再被误判为可重试的上游抖动而立刻再起一个。
- goal 文件识别改为大小写不敏感，并补上 `MultiEdit`：此前大写文件名对两道闸门的证据路径均不可见，`MultiEdit` 写入则完全绕过判定。
- subagent 写入检查改为对所有 case 无条件执行，并更名 `subagent_writes_before_execution`（禁止窗口止于 start 审批，而非 save 审批）。此前 13 个 case 中仅 3 个 opt-in，把它改成无条件后整个 suite 仍全绿，说明这条本身未被测到。
- 达到 `--max-turns` 时先算确定性证据：既死循环又在审批前写了 goal 文件，现在报 skill 失败而非 harness error。
- 覆盖确认成为可表达的场景：预置 goal 文件发生变化，仅在 case 声明 `overwrite_consent` 且脚本用户在 `overwrite_approval` 确认时才不算违规。此前该检查无条件失败，skill 的第三个确认决策根本无法测试。
- 收紧 gate-2 的 goal 产物豁免（此前 `tmp-goals.py`、`src/goal_tmp_helper.py`、`x2026-07-26-abc.md.bak` 等都被豁免）；`list_goal_files` 改按相对路径排除，子目录中的真实 goal 文件不再隐形；`TRANSIENT_RE` 不再匹配模型自己写的文本（`invalid JSON in model reply: ...500 records...` 曾被当作上游故障重试）。
- 预置 goal 文件的排除改为内容敏感：仅在内容仍与播种时一致时排除，一旦被改写就算作「工作流写下的 goal 文件」。否则「批准覆盖播种路径」这一正确行为会被判成「用户批准保存但从未写入 goal 文件」——新增的 `explicit-overwrite-consent` 用例正好踩在这条上。是否允许该次写入仍由 `overwritten_fixtures` 与覆盖确认决定。
- eval fixture 新增 `.env`（自描述的假值，不含任何形似真实凭据的内容），使「密钥只按名引用」这条不变量有可测对象。
- `--keep-workdirs` 保留的临时目录不再残留复制进去的 `.credentials.json`。

### Fixed（校验器）

- 文本卫生检查此前按绝对路径 `path.parts` 匹配跳过表，导致整个 checkout 只要有 `.claude` 祖先目录就全部豁免——正好命中本项目自己使用的 `.claude/worktrees/<name>/` 布局。改按相对 root 的路径比较。
- frontmatter 校验改为允许合法的 Claude Code skill 字段白名单（此前 `allowed-tools` 会被拒，报错信息却在说 `version`）；代码围栏检查改为正确跟踪围栏字符与长度（此前未闭合的 `~~~` 放行，而合法的四反引号嵌套三反引号被误判未闭合）。
- `--root` 指向不存在的目录改为清晰报错，不再抛未捕获的 `FileNotFoundError`;安装 URL 的「同一仓库」检查不再因 README 里出现任何第三方 GitHub 链接而失败；CI 内容检查补上 `tests/test_harness_checks.py`，避免 CI 悄悄停止测试 harness。

### Added（eval 契约）

- `tests/evals.json` 升级到 schema_version 4，用例从 13 增至 16：新增 `explicit-overwrite-consent`（覆盖确认全流程）、`secret-reference-only`（密钥只按名引用，不得抄进 goal 文件）、`localized-handoff-message`（ja-JP 交接语本地化）。新增 checkpoint `overwrite_approval`、`conflict_decision`、`matching_goal_decision`，三者的脚本化回复与另两道闸门共用 y/yes/n/no 词表。
- 补上此前完全没有断言的要求：暂存文件名必须由目标文件名派生且后缀为本次新生成、审批后的碰撞复查必须区别于早先的路径解析检查、Draft 模板的必需段落、非 ASCII 标题的音译与 60 字符 slug 上限、保存过程叙述的语言一致性。
- `conflicting-active-goal`、`matching-active-goal`、`japanese-user-language` 此前声明零 checkpoint 回复，其终态与 forbidden action 都是 driver 提前结束对话的产物（第 2 轮就抢跑的 skill 根本等不到第 2 轮）。现在各自给出足够的脚本化回复，使违规有真实发生的机会。
- `goal-file-collision` 此前止步于 `coverage_summary`，其标题声称保护的写入闸门根本到不了，`write_goal_file` 与 `overwrite_goal_file` 因此不可证伪；现在拒绝路径会真正走到以无碰撞名写入。
- 并行派发与 `token_budget` 禁止推断的断言扩展到穷尽档用例（此前仅覆盖最不可能触发的两个 case）；`verification-integrity` 的断言改为「goal 中写明该防护」，与「校准在 `active` 之后才执行」一致。
- `tests/README.md` 补记三条此前未声明的结构性限制：harness 只记录工具调用而非结果、driver 在脚本回复用尽时结束对话（因此部分终态是脚本长度的产物）、fixture 仅在会话开始前播种（保存失败与回读不一致路径无法诱发）。

## [0.7.0]

### Added

- SKILL.md 新增 **Parallelism And Delegation** 一节：彼此独立的工作默认并行、默认派发给 subagent，覆盖规划期的上下文探查与执行期的独立子任务和验证项。串行需要给出理由（真实数据依赖、共享改动同一文件、属于主会话的闸门与提问、或小到不值得派发开销）。
- 同一节写明**授权边界**，使并行不成为绕过两道闸门的通道：goal 变为 `active` 之前派出的 subagent 严格只读，不得创建、修改、删除、移动任何文件；subagent 永远不展示草稿、不索取审批、不解释回复为审批、不写 goal 文件；保存流程（暂存-回读-改名-回读）不得委派；subagent 声称通过不构成证据，仍按 `Verification Integrity` 核对退出码与产物；并行属于工具层面，不改变「一次只问一个问题」。
- `tests/evals.json` 新增 case `parallel-subagent-delegation`（category 复用 `exhaustive`）：四个互不依赖的模块必须在同一轮并发派发只读 subagent 审计，审批后才并行执行与验证。新增 action 词汇 `delegate_parallel_subagents`、`parallel_independent_verification`、`serialize_independent_work`、`subagent_writes_before_save_approval`、`subagent_opens_gate`。
- eval harness 新增确定性检查 `subagent_writes_before_save_approval`：规划期任何带 `parent_tool_use_id` 的写入调用一律判违规，包括写 goal 文件本身、也包括保存审批之后（保存流程不得委派）。这条比 gate 2 更严——gate 2 豁免 goal 产物，也豁免 start 审批之后的所有轮次。

### Changed

- **行为变更**：skill 现在默认并行并派发 subagent，此前是隐式串行。两道闸门语义未变。
- eval harness 记录每个工具调用的 subagent 归属（stream-json 的 `parent_tool_use_id` / `subagent_type`），报告、进度日志和 judge transcript 都区分 `[main session]` 与 `[inside subagent <id>]`。实测确认 subagent 的工具调用会出现在父会话流里，因此两道闸门的确定性判定对委派写入天然仍然有效；此前 harness 丢弃了归属字段，无法分辨是谁写的。
- harness 不再把 subagent 的最终文本计入 `assistant_text`：那是对父会话的汇报，不是用户可见消息，否则语言不变量与「一次一问」会被误判。
- `Purpose` 段澄清「不委派外部 skill」限制的是 *skill* 而非 subagent，`active` 之前不实现任务的规则明确同样约束 subagent——可做的事才可派发。
- `invoke_external_skill` 确定性检查同时覆盖 subagent 发起的 `Skill` 调用。

### Verification (0.8.0)

- 结构检查全绿：`./scripts/validate.sh`、`python3 tests/test_harness_checks.py`（18 → 61 项）、`./scripts/smoke-install.sh`、`git diff --check`。安装器与卸载器的 21 道安全守卫全部经变异测试证明可被证伪（逐一删除后 smoke 必失败）。
- 前向测试（`--model claude-sonnet-5`，judge 同款，simulator `claude-haiku-4-5-20251001`）：`secret-reference-only`、`verification-integrity`、`goal-file-collision`、`explicit-overwrite-consent`、`localized-handoff-message` 全过，0 harness error。其中三项是先失败、按实测证据修正后复跑通过；另外独立于 judge 核对过：没有任何一次写入调用包含字面密钥值。
- 两处破坏性安装器缺陷各自复现过一次再验证修复：`--dest <repo>/skills/goal-workflow` 此前删除 canonical bundle 并返回 0，`--dest ""` 此前误删用户级安装并返回 0。

### Verification

- 前向测试：`narrow-fast-path`、`verification-integrity`、`parallel-subagent-delegation` 三例全过，0 harness error（`--model claude-sonnet-5`，judge 同款，simulator `claude-haiku-4-5-20251001`）。`parallel-subagent-delegation` 在措辞修正后单独复跑仍过。两次记录一致：规划期同一轮并发派出 4 个 subagent 且零写入；保存流程的暂存与回读全部由主会话完成；`auth.py` / `storage.py` / `cli.py` / `api.py` 及其测试的写入全部发生在 start 审批之后、由 subagent 并行完成，且每个文件只由一个 subagent 改动。

## [0.6.0]

### Changed

- **不兼容变更**：goal 文件的默认保存位置由项目根 `.claude/goals/` 改为当前工作目录，文件名仍为 `<YYYY-MM-DD>-<slug>.md`。skill 不再自行推断项目根，也不再把文件放进子目录或隐藏目录；只有用户显式指定其他目录时才改变位置。此前安装的版本写在 `.claude/goals/` 下的既有 goal 文件不会被自动迁移，仍可按绝对路径继续执行。
- eval harness 的两道闸门判定改按文件名识别 goal 文件（`<YYYY-MM-DD>-<slug>.md` 出现在 basename 中，同时覆盖保存流程暂存的临时文件），不再依赖 `.claude/goals/` 目录前缀；盘上检测补充「含 `## Goal Directive` 的 `.md`」这一回退，命名错误但真实存在的 goal 文件不会被读成「从未写入」。判定同时接受不含日期的暂存名（basename 兼有 `tmp` 与 `goal`，如实测出现的 `.goal-tmp-20260728.md`），否则规范的暂存-回读-改名会被 gate 2 误判为审批前改动项目文件。Bash 间接写入仍是该判定的既有盲区，交给 judge 的 `start_before_second_approval`。
- 校验器对 `.gitignore` 的要求由「必须忽略 `.claude/goals/`」改为「必须有能匹配到带日期 goal 文件名的忽略规则」，不再固定具体 glob。
- eval fixture 不再预建 `.claude/goals/` 目录，预置 active/collision goal 直接种在工作目录根。

### Changed (评测驱动的收紧)

- 保存步骤的碰撞复查明确「审批之后、写入之前」：为解析路径而在更早时点做过的存在性检查不算复查（实测中会话在展示草稿前查了一次，审批后直接写入，并用可覆盖的 `mv` 发布目标文件）。改名同时要求使用不会覆盖既有目标文件的形式。
- 暂存文件名的规定改为「目标文件名 + 本次保存新生成的短后缀」，不再给出可照抄的具体后缀：实测中会话把示例里的 `.tmp-a1b2` 原样用作文件名，并发保存会互相踩踏。goal 文件现在与用户自己的文件同处一个目录，暂存名必须仍能看出派生自目标文件。
- Save 步骤补充语言约束：goal 文件正文可用用户批准的任何语言，但关于保存过程本身的叙述（回读是否一致、即将改名等）必须使用用户语言。中文会话在该处夹带英文过程叙述，在 0.5.0 与本轮评测中重复出现。

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
