"""test_direct_role_chat — 角色面板点角色即聊(Hardy:不必先加进业务域,l0/personal scope 直聊)。

设计:l0 直聊某角色 = (l0, agent, <role_id>) peer。用**该角色**的人格(通用/镜像认知层),
**不挂任何业务域 value.md/deontic 治理**,不做域专属角色经验沉淀(experience.py 对无域返 False)。
小卡不路由/委派/截胡(那个角色自己答);回复方署名是角色不是小卡。

AC:
- AC1: is_direct_role_peer 判据(l0+agent+agent_id=True;小卡 observer / 群 / 业务域 = False)
- AC2: _persona_for_current_peer 在 l0 直聊角色 → 该角色人格(非小卡人格)
- AC3: speaker_display 在 l0 直聊角色 → 角色显示名(非空)
- AC4: maybe_route_to_role 在 l0 直聊角色 → None(不路由,角色自己答)
- AC5: /api/intent 真路径 —— l0 直聊无域角色 → drive(非路由提案)且喂该角色 persona,scope=user
- AC6: /api/peer/switch 能切到 l0 直聊角色线(不必先建业务域)
- AC7: 前端接线(直聊按钮 + directChatRole + i18n en/zh parity)
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.cognition.conversation import ConversationManager, ConversationStore  # noqa: E402
from karvyloop.domain.registry import Address  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402
from karvyloop.karvy.capability import is_direct_role_peer  # noqa: E402


# ---- fake role registry(duck-type;不落地 materialized 目录 → 走轻量角色人格回退)----


class _FakeRoleView:
    def __init__(self, role_id: str, nickname: str = "", title: str = "") -> None:
        self.id = role_id
        self.identity = f"{role_id} 的身份"
        self.atom_ids: list = []
        self.skill_ids: list = []
        self.model = ""
        self.nickname = nickname
        self.title = title
        self.path = pathlib.Path("/nonexistent")  # 无 COMPOSITION.yaml → paradigm 编译返 None → 回退

    def display_name(self) -> str:
        name = self.nickname or self.id
        return f"{name}({self.title})" if self.title else name


class _FakeRoleReg:
    def __init__(self, roles: list) -> None:
        self._roles = {r.id: r for r in roles}

    def get(self, role_id: str):
        return self._roles.get(role_id)

    def list_all(self):
        return list(self._roles.values())


def _app_with_lone_role(tmp_path):
    """一个不在任何业务域的独立角色 '文案' + 私聊小卡起手的对话编排器。"""
    mgr = ConversationManager(ConversationStore(tmp_path / "conv"))
    mgr.start()  # 默认私聊小卡(l0, observer, karvy)
    a = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    a.state.conversation_manager = mgr
    a.state.role_registry = _FakeRoleReg([_FakeRoleView("文案", nickname="小文", title="文案")])
    return a, mgr


# ---- AC1: is_direct_role_peer 判据 ----


def test_is_direct_role_peer_classification():
    assert is_direct_role_peer(Address(domain_id="l0", role="agent", agent_id="文案")) is True
    # 小卡本人(observer)不是"直聊角色"
    assert is_direct_role_peer(Address(domain_id="l0", role="observer", agent_id="karvy")) is False
    # 群场不是
    assert is_direct_role_peer(Address(domain_id="l0", role="group", agent_id="")) is False
    # 业务域直聊走既有 per-role(带域治理),不归这条
    assert is_direct_role_peer(Address(domain_id="dom-x", role="agent", agent_id="设计师")) is False
    # 无 agent_id 不算
    assert is_direct_role_peer(Address(domain_id="l0", role="agent", agent_id="")) is False
    assert is_direct_role_peer(None) is False


# ---- AC2: _persona_for_current_peer → 该角色人格(非小卡)----


def test_persona_is_the_role_not_karvy(tmp_path):
    from karvyloop.console.routes import _persona_for_current_peer

    a, mgr = _app_with_lone_role(tmp_path)
    mgr.set_peer(Address(domain_id="l0", role="agent", agent_id="文案"))
    persona = _persona_for_current_peer(a, mgr, "/tmp", intent="帮我写段文案")
    assert persona is not None
    blob = " ".join(getattr(persona, "static", []) or [])
    assert "文案" in blob, "l0 直聊应是那个角色的人格"
    assert "卡皮巴拉" not in blob and "小卡" not in blob, "不该退化成小卡人格"


def test_persona_karvy_unchanged_at_l0(tmp_path):
    """回归:真私聊小卡(l0, observer, karvy)仍是小卡人格(没被新分支误伤)。"""
    from karvyloop.console.routes import _persona_for_current_peer

    a, mgr = _app_with_lone_role(tmp_path)
    mgr.set_peer(Address(domain_id="l0", role="observer", agent_id="karvy"))
    persona = _persona_for_current_peer(a, mgr, "/tmp", intent="你好")
    blob = " ".join(getattr(persona, "static", []) or [])
    assert "文案" not in blob  # 不是角色人格


# ---- AC3: speaker_display → 角色显示名 ----


def test_speaker_display_is_role(tmp_path):
    from karvyloop.console.routes import speaker_display

    a, mgr = _app_with_lone_role(tmp_path)
    mgr.set_peer(Address(domain_id="l0", role="agent", agent_id="文案"))
    who = speaker_display(a, mgr)
    assert who == "小文(文案)", f"应显示角色花名(职务),得到 {who!r}"
    # 回归:真小卡仍返 ""(前端本地化成小卡)
    mgr.set_peer(Address(domain_id="l0", role="observer", agent_id="karvy"))
    assert speaker_display(a, mgr) == ""


# ---- AC4: maybe_route_to_role → None(不路由,角色自己答)----


@pytest.mark.asyncio
async def test_no_route_for_direct_role(tmp_path):
    from karvyloop.console.routes import maybe_route_to_role

    a, mgr = _app_with_lone_role(tmp_path)
    mgr.set_peer(Address(domain_id="l0", role="agent", agent_id="文案"))
    # 用一句带"让/找"字样的意图(私聊小卡时会被判 route)——直聊角色时必须 None(不截胡)。
    routed = await maybe_route_to_role(a, mgr, "让我们把这段改得更活泼")
    assert routed is None, "l0 直聊某角色不该被小卡路由/委派"


# ---- AC5: /api/intent 真路径 —— 直聊无域角色 drive 且喂该角色 persona,scope=user ----


def test_intent_drives_as_role_not_routed(tmp_path, monkeypatch):
    from karvyloop.runtime.main_loop import Brain
    import karvyloop.console.routes as routes_mod

    seen = {}

    async def fake_drive(intent, ml, *, ctx=None, governance="", persona=None, scope="", **kw):
        from karvyloop.workbench.main_loop_bridge import DriveOutcome
        seen["persona"] = persona
        seen["scope"] = scope
        seen["governance"] = governance
        return DriveOutcome(intent=intent, brain=Brain.SLOW, text="活泼版文案来了",
                            skill_name="", fast_brain_hit=False, crystallized=False, task_id="t")

    monkeypatch.setattr(routes_mod, "drive_in_tui", fake_drive)

    mgr = ConversationManager(ConversationStore(tmp_path / "conv"))
    mgr.set_peer(Address(domain_id="l0", role="agent", agent_id="文案"))
    a = build_console_app(workbench=WorkbenchObserver(), main_loop=object())
    a.state.conversation_manager = mgr
    a.state.role_registry = _FakeRoleReg([_FakeRoleView("文案", nickname="小文", title="文案")])
    client = TestClient(a)
    r = client.post("/api/intent", json={"intent": "让这段更活泼"})
    body = r.json()
    # 真 drive 了(不是路由提案:routed 提案会带 route_to_role/text 但不 drive)
    assert seen.get("persona") is not None, "应喂角色 persona 真 drive,而非被路由拦下"
    blob = " ".join(getattr(seen["persona"], "static", []) or [])
    assert "文案" in blob and "小卡" not in blob
    # l0/personal scope → 个人技能(不污染业务域技能)
    assert seen["scope"] == "user"
    # l0 无域 → 不注入任何业务域治理
    assert "value.md" not in (seen.get("governance") or "")
    # 回复署名是角色不是小卡
    assert body.get("speaker") == "小文(文案)"


# ---- AC6: /api/peer/switch 切到 l0 直聊角色线(不必先建业务域)----


def test_switch_to_lone_role_line(tmp_path):
    a, mgr = _app_with_lone_role(tmp_path)
    client = TestClient(a)
    r = client.post("/api/peer/switch", json={"domain_id": "l0", "role": "agent", "agent_id": "文案"})
    assert r.status_code == 200 and r.json()["ok"] is True
    assert mgr.current_peer().domain_id == "l0"
    assert mgr.current_peer().role == "agent"
    assert mgr.current_peer().agent_id == "文案"


# ---- AC7: 前端接线 ----


def test_frontend_direct_chat_wired():
    static = ROOT / "karvyloop" / "console" / "static"
    roles_js = (static / "roles_panel.js").read_text(encoding="utf-8")
    assert "_directChatRole" in roles_js, "roles_panel.js 缺直聊触发"
    assert 'text: t("role.direct_chat")' in roles_js, "角色卡缺「直聊」按钮"
    app_js = (static / "app.js").read_text(encoding="utf-8")
    assert "function directChatRole" in app_js, "app.js 缺 directChatRole"
    # 切到 l0 直聊角色线(domain_id l0 + role agent + agent_id)
    assert 'domain_id: "l0", role: "agent", agent_id: roleId' in app_js
    assert "window.KarvyChat" in app_js, "缺全局兜底钩子(nav 无参调用回退)"
    # i18n en+zh parity
    i18n = (static / "i18n.js").read_text(encoding="utf-8")
    assert i18n.count('"role.direct_chat"') == 2, "role.direct_chat 不是 en+zh 各一份"


# ---- AC8: 小卡专属行为不越到直聊角色场(共创递口 / 斜杠 ops)----


def test_cocreation_not_active_for_direct_role(tmp_path):
    """建 agent/域=小卡 K1 编排活;l0 直聊某角色不进共创态(_conv_key 排除直聊角色)。"""
    from karvyloop.karvy.cocreation import _conv_key

    a, mgr = _app_with_lone_role(tmp_path)
    # 真小卡私聊 → 有 conv_key(共创可粘)
    mgr.set_peer(Address(domain_id="l0", role="observer", agent_id="karvy"))
    assert _conv_key(mgr) != "", "真私聊小卡应能进共创(回归不破)"
    # 直聊角色 → 空 key(共创递口/共创态都不触发)
    mgr.set_peer(Address(domain_id="l0", role="agent", agent_id="文案"))
    assert _conv_key(mgr) == "", "l0 直聊某角色不该进小卡的共创态"


def test_slash_not_intercepted_for_direct_role(tmp_path):
    """/version 等 ops 斜杠:私聊小卡拦(SLASH),l0 直聊某角色不拦(交给角色 drive)。"""
    from karvyloop.runtime.main_loop import Brain
    import karvyloop.console.routes as routes_mod

    async def fake_drive(intent, ml, *, ctx=None, governance="", persona=None, scope="", **kw):
        from karvyloop.workbench.main_loop_bridge import DriveOutcome
        return DriveOutcome(intent=intent, brain=Brain.SLOW, text="(role handled)",
                            skill_name="", fast_brain_hit=False, crystallized=False, task_id="t")

    monkeypatch = pytest.MonkeyPatch()
    monkeypatch.setattr(routes_mod, "drive_in_tui", fake_drive)
    try:
        mgr = ConversationManager(ConversationStore(tmp_path / "conv"))
        mgr.set_peer(Address(domain_id="l0", role="agent", agent_id="文案"))
        a = build_console_app(workbench=WorkbenchObserver(), main_loop=object())
        a.state.conversation_manager = mgr
        a.state.role_registry = _FakeRoleReg([_FakeRoleView("文案", nickname="小文", title="文案")])
        client = TestClient(a)
        r = client.post("/api/intent", json={"intent": "/version"})
        # 不是 SLASH:交给角色 drive(SLASH 会 brain==SLASH + 不 drive)
        assert r.json().get("brain") != "SLASH", "直聊角色时斜杠不该被小卡 ops 截胡"
    finally:
        monkeypatch.undo()


def test_frontend_ts_source_direct_chat_wired():
    """真相源 TS 与构建产物一致(两边都改了)。"""
    src = ROOT / "karvyloop" / "console" / "frontend" / "src"
    roles_ts = (src / "roles_panel.ts").read_text(encoding="utf-8")
    assert "_directChatRole" in roles_ts and 't("role.direct_chat")' in roles_ts
    i18n_ts = (src / "i18n.ts").read_text(encoding="utf-8")
    assert i18n_ts.count('"role.direct_chat"') == 2
