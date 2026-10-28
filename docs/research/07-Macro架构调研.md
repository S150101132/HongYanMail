# 07 · Macro 架构调研与对鸿雁的借鉴

日期：2026-10-28。对象：开源项目 [macro-inc/macro](https://github.com/macro-inc/macro)，`main` 分支提交 `4a79d95`（2026-10-28），约 3.8 万个文件。
目的：只做调研，弄清 Macro 怎样把邮件、任务、项目、文档、人和组织连成一张图，怎样生成和使用统一工作记忆，怎样让 Agent 读写这些对象；再对照鸿雁现有能力，给出可借鉴的设计、可参考的代码和分阶段路线。**本文不改任何代码，路线图里的项目进不进「开发顺序」表由用户决定。**

> **许可证提醒：Macro 是 AGPL-3.0。** 鸿雁可以读它的代码学设计，但不能把代码（包括改写成 Python 的逐行翻译）复制进鸿雁，否则整个鸿雁都要按 AGPL 开源。下文「值得参考的代码」只用于理解思路，实现一律自己写。

---

## 0. 结论（一页）

1. **Macro 的「双向图」其实很朴素**：一张有向边表 `comms_entity_mentions`（来源类型/ID → 目标类型/ID），正向、反向各建一条索引，保存正文时「先删这个来源的全部边，再按正文重新抽取写入」。所谓双向，是**反向索引 + 反向查询**，不是存两条边。另有两类关系：带类型的属性值（任务的「项目」属性 = 指向项目的 `EntityReference`），和少数专表（任务↔PR、Agent 会话↔任务）。
2. **Macro 的统一记忆反而比鸿雁粗糙**：每个用户一段 1000–3000 字的 Markdown，由 Agent 每天用用户自己的工具重新研究一遍、以旧记忆为底稿重写，再让另一个模型当「评审」否决空洞或带自言自语的稿子；**没有逐条来源、没有历史、没有用户确认**。鸿雁的项目事实（每条带来源邮件、被替代留痕、AI 改动先待确认）和「关于我」在可追溯性上已经领先，不要倒退去学它。
3. **邮件转任务**在 Macro 里就是：从邮件点「Create task」，打开任务编辑器，正文预填一个指向这封邮件会话的 @提及。任务正文保存时抽取提及，生成「任务 → 邮件」的边；邮件侧通过反向查询看到它被哪个任务引用。之后「任务 → Agent 会话（`agent_session.task_id`）→ PR（`agent_session_pull_request`、`github_pr_tasks`）→ 代码变更摘要（`agent_session_changes`）」都是专表外键，整条链可审计。
4. **Signal / Noise 是规则，不是 AI**：线程上冗余一个 `is_signal` 布尔，按 Gmail 分类标签（推广、更新等降级标签）加用户的「发件人 / 域名」覆盖规则（地址规则优先于域名规则）算出。首页 Signal 是跨类型的统一查询（「Soup」）加一条成员资格策略。鸿雁的理解层（要我做 / 要我知道 / 可以忽略）已经比它细。
5. **Agent 权限有三层，值得学**：① 每个工具编译期必须声明「只读 / 新增 / 覆盖或删除」等注解；② 实体级访问检查返回一个「凭据」类型，拿不到凭据就调不了下游；③ 别人驱动、花的是主人权限的调用，在出口处挂起，写一行待审批记录，只有主人能批，可选「这一会话里不再问」。
6. **给鸿雁的主要差距**不在 AI，而在**「对象」和「关系」的通用层**：鸿雁只有「任务 ↔ 邮件」一种关联、项目只是名单里的名字、人只是邮件地址、没有统一的活动记录和反向引用。建议 P0 做一张本地加密的通用引用表和统一的「谁引用了它」面板，把已有的任务关联、事实来源、`[tN]` 引用都接上去；P1 再补项目 / 组织实体、工具注解与审批分级、任务查重；P2 才考虑 Agent 执行留痕和多窗格。技术栈不动：仍是 Python + SQLite（AES-GCM + DPAPI）+ Qt WebEngine。

---

## 1. 调研方法与范围

- 只读克隆到仓库外的 `ref/macro`，不运行它的任何脚本。阅读顺序：根 `README.md`、`CLAUDE.md`、`docs/`，各 crate 的 `AGENTS.md`，再看关键源码和 PostgreSQL 迁移（`crates/macro_db_client/migrations/`，719 个）。
- 规模：后端 Rust（`crates/` 约 300 个 crate、`services/` 约 50 个服务），前端 SolidJS（`apps/web`），PostgreSQL + OpenSearch + Kafka + SQS + Redis，文档协作用 Loro CRDT 与 Cloudflare Durable Objects，GraphQL 规范化缓存。
- 重点看的模块：`comms_db_client/entity_mentions`、`channels/.../references.rs`、`properties`、`document_sub_type`、`memory`、`agent_inmem`、`agent_trigger`、`agent_session*`、`task_dedup`、`soup`、`work_feed`、`email`（Signal）、`entity_access`、`ai_toolset`、`ai_tools`、`mcp_toolset`、`crm`、`activity`。
- 没有深入：日历、通话、数据库块、Canvas、WhatsApp、计费——与鸿雁目标关系弱。

---

## 2. Macro 核心架构

### 2.1 总体分层

```
apps/web (SolidJS)  ──GraphQL / REST / WebSocket──▶  services/*（每个服务是组合根）
                                                         │
                       crates/<domain>：domain（纯逻辑 + ports 接口）
                                        inbound（HTTP 路由、AI 工具、Kafka 消费）
                                        outbound（Postgres / 外部 API 适配器）
                                                         │
                         PostgreSQL（MacroDB）· OpenSearch · Kafka · SQS
```

- **六边形结构是硬约定**：每个领域 crate 都是 `domain / inbound / outbound`，`domain` 只认 `ports` 里的 trait。AI 工具放在领域自己的 `inbound::toolset`，由 `ai_tools` 统一收集——**同一个领域服务同时服务 UI、内置 Agent 和外部 MCP**，三条路径没有各写一套。
- **「Soup」统一查询**：`crates/soup` 把文档、任务、邮件线程、频道、Agent 会话、PR 等放进一个分页、可过滤、可分组的结果集；过滤条件是一棵 AST（`filter_ast`、`item_filters`）。首页、最近、Signal、AI 的 `ListEntities` 工具都走它。
- **GraphQL 实体约定**（`crates/complete_graph/AGENTS.md`）：有全局身份的类型才有 `id`；跨类型共同字段放在 `GraphqlSoupEntity` 接口上（`id`、`entityType`、`displayName`、`properties`、`viewerPermission` 等），内容字段不进接口。这是一份很好的「统一实体」最小契约。

### 2.2 对象之间的双向关系

Macro 同时用三种机制，各管一类关系：

| 机制 | 存储 | 管什么 | 怎么「双向」 |
| --- | --- | --- | --- |
| **提及边**（主力） | `comms_entity_mentions(source_entity_type, source_entity_id, entity_type, entity_id, user_id, created_at)` | 正文里 @ 到的任何对象：消息、文档、任务正文里提到的邮件、人、公司、PR… | 两条索引：`(source…)` 查「我引用了谁」，`(entity_type, entity_id) INCLUDE (source…)` 查「谁引用了我」 |
| **类型化属性** | `property_definitions`（`data_type = 'ENTITY'`、`specific_entity_type`）+ `entity_properties.values`（JSONB `EntityReference`） | 结构化的关系：任务的「项目」、负责人、自定义的「客户」字段 | 属性按目标实体反查；系统属性用固定 UUID（如 Project = `…0014`） |
| **专表外键** | `github_pr_tasks`、`agent_session.task_id`、`agent_session_pull_request`、`crm_contacts.company_id`、`crm_domains` 等 | 有生命周期语义的关系：一个 Agent 会话最多做一个任务、PR 由 Agent 还是人关联 | 普通外键 + 反向索引；删除语义写在约束里（任务删了，会话 `ON DELETE SET NULL`） |

要点：

- **边按来源整体替换**：`create_entity_mentions` 批量 `UNNEST` 插入，`delete_entity_mentions_by_source` 先删。保存一次正文 = 删光这个来源的旧边 + 写入新抽取的边，所以编辑掉一个 @ 后关系自然消失，不需要「解除关联」按钮。
- **提及的线上格式是正文里的标记**（`crates/mention_utils`：`<m-user>`、`<m-channel>`、文档提及等 XML 小标签；前端 Lexical 编辑器渲染成胶囊）。AI 输出、通知文本、编辑器共用一种格式，Agent 写的回复里也能 @ 出可点击的对象。
- **反向查询会过滤已删除的来源**（`channels/.../references.rs`：被删消息不再算引用，未知父类型跳过并记日志）。
- 实体映射：Email = 邮件线程；Task = `Document` + `document_sub_type = 'task'`（正文是 Markdown）+ 系统属性（状态、优先级、负责人、项目）+ 团队内序号（`team_task`、`team_task_counter`，即 `#123`）；Project = `initiative`（由旧的 `task_initiative` 关联表迁成「项目」实体引用属性）；Person = 内部用户 `macro_user` + 外部联系人 `crm_contacts`；Organization = `crm_companies` + `crm_domains`（按邮箱域名把往来邮件聚合到公司）。

### 2.3 统一工作记忆

`crates/memory`（共约 900 行）：

- **存储**：`memory(id, user_id UNIQUE, memory TEXT, updated_at)`，每人一行，`ON CONFLICT (user_id) DO UPDATE` 覆盖。没有版本、没有条目、没有来源。
- **生成**：`get_or_generate_memory` 读的时候若超过 24 小时就后台重算（另有每日定时任务）。重算 = 起一个 Agent 循环，**给它这个用户自己的全部工具**（文档、项目、邮件、频道、搜索），提示「研究我是谁、在做什么，写 1000–3000 字的长期记忆」，上一版记忆作为 `<previous_memory>` 放进系统提示，要求「保留仍准确的、用新研究核实、删掉过时或无依据的」。
- **防噪声**：要求输出包在 `<memory>` 标签里，标签外全部丢弃；职位、创始人身份只有原文明确写了才写，旧记忆里的职位无法再次证实就删。
- **第二遍评审**：另一个模型按清单拒绝「数据不足、满篇推测、不足 500 字、像性格测试、带生成过程自言自语」的稿子，被拒就保留旧记忆。
- **使用**：`agent_inmem/src/rig_engine.rs` 的 `system_prompt` 把记忆**放在系统提示最后**，注释写明「这样记住的事实永远不会被当成指令读」；前面是身份、产品说明、会话指令。外部 MCP 也能读到。日志里明确不记录记忆正文（`skip(previous_memory)`）。
- **相关**：`ai_projections` 是「按提示词哈希缓存的 AI 视图」，提示词变了才重算；`cognition` 提供命名的是 / 否判断。

**评价**：生成、更新、压缩都靠「整段重写」，追溯来源完全没有。它的价值在两点——**用工具主动研究而不是只看最近邮件**，和**生成后独立评审**。

### 2.4 邮件 → 任务 → Agent → PR → 结果

```
邮件线程 ──「Create task」──▶ 任务（正文预填 @邮件）──提及边──▶ 反向：邮件看到「被任务引用」
   │                               │ 分配给 Agent / @Macro
   │                               ▼
   │                        agent_trigger：判断是否启动（显式 @、隐式回复由小模型判断、任务分配、项目分配）
   │                               ▼
   │                        agent_session(task_id)  ──▶ agent_session_log / tool_approval / review
   │                               ▼
   │                        agent_session_pull_request(source: agent|user, linked_by)
   │                        github_pr_tasks(github_key = org:repo:num, task_id)
   │                               ▼
   └──────────── 结果 ◀── agent_session_changes（分支、基线、文件列表、增删行数、补丁位置）
```

- 前端入口：`apps/web/src/features/block-email/EmailThreadHostView.tsx` 的 `createTask`，在分屏里弹出任务编辑器，`initialContent` 是指向邮件线程的提及 Markdown。也可以 `c t`、文档里 `/task`、选中 `- [ ]` 一键成任务、在频道里 @Macro、Agent 对话、MCP。
- **查重**：`crates/task_dedup` 对任务做向量化（pgvector），检索候选，重排序，再让大模型判是否重复；结果存 `task_duplicate_match`（两边任务、向量分、重排分、判定模型和理由、可被用户「不是重复」驳回）。
- **触发与上下文**：`crates/agent_trigger` 统一决定「这条消息 / 这次分配要不要启动 Agent」，并组装上下文（线程历史、相关人、讨论）。
- **任务撤销 / 进展**：任务正文是协作文档，历史靠 CRDT 版本；结构化的事件进 `activity_events`（「某主体对某实体做了某事」一张表，供时间线和团队动态）。

### 2.5 Omni Inbox、Signal / Noise、多窗口

- **一个收件箱**：邮件、频道消息、任务分配、@提及、Agent 回复都进同一列表，`j/k/e` 一套键。后端是 `work_feed`：候选来自 Soup + 通知 + 邮件 + 活动，`policy.rs` 定成员资格，`stacking.rs` 把同一对象的多条通知叠成一行，`done / seen` 状态单独存。
- **Signal 规则**（`work_feed/src/domain/policy.rs` 的 `signal_filter`）：邮件要 `is_signal` 且是自己的（共享邮箱的不算）；频道线程要我参与；PR 要包含我；Agent 会话要不是隐藏的例行任务。
- **`is_signal` 算法**：迁移 `20260708215614_add_email_threads_is_signal.sql` 把「线程里有重要邮件」冗余成列，避免每次查询都跑启发式；重要 = 不带降级分类标签（推广、更新等）或被用户规则提升；用户规则按发件人地址优先、域名次之（`email/src/domain/service/user.rs` 的 `draft_sender_is_signal`）。草稿默认算 Signal。
- **多窗口**：内置分屏管理器，按屏幕宽度可开 3 个以上分屏；`Shift+点击` 在新分屏打开；邮件转任务也是在分屏里弹出编辑器，原邮件留在旁边。手机端只有单窗格。

### 2.6 Agent、MCP 与权限

| 层 | 做法 | 位置 |
| --- | --- | --- |
| 工具定义 | `AsyncTool` + `schemars` 生成 JSON Schema；每个工具**必须**实现 `ToolAnnotated`，否则编译不过 | `crates/ai_toolset/src/{tool,annotations}.rs` |
| 工具注解 | `ToolKind::{ReadOnly, Additive, Destructive}`（映射 MCP 的 `readOnlyHint` / `destructiveHint`），另有 `open_world`（会不会碰外部系统）、`idempotent`、给人看的标题 | 同上 |
| 工具设计原则 | 按工作流而不是按接口设计；精选而不是全量暴露；用枚举不用自由字符串；**不给 AI 分页参数，给语义过滤**；返回要点，必要时 `concise / detailed` | `crates/ai_toolset/TOOL_DESIGN.md` |
| 代行身份 | 会话里才注入 `acting_as`，主人 / 请求者从可信上下文解析，公开工具入参不变 | `crates/ai_toolset/src/actor.rs` |
| 访问控制 | `EntityAccessService` 每类实体一个检查，返回 `EntityAccessReceipt`（类型化的「已检查过」凭据）；有命令行解释「为什么有 / 没有权限」 | `crates/entity_access` |
| 调用审批 | 别人在主人的会话里发起、花主人权限的 MCP 调用：在出口代理处挂起，写 `agent_session_tool_approval`（pending / approved / denied / cancelled / expired，只有主人能批，恰好一次生效），可选「本会话内对这个工具不再问」（`agent_session_tool_standing_approval`） | 迁移 `20261005203905*`、`20261005203907*` |
| 结果审阅 | Agent 起草的邮件、日程等作为「审阅」保存（`agent_session_review`），用户在卡片里改、发、放弃 | 迁移 `20261015164252_agent_session_reviews.up.sql` |
| 外部 MCP | 把已连接的外部 MCP 服务器包装成同一个 ToolSet，工具名编码成 `mcp__<server>__<tool>`，调用按名路由回去 | `crates/mcp_toolset` |
| 代码模式 | 工具多时让 Agent 写一段调用 SDK 的 JS，在沙箱里批量执行；授权不变，执行需要审批 | `docs/agent-guide/ai-chat.md`、`packages/code-mode-sdk` |

### 2.7 数据模型摘录（与本课题相关）

| 表 | 关键列 | 说明 |
| --- | --- | --- |
| `comms_entity_mentions` | `source_entity_type, source_entity_id, entity_type, entity_id, user_id, created_at` | 通用引用边；5 条索引（四列组合、来源、目标含来源、用户、时间） |
| `Document` + `document_sub_type` | `sub_type ∈ {task, snippet, skill, initiative_description}` | 任务、片段、技能都是文档的子类型 |
| `team_task` / `team_task_counter` | `(team_id, task_num)` | 团队内递增编号 |
| `property_definitions` / `entity_properties` | `data_type`、`specific_entity_type`、`values JSONB` | 自定义与系统属性；实体引用也是一种属性值 |
| `initiative` / `initiative_member` | 名称、描述、负责人、共享权限 | 项目 |
| `crm_companies` / `crm_domains` / `crm_contacts` | 公司 ↔ 域名 ↔ 联系人邮箱 | 组织与人 |
| `memory` | `user_id UNIQUE, memory TEXT` | 每人一段记忆 |
| `task_duplicate_embedding` / `task_duplicate_match` | 向量、分数、判定理由、驳回人 | 任务查重 |
| `agent_session` | `task_id`、`turn_prompter` 等 | Agent 会话 |
| `agent_session_pull_request` / `github_pr_tasks` | `github_key`、`source`、`linked_by` | PR 关联 |
| `agent_session_changes` | 分支、SHA、文件、增删行、补丁位置 | 结果摘要 |
| `agent_session_tool_approval` / `…_standing_approval` | 状态机、主人、请求者、工具、参数 | 审批 |
| `activity_events` | 主体、动作、实体、时间 | 统一活动记录 |
| `email_threads.is_signal` | 布尔 | Signal 冗余列 |

### 2.8 可复用的设计模式

1. **一张边表 + 两条索引 + 按来源整体替换**：最小的双向关系实现，不需要图数据库。
2. **关系分三类存**：随正文变化的（提及边）、结构化字段（类型化属性）、有生命周期的（专表）。
3. **统一实体最小契约**：类型、ID、显示名、属性、我的权限；内容各自定义。
4. **派生标志冗余成列**（`is_signal`），由写入路径维护，查询只读列。
5. **工具注解强制声明**，审批策略按注解自动分级。
6. **类型化凭据**：检查过权限才拿得到的值，下游函数只收凭据。
7. **挂起式审批**：一行记录是唯一事实，恰好一次生效，带「不再问」的范围。
8. **记忆放提示最后、明说不是指令**；生成后独立评审；按提示词哈希缓存 AI 视图。
9. **同一个领域服务同时供 UI、内置 Agent、外部 MCP**。
10. **每个目录一份 `AGENTS.md`（是什么 / 从哪看 / 范围 / 依赖 / 配方 / 检查 / 坑）**，对 AI 协作开发很有效。

---

## 3. 与鸿雁现有能力的差距矩阵

鸿雁现状依据：`coremail_desktop/` 源码（`memory/`、`tasks/`、`ai/`、`mcp/`、`app/backend.py`）与 `docs/roadmap/`。「领先 / 相当 / 落后 / 无」是对照 Macro 的判断；「需要吗」按鸿雁是**单用户、本机、Coremail、公司内网**的定位判断。

| 维度 | Macro | 鸿雁现状 | 对比 | 需要吗 |
| --- | --- | --- | --- | --- |
| 邮件分级 | 规则：分类标签 + 发件人 / 域名覆盖 → `is_signal` | 理解层：AI 判要我做 / 要我知道 / 可以忽略，带一句话要点、截止、是否确定；规则先于 AI（`ai/understand.py`）；用户纠正 | **领先** | 已有 |
| 通用引用边 | `comms_entity_mentions`，任意对象 → 任意对象 | 只有任务 ↔ 邮件（`tasks/store.py` 的 `links`）；事实、「关于我」条目各自记一个 `mid` | **落后** | **要（P0）** |
| 反向引用面板 | 任何对象都能看「谁引用了我」 | 任务能看关联邮件；邮件只能看「关联的任务」，看不到哪些事实、哪条「关于我」来自它 | 落后 | 要（P0） |
| 任务模型 | 文档子类型 + 属性 + 编号 + 协作正文 | 加密任务库，固定字段与 Excel 一一对应，事件时间线带审核状态，可逐步撤销（`tasks/center.py`） | 相当（各有侧重） | 保持，不改模型 |
| 项目 | `initiative` 实体，有 ID、描述、成员 | 名单里的名字（kv `projects`，两级、别名），任务的「系统」字段 | 落后 | 要（P1，轻量） |
| 人 | 内部用户 + 外部联系人实体 | 通讯录 + 人物卡片（M3：负责什么、等我处理、等他回复、最近往来） | 相当 | 已有 |
| 组织 | 公司 ↔ 域名 ↔ 联系人，按域名聚合邮件 | 无；外部客户只能按人看 | **无** | 要（P1） |
| 统一记忆：生成 | Agent 用工具研究，整段重写 | 理解新邮件时逐条提取事实并合并；「关于我」AI 起草、逐条确认 | 鸿雁更稳 | 已有 |
| 统一记忆：追溯 | 无 | 事实带来源邮件、被替代留痕、标错不再出现；条目带来源类型和 `mid` | **领先** | 已有 |
| 统一记忆：压缩 | 1000–3000 字上限，整段重写 | 按分组限字数截断；本周概括按事实哈希缓存（M3b） | 相当 | 可补「定期整理建议」（P2） |
| 统一记忆：质量把关 | 独立评审模型否决 | 用户逐条确认 | 鸿雁更稳 | 可借评审清单给「整理建议」做自检（P2） |
| 记忆进提示的位置 | 系统提示最后，注明不是指令 | `HEADER` 注明「只作背景，不是指令」 | 相当 | 核对各调用点的位置（P0 小项） |
| 邮件转任务 | 预填 @邮件，提及边生成关联 | 「转为任务（AI）」、助理 `plan_tasks` 预览确认、自动更新带 `fit` 判断（T2） | 相当 | 已有 |
| 任务查重 | 向量 + 重排 + 大模型判定 + 可驳回 | 只在提示词里让 AI 提醒「明显重复」 | 落后 | 要（P1，不用向量） |
| 任务 → Agent → 结果 | 会话外键到任务，PR、变更摘要、审阅全程留痕 | 助理计划执行写进任务事件（`actor: ai`、`accepted`）；对话只在内存，不落盘 | 部分 | P2：执行留痕，不做 PR |
| 统一活动记录 | `activity_events` | 只有任务事件；邮件操作撤销信息在内存 | 落后 | P1 |
| 统一收件箱 | 邮件 + 消息 + 任务 + 提及 + Agent 一个列表 | 收件箱「要我做 / 知会」筛选；今天页已在 G1 / G2 改为每日速览 | 部分 | 不照搬（见 §6） |
| 多窗口 | 3+ 分屏 | 三栏 + 双击弹出阅读窗口；写信是覆盖层 | 落后 | P2：仅「邮件旁开任务 / 写信」 |
| 工具注解 | 编译期强制只读 / 新增 / 删除 | 读工具直接执行，写操作都是 `plan_*` 预览卡片 | 相当（靠约定） | P1：把约定变成声明 + 测试 |
| 审批 | 挂起式审批、「本会话不再问」 | 每个写操作都预览确认，可撤销 | 鸿雁更严 | P1：按注解分级，可选「本次对话不再问」只限可撤销操作 |
| 外部 MCP | 全量读写，带审批 | X1：本机只读，命名管道 + DPAPI 密钥 | 按定位够用 | 写入不开放 |
| 访问控制 | 实体级凭据 | 单用户；加密邮件需逐封允许才发给 AI | 不同问题 | 只学「凭据」思路做加密邮件 |
| 开发文档 | 每目录 `AGENTS.md` | 根 `CLAUDE.md` + 设计文档 | 落后 | P2：给大目录补简短说明 |

---

## 4. 可直接借鉴的设计思想

1. **引用是数据，不是界面**：任何「A 提到 / 来自 / 属于 B」都写成一条边，任何对象的详情都能列出「引用了谁、被谁引用」。鸿雁现在每个模块各存各的 `mid`，合起来就是一张没建出来的边表。
2. **关系分三类**：正文里的引用（`[tN]`、@人、邮件里提到的项目）随正文整体替换；结构字段（任务的系统 / 项目、负责人）作为字段；有生命周期的（任务 ↔ 邮件的确认 / 待确认 / 驳回）保留专表和审核状态。不要把鸿雁现有的任务关联改成提及边——它的审核状态和撤销语义更重要。
3. **派生判断写回成冗余列**：理解卡片的分类已经是这个思路；项目最近动态、组织聚合也照此做，界面只读结果。
4. **AI 只提议、人来定**——鸿雁已经比 Macro 做得彻底，保持。可以借 Macro 的两条细则：职位、身份类信息原文没明说就不写；旧的无法再证实就降级为「未确认」而不是保留。
5. **记忆放最后、声明不是指令**：统一成一个拼提示的函数，所有 AI 调用走它，避免某处把记忆放到用户问题前面。
6. **工具按工作流设计、不给分页、返回要点**：`ai/tools.py` 大体符合；`search_mail` 等可加「共 N 封，只列前 M 封」的截断说明，`read_task` 可加简略 / 详细两档。
7. **工具注解 + 审批分级**：把「只读 / 可撤销的写 / 不可撤销或外发」声明在工具规格里，由一处代码决定是直接执行、预览确认还是二次确认；测试保证新工具不能漏声明。
8. **统一活动记录**：一张「谁（用户 / AI / 规则）在什么时候对什么对象做了什么、能否撤销」的表，任务事件、邮件操作、事实合并、规则执行都写进去；撤销、周报素材、「这个项目最近」都从这里取。
9. **查重先便宜后贵**：先用标题 / 系统 / 关联邮件做本地候选，再让 AI 判，结果可驳回且记住驳回。
10. **按域名聚合组织**：外部客户邮件按发件人域名归到公司，公司页列人、往来邮件、相关任务和事实——这是「人物卡片」向上一级的自然延伸。

---

## 5. 值得参考的具体代码模块（只读学习，不复制）

| Macro 位置 | 看什么 | 对应鸿雁 |
| --- | --- | --- |
| `crates/macro_db_client/migrations/20251104101012_comms_db_schema.sql`（`comms_entity_mentions` 与索引） | 边表的列和 5 条索引 | P0 通用引用表 |
| `crates/comms_db_client/src/entity_mentions/{create,delete_…_by_source}.rs` | 批量写入、按来源删除 | P0 写入路径 |
| `crates/channels/src/outbound/pg_channels_repo/references.rs` | 反查时过滤已删除来源、跳过未知类型 | P0「被谁引用」 |
| `crates/mention_utils/src/{parse,serialize}.rs` | 正文里的引用标记格式、往返测试 | `[tN]`、`@人` 统一解析 |
| `crates/complete_graph/AGENTS.md` §1–3 | 统一实体的最小字段契约 | P0 引用目标的显示名、打开方式 |
| `crates/macro_db_client/migrations/20261003040141_task_project_property.up.sql` | 关联表迁成实体引用属性的做法 | P1 项目实体 |
| `crates/memory/src/domain/service.rs` | 生成提示、`<memory>` 标签提取、评审清单、过期重算 | P2 记忆整理建议 |
| `crates/agent_inmem/src/rig_engine.rs`（`system_prompt`） | 提示各段的顺序与理由 | P0 提示拼装统一 |
| `crates/task_dedup/src/domain/service.rs`、迁移 `20260528120000_task_duplicate_detection.sql` | 候选 → 判定 → 可驳回的流程与存储 | P1 任务查重 |
| `crates/agent_trigger/src/domain/{sources,context}.rs` | 触发来源的分类、上下文组装 | P2 助理执行留痕 |
| `crates/ai_toolset/src/annotations.rs`、`TOOL_DESIGN.md` | 注解枚举与工具设计原则 | P1 工具注解 |
| 迁移 `20261005203905_agent_session_tool_approval.sql`、`20261005203907_agent_session_tool_standing_approval.sql` | 审批状态机、「不再问」的范围 | P1 审批分级 |
| `crates/work_feed/src/domain/{policy,stacking}.rs` | 成员资格策略、同对象通知叠行 | 收件箱「要我做」叠行（可选） |
| `crates/email/src/domain/service/user.rs`（`draft_sender_is_signal`） | 地址规则优先于域名规则 | 理解层规则的优先级核对 |
| `crates/activity/src/domain/models.rs` | 动作词表与活动行形状 | P1 统一活动记录 |
| `crates/crm`、迁移 `20260512120000_crm_tables.up.sql` | 公司 ↔ 域名 ↔ 联系人 | P1 组织 |
| `CLAUDE.md`、`.agents/templates/AGENTS.md` | 目录说明模板 | P2 开发文档 |

---

## 6. 不建议照搬的部分

| 不照搬 | 原因 |
| --- | --- |
| 整个技术栈（Rust 微服务、SolidJS、PostgreSQL、Kafka、OpenSearch、CRDT） | 鸿雁是单用户本机程序，Python + SQLite + Qt WebEngine 已够用；换栈收益为零、风险极大。用户也明确要求不为 Macro 换栈。 |
| 代码本身 | AGPL-3.0，复制即传染。 |
| 「整段重写」式记忆 | 丢失来源与历史，和鸿雁「AI 只提议、可追溯、可纠正」的原则相反；而且每天全量重研究的 token 成本在公司内网接口上不合算。 |
| 用通用文档承载任务 | 鸿雁任务与 Excel 一一对应、事件可逐步撤销，这是用户已验收的工作方式；改成富文本文档会打断导出和撤销。 |
| 规则式 Signal / Noise | Coremail 没有 Gmail 分类标签；鸿雁 AI 理解层更准，已在用。 |
| 混合收件箱（邮件 + 任务 + 提及 + Agent 一列） | 鸿雁试过「今天」页，用户在 G1 / G2 主动换掉了；评审也指出概念重叠是主要问题。只借「同对象叠行」这一点。 |
| 多人权限、频道即共享、团队记忆 | 单用户，没有团队空间；公司邮件不能出本机。 |
| 外部 MCP 写入、代码模式沙箱 | X1 定为只读是安全边界；本机执行 AI 生成的脚本风险高。 |
| Agent 自动触发（隐式回复判定、分配即启动） | 鸿雁 2026-10-28 已确定「主动协作（提醒、自动处理）不做」。 |
| 向量库查重（pgvector + 重排 + 第三方 embedding） | 任务只有几百条，本地规则候选 + 一次 AI 判定足够；外发向量化接口也增加数据出境面。 |
| PR / 编码 Agent 链路 | 与邮件工作台无关；只借「执行留痕」的思路。 |

---

## 7. 分阶段改进路线图

原则：每一步都**少一个概念或多一条可追溯的线**，不加新的入口；全部在现有 `coremail_desktop` 包内实现，存储沿用 AES-GCM + DPAPI 的 SQLite；真实账号只读或发给自己；错误只以固定代码进界面。下表代号是建议，进入「开发顺序」表前各自先写设计文档。

### P0：把已有的关联连成一张图（不加新功能入口）

| 代号 | 内容 | 涉及 | 验收 |
| --- | --- | --- | --- |
| G0a | **通用引用表**：在工作记忆库（`memory/store.py` 同一密钥）加 `refs(src_type, src_id, dst_type, dst_id, kind, at, blob)`，类型限 `mail / task / fact / profile / project / person`；`src`、`dst` 两条索引；正文类来源按来源整体替换；ID 用 HMAC 存明文列，内容只存密文 | `memory/store.py`、新模块 `memory/refs.py` | 单元测试：替换、反查、删除来源后反查为空 |
| G0b | **回填与双写**：任务关联（`tasks/store.py` 的 `links`，保留原表与审核状态，仅镜像已确认的）、事实来源 `mid`、「关于我」条目 `mid`、`[tN]` 引用写入 `refs`；之后新写入同时写 | `tasks/center.py`、`memory/merge.py`、`memory/profile.py` | 回填前后计数一致；关闭后再开计数不变 |
| G0c | **「相关」面板**：阅读栏、任务详情、人物卡片各加一个折叠的「相关」块：引用了谁、被谁引用（任务、事实、关于我、邮件），点了直接打开；不新增左栏入口 | `app/web/*.js`、`backend.py` 一个只读接口 | 演示数据走查；真机看一封已转任务、已提取事实的邮件 |
| G0d | **提示拼装统一**：所有 AI 调用经一个函数拼「系统规则 → 工具说明 → 当前对象 → 记忆（最后，标明只作背景）」 | `ai/client.py` 或新 `ai/prompt.py` | 测试断言每个调用点记忆在最后 |

### P1：补齐对象、留痕与安全分级

| 代号 | 内容 | 依赖 | 说明 |
| --- | --- | --- | --- |
| G1a | **项目成为对象**：名单里的项目和系统各得一个稳定 ID（改名不丢关联），任务「系统」字段、事实 `pkey` 映射到它；Excel 列不变 | G0a | 学 Macro 关联表迁属性的做法，但只在本地 kv 加 ID 映射 |
| G1b | **组织**：按发件人域名把外部联系人归到公司（公司内域名除外），公司卡片列人、最近往来、相关任务和事实；用户可合并 / 改名 | G0a | 只读聚合，不新建 CRM |
| G1c | **统一活动记录**：一张加密 `activity` 表，任务事件、邮件操作（含撤销信息，替代内存）、事实合并、规则执行、助理执行都写入；「这个项目最近」、周报素材改从这里取 | G0a | 学 `activity_events` 的「主体 / 动作 / 对象」三元组 |
| G1d | **工具注解与审批分级**：`ai/tools.py` 每个工具声明 `read / undoable / irreversible`；读直接执行，可撤销的预览确认（现状），不可撤销或外发（发信、定时发）二次确认；可选「本次对话对这类操作不再问」只对可撤销类开放；测试保证没有未声明的工具 | — | MCP（X1）只暴露 `read` 类，测试同样约束 |
| G1e | **任务查重**：新建（含转任务、助理 `create`）前，按系统、标题词、关联邮件同会话找候选，最多 5 条，再由 AI 判「重复 / 相关 / 无关」；结果在确认卡片里显示，用户可「不是重复」，记住驳回 | G0b | 不用向量 |

### P2：体验与工程

| 代号 | 内容 | 说明 |
| --- | --- | --- |
| G2a | **助理执行留痕**：助理对话里被执行的计划（不是对话全文）写入活动记录，带「来自哪次提问、改了哪些对象、能否撤销」；任务时间线能点回那次计划 | 对话正文仍不落盘，符合现有隐私做法 |
| G2b | **记忆整理建议**：每周一次（用户可关），AI 按 Macro 的评审清单检查「关于我」与项目事实：重复、互相矛盾、过期、无来源的，生成待确认的合并 / 降级建议；不自动改 | 只借评审清单，不做整段重写 |
| G2c | **邮件旁开任务**：从阅读栏转任务时，任务详情在右侧并排打开（复用弹出窗口或第二栏），不遮住邮件 | 只做这一个分屏场景 |
| G2d | **收件箱同对象叠行**：同一会话、同一任务的多封「要我做」叠成一行，展开看明细 | 学 `work_feed/stacking.rs` |
| G2e | **目录说明**：给 `app/`、`ai/`、`memory/`、`tasks/` 各补一份简短说明（是什么、从哪看、边界、检查、坑），根 `CLAUDE.md` 链过去 | 方便后续 AI 协作开发 |

### 与现有排期的关系

- 「开发顺序」表当前在 R0（冻结新功能、真实试用一周）之前；以上路线**不影响 R0**。建议 R0 试用结束后，按用户判断把 G0a–G0d 作为一组插入，再视试用记录决定 P1 的先后（若试用中常出现「这封邮件和哪个任务 / 事实有关找不到」，G0 优先；若常出现重复任务，G1e 提前）。
- 按项目规则，本调研不修改「开发顺序」表；用户认可后再由下一次改动写入表格与调整记录。

---

## 附：调研中核对过的鸿雁事实

- 理解层分类 `act / know / ignore`，规则先于 AI：`coremail_desktop/ai/understand.py`。
- 工作记忆库表 `cards / rules / kv / facts`，事实状态 `current / superseded / wrong / pending`：`coremail_desktop/memory/store.py`、`memory/merge.py`。
- 「关于我」条目带 `by / src / mid / state`，AI 只提议：`coremail_desktop/memory/profile.py`。
- 任务库表 `tasks / events / links / kv`，审核状态 `confirmed / pending / rejected / accepted`：`coremail_desktop/tasks/store.py`；关联与撤销：`tasks/center.py`。
- 助理工具与 `plan_*` 预览、`MAIL_ACTIONS`：`coremail_desktop/ai/tools.py`；对话只在内存：`app/backend.py` 中 `self._chats`。
- 本机只读 MCP：`coremail_desktop/mcp/server.py`。
- 人物卡片：`app/backend.py` 的 `memory_person`；弹出阅读窗口：`app/shell.py` 的 `ReaderWindow`。
