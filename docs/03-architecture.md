# 系统架构设计

## 1. 架构总览

Alfred 是**跨三端（电脑 / 手机 / 平板）的完整应用**，采用 **Local-first** + 云端辅助的架构，并以**同源双发行**的方式呈现：

- **端上（Flutter 应用）**：三端共用一套代码基座，自适应 UI；本地 SQLite 是主数据存储；语音、提醒、工具调用（包括 `invoke`）全部在端上运行（离线可用）。**只有一个客户端**。
- **云端（协作服务）**：仅负责账号、AI 能力（LLM/ASR/同步中继），不做数据主权存储的单一事实源。云端是**可替换**的——既可指向官方服务（Cloud 版），也可由用户自建（Community 自托管版）。
- **跨设备同步**：本地数据变更按序同步到云端，多端合并后下发，最终一致。
- **构建形态**：同一代码库按 **Build Flavor / 配置注入**产出 `cloud` 与 `community` 两个构建；能力完全对齐，只有"基础设施由谁提供"不同。业务核心不复制、不分裂。

**Agent-native**：**Agent 是系统唯一的智能决策核心**。它以 **结构化 Tool** 暴露日程、提醒、记忆、搜索、财务与设备能力；在 **Agent Loop** 中动态规划 → **Tool Calling** → **Observation（观察）** → 再决策，直到达成用户目标。确定性执行、安全权限与持久化由 **Agent 外部运行时（Runtime / Policy Engine）** 保障。

**架构分层（自顶向下）**

```txt
┌──────────────────────────────────────────────────────────────────────┐
│                          应用层 (Flutter 三端共享)                     │
│                                                                    │
│  ┌─────────────┐   ┌─────────────┐   ┌─────────────┐   ┌──────────┐  │
│  │  自适应 UI   │   │  语音入口     │   │  推送提醒     │   │  离线视图  │  │
│  │ responsive │   │  wake/vad/asr│   │  notification│   │           │  │
│  └─────────────┘   └─────────────┘   └─────────────┘   └──────────┘  │
│                              │                                   │
│  ┌──────────────────────── ──┼───────────────────────────────────┐ │
│  │                    应用核心 (共享业务核心)                       │ │
│  │   ┌───────────────┐   ┌──────────────────┐   ┌──────────────┐  │ │
│  │   │  Agent Loop   │──▶│  Tool Registry   │──▶│    Memory    │  │ │
│  │   │  (唯一决策核心) │   │  (工具注册/调用)  │   │  (检索+写入)  │  │ │
│  │   └───────────────┘   └──────────────────┘   └──────────────┘  │ │
│  └──────────────────────────────┬─────────────────────────────────┘ │
└─────────────────────────────────┬────────────────────────────────────┘
                                 │
┌─────────────────────────────────▼────────────────────────────────────┐
│                 运行时层 Runtime / Policy Engine                      │
│   确定性执行 · 权限校验 · 风险分级 · 强制确认门 · 写库+oplog · 撤销     │
└─────────────────────────────────┬────────────────────────────────────┘
                                 │
┌─────────────────────────────────▼────────────────────────────────────┐
│                     本地存储 / 同步层                                 │
│  SQLite (主数据)  ·  同步日志/冲突解决  ·  向量检索(memory)           │
└─────────────────────────────────┬────────────────────────────────────┘
                                 │
        ┌────────────────────────┴────────────────────────┐
        ▼                                                   ▼
┌─────────────────────────┐                   ┌────────────────────────┐
│   云端层（Cloud / 自建） │                   │  外部服务                │
│  · 账号 / 认证           │                   │  · LLM 网关 (Agent 思考) │
│  · 同步中继 (合并)        │                   │  · ASR 兜底              │
│  · 推送 (FCM/APNs)       │                   │  · AI 异步任务(记忆提取)  │
└─────────────────────────┘                   └────────────────────────┘
```

### 2.1 为什么用 Agent-native + 外部运行时

| 关注点 | Agent（LLM） | 外部运行时（Policy / Rules） |
|--------|--------------|------------------------------|
| 理解、生成、共情、动态规划 | ✅ | |
| 工具选择与组合（Tool Calling） | ✅ | ✅ 校验参数合法性 |
| 时间计算、提醒触发 | | ✅ |
| 数据持久化、一致性 | | ✅ |
| 危险/敏感识别（自伤、诈骗） | ✅ (带规则兜底) | ✅ (兜底) |
| 主动提醒推送 | | ✅ |
| 同步、冲突解决 | | ✅ |

核心原则：**Agent 决定「想做什么」，运行时决定「能不能做、什么时候必须做」**。Agent 输出动作意图（Tool Call），由运行时校验权限、风险分级、强制确认门，通过后才确定性执行。

**关键决策**：

1. **本地是事实源**：SQLite 是每个端的主数据存储。云端不持有权威副本，只做多端间的「同步交换站」。断网、弱网、单端都不瘫痪。
2. **三端一套代码基座**：用 Flutter，UI 层按平台自适应（断点 / 横竖屏），业务核心完全共享。适配异形屏、平板、桌面是设计目标而非事后补丁。
3. **云端薄、端上可替换**：云端只做账号、LLM / ASR 网关、推送、同步合并中继；凡是能放端上的一律放端上，云端是可替换的（官方 / 自建）。
4. **Agent 决策、运行时把关**：唯一智能决策核心是 Agent；权限、风险、确认、持久化由独立运行时（Policy Engine）负责，二者职责分离。
5. **同源双发行**：社区自托管版与官方 Cloud 版共用同一 Flutter 客户端与同一 `core/`；仅通过 Provider / 构建配置注入基础设施（自带 LLM Key / 本地模型 / 自建 relay）。**能力零分裂**。

### 2.2 模块划分（Flutter 三端共享基座）

```
alfred_app/                        # Flutter 项目（macOS/Win/Linux + iOS/Android）
├── lib/
│   ├── app/                       # 应用壳：启动 / 路由 / 主题（含自适应断点）
│   ├── ui/                        # 响应式 UI
│   │   ├── responsive/            # 断点与布局适配（phone / tablet / desktop）
│   │   ├── screens/               # 各功能屏（日程、记忆、健康、聊天…）
│   │   └── widgets/               # 通用组件
│   ├── core/                      # 与 UI 无关的业务核心（纯 Dart，可单测）
│   │   ├── agent/                 # Agent Loop（观察 → 规划 → 工具调用 → 观察）
│   │   ├── tools/                 # Tool Registry + 原子工具：schedule / memory / finance / health / life / device
│   │   ├── runtime/               # 运行时 / Policy Engine（权限 · 风险分级 · 确认门 · 撤销）
│   │   │   ├── policy/            # 策略：确认规则、风险分级（时间/金钱/健康）
│   │   │   └── executor/          # Tool 执行器：参数校验、写库 + oplog
│   │   ├── memory/                # 长期记忆（结构化 + 语义检索 + 静默吸收）
│   │   ├── notifier/              # 本地提醒调度器（系统通知）
│   │   └── storage/               # SQLite / Repository 接口
│   ├── speech/                    # 语音：恒听 / vad / asr（本地优先）/ tts
│   ├── sync/                      # 同步：变更日志、上传队列、冲突解决
│   ├── auth/                      # 账号 / 会话
│   ├── cloud/                     # 云端 API 客户端（LLM、ASR、推送、同步）
│   │   ├── providers/             # Provider 注入层：官方端点 vs 自建端点（self-hosted）
│   │   └── flavors/               # build flavors：cloud / community
│   └── shared/                    # 通用工具、实体定义
└── native/                        # 平台通道：通知、录音、语音识别、TTS 等
```

- **双发行实现方式**：Flutter `--dart-define` / build flavor 注入 `cloud` / `community` 两种配置；`core/`、`speech/`、`sync/`、`ui/` 完全复用，只有 `cloud/providers` 的实例化不同。
- 自托管版不强制官方账号：依赖本地配置（自带 LLM Key / 自定义 relay URL / 本地模型优先）；Cloud 版默认对接官方服务。

### 2.3 数据流（一次日程创建，三端并发）

1. 用户语音输入："明晚 7 点健身，改成周四"
2. 语音层 → ASR → 文本 → `UserMessage` → 进入 **Agent Loop**
3. Agent 观察上下文（记忆/偏好/当前日程）→ 规划：需要 `schedule.update` 工具 → 发出 **Tool Call**（参数：周四 19:00 健身）
4. **Policy Engine 校验**：属修改日程（时间相关）→ 进入**确认门**，**用户确认后**才放行
5. Tool 执行器调用 `schedule.update` → 写入**本地 SQLite**
6. 返回成功，UI 立即反映（乐观更新）
7. 后台同步异步推进：变更写入 oplog → 上传云端 → 分发给同账号其它设备 → 各端合并
8. 断网时同步暂存，网络恢复自动重试，最终一致

> 注：修改类操作默认要求用户确认后执行。**先写本地、再谈同步**——用户永远不必等待网络。

## 3. 模块职责

### 3.0 自适应 UI 层 (responsive)

**对话即主界面（Conversation-First）**：三端打开 Alfred 永远先看到对话——这是唯一的一等界面。所有功能都通过对话触达，管理页面（日程列表、记账本、档案）降级为"后台"入口。

- 单一代码基座渲染三端；用断点宽度（phone < 600dp / tablet 600–1024 / desktop > 1024）切换布局
- 横竖屏、折叠屏、异形屏（刘海/挖孔/圆角）通过 `SafeArea` + `MediaQuery` 自动适配
- **导航层级**：
  - 主界面：对话流（三端一致，永远可见的输入条 + 语音按钮）
  - 二级入口：管理页仅从「设置 / 数据管理」进入，不设主 Tab，不占主导航
  - 平板/桌面可用多栏，但**主栏永远是对话**，侧栏只承载上下文/辅助信息
- 查询默认在对话中应答（"我下周有什么安排" → 对话直接报结果），不跳转列表
- 业务逻辑在 `core/`，UI 只做编排；布局切换不触碰数据层
- **输入条是"语音/文本"复合入口**：默认语音优先（`default_input_mode=voice`）；一键切换到文本；偏好设置可把默认改为文本——首屏输入模式由偏好决定，切换不打断会话

```
三端统一的页面树（示意）
├── 主：对话 (Chat)          ← 启动即此，一等界面
├── 设置 (Settings)
│   ├── 数据管理 (后台弱化入口)
│   │   ├── 日程列表 / 记账本 / 记忆档案    ← 只读校对为主
│   │   └── 导出 / 清空
│   └── 账号 / 同步 / 语音 / 隐私
└── （无其它主导航）
```

### 3.1 语音层 (speech)
语音是 Alfred 的第一输入方式。**应用启动即进入常驻聆听（always-listening）**：持续采集 → 本地 VAD → 本地 ASR，全程不出本机。

```
[常驻聆听] 持续采集(麦克风) → VAD 检测说话 → 语音片段
   → 本地 ASR 转文本 → **Attention Gate**（本地轻量：滤无语音/噪声/低质量转写）
   → 进入 Agent Loop（业务决策全部由 Agent 完成）
       ├── FINAL_RESPONSE → 直接回答对话 / 卡片
       ├── CLARIFY       → 追问澄清（ask 类）
       ├── TOOL_CALL     → 工具执行（act 写操作经 Policy 确认门；query 直接执行）
       └── IGNORE        → 丢弃，不打扰、不记录
```

- **常驻 VS 唤醒**：默认常驻聆听（无唤醒词等待）；唤醒词/按键作为"希望能立刻回应"时的增强入口
- **两种触发语音的方式**：(1) 常驻聆听（后台值班，无需操作，VAD/ASR → Attention Gate → Agent）；(2) 主动语音输入（点输入条的麦克风，说出指令直接进对话，等同文本发送）。两者都走本地 ASR。
- **文本输入**：输入条一键切换语音/文本；文本模式连麦克风都不用，直接打字发送；两种模式都进 `UserMessage` 同一条管线。
- **ASR 抽象**：`SpeechToTextProvider`，实现可切（本地 Whisper / 云端 / mock）；常驻聆听**强制走本地**（隐私 + 离线）
- **VAD（语音活动检测）**：检测说话起止，静音 1.5s 分段，非说话不触发 ASR（省电）
- **Attention Gate（常驻聆听唯一外部过滤器）**：本地轻量规则，仅滤除无语音、明显背景噪声、极低质量转写；**不判断业务语义**（语义判断由 Agent 完成）
- **TTS 可选**：确认音 / 回复朗读（默认关闭，避免轰炸）
- **降噪（P2）**：厨房、通勤场景增强

**隐私基线（常驻聆听）**：不传原始音频上云（本地转写即丢弃）；界面常驻「聆听中」指示；一键闭麦；可彻底关闭常驻聆听退回按键触发。

### 3.2 Agent Loop（唯一智能决策核心）

Agent 是系统唯一的智能决策核心。**决策分两层，互不混淆**：

**第一层 · Agent Loop 控制状态**——决定循环下一步做什么，是唯一驱动 Loop 的信号：

| 控制状态 | 含义 | 对 Loop 的影响 |
|---------|------|--------------|
| `TOOL_CALL` | 需要调用一个（或多个）工具 | 进入工具执行：注册表 → Policy → 执行 → Observation 回流 → 下一轮 |
| `CLARIFY` | 信息不足，需用户补充 | 暂停，输出追问，等用户回答后回到 Loop |
| `FINAL_RESPONSE` | 目标已达成（或可安全给出终止性回答） | 输出最终结果，结束本轮 |
| `IGNORE` | 非目标输入 / 无需任何回应 | 静默结束，不产生 UI、不记录 |

**第二层 · 业务意图标签**——标记 Agent 对一个 Tool Call 的「目的归属」，仅供记忆/审计/确认门参考，**不控制 Loop**：

| 标签 | 含义 | 典型 Tool 调用 |
|------|------|--------------|
| act（办） | 修改数据的操作（写） | `schedule.create/update/delete`、`finance.record` |
| query（查） | 只读查询 | `schedule.query`、`memory.search`、`weather.query` |
| remember（记） | 静默写入记忆 | `memory.write`（`silent_absorb`） |
| ask（问） | 需要用户补充 | （无工具调用，触发 CLARIFY） |
| ignore（忽略） | 不应打扰 | （无工具调用，触发 IGNORE） |

> 业务标签到 `TOOL_CALL` 的映射取决于 Agent 对工具本身的选定——**查询（query/read）天然无需确认门，写操作（act）才进 Policy 门**。

```
while 目标未完成 且 迭代数 < MAX:
  observation = build_context(用户消息 / 工具结果 / 记忆检索 / 环境事件)
  step        = agent.step(observation)   # 输出：控制状态 + 可选 ToolCall / 最终回应
  switch step.control:
    case TOOL_CALL:
        tool_call  = step.tool_call            # 由 agent 选定（含 意图标签）
        通过 Policy 校验（权限 / 风险 / 需要确认的写操作）
        result     = tool_execute(tool_call)  # 确定性执行（Runtime）
        observation <- result
    case CLARIFY:
        输出追问 -> 等待用户补充 -> 回到 Loop
    case FINAL_RESPONSE:
        output(step.final)   # 结束本轮
    case IGNORE:
        静默结束，不产生 UI / 记录
```

多步工具链在同一请求内循环完成——例如：
`memory.search` → `schedule.query` → `weather.query` →（组合）→ `schedule.create` → `FINAL_RESPONSE`。

- **统一入口**：无论语音转写、常驻聆听转写，还是键盘输入，都以 `UserMessage` 进入 Agent Loop
- **默认输入模式**：由偏好 `default_input_mode`（voice / text）决定首屏输入条形态；会话中随时可手动切换
- **查询类工具（只读）不进确认门**：`schedule.query` 直接执行并返回 Observation，只有**写操作（act）**经 Policy 确认门
- **查询的默认出口是对话**：FINAL_RESPONSE 直接以对话/卡片返回，不自动跳转管理页
- **记忆注入**：Agent 在需要时主动调用 `memory.search`，而非每次全量注入上下文
- 危险内容识别：对心理危机、医疗/法律敏感话题启用规则，优先给安全响应

> Agent **没有直接写库/直接调系统能力的权限**；一切动作都表达为 Tool Call，交给下方 Runtime 执行。

### 3.2.1 意图标签与确认流（判断力的归属）

对每一段输入，Agent 依据上下文给 ToolCall 打上**业务意图标签**；业务级"该办/该记/该查/废话"判断完全归 Agent，不再有独立的外部语义分类器：

| 意图标签 | 处理 |
|---------|------|
| act（办） | 写操作，走 Tool 调用，**经 Policy 确认门**后才执行，未确认不落库 |
| query（查） | 只读查询（schedule.query / memory.search / weather.query），直接执行返回结果，**不进确认门** |
| remember（记） | 调记忆工具静默写入 memory，不进对话 |
| ask（问） | 信息不足，向用户追问（触发 CLARIFY） |
| ignore（不理） | 废话：不产生任何 UI / 记录（触发 IGNORE） |

Agent 判断吸收轻量规则信号（"提醒/记/买/约/帮我"等执行信号，"我喜欢/奶奶/妈妈"等记忆信号）作为提示词，但**最终决策权在 Agent**。

**确认状态机**（仅 act 写操作，由 Policy Engine 强制把关，防误执行、可撤销）：

```
TOOL_CALL（act 写操作）→ 进入确认态（轻提示）
   → 运行时轻声复述："要记下『明晚 7 点健身』吗？"
   ├── 确认 → 工具执行（写本地 SQLite + oplog）→ 同步 → 完成
   ├── 否定 → 丢弃，不记录
   └── 超时（默认 8s）→ 再问一次 / 取消，不执行
```

- act 未确认绝不落库；确认操作可随时撤销（undo）
- remember 写入记忆但不进当前对话，用户随时可查、可删（管理页 / 对话内"我们上次记了什么"）
- 误判兜底（AGT-05）：所有确认可否决，所有记忆可删除

### 3.3 工具层 (tools) + Tool Registry

每个原子能力是一个 Tool，暴露给 Agent 供其动态选择与组合：

```
interface Tool {
  name: string
  description: string
  inputSchema: JsonSchema          // 参数定义，Agent 在 Schema 下生成参数
  policy: Policy                   // 风险分级 / 是否需确认（时间、金钱、健康）
  invoke(args: any, ctx: Context): Promise<ToolResult>
  undo(result): UndoContext        // 可选的撤销
}
```

由 **Tool Registry** 负责注册与路由。新增能力即新 Tool 包（`core/tools/<name>/`），不侵入核心。

| Tool 族 | 能力 |
|---------|------|
| schedule | 日程/待办 CRUD、提醒偏移、重复事件 |
| memory | 记忆读取、写入、检索（含 remember 静默写入） |
| finance | 记账、预算、月度汇总 |
| health | 健康记录、用药提醒 |
| life | 天气、通勤、购物清单 |
| device | 设备侧能力（通知、网络、定位等，受权限控制） |

> 工具本身不含"业务智能"——是否调用、何时调用、如何组合由 Agent 动态决定；Tool 只按 Schema 确定性执行。

### 3.4 运行时 / Policy Engine（确定性执行保障）

Agent 提供"想法"，运行时是"把守的关卡"：

- **执行器（executor）**：校验 Tool Call 参数（Schema）、调用 Tool、统一写 SQLite + oplog
- **策略引擎（policy）**：风险分级（时间/金钱/健康）、权限卡控、**强制确认门**（act 类必须先确认）、撤销支持
- **降级**：LLM 不可用时，基础操作走纯规则的极简可用集（固定指令匹配直接调 Tool，用户确认后执行）
- **审计**：所有 Tool 调用、确认、拒绝计入 listen_audit

—— 决策与执行分离：**Agent 想做什么，Runtime 决定能不能做、何时必须做**。

### 3.5 提醒调度器 (notifier)
- 基于本地 cron/job 队列的定时器，**系统原生通知**（iOS/Android/桌面）
- 提醒在设备上由该设备调度器触发（断网也提醒）
- 可靠性：应用启动时检查错过的任务并补救（补发错过提醒）

### 3.6 存储层 (storage)
- **主数据**：SQLite（端上主数据源）——日程、待办、记账、健康、情绪、记忆
- **记忆向量**：储在 SQLite 中的轻量向量检索（sqlite-vec）
- 提供统一 Repository 接口；每张表带 `updated_at` + `device_id` 用于同步

### 3.7 同步引擎 (sync)
- 变更捕获：所有写操作经统一 `Repository`，自动写入 `oplog`（追加日志）
- 上传/下载：接入云端 relay，拉取其它设备变更、推送本地变更
- 冲突解决：按 `device_id` + 时钟/LWW：无冲突合并、同字段冲突采用更新时间较新者
- 墓碑机制：删除不物理删，写 tombstone 以便广播到所有端

## 4. 记忆系统（核心差异化，作为 Agent 上下文/工具）

记忆系统为 Alfred 提供"管家在长期陪伴中记住你"的能力。在 Agent-native 架构下，记忆**以 Tool 形式暴露给 Agent**：Agent 需要时调用 `memory.search` 检索往事、`memory.save` 记录（含 remember 决策的静默吸收）。

| 类型 | 说明 | 存储 |
|------|------|------|
| 结构化档案 | 姓名、生日、偏好、关系 | 结构化字段 / JSON |
| 语义记忆 | "奶奶喜欢龙井" 等自由语义 | 向量 + 摘要 |
| 会话记忆 | 最近 N 轮对话 | 短期缓存 |
| 承诺/待办记忆 | 需要主动跟进的未完成事项 | 独立任务表 |

**记忆写入策略**：
- 关键事实（时间、金额、人名）→ 结构化
- 对话中自然出现的偏好 → 语义记忆
- 写入前用户无感，但记忆可查询、可修正
接口参考：

```ts
interface MemoryStore {
  saveFact(record: MemoryRecord): void
  searchSemantic(query: string, k?: number): Promise<MemoryRecord[]>
  listByType(type: MemoryType): Promise<MemoryRecord[]>
  delete(id: string): Promise<void>
}
```

## 5. 安全与隐私

- **本地优先**：SQLite 为主数据源，离线可用全部工具能力
- **权限机制**：Tool 按敏感类型分级（时间/金钱/健康/设备），策略引擎强制高风险操作先确认；Agent 无普通用户权限之外的直通能力
- 导出 / 清空数据一键完成；用户可随时关闭记忆
- LLM 调用仅发送必要上下文，支持降级（LLM 不可用时基础操作走纯规则极简集）
- 端到端加密（P2，优先级延后）；敏感提示（自伤、药物过量）走策略路由，优先安全资源
- 录音仅在主动触发后采集，识别后即丢弃（默认不上传）

## 6. 技术选型

| 层面 | 选择 | 理由 |
|------|------|------|
| 客户端 | **Flutter**（iOS / Android / macOS / Win / Linux） | 一套代码三端 + 自适应布局 |
| 响应式 UI | Flutter `LayoutBuilder` / 断点 / `SafeArea` | 适配异形屏 / 横竖屏 / 平板 |
| 业务语言 | **Dart**（`core/` 纯 Dart 可测） | 与 Flutter 同语言，三端共享 |
| 本地存储 | **SQLite**（drift / sqflite） | 端上主数据，可靠可移植 |
| 同步 | **oplog + 云端 relay**（LWW + 墓碑） | 离线最终一致 |
| 语音识别 | 本地 Whisper（faster-whisper）+ 云端兜底；**常驻模式用极小本地模型**（如 whisper tiny / distil-whisper） | 离线稳定，隐私友好，低功耗 |
| 语音活动检测 | WebRTC VAD / silero-vad（低功耗触发） | 常驻聆听只在有人说话时唤醒 ASR |
| 唤醒词 | Porcupine（离线多语言） | 辅助入口，非必需 |
| Agent 决策 | **LLM function calling**（Agent Loop + Tool Calling） | Agent 唯一决策核心，工具动态规划组合 |
| 运行时决策护 | **Policy Engine（规则引擎）** | 权限/风险分级/确认门/写库，确定性执行 |
| 语音合成 | 系统 TTS / edge-tts | 默认关闭不轰炸 |
| LLM 接入 | OpenAI / Claude / 本地 OpenAI 兼容 | 灵活替换，默认可本地 |
| 云端 | 账号 / 推送（FCM / APNs）/ relay | 薄云端，可指向官方或自建 |
| 双发行 | Flutter `--dart-define` / build flavor（`cloud` / `community`） | 同源码产两包，仅基础设施端点不同 |
| 调度 | Dart chrono + `workmanager` | 三端后台提醒 |

> 选型在 M1 实现中确认；「三端共享 core、语音本地优先」是不变约束。

## 6.1 双发行形态（Community 自托管 / Cloud 官方）

| 维度 | Community（自托管） | Cloud（官方） |
|------|--------------------|---------------|
| 获取 | 源码编译 / Docker 镜像 | 应用商店 / 官网安装包 |
| 账号 | 本地 / 自建 relay；可选同步 | 官方账号与同步 |
| AI | 自带 LLM API Key / 本地模型 | 官方 LLM / ASR 网关 |
| ASR | 本地 Whisper 优先，云端兜底可配置 | 官方 ASR / 本地 Whisper |
| 推送 | 本地通知（桌面 OK）；手机端需自建推送或拉取 | 官方 FCM / APNs |
| 数据 | 全留在本地设备 / 自建服务器 | 本地 + 官方同步中继 |
| 关系 | 同一代码，能力零分裂 | 同上，官方托管基础设施 |

> 基础设施通过 `cloud/providers` 接口抽象：`CloudLLMProvider` / `SelfHostedAsyncProvider`；Flavor 仅改变注入，不复制任何业务代码。**不阉割能力（Capability）**。

## 7. 关键时序图

### 7.1 常驻聆听与 Agent 决策（三端）

```
用户说话（无需唤醒）──▶ [常驻聆听] 麦克风持续采集
        → VAD 检测到话段 → 本地 ASR → 转写文本
        → Attention Gate（本地轻量：仅滤噪声/无语音，不做语义）
        → 进入 Agent Loop（控制层驱动）
          ScheduleLoop（"明早 7 点提醒吃药"）：
            step1 → TOOL_CALL（act | schedule.create）→ Policy 确认门
            → 轻声复述："要记下『明早 7 点提醒吃药』吗？"
                ├─ 确认 → 执行 schedule.create → 本地 SQLite + 生成 Reminder
                │         → oplog 排队（后台同步）→ UI 显示 → FINAL_RESPONSE
                ├─ 否定 → 丢弃，不记录
                └─ 超时 → 再问 / 取消
          MultiToolLoop（"我明天有什么安排，要下雨吗"）：
            step1　TOOL_CALL（query | schedule.query）     ← 只读，不走确认门
                  → Observation：明天有 3 个日程
            step2　TOOL_CALL（query | weather.query）
                  → Observation：明天下雨
            step3　FINAL_RESPONSE → 组合回答（+提醒是否改期）
```

### 7.2 主动提醒（Active Reminder）

```
本端定时器 ──到期──▶ 查询本地日程表
                      │
                      ▼
              命中今日提醒任务吗？
                      │
              ┌───────┴───────┐
            是              否
              │               │
              ▼               ▼
          生成提醒消息      静默
              │
              ▼
     系统通知（本端 UI / 可选 TTS）
```

### 7.3 跨设备同步

```
设备 A（改日程） → 本地 SQLite 立即生效
     └─ oplog: insert/update × 1
            └─▶ 云端 relay 接收
                    ├──▶ 设备 B（在线）：merge → 本地 SQLite
                    └── 若设备 B 离线：云端暂存，B 上线后补发
```

## 8. 扩展点

- **新工具（Tool）**：`core/tools/<name>/` 注册即可，Agent 自动发现
- **新策略规则**：`core/runtime/policy/` 增加风险分级 / 新确认规则，不改 Agent
- **新通知渠道**：`Notifier` 接口实现
- **新存储**：`Repository` 接口抽象
- **新 LLM**：`LLMProvider` 接口
- **新端形态**：给 Flutter 添加 `ui/responsive` 断点即可
- **新部署形态**：`cloud/providers` 接口实现 + `build flavor` 注入；自托管部署无需改动客户端业务代码