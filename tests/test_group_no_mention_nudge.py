"""test_group_no_mention_nudge — 群里不 @ 任何人 → 没人回 + 小卡提醒(Hardy 群语义)。

锁:① 群场 + 空 mention → 出 nudge(no_mention_nudge 标志,不跑模型);
② 群场 + 有 @ → 不 nudge(交给正常 @ 路由);③ 私聊(role!=group)→ 不 nudge(走 route_to_role)。
"""
from __future__ import annotations

import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console.routes import group_no_mention_nudge  # noqa: E402


def _mgr(role, cur_title=""):
    peer = types.SimpleNamespace(role=role, domain_id="world", agent_id="")
    cur = types.SimpleNamespace(title=cur_title) if cur_title else None
    return types.SimpleNamespace(current_peer=lambda: peer, current=lambda: cur)


def test_group_no_mention_yields_nudge():
    n = group_no_mention_nudge(app=None, mgr=_mgr("group"), mention="")
    assert n is not None and n["no_mention_nudge"] is True
    assert n["speaker"] == "小卡" and n["text"] == ""   # 不跑模型,无正文


def test_group_with_mention_no_nudge():
    assert group_no_mention_nudge(app=None, mgr=_mgr("group"), mention="designer") is None


def test_roundtable_line_no_nudge():
    # 圆桌线(标题 🎡)挂在群 peer 下,但追问 = 继续圆桌 → 放行,不 nudge
    assert group_no_mention_nudge(app=None, mgr=_mgr("group", "🎡 登录页流程"), mention="") is None


def test_private_peer_no_nudge():
    # 私聊小卡(role 非 group)→ 不 nudge,留给 route_to_role
    assert group_no_mention_nudge(app=None, mgr=_mgr(""), mention="") is None


def test_no_peer_no_nudge():
    assert group_no_mention_nudge(app=None, mgr=types.SimpleNamespace(current_peer=lambda: None), mention="") is None
