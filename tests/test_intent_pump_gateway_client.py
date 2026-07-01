"""test_intent_pump_gateway_client — 主动建议(预判象限)的 LLM client 走已接好的 gateway。

病根(2026-06-20 起服务抓到):intent_pump 旧走 `karvyloop.llm.config`(认 `llm.*` schema),
真实 config + 主运行时走 gateway 的 `models.providers` → schema 对不上 → 主动建议永空转。
修法:复用主 loop 的 gateway(单一真理来源)。本测锁:

- AC1: GatewayLlmClient.chat()(同步)能把异步流式 gateway 收成一段文本
- AC2: _try_build_llm_client 传了 gateway → 优先用 GatewayLlmClient(不碰旧 llm.* loader)
- AC3: 没传 gateway → 回退旧 loader 路径(向后兼容);坏 config 仍优雅返 None
- AC4: 满足 LlmClientProtocol(BehaviorPatternAnalyzer 能直接吃)
"""
from __future__ import annotations

from karvyloop.gateway.events import TextDelta
from karvyloop.karvy.fastbrain.trace_habit import GatewayLlmClient
from karvyloop.cli.intent_pump import _try_build_llm_client


class _FakeGateway:
    """duck-type 的 gateway:resolve_model + 异步流式 complete(发 TextDelta)。"""

    def __init__(self) -> None:
        self.seen_ref = None
        self.seen_msgs = None

    def resolve_model(self, scope):
        # 把 atom_model 原样当 ref 回(测里只验透传)
        return getattr(scope, "atom_model", None) or "minimax/MiniMax-M3"

    async def complete(self, messages, tools, model_ref, *, system=None):
        self.seen_ref = model_ref
        self.seen_msgs = messages
        for chunk in ("PRE", "DICT", "ED"):
            yield TextDelta(text=chunk)


# ---- AC1: 同步 chat 收异步流 ----
def test_gateway_client_chat_collects_stream():
    gw = _FakeGateway()
    client = GatewayLlmClient(gw, default_model_ref="minimax/MiniMax-M3")
    out = client.chat("minimax/MiniMax-M3", [{"role": "user", "content": "hi"}])
    assert out == "PREDICTED"                       # 三段 TextDelta 拼起来
    assert gw.seen_ref == "minimax/MiniMax-M3"      # ref 透传
    assert gw.seen_msgs == [{"role": "user", "content": "hi"}]


# ---- AC2: 传 gateway → 优先 GatewayLlmClient ----
def test_try_build_prefers_gateway():
    gw = _FakeGateway()
    client = _try_build_llm_client(None, gateway=gw, model_ref="minimax/MiniMax-M3")
    assert isinstance(client, GatewayLlmClient)
    # 真能用(不碰旧 llm.* loader 即不会因 schema 不符而 None)
    assert client.chat("", [{"role": "user", "content": "x"}]) == "PREDICTED"


# ---- AC3: 没 gateway → 回退;坏 config 优雅 None ----
def test_no_gateway_falls_back_and_degrades(tmp_path):
    # 没 gateway + 指一个不存在/不合 schema 的 config → 旧 loader 抛 → 优雅 None(不崩)
    bad = tmp_path / "nope.yaml"
    client = _try_build_llm_client(bad, gateway=None)
    assert client is None


# ---- AC4: 结构上满足 LlmClientProtocol(analyzer 鸭子吃 .chat)----
def test_gateway_client_quacks_like_llm_protocol():
    gw = _FakeGateway()
    client = GatewayLlmClient(gw)
    assert callable(getattr(client, "chat", None))   # 有 chat 方法 = analyzer 能吃
    # default_model_ref 空 + 传空 model → resolve 仍给出兜底 ref,不崩
    assert client.chat("", [{"role": "user", "content": "x"}]) == "PREDICTED"
