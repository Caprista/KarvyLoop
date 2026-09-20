# OA_A 运行与自动审批规则说明

本文描述 OA_A 当前的审批运行流程、角色文件职责、MCP 调用边界，以及自动创建审批规则的规则模型和判定算法。

## 1. 目标与边界

OA_A 负责当前绑定 OA 身份下的待办查询、详情分析、人工审批、规则预览和已确认规则的自动审批。

OA_A 不是 OA 管理员。它不能更换 OA 身份、扩大权限、猜测审批结果，或把历史偏好当作授权。钉钉 sender、会话 ID 只用于渠道隔离，不能替代 OA 身份。

系统分为两层：

```text
Agent 七文件：流程、表达、承诺、验收要求
代码与 OA adapter：权限、参数、待办新鲜度、规则匹配、实际执行
```

提示词或用户文本不能绕过代码层的检查。

## 2. 七文件职责

| 文件 | 唯一职责 | 不承担的职责 |
|---|---|---|
| `IDENTITY.md` | 身份、业务范围、不可逾越的角色边界 | 工具清单、详细流程 |
| `SOUL.md` | OA 业务处理顺序和工作原则 | 结果验收、工具绑定 |
| `USER.md` | 用户语言、编号习惯和回复展示偏好 | 授权、权限、规则生效条件 |
| `MEMORY.md` | 已验证历史事实和规则审计摘要 | 当前待办、临时权限、推测性偏好 |
| `COMMITMENT.md` | 对用户的结果反馈承诺 | 执行流程细节 |
| `VERIFY.md` | 执行前后验收检查项 | 角色语气、工具清单 |
| `COMPOSITION.yaml` | OA_A 可见 MCP 工具清单 | 业务流程、回复模板 |

## 3. OA_A 运行流程

### 3.1 总流程

```text
钉钉/Web/CLI 消息
  -> ChannelEnvelope
  -> OAIntent 或 RuleIntent
  -> 当前待办快照
  -> 详情与规则分析
  -> 人工操作或自动规则判断
  -> OA adapter 实时校验与执行
  -> 结果验收、游标推进、真实回复
```

渠道层只负责身份白名单、消息收发、会话标识和展示。OA 指令、规则、审批快照和执行结果属于平台无关的 OA domain 层。

### 3.2 查询待办

1. 调用 `mcp_oa_get_doing_workflow_request_list`。
2. adapter 取完分页并返回当前 `index_map`。
3. OAApprovalService 保存 `编号 -> requestId` 快照。
4. 快照默认有效期为 10 分钟；刷新、过期或 `/new` 后旧映射失效。
5. 回复列表时只展示用户需要的摘要；编号只在当前快照内有效。

### 3.3 查询详情

1. 用户按编号查看时，先由快照解析真实 requestId。
2. adapter 再次查询当前待办，避免旧编号、流转完成或撤回后的错位执行。
3. 调用 `mcp_oa_get_workflow_request`；必要时调用 `mcp_oa_get_request_operator_info`。
4. 将 OA 原始详情规范化为规则上下文，包括流程、申请人、部门、金额、项目、文本、附件和风险字段。

### 3.4 人工批准、拒绝和转发

| 操作 | 额外前置条件 |
|---|---|
| 批准 | 当前会话中有明确对象和批准动作 |
| 拒绝 | 明确对象、拒绝原因、有效退回节点 |
| 转发 | 明确对象和无歧义的目标人 |

执行前 adapter 都会校验当前待办。执行后以 OA 业务成功结果为准，不能仅依据 HTTP 成功或模型叙述判断。

批量操作按项执行。成功、失败和跳过分别记录到审批游标；失败不盲目重试。

### 3.5 错误处理

| 情况 | 行为 |
|---|---|
| `STALE_REQUEST` | 停止该项，提示刷新待办 |
| `TODO_PERMISSION_DENIED` | 说明待办查询权限不足 |
| `DETAIL_PERMISSION_DENIED` | 说明详情权限不足 |
| `ACTION_PERMISSION_DENIED` | 说明写操作权限不足 |
| `DETAIL_REQUEST_FAILED` | 停止该项，不猜测详情 |
| 规则字段缺失或类型错误 | 不自动执行，转人工确认 |
| 同优先级规则冲突 | 不自动执行，返回冲突规则 ID |

## 4. MCP 工具与白名单

`COMPOSITION.yaml` 中 OA_A 可见的工具为 14 个。代码和 config 的职责不同：

```text
COMPOSITION.yaml：角色能看到哪些工具
config.yaml outbound_bypass：哪些工具跳过通用 outbound 草稿卡
OA adapter：实际是否允许执行
```

当前需要默认执行、并加入 `outbound_bypass` 的短工具名：

```text
submit_request
reject_request
forward_request
auto_approve_by_rule
set_approval_rule
disable_approval_rule
delete_approval_rule
preview_approval_rule
simulate_approval_rule
```

`outbound_bypass` 不绕过 requestId 校验、OA 权限、preview token、规则冲突、限额或业务结果检查。

## 5. 自动审批规则创建流程

### 5.1 创建状态机

```text
用户自然语言
  -> RuleIntent
  -> 结构化条件与关键词草案
  -> schema v2
  -> preview_approval_rule / simulate_approval_rule
  -> preview_token
  -> 用户明确确认
  -> set_approval_rule
  -> 保存并启用规则
```

没有预览令牌、令牌过期、规则内容在预览后变化、令牌重复使用或 scope 不一致时，`set_approval_rule` 会拒绝保存。

scope 由 OA 实例、会话和 sender 组成，防止其他会话复用令牌。

### 5.2 RuleIntent

RuleIntent 只接受明确的规则管理语句，例如：

```text
创建自动审批规则，流程为报销，金额不超过5000
创建自动审批规则，申请说明包含客户现场
创建自动审批规则，关键词包含出差、上海
创建自动审批规则，任一关键词为上海、杭州，排除关键词超标
```

模糊表达不会直接生成可执行规则。缺少明确条件时只能继续澄清或生成不可保存草案。

### 5.3 schema v2

示例：

```json
{
  "schema_version": 2,
  "enabled": true,
  "priority": 100,
  "action": {
    "type": "approve",
    "auto_execute": true
  },
  "conditions": {
    "all": [
      {
        "field": "request.amount",
        "operator": "lte",
        "value": 5000,
        "currency": "CNY"
      },
      {
        "field": "applicant.department",
        "operator": "eq",
        "value": "财务部"
      }
    ]
  },
  "keywords": {
    "all": ["出差"],
    "any": ["上海", "杭州"],
    "none": ["超标", "补贴"]
  },
  "keyword_scope": [
    "request.title",
    "request.description",
    "request.form_text",
    "request.project"
  ],
  "limits": {
    "max_per_day": 20,
    "max_amount_per_day": 50000,
    "expires_at": "2026-12-31T23:59:59+08:00"
  },
  "unknown_policy": "require_human",
  "conflict_policy": "require_human"
}
```

自动执行只允许 `action.type = approve`。拒绝和转发规则不能自动执行。

## 6. 匹配算法

### 6.1 结构化条件

允许字段：

```text
workflow.id / workflow.name
applicant.id / applicant.name / applicant.department
request.amount / request.currency / request.project / request.cost_center
request.created_at / request.tags
attachment.count / risk.level
```

允许操作符：

```text
eq, neq, in, not_in, contains, starts_with,
gt, gte, lt, lte, between, exists
```

字段白名单以外的路径、任意 JSONPath、正则、脚本和模型表达式都不允许。

### 6.2 关键词匹配

关键词匹配是确定性算法。文本会先按以下方式归一：

```text
Unicode NFKC
大小写统一
全角半角统一
标点与空白统一
```

关键词语义：

| 组 | 规则 |
|---|---|
| `all` | 每个关键词都必须出现 |
| `any` | 至少一个关键词出现 |
| `none` | 任一关键词出现即不命中 |

结果只能为：

```text
MATCH：关键词条件满足
NO_MATCH：关键词明确不满足
UNKNOWN：匹配文本缺失或不可用
```

`UNKNOWN` 必须转人工确认。

### 6.3 大模型的限定职责

大模型可以：

```text
从用户自然语言提取候选关键词
建议关键词作用字段
生成可审阅的规则草案
解释规则为什么可能命中
```

大模型不能：

```text
最终授予自动审批
修改金额、币种或身份字段
绕过缺失字段
修改规则有效期或限额
将 UNKNOWN 当作 MATCH
临时发明同义词作为授权依据
```

最终自动审批使用确定性结构化 matcher 和确定性 keyword matcher。模型候选内容必须先由用户确认并写入规则。

### 6.4 最终自动审批判定

```text
规则已启用
AND action = approve + auto_execute
AND 未过期
AND 当前 requestId 仍在待办
AND 详情查询成功
AND 所有结构化条件满足
AND 关键词结果 = MATCH
AND 无异常、材料缺失或风险标记
AND 未超每日数量或金额限额
AND 无同优先级冲突规则
AND OA submitRequest 返回业务成功
```

任一条件失败、无法识别或冲突时：

```text
不自动审批，返回跳过原因，等待人工确认。
```

## 7. 规则预览、模拟与审计

`preview_approval_rule` 对单个当前待办返回：

```text
结构化条件结果
关键词结果
命中、缺失和禁止关键词
是否匹配及原因
```

`simulate_approval_rule` 最多检查 50 条当前待办，返回命中项、未命中项、原因和预览令牌。

规则保存后记录 revision；自动批准记录 rule_id、执行时间和金额，供每日限额与审计使用。

## 8. 安全原则摘要

```text
历史偏好不是授权
关键词不确定不是命中
预览不是确认
HTTP 成功不是业务成功
旧编号不是当前待办
模型建议不是执行许可
```
