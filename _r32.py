# -*- coding: utf-8 -*-
from pathlib import Path
p = Path('docs/03-architecture.md')
lines = p.read_text(encoding='utf-8').split('\n')

start = None
for i, l in enumerate(lines):
    if l.startswith('### 3.2 Agent Runtime'):
        start = i
        break
assert start is not None, '3.2 起点未找到'

end = None
for i in range(start + 1, len(lines)):
    if lines[i].startswith('### 3.3'):
        end = i
        break
assert end is not None, '3.3 终点未找到'

new_block = [
    '### 3.2 Agent Loop（唯一智能决策核心）',
    '',
    'Agent 是系统唯一的智能决策核心。**决策分两层，互不混淆**：',
    '',
    '**第一层 · Agent Loop 控制状态**——决定循环下一步做什么，是唯一驱动 Loop 的信号：',
    '',
    '| 控制状态 | 含义 | 对 Loop 的影响 |',
    '|---------|------|--------------|',
    '| `TOOL_CALL` | 需要调用一个（或多个）工具 | 进入工具执行：注册表 → Policy → 执行 → Observation 回流 → 下一轮 |',
    '| `CLARIFY` | 信息不足，需用户补充 | 暂停，输出追问，等用户回答后回到 Loop |',
    '| `FINAL_RESPONSE` | 目标已达成（或可安全给出终止性回答） | 输出最终结果，结束本轮 |',
    '| `IGNORE` | 非目标输入 / 无需任何回应 | 静默结束，不产生 UI、不记录 |',
    '',
    '**第二层 · 业务意图标签**——标记 Agent 对一个 Tool Call 的「目的归属」，仅供记忆/审计/确认门参考，**不控制 Loop**：',
    '',
    '| 标签 | 含义 | 典型 Tool 调用 |',
    '|------|------|--------------|',
    '| act（办） | 修改数据的操作（写） | `schedule.create/update/delete`、`finance.record` |',
    '| query（查） | 只读查询 | `schedule.query`、`memory.search`、`weather.query` |',
    '| remember（记） | 静默写入记忆 | `memory.write`（`silent_absorb`） |',
    '| ask（问） | 需要用户补充 | （无工具调用，触发 CLARIFY） |',
    '| ignore（忽略） | 不应打扰 | （无工具调用，触发 IGNORE） |',
    '',
    '> 业务标签到 `TOOL_CALL` 的映射取决于 Agent 对工具本身的选定——**查询（query/read）天然无需确认门，写操作（act）才进 Policy 门**。',
    '',
    '```',
    'while 目标未完成 且 迭代数 < MAX:',
    '  observation = build_context(用户消息 / 工具结果 / 记忆检索 / 环境事件)',
    '  step        = agent.step(observation)   # 输出：控制状态 + 可选 ToolCall / 最终回应',
    '  switch step.control:',
    '    case TOOL_CALL:',
    '        tool_call  = step.tool_call            # 由 agent 选定（含 意图标签）',
    '        通过 Policy 校验（权限 / 风险 / 需要确认的写操作）',
    '        result     = tool_execute(tool_call)  # 确定性执行（Runtime）',
    '        observation <- result',
    '    case CLARIFY:',
    '        输出追问 -> 等待用户补充 -> 回到 Loop',
    '    case FINAL_RESPONSE:',
    '        output(step.final)   # 结束本轮',
    '    case IGNORE:',
    '        静默结束，不产生 UI / 记录',
    '```',
    '',
    '多步工具链在同一请求内循环完成——例如：',
    '`memory.search` → `schedule.query` → `weather.query` →（组合）→ `schedule.create` → `FINAL_RESPONSE`。',
    '',
    '- **统一入口**：无论语音转写、常驻聆听转写，还是键盘输入，都以 `UserMessage` 进入 Agent Loop',
    '- **默认输入模式**：由偏好 `default_input_mode`（voice / text）决定首屏输入条形态；会话中随时可手动切换',
    '- **查询类工具（只读）不进确认门**：`schedule.query` 直接执行并返回 Observation，只有**写操作（act）**经 Policy 确认门',
    '- **查询的默认出口是对话**：FINAL_RESPONSE 直接以对话/卡片返回，不自动跳转管理页',
    '- **记忆注入**：Agent 在需要时主动调用 `memory.search`，而非每次全量注入上下文',
    '- 危险内容识别：对心理危机、医疗/法律敏感话题启用规则，优先给安全响应',
    '',
    '> Agent **没有直接写库/直接调系统能力的权限**；一切动作都表达为 Tool Call，交给下方 Runtime 执行。',
    '',
    '### 3.2.1 意图标签与确认流（判断力的归属）',
    '',
    '对每一段输入，Agent 依据上下文给 ToolCall 打上**业务意图标签**；业务级"该办/该记/该查/废话"判断完全归 Agent，不再有独立的外部语义分类器：',
    '',
    '| 意图标签 | 处理 |',
    '|---------|------|',
    '| act（办） | 写操作，走 Tool 调用，**经 Policy 确认门**后才执行，未确认不落库 |',
    '| query（查） | 只读查询（schedule.query / memory.search / weather.query），直接执行返回结果，**不进确认门** |',
    '| remember（记） | 调记忆工具静默写入 memory，不进对话 |',
    '| ask（问） | 信息不足，向用户追问（触发 CLARIFY） |',
    '| ignore（不理） | 废话：不产生任何 UI / 记录（触发 IGNORE） |',
    '',
    'Agent 判断吸收轻量规则信号（"提醒/记/买/约/帮我"等执行信号，"我喜欢/奶奶/妈妈"等记忆信号）作为提示词，但**最终决策权在 Agent**。',
    '',
    '**确认状态机**（仅 act 写操作，由 Policy Engine 强制把关，防误执行、可撤销）：',
    '',
    '```',
    'TOOL_CALL（act 写操作）→ 进入确认态（轻提示）',
    '   → 运行时轻声复述："要记下『明晚 7 点健身』吗？"',
    '   ├── 确认 → 工具执行（写本地 SQLite + oplog）→ 同步 → 完成',
    '   ├── 否定 → 丢弃，不记录',
    '   └── 超时（默认 8s）→ 再问一次 / 取消，不执行',
    '```',
    '',
    '- act 未确认绝不落库；确认操作可随时撤销（undo）',
    '- remember 写入记忆但不进当前对话，用户随时可查、可删（管理页 / 对话内"我们上次记了什么"）',
    '- 误判兜底（AGT-05）：所有确认可否决，所有记忆可删除',
]
lines[start:end] = new_block
p.write_text('\n'.join(lines), encoding='utf-8')
print('replaced', start + 1, 'to', end)