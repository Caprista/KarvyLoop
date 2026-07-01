"""test_drive_empty_fallback — EVE④/多渠道:drive 绝不静默空白(成功空→重试→兜底).

多渠道并发(网页+语音共用一把 key)会把响应截成空。放在 drive_in_tui 渠道共同边界:
成功但正文空 → 重试一次 → 仍空给友好兜底,**绝不返回空白**(尤其语音不能没声音)。
"""
from __future__ import annotations

import asyncio
import types

import karvyloop.workbench.main_loop_bridge as bridge


def _res(text: str):
    return types.SimpleNamespace(
        brain=types.SimpleNamespace(value="slow"), text=text, skill_name="",
        fast_brain_hit=False, crystallized=False, task_id="t", ctx_dependent=False)


class _ML:
    def __init__(self, texts):
        self.texts = list(texts)
        self.calls = 0

    def drive(self, intent, *, slow_brain=None, ctx=None, scope=None, fresh=False):
        i = self.calls
        self.calls += 1
        return _res(self.texts[min(i, len(self.texts) - 1)])

    def background_review(self):
        pass


def _run(ml, monkeypatch):
    monkeypatch.setattr(bridge, "forge_slow_brain_factory",
                        lambda **kw: (lambda intent, *, ctx=None: ("x", None)))
    return asyncio.run(bridge.drive_in_tui("hi", ml, token=1, sandbox=2, gateway=3,
                                           workspace_root="/tmp"))


def test_empty_then_retry_succeeds(monkeypatch):
    ml = _ML(["", "真回复"])           # 第一次空、重试拿到非空
    out = _run(ml, monkeypatch)
    assert out.text == "真回复" and ml.calls == 2


def test_always_empty_gives_fallback_never_blank(monkeypatch):
    from karvyloop.i18n import t
    ml = _ML([""])                     # 一直空 → 重试一次仍空 → 友好兜底
    out = _run(ml, monkeypatch)
    assert out.text.strip(), "正文为空 —— 违反'绝不静默空白'"
    assert out.text == t("chat.empty_retry_fallback")
    assert ml.calls == 2               # 重试过恰好一次,不无限重试


def test_nonempty_no_retry_zero_regression(monkeypatch):
    ml = _ML(["一次就好"])
    out = _run(ml, monkeypatch)
    assert out.text == "一次就好" and ml.calls == 1   # 非空不重试(0 回归,不浪费 token)
