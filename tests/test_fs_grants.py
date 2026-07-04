"""test_fs_grants — 工作区外访问授权台账 + 敏感路径硬地板(docs/42 安全骨架)。

不变量:① 敏感路径(密钥/ssh/凭据)**永不放行**:allows 恒 False、record 拒记、能力链
step6 免疫 bypass、note_denied 不记(永不出卡)② 授权=前缀语义+可过期+幂等+可撤
③ 工具层:工作区内照常;外+未授→拒+记"想要";外+已授→行 ④ 拒绝→升 H2A 授权卡(去重,
挂着不重复)⑤ ACCEPT handler 落台账 ⑥ API 增删查 + 能力总览带 grants ⑦ seatbelt profile
含授权写路径 + 敏感 deny。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from karvyloop.capability.fs_grants import (  # noqa: E402
    FsGrantsStore, is_sensitive_path, path_allowed, register_store)

pytestmark = pytest.mark.security   # 安全套件:敏感路径硬地板(密钥/ssh/凭据永不放行)


def teardown_function(_fn):
    register_store(None)   # 全局注册表不串测试


def test_sensitive_floor_absolute():
    st = FsGrantsStore()
    for p in ("~/.karvyloop/config.yaml", "/home/u/.ssh/id_rsa", "C:\\Users\\u\\.aws\\credentials",
              "/home/u/project/.env", "/etc/shadow"):
        assert is_sensitive_path(p), p
        assert st.record(p, ["read"]) is None          # 拒记
        assert st.allows(p, "read") is False           # 恒 False
        st.note_denied(p, "read")
    assert st.pop_denied() == []                       # 永不出卡
    # 普通路径不误伤
    assert not is_sensitive_path("/home/u/Documents/contracts/a.pdf")


def test_grant_prefix_expiry_idempotent_revoke(tmp_path):
    st = FsGrantsStore(tmp_path / "g.json")
    g = st.record(str(tmp_path / "docs"), ["read"])
    assert g is not None
    # 前缀语义:目录下文件放行;别处不放
    assert st.allows(str(tmp_path / "docs" / "a" / "b.txt"), "read")
    assert not st.allows(str(tmp_path / "elsewhere.txt"), "read")
    assert not st.allows(str(tmp_path / "docs" / "a.txt"), "write")   # op 不符
    # 幂等
    g2 = st.record(str(tmp_path / "docs"), ["read"])
    assert g2["id"] == g["id"] and len(st.list()) == 1
    # 过期
    ge = st.record(str(tmp_path / "tmp"), ["read"], ttl_seconds=10, now=1000.0)
    assert st.allows(str(tmp_path / "tmp" / "x"), "read", now=1005.0)
    assert not st.allows(str(tmp_path / "tmp" / "x"), "read", now=1011.0)
    # 撤回 + 落盘往返
    assert st.revoke(g["id"])
    st2 = FsGrantsStore(tmp_path / "g.json")
    assert not st2.allows(str(tmp_path / "docs" / "a.txt"), "read")


def test_path_allowed_and_note(tmp_path):
    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    st = FsGrantsStore()
    register_store(st)
    assert path_allowed(str(tmp_path / "ws" / "f.txt"), "read", workspace_root=ws)   # 区内照常
    outside = str(tmp_path / "out" / "f.txt")
    assert not path_allowed(outside, "read", workspace_root=ws)                      # 外+未授→拒
    st.record(str(tmp_path / "out"), ["read"])
    assert path_allowed(outside, "read", workspace_root=ws)                          # 外+已授→行
    assert not path_allowed(outside, "write", workspace_root=ws)                     # 授读不授写


def test_capability_chain_sensitive_and_granted_write(tmp_path):
    from karvyloop.capability.decision import Deny, authorize
    from karvyloop.capability.policy import Mode, PermissionContext
    # 敏感路径:FULL 模式也拒(step6 免疫 bypass)
    d = authorize(PermissionContext(tool="read_file", input={"path": str(pathlib.Path.home() / ".ssh" / "id_rsa")},
                                    mode=Mode.FULL))
    assert isinstance(d, Deny) and d.reason == "safety:sensitive_path"
    # 工作区外写:未授→拒;授了→行
    ws = str(tmp_path / "ws")
    out_file = str(tmp_path / "out" / "r.md")
    d2 = authorize(PermissionContext(tool="write_file", input={"path": out_file},
                                     mode=Mode.WORKSPACE_WRITE, workspace_root=ws))
    assert isinstance(d2, Deny) and d2.reason == "pathnorm:out_of_workspace"
    st = FsGrantsStore()
    st.record(str(tmp_path / "out"), ["read", "write"])
    register_store(st)
    d3 = authorize(PermissionContext(tool="write_file", input={"path": out_file},
                                     mode=Mode.WORKSPACE_WRITE, workspace_root=ws))
    assert not isinstance(d3, Deny)


def test_read_tool_grant_and_note(tmp_path):
    from karvyloop.coding.tools.read import ReadTool
    from karvyloop.coding.filestate import FileState

    class _Sbx:
        async def read_file(self, path, token):
            return b"secret-adjacent but granted content"

    ws = str(tmp_path / "ws")
    (tmp_path / "ws").mkdir()
    outside = tmp_path / "out"
    outside.mkdir()
    (outside / "doc.txt").write_text("x", encoding="utf-8")
    st = FsGrantsStore()
    register_store(st)
    tool = ReadTool(_Sbx(), FileState(), ws, token=object())
    r1 = asyncio.run(tool({"file_path": str(outside / "doc.txt")}))
    assert not r1.ok and "越出工作区" in (r1.error_message or "")
    denied = st.pop_denied()
    assert denied and denied[0]["path"] == str(outside / "doc.txt") and denied[0]["op"] == "read"
    st.record(str(outside), ["read"])
    r2 = asyncio.run(tool({"file_path": str(outside / "doc.txt")}))
    assert r2.ok


def test_raise_cards_flow_and_accept_records(tmp_path):
    """拒绝→升卡→ACCEPT→台账放行(全链路);同路径卡挂着不重复。"""
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.console.proposals import raise_fs_access_cards
    from karvyloop.console.proposal_handlers import build_proposal_handlers
    from karvyloop.karvy.observer import WorkbenchObserver
    from karvyloop.karvy.proposal_registry import KIND_FS_ACCESS, PendingProposalRegistry

    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.proposal_registry = PendingProposalRegistry()
    app.state.proposal_handlers = build_proposal_handlers(app)
    st = FsGrantsStore()
    app.state.fs_grants = st
    register_store(st)
    outside = str(tmp_path / "contracts")
    st.note_denied(outside, "read")
    st.note_denied(outside, "write")   # 同路径两个 op → 合并一张卡
    n = asyncio.run(raise_fs_access_cards(app))
    assert n == 1
    cards = [p for p in app.state.proposal_registry.pending() if p.kind == KIND_FS_ACCESS]
    assert len(cards) == 1 and cards[0].payload["ops"] == ["read", "write"]
    # 卡挂着 → 再碰壁不重复出卡
    st.note_denied(outside, "read")
    assert asyncio.run(raise_fs_access_cards(app)) == 0
    # ACCEPT → 台账落一条
    client = TestClient(app)
    r = client.post("/api/h2a_decide", json={"proposal_id": cards[0].proposal_id, "decision": "ACCEPT"})
    assert r.status_code == 200
    body = r.json()
    assert body["dispatch"]["ok"], body
    assert st.allows(outside + "/a.pdf", "write")


def test_api_and_overview(tmp_path):
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    st = FsGrantsStore()
    app.state.fs_grants = st
    client = TestClient(app)
    # 手动放行 + 敏感拒
    ok = client.post("/api/fs_grants", json={"path": str(tmp_path / "d"), "ops": ["read"]}).json()
    assert ok["ok"]
    bad = client.post("/api/fs_grants", json={"path": "~/.ssh", "ops": ["read"]}).json()
    assert not bad["ok"] and "敏感" in bad["reason"]
    lst = client.get("/api/fs_grants").json()
    assert len(lst["grants"]) == 1 and lst["sensitive_markers"]
    # 能力总览带 grants
    ov = client.get("/api/capability/overview").json()
    assert len(ov["fs_grants"]) == 1
    # 撤回
    rid = lst["grants"][0]["id"]
    assert client.post("/api/fs_grants/revoke", json={"grant_id": rid}).json()["ok"]
    assert client.get("/api/fs_grants").json()["grants"] == []


def test_seatbelt_profile_grants_and_sensitive_deny(tmp_path):
    from karvyloop.platform.darwin.seatbelt import build_profile
    from karvyloop.schemas import Capability, CapabilityToken
    import time as _t
    tok = CapabilityToken(task_id="t", grants=[Capability(resource=f"fs:{tmp_path}", ops=["read", "write"])],
                          expiry=_t.time() + 60)
    st = FsGrantsStore()
    st.record(str(tmp_path / "granted"), ["read", "write"])
    register_store(st)
    prof = build_profile(tok)
    assert "granted" in prof                     # 授权写路径进了 allow file-write*
    assert "deny file-read* file-write*" in prof  # 敏感地板 deny 存在
    assert ".ssh" in prof
