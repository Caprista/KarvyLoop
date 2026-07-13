"""gateway/structured — 约束解码 / 结构化输出(底层能力探测 + schema 归一)。

**为什么**:业界 2026 生产默认是**双层校验**——底层用约束解码保证 provider 吐出的就是
schema-合法的 JSON(不是"求模型听话"),上层再做严格解析(宁空勿毒)兜第二道。本项目上层
早已有(parse_facts 等严校验),这里补底层:让**支持**约束解码的 provider 走原生结构化
输出通道,**不支持**的(如本地小端点)自动退回无约束 + 上层严校验兜底,绝不发坏请求。

语义("接新模型=配置不是代码"纪律,配置驱动 + 优雅降级):
- **能力探测** `supports_structured(model)`:
    - 模型条目 `compat.structured_output`(bool)= 显式覆盖(端点自报是否支持,配置说了算);
    - 未显式声明 → 按 api 方言内置默认:anthropic-messages / openai-completions 支持,其余不支持。
- **schema 归一** `normalize_json_schema(schema)`(仅 OpenAI 严格模式需要):
    OpenAI response_format=json_schema 的 strict:true 要求每个 object 都
    `additionalProperties:false` 且 `required` 覆盖所有 properties 键 —— 这里递归补全,
    **不改调用方原 dict**(caller-injected config 必须尊重:只在副本上补 provider 硬性要求的字段)。
- **都不支持** → 调用方(gateway.complete)记一次 warning + 退回无约束(上层严校验兜底),
  **绝不报错、绝不发坏请求**。

**只产请求体形状,绝不碰记账**:Usage 事件 / token 账本路径一字不动(咽喉纪律)。
"""
from __future__ import annotations

import copy
import logging
from typing import Any, Optional

log = logging.getLogger(__name__)

# 内置默认:哪些 api 方言原生支持约束解码 / 结构化输出(未在模型 compat 里显式声明时用)。
# anthropic-messages:强制 tool-use(tool_choice 锁定单工具,input_schema 即输出 schema)。
# openai-completions:response_format={type:json_schema, strict:true}。
# 其余(google/ollama/bedrock…)默认视为不支持 → 优雅退回无约束(上层严校验兜底)。
_STRUCTURED_APIS = frozenset({"anthropic-messages", "openai-completions"})


def supports_structured(model: Any) -> bool:
    """该模型是否支持约束解码 / 结构化输出。compat.structured_output 显式覆盖优先;
    未声明按 api 方言内置默认。**只读判定,不产参数、不发请求**(0 副作用)。"""
    compat = getattr(model, "compat", None) or {}
    if isinstance(compat, dict) and "structured_output" in compat:
        return bool(compat.get("structured_output"))
    return getattr(model, "api", "") in _STRUCTURED_APIS


def normalize_json_schema(schema: dict) -> dict:
    """递归补全 OpenAI 严格模式硬性要求的字段,返回**新副本**(不改调用方原 dict)。

    规则(对每个 `type:"object"` 节点):
      1) `additionalProperties:false`(严格模式禁止未声明键);
      2) `required` = 所有 properties 键(严格模式要求全部显式必填);
    数组的 `items`、properties 的每个子 schema 递归处理。非 object 节点原样带回。

    注意:这是 OpenAI json_schema strict 的协议硬性要求(不补 → 4xx),不是"偷偷改语义"——
    调用方传的字段一个不动,只在**副本**上补 provider 拒绝缺省的结构约束(caller-injected 尊重)。
    """
    return _normalize_node(copy.deepcopy(schema))


def _normalize_node(node: Any) -> Any:
    if not isinstance(node, dict):
        return node
    t = node.get("type")
    if t == "object":
        props = node.get("properties")
        if isinstance(props, dict):
            node["properties"] = {k: _normalize_node(v) for k, v in props.items()}
            node.setdefault("additionalProperties", False)
            # required 覆盖全部 properties 键(严格模式要求;调用方给的 required 会被并集覆盖)
            node["required"] = list(node["properties"].keys())
    elif t == "array":
        items = node.get("items")
        if items is not None:
            node["items"] = _normalize_node(items)
    # anyOf/oneOf/allOf 等组合子也递归(每个分支可能是 object)
    for comb in ("anyOf", "oneOf", "allOf"):
        if isinstance(node.get(comb), list):
            node[comb] = [_normalize_node(x) for x in node[comb]]
    return node


def openai_response_format(schema: dict, *, name: str = "structured_output") -> dict:
    """schema → OpenAI `response_format`(json_schema strict 模式)。schema 已归一。"""
    return {
        "type": "json_schema",
        "json_schema": {"name": name, "strict": True, "schema": normalize_json_schema(schema)},
    }


def anthropic_structured_tool(schema: dict, *, name: str = "structured_output") -> tuple[dict, dict]:
    """schema → (强制 tool, tool_choice)。业界做法:用一个"输出工具"承载 schema、
    tool_choice 锁定它 → 模型必须按 input_schema 产出参数 = 约束解码。

    返回 (tool_def, tool_choice);gateway/adapter 把 tool 并进 tools、tool_choice 进 body。
    schema **不归一**(Anthropic tool input_schema 不要求 additionalProperties/全 required,
    原样透传更尊重调用方)。
    """
    tool = {
        "name": name,
        "description": "Return the result strictly matching the provided schema.",
        "input_schema": schema,
    }
    return tool, {"type": "tool", "name": name}


async def harvest_structured(stream: Any) -> str:
    """收结构化输出流 → 原始文本(供上层严格解析;宁空勿毒纪律一字不动)。

    为什么必须有它(2026-07-13 j3 真模型逮到的缝):anthropic 方言的约束解码 =
    强制 tool-use(上方 anthropic_structured_tool),**JSON 走工具入参、不走正文**;
    只收 TextDelta 的调用方会把正身整个漏掉 → 空串 → 上层宁空勿毒返 [] → 静默零产出。
    有的端点对 tool_choice 时循时不循 → 同一调用时红时绿,更隐蔽。

    收法:TextDelta 累计正文;ToolUseStop.input 是约束解码的正身,**优先**(它才是
    schema-合法保证的那份;array schema 的入参可能被 adapter 解成 list,一并容忍)。
    调用方都是"tools=[] + 强制输出工具"的形状 → 流里唯一可能的工具就是
    structured_output,不需要按名过滤(带真工具的 executor 路径不用本函数)。
    只负责"别把正身丢了",不做校验(校验归上层 parse_*)。
    """
    import json
    text = ""
    payload: Any = None
    async for ev in stream:
        tn = type(ev).__name__
        if tn == "TextDelta":
            text += getattr(ev, "text", "") or ""
        elif tn == "ToolUseStop":
            inp = getattr(ev, "input", None)
            if inp:   # 空 dict/None = 没正身,别覆盖
                payload = inp
    if payload is not None:
        try:
            return json.dumps(payload, ensure_ascii=False)
        except (TypeError, ValueError):
            return text
    return text


__all__ = [
    "supports_structured",
    "normalize_json_schema",
    "openai_response_format",
    "anthropic_structured_tool",
    "harvest_structured",
]
