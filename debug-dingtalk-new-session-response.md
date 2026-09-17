# Debug Session: DingTalk New Session Response Failure

Status: [OPEN]
Session ID: `dingtalk-new-session-response`

## Symptom

After DingTalk `/new` successfully opens a new conversation window, the Agent replies `这次没接住,能再说一遍吗？` to the first subsequent user message.

## Hypotheses

1. `/new` creates a new conversation but the new conversation object is not returned or used by the DingTalk request path.
2. The new conversation has an invalid or incomplete domain/peer/agent identifier, causing routing or context resolution failure.
3. The next message still uses a stale `conv` reference or an incorrect conversation ID.
4. The fallback response masks an exception in model invocation, tool initialization, or conversation persistence.
5. The remote deployment does not contain the local `/new` fix.

## Evidence Log

> 当前复现环境是用户本地 KarvyLoop Console + 本地钉钉 Stream 通道；远程主机证据与本次故障无关，仅保留作历史记录。

### Local evidence

- `/new` 后本地创建了新 conversation `ab95e25c5ae54a35`；旧 conversation 为 `eca40307fecc4414`。
- 新旧 conversation 的 `domain_id`、`role`、`agent_id` 完全一致；普通消息“你好”和“为什么没有接住”均写入新 conversation。
- 新 conversation 的首条普通消息连续两次记录空响应兜底，说明故障位于 MainLoop/SlowBrain 实际驱动阶段，不是 `/new` 路由或持久化目标错误。
- `trace.sqlite` 与 `trace_buffer.db` 在失败时间附近没有可关联的 trace；`tokens.db` 有模型调用，但缺少可关联的 task/run 标识，不能单独判定 provider 事件序列。
- 当前代码链路确认：Forge 只将 `TextEvent` 累加为正文；仅有 `ThinkingEvent`、工具事件或 `Done` 时，可能出现 `terminal=completed`、`run.success=True` 但 `text=""`，随后触发公共空响应重试。
- 已加入仅用于诊断的 WARNING instrumentation：记录 MainLoop task、terminal、run success、run output、tool call 数，以及桥接层的 context/事件类型。未改变空响应业务行为。

### Remote evidence (not applicable to current local reproduction)

- Remote process `/Users/enjoy/KarvyLoop/.venv/bin/python3 -m karvyloop console` was running commit `1293b11`, not local fix `09ea425`.
- Remote source lacked the `/new` handler and remote logs contained the shared empty-response fallback. This does not explain the current local reproduction.

## Current assessment

- Confirmed: `/new` creates and routes to a fresh local conversation.
- Confirmed: the first post-`/new` ordinary message reaches the new conversation.
- Latest local restart: `/new` 后发送“你好”已正常回复 OA 审批助手正文，空响应暂未复现；因此空正文诊断日志按设计没有输出。
- 新增复现日志确认：两次调用均为 `events=['terminal']`、`run_output=None`、`tool_calls=0`、`terminal=completed`、`run_success=True`；即 Provider/adapter 没有产生文本、思考或工具事件，却被执行器当作成功完成。
- 根因确认：`atoms/executor.py` 在 `not assistant_tool_uses` 分支无条件设置 `Terminal.COMPLETED`，空模型输出因此伪装成成功。
- 已实施最小修复：只有 `assistant_text.strip()` 非空时才判 `COMPLETED`；无文本无工具时判 `INFRA_DEAD`，`AtomRun.success=False`，并沿用上层的非正常终止提示。
- 已增加 executor 回归测试，覆盖仅有 `Done` 的空模型输出。

## Change Log

- Initial investigation: no business logic changed.
- Added local-only diagnostic logging in `main_loop_bridge.py` and `runtime/main_loop.py`; no behavior change.
