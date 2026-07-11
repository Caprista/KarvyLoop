"""跨 runtime 成员化 4 条并行线的接线缝集成测试(集成代理补的接线,不改各线核心逻辑)。

缝 1 egress 端到端:
  - 1a 工具侧:scoped + egress_allowlist 的公民派活 → external_agent 工具真把 allowlist 传进 bridge.start;
    guest / 未设 allowlist → 不传(零回归,既有 start(task,cwd=) 签名不受影响)。
  - 1b 桥→沙箱:bridge 据 egress_allowlist 构造 net_allowlist 非空的 CapabilityToken,透传给
    **沙箱后端 runner**(make_sandbox_runner)→ net_allowlist 真到 Sandbox.exec(B 留的另一半)。
缝 2 registry 接线:app.state.citizen_registry set 后 routes_external 不再 _integration_pending。
缝 3 四工具五步接线:external_agent/attach/list/revoke 都 注入✓ + policy✓ + catalog✓(+ list 只读豁免)。
缝 4 doctor recipe-driven:候选 bin 从配方 probe_bins 派生(builtin_probe_bins),非硬编码清单。
"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

import pytest  # noqa: E402

from karvyloop.atoms.tool_catalog import BUILTIN_TOOL_NAMES  # noqa: E402
from karvyloop.capability.deontic_gate import _READ_ONLY_TOOLS  # noqa: E402
from karvyloop.capability.policy import DEFAULT_TOOL_REQUIREMENTS, Mode, required_mode  # noqa: E402
from karvyloop.external_runtime import (  # noqa: E402
    BridgeResult, ExternalCitizen, ExternalCitizenRegistry, ExternalCitizenStore,
    STATUS_ACTIVE, STATUS_DONE, TIER_SCOPED, bridge_factory, builtin_probe_bins,
    builtin_recipe, compute_manifest_hash, make_sandbox_runner, sandbox_bridge_factory,
)
from karvyloop.external_runtime.bridge import SubprocessBridge  # noqa: E402
from karvyloop.karvy.tools import (  # noqa: E402
    make_attach_external_agent_tool, make_external_agent_tool,
    make_list_external_agents_tool, make_revoke_external_agent_tool,
)
from karvyloop.sandbox.exec_result import ExecResult  # noqa: E402


def _pinned_hash(runtime_kind="raw_text_sidecar", bin_path=None):
    r = builtin_recipe(runtime_kind)
    return compute_manifest_hash(bin_path=bin_path or sys.executable, version="",
                                 argv_template=r.argv_template,
                                 blocked_entrypoints=r.blocked_entrypoints)


def _run(tool, inp):
    return asyncio.run(tool.call(inp, token=None, sandbox=None))


# ============ 缝 1a:工具侧 egress 真传参断言 ============

class _CaptureBridge:
    """记下 start() 收到的 egress_allowlist(区分 scoped 传 / guest 不传)。"""
    last_kwargs: dict = {}

    def __init__(self, recipe):
        pass

    def start(self, task, *, cwd="", egress_allowlist=None):
        _CaptureBridge.last_kwargs = {"cwd": cwd, "egress_allowlist": egress_allowlist}
        return BridgeResult(status=STATUS_DONE, text="ok")


def _capture_factory(recipe):
    return _CaptureBridge(recipe)


def test_scoped_citizen_passes_egress_allowlist_to_bridge():
    reg = ExternalCitizenRegistry()
    reg.add(ExternalCitizen(
        citizen_id="scout", runtime_kind="raw_text_sidecar", bin_path=sys.executable,
        domain_id="d1", status=STATUS_ACTIVE, tier=TIER_SCOPED,
        egress_allowlist=("api.github.com", "pypi.org"),
        manifest_hash=_pinned_hash()))
    tool = make_external_agent_tool(citizen_registry=reg, bridge_factory=_capture_factory)
    out = _run(tool, {"citizen_id": "scout", "task": "1+1"})
    assert out["ok"] is True
    # scoped + allowlist → 工具真把域名白名单传进 bridge.start
    assert _CaptureBridge.last_kwargs["egress_allowlist"] == ("api.github.com", "pypi.org")


def test_guest_citizen_does_not_pass_egress_allowlist():
    reg = ExternalCitizenRegistry()
    reg.add(ExternalCitizen(
        citizen_id="cc", runtime_kind="raw_text_sidecar", bin_path=sys.executable,
        domain_id="", status=STATUS_ACTIVE, tier="guest",
        manifest_hash=_pinned_hash()))
    _CaptureBridge.last_kwargs = {}
    tool = make_external_agent_tool(citizen_registry=reg, bridge_factory=_capture_factory)
    out = _run(tool, {"citizen_id": "cc", "task": "1+1"})
    assert out["ok"] is True
    # guest → 走不带 egress 的路径(egress_allowlist kwarg 从未被传;零回归、旧 start 签名兼容)
    assert _CaptureBridge.last_kwargs.get("egress_allowlist") is None


def test_scoped_without_allowlist_does_not_pass_egress():
    # scoped 但没设 egress_allowlist → 不传(空=二元网络,不构造空 token)
    reg = ExternalCitizenRegistry()
    reg.add(ExternalCitizen(
        citizen_id="scout2", runtime_kind="raw_text_sidecar", bin_path=sys.executable,
        domain_id="d1", status=STATUS_ACTIVE, tier=TIER_SCOPED,
        manifest_hash=_pinned_hash()))
    _CaptureBridge.last_kwargs = {}
    tool = make_external_agent_tool(citizen_registry=reg, bridge_factory=_capture_factory)
    out = _run(tool, {"citizen_id": "scout2", "task": "x"})
    assert out["ok"] is True
    assert _CaptureBridge.last_kwargs.get("egress_allowlist") is None


def test_egress_allowlist_persists_roundtrip(tmp_path):
    # egress_allowlist 落盘 + 重载(from_dict 归一成 tuple)
    path = tmp_path / "external_citizens.json"
    reg = ExternalCitizenRegistry(store=ExternalCitizenStore(path))
    reg.add(ExternalCitizen(citizen_id="scout", runtime_kind="raw_text_sidecar",
                            bin_path="ext-cli", domain_id="d1", tier=TIER_SCOPED,
                            egress_allowlist=("api.github.com",)))
    reg2 = ExternalCitizenRegistry(store=ExternalCitizenStore(path))
    c = reg2.resolve_in("d1", "scout")
    assert c is not None
    assert c.egress_allowlist == ("api.github.com",)
    assert isinstance(c.egress_allowlist, tuple)


# ============ 缝 1b:egress 真到 Sandbox.exec(net_allowlist 非空) ============

class _FakeSandbox:
    """记下 exec() 收到的 token(尤其 net_allowlist)—— 证明 egress 真到沙箱 exec。"""
    def __init__(self):
        self.seen_token = None
        self.seen_argv = None

    async def exec(self, argv, *, token, cwd, stdin=b"", timeout_s=120.0,
                   max_output_bytes=30_000):
        self.seen_token = token
        self.seen_argv = list(argv)
        return ExecResult(stdout=b"sandbox-out", stderr=b"", exit_code=0)


def test_net_allowlist_reaches_sandbox_exec():
    """bridge.start(egress_allowlist=...) → net_allowlist 非空 CapabilityToken → Sandbox.exec 收到它。"""
    sandbox = _FakeSandbox()
    recipe = builtin_recipe("raw_text_sidecar")
    # 沙箱后端 bridge:runner = make_sandbox_runner(sandbox)
    factory = sandbox_bridge_factory(sandbox)
    bridge = factory(recipe)
    res = bridge.start("do a thing", egress_allowlist=("api.github.com", "pypi.org"))
    assert res.ok, f"bridge 失败:{res.reason}"
    assert res.text == "sandbox-out"
    # 核心断言:net_allowlist 真到 Sandbox.exec 的 token(不再半途蒸发)
    assert sandbox.seen_token is not None
    assert sandbox.seen_token.net_allowlist == ("api.github.com", "pypi.org")


def test_sandbox_runner_uses_base_token_when_no_egress():
    # 无 egress(guest)→ runner 用兜底 token(net_allowlist 空),仍真进沙箱跑
    sandbox = _FakeSandbox()
    recipe = builtin_recipe("raw_text_sidecar")
    bridge = sandbox_bridge_factory(sandbox)(recipe)
    res = bridge.start("x")  # 不带 egress_allowlist
    assert res.ok
    assert sandbox.seen_token is not None
    assert sandbox.seen_token.net_allowlist == ()  # 空 = 二元网络,零回归


def test_bridge_introspection_recognizes_sandbox_runner():
    # 签名内省:make_sandbox_runner 造的 runner 带 egress_token 形参 → 被识别为"接受 egress"
    sandbox = _FakeSandbox()
    runner = make_sandbox_runner(sandbox)
    b = SubprocessBridge(builtin_recipe("raw_text_sidecar"), runner=runner)
    assert b._runner_takes_egress() is True


def test_default_runner_ignores_egress_no_double_exec():
    # 默认 subprocess runner 不接 egress_token → 走旧调用形态(非破坏,不 double-exec)
    calls = {"n": 0}

    def plain_runner(argv, *, env, timeout, cwd):
        calls["n"] += 1

        class _P:
            returncode = 0
            stdout = "hi"
            stderr = ""
        return _P()

    b = bridge_factory(builtin_recipe("raw_text_sidecar"), runner=plain_runner)
    b.start("x", egress_allowlist=("api.github.com",))  # 传了 egress 但 runner 不接
    assert calls["n"] == 1  # 只调一次(不 double-exec)


# ============ 缝 3:四工具五步接线 ============

@pytest.fixture
def _reg():
    return ExternalCitizenRegistry()


def _four_tools(reg):
    return {
        "external_agent": make_external_agent_tool(
            citizen_registry=reg, bridge_factory=bridge_factory),
        "attach_external_agent": make_attach_external_agent_tool(citizen_registry=reg),
        "list_external_agents": make_list_external_agents_tool(citizen_registry=reg),
        "revoke_external_agent": make_revoke_external_agent_tool(citizen_registry=reg),
    }


def test_four_tools_factory_names_exact(_reg):
    tools = _four_tools(_reg)
    for expected_name, tool in tools.items():
        assert tool.name == expected_name, f"工厂名漂移:{tool.name} != {expected_name}"


def test_four_tools_in_policy_table(_reg):
    for name in _four_tools(_reg):
        assert name in DEFAULT_TOOL_REQUIREMENTS, f"{name} 未在 policy 下限表 → 落回 FULL 被误拒"


def test_four_tools_required_modes(_reg):
    assert required_mode("external_agent") == Mode.FULL
    assert required_mode("attach_external_agent") == Mode.WORKSPACE_WRITE
    assert required_mode("list_external_agents") == Mode.READ_ONLY
    assert required_mode("revoke_external_agent") == Mode.WORKSPACE_WRITE


def test_four_tools_in_catalog(_reg):
    for name in _four_tools(_reg):
        assert name in BUILTIN_TOOL_NAMES, f"{name} 未进 BUILTIN_TOOL_NAMES → atom unresolved 误判"


def test_only_list_tool_is_read_only_exempt():
    # 只有 list 是只读豁免;写工具(attach/revoke/external)不该在只读豁免集
    assert "list_external_agents" in _READ_ONLY_TOOLS
    for name in ("external_agent", "attach_external_agent", "revoke_external_agent"):
        assert name not in _READ_ONLY_TOOLS


def test_revoke_tool_callable_by_karvy(_reg):
    # revoke 工具能被小卡调(接线通:注册表有此成员 → detach → ok)
    _reg.add(ExternalCitizen(citizen_id="gone", runtime_kind="raw_text_sidecar",
                             bin_path="ext-cli", domain_id="d1", status=STATUS_ACTIVE))
    tool = make_revoke_external_agent_tool(citizen_registry=_reg)
    out = _run(tool, {"citizen_id": "gone", "domain_id": "d1"})
    assert out["ok"] is True and out["citizen"] == "gone"
    # 撤过 → 注册表里没了
    assert _reg.resolve_in("d1", "gone") is None


def test_revoke_tool_honest_when_missing(_reg):
    tool = make_revoke_external_agent_tool(citizen_registry=_reg)
    out = _run(tool, {"citizen_id": "nobody", "domain_id": "d1"})
    assert out["ok"] is False and "没有" in out["reason"]


# ============ 缝 4:doctor recipe-driven ============

def test_builtin_probe_bins_recipe_derived_verified_only():
    bins = builtin_probe_bins()
    assert bins, "内置配方应派生出至少一个候选 bin(可接入是真的)"
    # 只列**确知用此确切 CLI**的 bin:claude(`-p --output-format stream-json` 公开文档语法)。
    assert "claude" in bins, "claude(Claude Code 真 headless 语法)应被派生进候选集"
    # 诚实纪律锁:openclaw/hermes 形态的两份配方是 shape-only(参照名不入出货代码),
    # 绝不给没验过此 CLI 的 runtime 认领画饼 —— 这些编造映射必须不出现。
    for fabricated in ("goose", "aider", "crush"):
        assert fabricated not in bins, f"{fabricated} 未验用此 CLI,不该被认领(防画饼回归)"


def test_doctor_probes_recipe_driven_bins():
    from karvyloop.doctor_liveness import check_external_runtime
    seen = []

    def which(name):
        seen.append(name)
        return name == "claude"

    findings = check_external_runtime(which=which)
    # doctor 探的正是配方派生的 bin 集(不多不少 = builtin_probe_bins)
    assert set(seen) == set(builtin_probe_bins())
    assert findings[0].code == "external_runtime_present"
    assert "claude" in findings[0].params["bins"]


def test_doctor_absent_when_no_recipe_bin_on_path():
    from karvyloop.doctor_liveness import check_external_runtime
    findings = check_external_runtime(which=lambda n: False)
    assert findings[0].code == "external_runtime_absent"
