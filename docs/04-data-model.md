# 数据模型设计

## 1. 概览

数据分两大类：**结构化数据**（日程、待办、记账、健康记录、记忆）与**记忆数据**（用户档案、语义记忆）。每个端都有一个本地 SQLite 数据库作为**主数据源**（Local-first），通过 oplog 与云端 relay 做多端同步；relay 持久化同步副本（oplog 全量 + snapshot）以支撑新设备 Bootstrap，但不是业务权威源（详见 §6.1）。

```
┌─────────────┐     1:N     ┌──────────────┐
│   Account   │ ──────────▶ │   Schedule   │  (待办/事件)
│   (账号)     │             └──────────────┘
│             │     1:N     ┌──────────────┐
│             │ ──────────▶ │  FinanceLog  │  (记账)
│             │             └──────────────┘
│             │     1:N     ┌──────────────┐
│             │ ──────────▶ │  HealthLog   │  (健康)
│             │             └──────────────┘
│             │     1:N     ┌──────────────┐
│             │ ──────────▶ │  EmotionLog  │  (情绪)
│             │             └──────────────┘
│             │     1:N     ┌──────────────┐
│             │ ──────────▶ │  MemoryFact  │  (记忆)
└─────────────┘
      ↑
      │ （多个 Device）
  Device（设备）  同步支撑表：oplog / tombstone
```

- 每个账号可有多台设备；每台设备持有全量本地镜像（Local-first）
- 每张业务表带 `updated_at`（墙钟展示）+ 来源 `device_id`（tie-break）；LWW 判胜走 oplog 的 `hlc_timestamp`（HLC，见 §2.0.1/§6）

## 2. 实体定义

### 2.0 账号与设备

```
account (
  id: string (PK)
  name: string
  email: string
  created_at
)

device (
  id: string (PK)            // 设备生成的 UUID
  account_id: string (FK)
  name: string              // "我的手机"
  platform: string          // ios / android / macos / windows / linux
  last_seen_at: datetime
)

oplog (
  op_id: uuid (PK)          // 全局唯一操作 ID，跨设备不冲突
  device_id: string         // 来源设备（HLC 相等时作 tie-break）
  device_seq: integer       // 本设备内单调递增，仅保证单设备有序
  table: string             // schedule | memory_fact | finance ...
  op_type: string           // insert / update / delete
  row_id: string
  payload: json             // 变更后的字段（或墓碑标记）
  hlc_timestamp: integer    // HLC 时间戳（见 §2.0.1），Row-level LWW 判据，非 wall clock
)

tombstone (
  row_id: string (PK)
  deleted_at: datetime
)

sync_profile (
  id: string (PK)
  account_id: string (FK)     // 官方账号或自建账号
  mode: string                // 'cloud'（官方托管）| 'self_hosted'（自建 relay）
  endpoint: string?           // 自托管时填 relay URL；Cloud 用官方默认
  auth_provider: string       // 'official' | 'self'
  llm_provider: string        // 'official' | 'self_key' | 'local'
  asr_provider: string        // 'local' | 'official' | 'custom'
  created_at / updated_at
)

-- 以下为 relay 侧持久化副本（非端上主库；非业务权威源，仅供新设备 Bootstrap）
relay_snapshot (
  account_id: string (PK)
  version: integer           // 递增快照版本
  hlc_watermark: integer     // 生成快照时该账号已知的最大 HLC
  payload_uri: string        // 快照文件地址（加密，见 §6.1 encrypted replica）
  created_at: datetime
)
```

> relay 侧存储：**oplog 全量**（按账号）+ **`relay_snapshot`** 最新 N 版。角色是「持久化交换站」，非 CRUD 权威源；任何设备本地 SQLite 仍可直接改并回传。

### 2.0.1 Hybrid Logical Clock（HLC 时间戳）

LWW 的时间比较**不依赖墙钟**（设备时钟会漂移），统一用 **HLC**。每台设备在本地 SQLite 维护一个单行时钟：

```
hlc_state (
  device_id: string (PK)
  physical_ts: integer      // 最近一次刷新的墙钟时间（epoch ms），仅作单调性参考
  logical: integer          // 逻辑计数，同毫秒内递增，保证严格单调
)
```

- 每个 `op` 入队 oplog 时取 `HLC.now()`，写入 `oplog.hlc_timestamp`（对应 §6 的 LWW 判据）
- **HLC 编码**：`hlc_timestamp = (physical_ms << 16) | logical`（64-bit integer，`logical` 低 16 位）。比较即整数比较，无需解析
- **收到远端更大的 HLC** 时本地以它为准跳跃（`physical = 远端.physical`，`logical = 远端.logical + 1`），保证跨设备偏序一致
- **判胜**：`(hlc_timestamp, device_id)` 字典序，后者胜出——`device_id` 打破 HLC 打平
- `updated_at` 是用户可读的墙钟（展示/排序），但**判胜不用墙钟**，用 `hlc_timestamp`；墙钟仅作展示与 HLC 物理分量的来源
- 业务表统一带 `updated_at`（墙钟展示）+ 本地 `hlc_state` 维持时序；不期望业务表单独存 HLC——oplog 里的 `hlc_timestamp` 就是权威比较对象，业务表从 oplog 落库时记 `updated_at`

> **冻结**：初始实现直接落 **HLC**，不遗留"开发时再设计逻辑时钟"的空洞；LWW 判胜 = `(hlc_timestamp, device_id)` 字典序。

### 2.1 User 用户档案（个人偏好）

```
users (
  id: string (PK)
  account_id: string (FK)   // 归属账号；多账号扩展时用
  name: string
  timezone: string
  preferences: json         // 偏好结构化（咖啡、饮食等）
                          // 含 default_input_mode: 'voice' | 'text'（默认 'voice'）
  created_at: datetime
  updated_at: datetime
)
```

> 业务表统一携带 `account_id`（归属）与 `updated_at`（墙钟展示）；LWW 判胜用 oplog 的 `hlc_timestamp`（§2.0.1）。所有表的主键是**全局唯一 ID（UUID）**，天然适合跨设备合并。

### 2.2 Schedule 日程 / 待办

```
schedule (
  id: string (PK)
  user_id: string (FK)
  title: string
  description: string?
  start_at: datetime          // 事件时间
  end_at: datetime?           // 可选
  is_todo: bool               // 区分日程 vs 待办
  done: bool
  repeat_rule: string?        // iCal RRULE 或 'none'
  reminder_offsets: json     // [15, 60] 提前分钟数
  source: string             // created_by 还是 llm
  created_at / updated_at
)
```

**示例**：`"每周四 19:00 健身"` → `repeat_rule: "FREQ=WEEKLY;BYDAY=TH"`，`reminder_offsets: [60]`

### 2.3 Finance 记账

```
finance (
  id: string (PK)
  user_id: string (FK)
  amount: decimal(10,2)
  category: string      // 食物/交通/购物/其他 ...
  note: string
  occurred_at: datetime
  created_at / updated_at
)
```

可选扩展：budget 表（预算）、split 分摊。

### 2.4 Health 健康记录

```
health (
  id, user_id
  type: string      // 'vitamin', 'weight', 'sleep', 'exercise', ...
  value: string / numeric
  logged_at: datetime
  note: string?
)
```

健康习惯类用 `schedule` + `type='health'` 实现，记录类用本表。

### 2.5 Emotion 情绪日志

```
emotion (
  id, user_id
  mood: string        // happy / stressed / anxious / sad /
  intensity: integer  // 1-5
  note: string
  logged_at: datetime
  follow_up: bool     // 是否需后续关怀
)
```

### 2.6 MemoryFact 记忆（长期管家模型）

记忆是 Alfred 的长期资产，模型按「可演进、可追溯、可替代」设计：

> **承诺归记忆，执行归日程**：Memory 只记录"我承诺要做什么"的认知事实（如"答应奶奶周末去看她"），真正需要执行/提醒的任务落到 `schedule` 表（`is_todo`），由现有调度器触发提醒。Memory 不承担可靠提醒，避免 Memory Scheduler 与 Schedule Scheduler 双任务系统。二者用 `linked_schedule_id` 关联。

```
memory_fact (
  id, user_id
  type: string        // 'relationship' / 'preference' / 'fact' / 'commitment'
  content: string     // 当前内容："我最近戒咖啡了"
  summary: string?    // 语义摘要

  status: string      // 'current'（当前有效） / 'superseded'（被替代）/ 'obsolete'（过期失效）
  importance: int     // 重要度 0-10（rerank 因子；"最好的朋友叫 Lisa"=10，"今天中午吃面"=1）
  confidence: float   // 置信度（rerank 因子；写入时校验 + 用户修正递增）

  valid_from: datetime?   // 事实有效期的开始（如"我最近戒咖啡了"从 2026-05 起）
  valid_until: datetime?  // 有效期结束（到时自动降级为 obsolete / 需重新确认）

  supersedes_id: string?  // 本条替代哪条旧事实（旧记录保留，但默认不再作为当前事实返回）
  superseded_by: string?  // 被哪条替代（反向索引，用于溯源链）

  linked_schedule_id: string?  // 若为 commitment，指向真正执行/提醒的 schedule 行
  created_at / updated_at / last_seen_at
)
```

#### 2.6.1 来源追溯（provenance）——Alfred 为什么记得这个

```
memory_fact_source (
  memory_id: string (FK)
  source_type: string   // 'conversation' / 'silent_absorb' / 'user_added' / 'imported'
  source_message_id: string?   // 对话消息 ID（可定位到原始语句）
  source_device_id: string?    // 哪台设备写入
  created_by: string           // 'llm' / 'user' / 'rule' / 'migration'
  captured_at: datetime
)
```

> `source_type='silent_absorb'` 表示 Agent 决策 `remember` → 静默写入（用户未显式要求记录）；此类记忆默认低打扰、可一键筛选查看与删除。

#### 2.6.2 向量存储（与业务事实解耦）

embedding 不放在事实表内——**换模型不用改写事实**：

```
memory_embedding (
  memory_id: string (FK)
  model: string      // 嵌入模型标识，如 "local:all-MiniLM-L6-v2:v1"
  dimension: int     // 向量维度
  embedding: blob    // 向量
  created_at: datetime
  PRIMARY KEY (memory_id, model)   // 同一条事实可有多版本向量
)
```

> 检索双路：lexical 走 `content` 的 **FTS5** 索引；semantic 走 `memory_embedding` 的 **sqlite-vec**。两路 merge + deduplicate + rerank 见 03 §4.2。嵌入模型本地按需构建，不上云。

### 2.7 Agent 决策与审计相关

```
pending_confirmation (
  id: string (PK)
  user_id: string (FK)
  tool_call: json       // 待确认的 Tool Call / Agent 决策（如 schedule.create 参数）
  original_text: string // 用户原话（转写文本）
  status: string        // 'asking' / 'confirmed' / 'rejected' / 'expired'
  created_at: datetime  // 进入确认态时间
  confirmed_at: datetime?
)

listen_audit (
  id: string (PK)
  device_id: string
  heard_at: datetime        // 语音话段时间
  control_state: string     // 'TOOL_CALL' / 'CLARIFY' / 'FINAL_RESPONSE' / 'IGNORE'
  classification: string    // 业务标签：'act' / 'remember' / 'query' / 'ask' / 'ignore'
  silent: bool              // 是否静默（remember 静默写入 / ignore）
  text: string              // 转写文本（敏感：仅本地，不上云）
  tool_calls: json?         // 记录本轮 tool_call 序列（含每个调用的业务标签）
  cause: string?            // 拒绝 / 误判时可选备注
)
```

- `pending_confirmation`：**act 写操作需确认时的核心状态存储**——Tool Call 经用户确认后才转正式记录（schedule/finance），未确认绝不落正式表；query/read 类工具调用不产生确认
- `listen_audit`：Agent 决策的**本地审计**（默认不上云、不参与同步），同时记录**控制状态**（本轮循环如何结束）与**业务标签**（act/remember/query/ask/ignore），用于追溯误判、调优 Agent 提示词与规则；用户可一键清空
- **隐私**：两类表均为本地数据，不进入同步 oplog（audit 尤其敏感）

## 3. 关系与引用规则

- 只有账号本人能读写自己 `account_id` 的行
- 主键统一用 UUID，天然跨设备合并无冲突
- 删除账号级联删除设备与全部业务数据
- 记忆写入总是可追溯（source + id）
- **跨设备并发修改同一行是预期场景**（手机离线改 title、平板离线改 reminder，后合并）；删除 = 写 tombstone；字段冲突统一收口到 **Row-level LWW：整行以 `hlc_timestamp` 较新者胜出**（判胜见 §6 与 §2.0.1）

## 4. 索引

- `schedule(start_at, user_id)` — 提醒查询
- `finance(occurred_at, user_id)` — 月度汇总
- `memory_fact(user_id, type, status)` — 档案查询（默认只查 status='current'）
- `memory_fact(user_id, valid_from, valid_until)` — 时间窗口过滤
- `memory_fact(content)` **FTS5** — lexical 全文召回
- `memory_embedding(memory_id, model)` — **sqlite-vec** semantic 向量召回（按 model 版本查询）
- `memory_fact(supersedes_id)` — 溯源链回溯
- `oplog(op_id)` — 全局唯一，幂等应用（按 op_id 去重）
- `oplog(device_id, device_seq)` — 按设备有序拉取，回执以 device_seq 作游标

## 5. 数据生命周期与隐私

| 类型 | 保留 | 导出 | 清空 |
|------|------|------|------|
| 日程 | 最多 1 年（过期归档） | ✅ JSON 导出 | ✅ 一键清除 |
| 记账 | 长期 | ✅ | ✅ |
| 健康 | 长期 | ✅ | ✅ |
| 情绪 | 3 个月可选保留 | ✅ | ✅ |
| 记忆 | 长期（可衰减） | ✅ | ✅ |

- 用户随时可 `导出数据 / 清空全部 / 关闭记忆`（清空会生成 tombstone 同步到所有设备）
- 每端本地库是完整镜像；删除操作需广播 tombstone 以保证他端不复活

## 6. 同步语义（跨设备一致性）—— Row-level LWW + HLC

三端保持**最终一致（eventual consistency）**。**并发修改同一行是预期场景**，不做字段级合并：

| 场景 | 行为 |
|------|------|
| 设备 A 离线新增日程 | A 本地立即生效；oplog 入队，B 上线后收合并 |
| 两端并发修改同一记录 | 字段冲突统一走 **Row-level LWW：整行以较新者胜出覆盖**。判胜用 `oplog.hlc_timestamp`（HLC，见 §2.0.1，不是墙钟）；平局以 `device_id` 字典序定胜负 |
| 一端删除 | 写 tombstone，广播后各端物理清除 |
| 网络恢复 | 各端按 `device_id` 拉取 `device_seq > 本地游标` 的远端 oplog → 按表应用 → 回执 ack（记到 device_seq 游标）→ 清空本地已确认队列 |
| 新设备首次登录 | 下载 relay snapshot（全量状态）→ 应用后续 oplog 增量 → SQLite 就绪；旧设备全离线也能拿到历史（详见 §6.1） |

**冲突解决选择的取舍**：
- 初版采用 **Row-level LWW + Hybrid Logical Clock（HLC）**：简单、确定、好测试。A 改 `title`、B 改 `reminder` 的两处改动，按整行覆盖只保留 `hlc_timestamp` 较新一端（**field-level 合并不做**）。
- 判胜即 `(oplog.hlc_timestamp, device_id)` 字典序，后者胜；`device_id` 只在 HLC 打平时起 tie-break 作用。
- **不做 Field-level CRDT 合并**——那会引入每字段版本/合并器，同步复杂度爆炸。仅当未来出现同字段编辑冲突率显著上升的真实需求时，再评估升级。

### 6.1 Bootstrap（新设备首次登录）

新 iPad / 手机第一次登录同一账号，旧设备可能全离线——**必须能从 relay 重建历史状态**，因此 relay 除了交换还做**持久化同步副本**（非业务权威源，Local SQLite 仍是客户端运行的源）：

- **relay 持久化**：
  1. **oplog 全量**：接收所有设备的 oplog 追加并长期保存（幂等，按 `op_id` 去重）；
  2. **snapshot**：定期为该账号生成一份「当前全量状态」快照（业务行最终态 + 活跃 tombstone，时间点用该账号已知的最大 HLC 标记）；
  3. 可选 **encrypted replica(加密镜像)**：若隐私策略允许，加密存储业务副本，供更快的离线恢复/多端全量恢复；本地仍唯一权威。
- **Bootstrap 流程**：
  1. 新设备注册并登录账号；
  2. 下载该账号最新 **snapshot**（内含截止 HLC 的全部业务行 + tombstone）；
  3. 应用 snapshot 后，以 snapshot 截止 HLC 为起点，拉取随后补发的 **oplog 增量**（LWW / tombstone 照常处理）收敛到最新；
  4. 本地 SQLite 就绪，新设备成为普通节点参与增量同步。
- **前提**：relay 保存的 oplog 必须覆盖自最新 snapshot 以来的一切变更（snapshot 之后不可裁剪对应 oplog）；relay 从不以副本覆盖任何设备 SQLite。

**双版本下的同步**：同步语义与部署形态无关。Cloud 版同步经官方 relay；**self_hosted 版**的 `sync_profile.endpoint` 指向自建 relay，同一套 oplog/tombstone/LWW/Bootstrap 协议照常工作。**未配置任何 relay** 时降级为纯本地（单机）模式，所有端能力不下降；但此时多端自动同步与**新设备历史回档**不可用。

**提醒的一致性**：提醒由各端本地调度器触发，不依赖云端补发；跨端重复提醒容忍（行业常见做法是先去重标识，后续优化）。

## 7. 提醒任务补充说明

提醒不直接存于表中，由调度器根据 `schedule.reminder_offsets`（提前 N 分钟）与 `start_at`（或 RRULE 展开后的实例）触发一次（生成 ReminderJob）。避免冗余 cron 侦测。记忆中的 commitment 通过 `linked_schedule_id` 关联到 schedule 行，**由 schedule 调度器统一触发提醒**，Memory 不建第二套提醒。

`schedule` 每次变更 / 重复事件生成时，重新生成对应的 ReminderJob（QUEUED 状态，到时间 → DONE）。多端可能重复提醒，采用"同事件去重 key"取其到达顺序去重。
