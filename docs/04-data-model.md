# 数据模型设计

## 1. 概览

数据分两大类：**结构化数据**（日程、待办、记账、健康记录、记忆）与**记忆数据**（用户档案、语义记忆）。每个端都有一个本地 SQLite 数据库作为**主数据源**（Local-first），通过 oplog 与云端 relay 做多端同步。

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
- 每张业务表带 `updated_at` + 来源 `device_id`，配合 oplog 解决冲突

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
  seq: integer (PK)         // 本机单调递增
  device_id: string         // 来源设备
  table: string             // schedule | memory_fact | finance ...
  op: string                // insert / update / delete
  row_id: string
  payload: json             // 变更后的字段（或墓碑标记）
  timestamp: datetime       // LWW 判据
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
```

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

> 业务表统一携带 `account_id`（归属）与 `updated_at`（用于同步 LWW）；所有表的主键是**全局唯一 ID（UUID）**，天然适合跨设备合并。

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

### 2.6 MemoryFact 记忆

```
memory_fact (
  id, user_id
  type: string        // 'relationship' / 'preference' / 'fact' / 'commitment'
  content: string     // "奶奶喜欢龙井"
  summary: string?    // 语义摘要
  embedding: blob?    // 向量
  source: string      // 'conversation' / 'silent_absorb' / 'user_added'
  confidence: float
  remind_at: datetime? // 若为 commitment，需要主动提醒的时间
  created_at / updated_at / last_seen_at
)
```

> `source='silent_absorb'` 表示由 Agent 决策 `remember` → 记忆工具静默写入（用户未显式要求记录）；此类记忆默认低打扰、可一键筛选查看与删除。

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
  classification: string    // 'respond' / 'act' / 'remember' / 'ignore' / 'ask'
  silent: bool              // 是否静默（remember 静默写入 / ignore）
  text: string              // 转写文本（敏感：仅本地，不上云）
  tool_call: json?          // act 时记录对应的 Tool Call
  cause: string?            // 拒绝 / 误判时可选备注
)
```

- `pending_confirmation`：**act 决策需确认时的核心状态存储**——Tool Call 经用户确认后才转正式记录（schedule/finance），未确认绝不落正式表
- `listen_audit`：Agent 决策的**本地审计**（默认不上云、不参与同步），用于追溯误判、调优 Agent 提示词与规则；用户可一键清空
- **隐私**：两类表均为本地数据，不进入同步 oplog（audit 尤其敏感）

## 3. 关系与引用规则

- 只有账号本人能读写自己 `account_id` 的行
- 主键统一用 UUID，天然跨设备合并无冲突
- 删除账号级联删除设备与全部业务数据
- 记忆写入总是可追溯（source + id）
- **跨设备规则**：同一行只能被一台设备修改；删除 = 写 tombstone；冲突用 LWW（`updated_at` 较新胜出）

## 4. 索引

- `schedule(start_at, user_id)` — 提醒查询
- `finance(occurred_at, user_id)` — 月度汇总
- `memory_fact(user_id, type)` — 档案查询
- `oplog(seq)` 顺序消费；`oplog(device_id)` 回执确认

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

## 6. 同步语义（跨设备一致性）

三端保持**最终一致（eventual consistency）**：

| 场景 | 行为 |
|------|------|
| 设备 A 离线新增日程 | A 本地立即生效；oplog 入队，B 上线后收合并 |
| 两端修改同一条记录 | LWW：更新时间较新者胜出（设备时钟偏差用逻辑时钟兜底） |
| 一端删除 | 写 tombstone，广播后各端物理清除 |
| 网络恢复 | 拉取远端 oplog → 按表应用 → 回执 ack → 清空本地已确认队列 |

**双版本下的同步**：同步语义与部署形态无关。Cloud 版同步经官方 relay；**self_hosted 版**的 `sync_profile.endpoint` 指向自建 relay，同一套 oplog/tombstone/LWW 协议照常工作。**未配置任何 relay** 时降级为纯本地（单机）模式，所有端能力不下降。

**提醒的一致性**：提醒由各端本地调度器触发，不依赖云端补发；跨端重复提醒容忍（行业常见做法是先去重标识，后续优化）。

## 7. 提醒任务补充说明

提醒不直接存于表中，由调度器根据 `schedule.remind_at`（或 RRULE 展开后的实例）触发一次（生成 ReminderJob）。避免冗余 cron 侦测。

`schedule` 每次变更 / 重复事件生成时，重新生成对应的 ReminderJob（QUEUED 状态，到时间 → DONE）。多端可能重复提醒，采用"同事件去重 key"取其到达顺序去重。