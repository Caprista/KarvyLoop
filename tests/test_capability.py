"""capability 验收测试 —— 逐条对应 docs/modules/capability.md §5 验收标准。

每个测试函数名标注它验收哪一条（AC1..AC10）。沙箱不可用 → 仅测决策链 +
词法归一化 + token + broker 编排；M0 不启真 bwrap。
"""

from __future__ import annotations

import os
import tempfile

import pytest

from karvyloop.capability import (
    Allow,
    Ask,
    DEFAULT_TOOL_REQUIREMENTS,
    Deny,
    Mode,
    PermissionContext,
    Prompter,
    Rule,
    Verdict,
    authorize,
    check,
    derive_min_capabilities,
    has_grant,
    is_within_workspace,
    is_expired,
    mint,
    required_mode,
    verify,
)
from karvyloop.schemas import Capability


def _ctx(tool="read_file", input=None, mode=Mode.READ_ONLY, **kw):
    return PermissionContext(tool=tool, input=input or {}, mode=mode, **kw)


# ============ AC1：未在下限表声明的工具 → 默认 Deny/Ask（除非 Full） ============
def test_ac1_unlisted_tool_defaults_to_full_requirement():
    # 任何"自造"工具 → required_mode=FULL
    assert required_mode("totally_undeclared_tool") == Mode.FULL


def test_ac1_unlisted_tool_readonly_mode_deny_or_ask():
    # READ_ONLY 模式 + 未知工具 → 走 step 9 默认 Ask（无 prompter → Deny）
    d = authorize(_ctx(tool="totally_undeclared_tool", mode=Mode.READ_ONLY))
    assert isinstance(d, Deny)
    assert d.reason.startswith("default:")


def test_ac1_unlisted_tool_full_mode_allow():
    d = authorize(_ctx(tool="totally_undeclared_tool", mode=Mode.FULL))
    assert isinstance(d, Allow)
    assert d.reason == "mode:full"


# ============ AC2：denied_tools 在 Full 仍 Deny（一票否决） ============
def test_ac2_denied_tools_one_vote_veto():
    d = authorize(_ctx(tool="read_file", mode=Mode.FULL, denied_tools=["read_file"]))
    assert isinstance(d, Deny)
    assert d.reason == "denied_tools:hit"


# ============ AC3：ask 规则命中即 ask；无 prompter → Deny ============
def test_ac3_ask_rule_triggers_ask_no_prompter_denies():
    d = authorize(_ctx(
        tool="run_command",
        input={"command": "rm -i foo"},
        mode=Mode.FULL,  # 即便 Full
        ask_rules=[Rule(tool="run_command", subject="*", verdict=Verdict.ASK)],
    ))
    assert isinstance(d, Deny)
    assert d.reason == "ask:no_prompter"


# ============ AC4：hook=Allow + ask 规则命中 → 仍 Ask（不被直接放行） ============
def test_ac4_hook_allow_does_not_bypass_ask_rule():
    d = authorize(_ctx(
        tool="run_command",
        input={"command": "danger"},
        mode=Mode.READ_ONLY,
        hook=Verdict.ALLOW,
        ask_rules=[Rule(tool="run_command", subject="danger", verdict=Verdict.ASK)],
    ))
    assert isinstance(d, Deny)
    assert d.reason == "ask:no_prompter"


# ============ AC5：deny 规则与 allow 规则同时命中 → Deny 胜（顺序固定） ============
def test_ac5_deny_beats_allow():
    d = authorize(_ctx(
        tool="run_command",
        input={"command": "rm foo"},
        mode=Mode.FULL,
        deny_rules=[Rule(tool="run_command", subject="rm foo", verdict=Verdict.DENY)],
        allow_rules=[Rule(tool="run_command", subject="*", verdict=Verdict.ALLOW)],
    ))
    assert isinstance(d, Deny)
    assert d.reason == "rule:deny"


# ============ AC6：安全检查免疫 bypass（Full/bypass 下 rm -rf /、写 .git/ 仍挡） ============
def test_ac6_safety_full_mode_blocks_rm_rf_root():
    d = authorize(_ctx(tool="run_command", input={"command": "rm -rf /"},
                       mode=Mode.FULL))
    assert isinstance(d, Deny)
    assert d.reason == "safety:rm_rf_root"


def test_ac6_safety_full_mode_blocks_git_dir():
    d = authorize(_ctx(tool="write_file", input={"path": "/repo/.git/config"},
                       mode=Mode.FULL))
    assert isinstance(d, Deny)
    assert "internal_dir" in d.reason


def test_ac6_safety_full_mode_blocks_claude_dir():
    d = authorize(_ctx(tool="edit_file", input={"file_path": "/home/u/.claude/hooks/x"},
                       mode=Mode.FULL))
    assert isinstance(d, Deny)
    assert "internal_dir" in d.reason


# ============ AC7：路径归一化回归（参照工程 test 套件） ============
@pytest.mark.parametrize("path,root,expected", [
    ("/ws/../etc/passwd", "/ws", False),       # .. 越根
    ("/ws/../../etc/crontab", "/ws", False),   # 连续 .. 越根
    ("../etc/passwd", "/ws", False),           # 相对越根
    ("/ws/a/../../out", "/ws", False),         # 中间越界后落外
    ("/ws/./src", "/ws", True),                # `.` 应折叠
    ("/ws/src/../src", "/ws", True),           # .. 后仍在
    ("/wsx/hack", "/ws", False),               # 假前缀
    ("C:\\evil", "/ws", False),                # backslash 全平台拒
    ("/ws\\evil", "/ws", False),
])
def test_ac7_path_normalization(path, root, expected):
    assert is_within_workspace(path, root) is expected


# ============ AC8：symlink：workspace 内 symlink 指向外部，写入被拒 ============
def test_ac8_symlink_escape_blocked(tmp_path):
    # 构造：/tmp/ws  (workspace)  /tmp/outside
    ws = tmp_path / "ws"
    ws.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    # 在 ws 内创建 symlink 指向 outside
    link = ws / "leak"
    try:
        link.symlink_to(outside)
    except (OSError, NotImplementedError):
        pytest.skip("此平台不支持 symlink")

    # 写文件到 ws/leak/secret.txt —— 路径词法在 ws 内，但 symlink 跳出
    target = str(link / "secret.txt")
    # posix 拼法
    posix_target = target.replace("\\", "/")
    posix_root = str(ws).replace("\\", "/")
    assert is_within_workspace(posix_target, posix_root) is False


# ============ AC9：每个 Decision 含可回溯 reason；check 不抛异常 ============
def test_ac9_every_decision_has_reason():
    cases = [
        _ctx(tool="read_file", mode=Mode.READ_ONLY),                 # Allow
        _ctx(tool="totally_undeclared", mode=Mode.READ_ONLY),        # Deny
        _ctx(tool="read_file", mode=Mode.READ_ONLY,
             denied_tools=["read_file"]),                            # Deny
        _ctx(tool="run_command", input={"command": "rm -rf /"},
             mode=Mode.FULL),                                         # Deny (safety)
    ]
    for c in cases:
        d = authorize(c)
        assert hasattr(d, "reason") and d.reason, f"no reason: {d}"


def test_ac9_check_does_not_raise_on_bad_action():
    # action 缺字段、token 过期都不抛 → 走 Deny+reason
    tok = mint("t1", [Capability(resource="fs:/tmp", ops=["read"])], ttl_seconds=-1)
    d = check(tok, {"tool": "read_file", "input": {"path": "/tmp/a"}})
    assert isinstance(d, Deny)
    assert "expired" in d.reason or "token" in d.reason


# ============ AC10：derive_min："整理 /tmp/x 文件夹" → 令牌仅含 fs:/tmp/x rw，无 net ============
def test_ac10_derive_min_organize_folder():
    tok = derive_min_capabilities("帮我整理 /tmp/x 文件夹")
    # 至少含 fs:/tmp/x with read+write
    fs = [g for g in tok.grants if g.resource == "fs:/tmp/x"]
    assert fs, f"未推导出 fs:/tmp/x：{tok.grants}"
    assert "read" in fs[0].ops and "write" in fs[0].ops
    # 不含 net
    assert not any(g.resource.startswith("net:") for g in tok.grants)


def test_ac10_derive_min_with_network_task_adds_net():
    tok = derive_min_capabilities("下载 https://example.com/data.csv 并保存到 /tmp")
    assert any(g.resource.startswith("net:") for g in tok.grants)


# ============ token 辅助（不在 AC 列表里，但 broker 依赖） ============
def test_token_expired():
    tok = mint("t", [Capability(resource="fs:/", ops=["read"])], ttl_seconds=-1)
    assert is_expired(tok)
    with pytest.raises(ValueError, match="过期"):
        verify(tok)


def test_token_has_grant_with_wildcard_ops():
    tok = mint("t", [Capability(resource="fs:/ws", ops=[])])  # 空 ops = 任意
    assert has_grant(tok, "fs:/ws", "read")
    assert has_grant(tok, "fs:/ws", "exec")


# ============ broker.check 端到端（不抛异常，Decision 形态正确） ============
def test_broker_check_read_allowed_with_grant():
    tok = mint("t", [Capability(resource="fs:/tmp", ops=["read"])])
    d = check(tok, {"tool": "read_file", "input": {"path": "/tmp/a.txt"}})
    assert isinstance(d, Allow)


def test_broker_check_read_outside_grant_denied():
    tok = mint("t", [Capability(resource="fs:/tmp", ops=["read"])])
    d = check(tok, {"tool": "read_file", "input": {"path": "/etc/passwd"}})
    assert isinstance(d, Deny)
    assert d.reason == "token:not_granted"
