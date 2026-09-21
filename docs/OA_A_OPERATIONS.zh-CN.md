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

`COMPOSITION.yaml` 只暴露当前业务需要的工具。代码中仍保留旧版预览/模拟工具，供历史规则兼容和测试使用，但不再放入 OA_A 的主流程或角色白名单。

角色主流程工具：

```text
get_doing_workflow_request_list
get_workflow_request
get_workflow_request_summary
get_request_operator_info
get_approval_habits
list_approval_rules
set_approval_rule
disable_approval_rule
delete_approval_rule
submit_request
reject_request
forward_request
auto_approve_by_rule
```

以下工具不再由 OA_A 主动调用：

```text
preview_approval_rule
simulate_approval_rule
```

`outbound_bypass` 只影响通用 outbound 草稿卡，不绕过 requestId 校验、OA 权限、规则状态、异常检查或业务结果检查。

## 5. 自动审批规则创建、停用与删除

### 5.1 创建或更新

```text
用户描述规则
  -> Agent 整理 name / condition / examples
  -> 向用户展示规则草案
  -> 用户明确确认
  -> set_approval_rule
  -> 保存为 schema_version = 3
```

自然语言规则的最小结构：

```json
{
  "name": "小额差旅自动批准",
  "condition": "申请金额不超过500元，属于正常出差，且没有明显异常",
  "examples": [
    {"summary": "客户拜访，金额 320 元"}
  ],
  "action": "approve",
  "enabled": true
}
```

创建规则时不再要求用户填写字段路径、操作符、关键词权重、优先级或预览令牌。`condition` 必须是可读的完整条件描述；无法表达清楚时先澄清，不保存为自动规则。

### 5.2 停用与删除

- `disable_approval_rule(rule_id)`：保留规则和历史执行记录，仅设置 `enabled = false`。
- `delete_approval_rule(rule_id)`：用户明确指定规则后删除规则记录；不修改历史审批偏好。
- 停用或删除后，自动审批不得继续使用该规则。

### 5.3 规则判断

详情或摘要接口返回当前启用的 `approval_rule_candidates`，每项包含规则名称、自然语言条件和参考示例。Agent 必须结合真实审批详情逐条判断：

```text
MATCH      条件全部明确满足
NO_MATCH   条件明确不满足
UNCERTAIN  信息缺失、存在异常、语义无法确认或规则之间冲突
```

规则候选仅是判断参考，不是后端已经确认的命中结果。

## 6. 自动审批执行边界

调用：

```text
auto_approve_by_rule(request_id, rule_id,
                     decision="MATCH",
                     match_reason="明确说明命中的审批事实")
```

自然语言规则分支只有同时满足以下条件才继续：

```text
规则存在、已启用且 action = approve
AND Agent 明确提交 decision = MATCH
AND match_reason 非空
AND requestId 仍在当前待办
AND 详情接口业务成功
AND 详情没有材料缺失或明显异常
AND submitRequest 返回 OA 业务成功
AND 刷新待办后确认 requestId 已离开待办
```

`NO_MATCH`、`UNCERTAIN`、信息不足、规则停用、规则冲突、详情异常或提交结果无法确认时，不自动审批，返回原因并交用户确认。

旧 schema-v2 的确定性匹配代码暂时仅用于兼容历史规则，不是新规则的创建格式，也不是自然语言规则的判断依据。

## 7. 大模型与历史偏好的边界

大模型可以：

```text
整理用户的自然语言规则
对照审批详情和规则条件给出三态判断
提供简短、可追溯的命中理由
```

大模型不能：

```text
把 NO_MATCH 或 UNCERTAIN 当作 MATCH
在审批详情缺失时自行补全事实
修改规则条件、启用状态或规则 ID
把历史审批偏好当作自动审批授权
声称后端已经验证了自然语言语义
```

`get_approval_habits` 仅提供历史参考，不改变审批权限和自动执行条件。

## 8. 兼容工具说明

`preview_approval_rule` 和 `simulate_approval_rule` 保留在 adapter 中，用于旧 schema-v2 规则兼容和回归测试；它们不参与 schema-v3 自然语言规则流程。后续确认没有旧规则依赖后，再删除实现和相关测试。

规则执行记录保存 `request_id`、金额、命中理由、规则 ID 和时间，用于审计；执行记录不代表自然语言命中已由后端独立验证。

## 9. 安全原则摘要

```text
历史偏好不是授权
关键词不确定不是命中
预览不是确认
HTTP 成功不是业务成功
旧编号不是当前待办
模型建议不是执行许可
```
