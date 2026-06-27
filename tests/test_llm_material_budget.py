"""context engineering 基建化收尾(第一问):直连 gateway 的内容调用,材料必过 token 天花板。

锁:一段病态超大材料喂给 compile_material,真正发给 gateway 的内容被截到 LLM_MATERIAL_TOKENS 内。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.context.budget import LLM_MATERIAL_TOKENS, clip_to_tokens, count_tokens_text  # noqa: E402


class _StubGateway:
    """捕获真正发出去的 messages[0].content;complete 是个不产出的 async generator。"""
    def __init__(self):
        self.sent = None

    def resolve_model(self, scope):
        return "stub-model"

    async def complete(self, messages, tools, ref, system=None):
        self.sent = messages[0]["content"]
        return
        yield  # pragma: no cover  (使其成为 async generator)


def test_clip_to_tokens_at_material_ceiling():
    huge = "字" * (LLM_MATERIAL_TOKENS * 4 * 3)         # 远超预算
    out, truncated = clip_to_tokens(huge, LLM_MATERIAL_TOKENS)
    assert truncated and count_tokens_text(out) <= LLM_MATERIAL_TOKENS + 2


def test_compile_material_budgets_before_gateway():
    from karvyloop.cognition.ingest import compile_material
    gw = _StubGateway()
    huge = "事实一二三 " * (LLM_MATERIAL_TOKENS * 2)     # 病态大材料
    asyncio.run(compile_material(huge, gateway=gw))
    assert gw.sent is not None
    assert count_tokens_text(gw.sent) <= LLM_MATERIAL_TOKENS + 2   # 发出去的真被基建截了


def test_fuzzy_dispatch_budgets_roster():
    from karvyloop.karvy.fuzzy_dispatch import decompose_dispatch
    gw = _StubGateway()
    roster = [{"domain_name": "域%d" % i, "members": [{"name": "n" * 200}]}
              for i in range(2000)]                       # 巨大 roster
    asyncio.run(decompose_dispatch("去找人分析", roster=roster, gateway=gw))
    assert gw.sent is not None
    assert count_tokens_text(gw.sent) <= LLM_MATERIAL_TOKENS + 2
