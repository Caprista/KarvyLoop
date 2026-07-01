"""test_self_create_in_direct_chat — §15.5:直接聊天路径也挂 create_atom(Hardy)。

诊断(Hardy 报"role 都没这个 atom"):create_atom 是运行时**工具**(make_self_create_tool),不写进
role.atom_ids → 角色面板永远不显示它(不是 bug)。真问题:主驱动路径 drive_in_tui 之前**没传**
atom_registry → 直接私聊角色时根本没挂。这里锁:① 传了 atom_registry/role_registry/self_create_role
就透传进 forge 工厂 + 给一个 minted list;② drive 崩了 → 撤掉本次自造的孤儿 atom(0 引用安全);
③ 不传(其它路径)= 0 回归。
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
    def __init__(self, *, raise_after_mint=False):
        self.raise_after_mint = raise_after_mint

    def drive(self, intent, *, slow_brain=None, ctx=None, scope=None, fresh=False):
        if self.raise_after_mint:
            raise RuntimeError("boom mid-drive")
        return _res("ok")

    def background_review(self):
        pass


def test_create_atom_params_flow_to_forge_factory(monkeypatch):
    captured = {}

    def fake_factory(**kw):
        captured.update(kw)
        return lambda intent, *, ctx=None: ("x", None)

    monkeypatch.setattr(bridge, "forge_slow_brain_factory", fake_factory)
    sentinel_areg, sentinel_rreg = object(), object()
    asyncio.run(bridge.drive_in_tui(
        "hi", _ML(), token=1, sandbox=2, gateway=3, workspace_root="/tmp",
        atom_registry=sentinel_areg, role_registry=sentinel_rreg, self_create_role="designer"))
    assert captured.get("atom_registry") is sentinel_areg     # 真透传进工厂(=工厂会挂 create_atom)
    assert captured.get("role_registry") is sentinel_rreg
    assert captured.get("self_create_role") == "designer"     # 归属当前角色
    assert isinstance(captured.get("self_create_minted"), list)  # 给了收集 minted 的 list


def test_no_atom_registry_zero_regression(monkeypatch):
    """其它路径(workflow/圆桌/定时…)不传 → 工厂收到 atom_registry=None(不挂,0 回归)。"""
    captured = {}
    monkeypatch.setattr(bridge, "forge_slow_brain_factory",
                        lambda **kw: (captured.update(kw) or (lambda intent, *, ctx=None: ("x", None))))
    asyncio.run(bridge.drive_in_tui("hi", _ML(), token=1, sandbox=2, gateway=3, workspace_root="/tmp"))
    assert captured.get("atom_registry") is None
    assert captured.get("self_create_role") == ""


def test_drive_crash_reverts_minted_orphans(monkeypatch):
    """drive 崩了 → 本次自造的孤儿 atom 被撤(sediment approved=False);成功路径不撤。"""
    def fake_factory(**kw):
        kw["self_create_minted"].append("atom_orphan_1")   # 模拟 create_atom 工具造了一个
        return lambda intent, *, ctx=None: ("x", None)

    monkeypatch.setattr(bridge, "forge_slow_brain_factory", fake_factory)
    reverted = []
    import karvyloop.atoms.self_create as sc
    monkeypatch.setattr(sc, "sediment_self_created",
                        lambda aid, *, approved, atom_registry=None, role_registry=None, role_id=None:
                        reverted.append((aid, approved)))
    out = asyncio.run(bridge.drive_in_tui(
        "hi", _ML(raise_after_mint=True), token=1, sandbox=2, gateway=3, workspace_root="/tmp",
        atom_registry=object(), self_create_role="designer"))
    assert out.error                                  # drive 确实崩了
    assert ("atom_orphan_1", False) in reverted       # 孤儿被撤(approved=False)
