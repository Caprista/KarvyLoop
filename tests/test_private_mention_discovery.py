"""test_private_mention_discovery — docs/91 候选池·@可发现性:私聊里 @ 下拉补全。

U-03 私聊 @ 快通道早已落地(test_private_mention_fastlane.py),但用户得**手打全名**才触发,
没人知道能 @。本刀:roster 端点对小卡私聊场也返回名册(与 mention_fastlane **同一来源**
_roundtable_roster —— 两份名册=下拉选了快通道不认),前端复用群聊 @ 下拉在私聊也弹。

锁四件:
① 后端:小卡私聊场 → roster ok:True(域成员+独立角色,带 domain_name 消歧);**不并入
   外部公民**(快通道不认它们);业务域私聊/l0 直聊角色/知识线照旧 ok:False;
② 同源硬纪律:私聊 roster 里**每个** agent_id,拼成「@agent_id 正文」都被
   _resolve_private_mentions 命中(下拉选的名字必然快通道命中);
③ 群场零回归:群 roster 行为不变(含外部公民席);
④ 前端静态:私聊 @ 下拉接线在(_isKarvyPrivatePeer 门/纯文本插入/顶部时机教学行)+
   i18n mention.private_hint en+zh 双表齐(TS 源 + 构建产物;away bundle 一致性由
   test_away_bundle.py 守)。

群 @ 语义回归锚:chip 插入路径一字不动(test_mention_routing.py / 本文件 ④ 锁存在性)。
"""
from __future__ import annotations

import pathlib
import sys

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.conversation import (  # noqa: E402
    ConversationManager, ConversationStore, karvy_world_peer,
)
from karvyloop.cognition.knowledge_chat import knowledge_peer  # noqa: E402
from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.domain.registry import Address, BusinessDomainRegistry  # noqa: E402
from karvyloop.external_runtime.citizen import (  # noqa: E402
    ExternalCitizen, ExternalCitizenRegistry,
)
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402

STATIC = ROOT / "karvyloop" / "console" / "static"
FRONTEND_SRC = ROOT / "karvyloop" / "console" / "frontend" / "src"


@pytest.fixture
def setup(tmp_path):
    reg = BusinessDomainRegistry()
    mgr = ConversationManager(ConversationStore(tmp_path / "conv"), domain_registry=reg)
    mgr.start()
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.conversation_manager = mgr
    app.state.domain_registry = reg
    d1 = reg.create(name="装修", created_by="user:ch", value_md_raw="",
                    member_query="user:ch AND agent:设计师")
    d2 = reg.create(name="财务", created_by="user:ch", value_md_raw="",
                    member_query="user:ch AND agent:会计")
    return app, mgr, reg, d1, d2


# ---- ① 小卡私聊场 → 名册(跨域全员 + 独立角色,带 domain_name) ----

def test_private_roster_returns_members_with_domain_names(setup, tmp_path):
    app, mgr, reg, d1, d2 = setup
    from karvyloop.atoms.registry import AtomRegistry
    from karvyloop.roles.registry import RoleRegistry
    rreg = RoleRegistry(tmp_path / "roles", atom_registry=AtomRegistry())
    rreg.create("solo-advisor", identity="独立角色(不归任何域)")
    app.state.role_registry = rreg

    mgr.set_peer(karvy_world_peer())   # 私聊小卡(l0, observer, karvy)
    body = TestClient(app).get("/api/roundtable/roster").json()
    assert body["ok"] is True
    by_id = {m["agent_id"]: m for m in body["members"]}
    assert set(by_id) == {"设计师", "会计", "solo-advisor"}
    # 所属域名给到前端(跨域同名可分辨 —— 补快通道"同名取第一"的消歧盲区:从下拉就看得见域)
    assert by_id["设计师"]["domain_name"] == "装修"
    assert by_id["会计"]["domain_name"] == "财务"
    assert by_id["solo-advisor"]["domain_name"] == ""   # 独立角色无域(前端不挂域徽)


# ---- ② 同源硬纪律:下拉选的每个名字,快通道必命中 ----

def test_private_roster_every_entry_hits_fastlane_resolver(setup, tmp_path):
    """名册与 mention_fastlane 同源(_roundtable_roster)的**行为级**验证:私聊 roster 的
    每个 agent_id 拼「@agent_id 正文」都被 _resolve_private_mentions 精确命中。
    两份名册(另拼一份)= 下拉选了快通道不认 —— 这条测试就是那道闸。"""
    app, mgr, reg, d1, d2 = setup
    from karvyloop.atoms.registry import AtomRegistry
    from karvyloop.roles.registry import RoleRegistry
    rreg = RoleRegistry(tmp_path / "roles", atom_registry=AtomRegistry())
    rreg.create("solo-advisor", identity="独立角色")
    app.state.role_registry = rreg

    mgr.set_peer(karvy_world_peer())
    body = TestClient(app).get("/api/roundtable/roster").json()
    assert body["ok"] is True and body["members"]
    from karvyloop.console.routes import _resolve_private_mentions
    peer = mgr.current_peer()
    for m in body["members"]:
        hits = _resolve_private_mentions(app, peer, f"@{m['agent_id']} 出一版方案")
        assert [a.agent_id for a in hits] == [m["agent_id"]], \
            f"下拉项 {m['agent_id']} 没被快通道解析命中(名册不同源?)"


# ---- ① 续:外部公民不进私聊名册(快通道不认);群场照旧列(零回归) ----

def test_private_roster_excludes_external_citizens(setup):
    app, mgr, reg, d1, d2 = setup
    creg = ExternalCitizenRegistry()
    creg.add(ExternalCitizen(citizen_id="cc-helper", runtime_kind="raw_text_sidecar",
                             bin_path="ext", domain_id="", status="active", tier="guest"))
    app.state.citizen_registry = creg

    mgr.set_peer(karvy_world_peer())
    body = TestClient(app).get("/api/roundtable/roster").json()
    assert body["ok"] is True
    assert not any(m.get("is_external") for m in body["members"]), \
        "外部公民进了私聊名册 —— 下拉选它快通道不认(mention_fastlane 名册没有它)"
    # 群场零回归:外部公民仍列(圆桌客人席入口不动)
    mgr.set_peer(Address(domain_id=d1.id, role="group", agent_id=""))
    gbody = TestClient(app).get("/api/roundtable/roster").json()
    assert any(m.get("is_external") for m in gbody["members"])


# ---- ① 续:不该弹的私聊场照旧 ok:False ----

def test_roster_rejected_in_non_karvy_private_scenes(setup):
    app, mgr, reg, d1, d2 = setup
    client = TestClient(app)
    # 业务域私聊(单角色场):对面就一个角色,@ 没意义,快通道也不跑
    mgr.set_peer(Address(domain_id=d1.id, role="agent", agent_id="设计师"))
    assert client.get("/api/roundtable/roster").json()["ok"] is False
    # l0 直聊角色(角色面板点卡即聊):那个角色自己答,不走小卡路由/快通道
    mgr.set_peer(Address(domain_id="l0", role="agent", agent_id="solo-advisor"))
    assert client.get("/api/roundtable/roster").json()["ok"] is False
    # 知识线:馆员自己接,路由层整个豁免
    mgr.set_peer(knowledge_peer())
    assert client.get("/api/roundtable/roster").json()["ok"] is False


# ---- ④ 前端静态:私聊 @ 下拉接线 + i18n en/zh 双表齐 ----

def test_app_js_private_mention_wiring():
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    # 场判定门:私聊场进 roster 加载 + 输入触发(复用群聊那套下拉,不另造)
    assert "function _isKarvyPrivatePeer" in app_js
    assert "!(peer && peer.is_group) && !_isKarvyPrivatePeer(peer)" in app_js      # _loadGroupRoster 门
    assert "(_currentPeer && _currentPeer.is_group) || _isKarvyPrivatePeer(_currentPeer)" in app_js  # 输入触发门
    # 默认落地(还没切场,_currentPeer=null)= 小卡私聊 → 开机预拉名册,首屏敲 @ 即弹
    assert "_loadGroupRoster(_currentPeer)" in app_js
    # 私聊选中 = 纯文本「@agent_id + 空格」(快通道对原始文本精确匹配,不读 mention 参数)
    assert '"@" + (m.agent_id || m.display)' in app_js
    # 顶部时机教学行(浅色,纯提示不参与选择)
    assert '"mention.private_hint"' in app_js
    assert "mention-hint" in app_js
    # 群聊 chip 插入路径原样在(群 @ 语义零改动的存在性锚;行为由 test_mention_routing 守)
    assert 'chip.className = "mention-tag"' in app_js


def test_mention_hint_i18n_en_zh_parity():
    """mention.private_hint:TS 源(en+zh 两表)+ 构建产物都齐;zh 文案按 docs/91 拍的句式。"""
    src = (FRONTEND_SRC / "i18n.ts").read_text(encoding="utf-8")
    built = (STATIC / "i18n.js").read_text(encoding="utf-8")
    for text, name in ((src, "i18n.ts"), (built, "static/i18n.js")):
        n = text.count('"mention.private_hint"')
        assert n == 2, f"{name} 里 mention.private_hint 应 en+zh 各一处(现 {n})"
        assert "备好委派单" in text and "拍板后才开工" in text   # zh(使用时刻教快通道行为)
        assert "delegation order" in text                        # en


def test_styles_css_has_mention_hint():
    css = (STATIC / "styles.css").read_text(encoding="utf-8")
    assert ".mention-hint" in css
