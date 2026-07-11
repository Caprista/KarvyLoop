"""external_agent 工具 _call 行为:产出标 untrusted / usage 记 ext: 账 / input_required 上报 /
不可达诚实拒 / 工具永不穿透异常。"""
from __future__ import annotations

import asyncio
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from karvyloop.external_runtime import (  # noqa: E402
    BridgeResult, ExternalCitizen, ExternalCitizenRegistry, STATUS_ACTIVE,
    STATUS_UNREACHABLE, builtin_recipe, compute_manifest_hash,
)
from karvyloop.external_runtime.bridge import STATUS_DONE, STATUS_FAILED  # noqa: E402
from karvyloop.karvy.tools import make_external_agent_tool  # noqa: E402


def _pinned_hash(runtime_kind="raw_text_sidecar", bin_path=sys.executable):
    r = builtin_recipe(runtime_kind)
    return compute_manifest_hash(bin_path=bin_path, version="",
                                 argv_template=r.argv_template,
                                 blocked_entrypoints=r.blocked_entrypoints)


def _reg_with_active():
    # use-time hash 复验后:公民须有真 bin + 对得上的 pin,否则派活前就被 needs_reattach 拦
    reg = ExternalCitizenRegistry()
    reg.add(ExternalCitizen(citizen_id="cc", runtime_kind="raw_text_sidecar",
                            bin_path=sys.executable, domain_id="", status=STATUS_ACTIVE,
                            manifest_hash=_pinned_hash()))
    return reg


def _bridge_factory_returning(result):
    class _B:
        def __init__(self, recipe):
            pass

        def start(self, task, cwd=""):
            return result
    return lambda recipe: _B(recipe)


def _run(tool, inp):
    return asyncio.run(tool.call(inp, token=None, sandbox=None))


def test_output_marked_untrusted():
    reg = _reg_with_active()
    bf = _bridge_factory_returning(BridgeResult(status=STATUS_DONE, text="7"))
    tool = make_external_agent_tool(citizen_registry=reg, bridge_factory=bf)
    out = _run(tool, {"citizen_id": "cc", "task": "1+2*3"})
    assert out["ok"] is True
    assert out["provenance"] == "untrusted"
    assert out["output"] == "7"
    assert "采纳" in out["note"] or "不可信" in out["note"]


def test_usage_recorded_to_ext_source():
    reg = _reg_with_active()
    usage = {"input": 100, "output": 5, "total": 105, "model": "M"}
    bf = _bridge_factory_returning(BridgeResult(status=STATUS_DONE, text="7", usage=usage))
    recorded = {}

    def recorder(source, u):
        recorded["source"] = source
        recorded["usage"] = u

    tool = make_external_agent_tool(citizen_registry=reg, bridge_factory=bf,
                                    token_recorder=recorder)
    out = _run(tool, {"citizen_id": "cc", "task": "1+2*3"})
    assert out["ok"] is True
    assert recorded["source"] == "ext:cc"  # 独立 token_source
    assert recorded["usage"]["total"] == 105
    assert "ext:cc" in out["usage"]


def test_no_usage_only_provenance():
    # 拿不到 usage 的 runtime → 只落 provenance,不假记
    reg = _reg_with_active()
    bf = _bridge_factory_returning(BridgeResult(status=STATUS_DONE, text="7", usage=None))
    called = {"n": 0}

    def recorder(source, u):
        called["n"] += 1

    tool = make_external_agent_tool(citizen_registry=reg, bridge_factory=bf,
                                    token_recorder=recorder)
    out = _run(tool, {"citizen_id": "cc", "task": "x"})
    assert out["ok"] is True and out["usage"] == "no_usage"
    assert called["n"] == 0  # 没 usage 不记账


def test_input_required_surfaced():
    reg = _reg_with_active()
    bf = _bridge_factory_returning(
        BridgeResult(status=STATUS_FAILED, reason="要权限", input_required=True))
    tool = make_external_agent_tool(citizen_registry=reg, bridge_factory=bf)
    out = _run(tool, {"citizen_id": "cc", "task": "x"})
    assert out["ok"] is False and out["input_required"] is True


def test_bridge_failure_surfaced_not_swallowed():
    reg = _reg_with_active()
    bf = _bridge_factory_returning(BridgeResult(status=STATUS_FAILED, reason="退码 1:boom"))
    tool = make_external_agent_tool(citizen_registry=reg, bridge_factory=bf)
    out = _run(tool, {"citizen_id": "cc", "task": "x"})
    assert out["ok"] is False and "boom" in out["reason"]


def test_unknown_citizen_rejected():
    reg = ExternalCitizenRegistry()
    tool = make_external_agent_tool(citizen_registry=reg,
                                    bridge_factory=_bridge_factory_returning(None))
    out = _run(tool, {"citizen_id": "ghost", "task": "x"})
    assert out["ok"] is False and "没有" in out["reason"]


def test_unreachable_citizen_rejected():
    reg = ExternalCitizenRegistry()
    reg.add(ExternalCitizen(citizen_id="dead", runtime_kind="raw_text_sidecar",
                            bin_path="ext-cli", status=STATUS_UNREACHABLE))
    tool = make_external_agent_tool(citizen_registry=reg,
                                    bridge_factory=_bridge_factory_returning(None))
    out = _run(tool, {"citizen_id": "dead", "task": "x"})
    assert out["ok"] is False and "不可达" in out["reason"]


def test_missing_args_rejected():
    reg = _reg_with_active()
    tool = make_external_agent_tool(citizen_registry=reg,
                                    bridge_factory=_bridge_factory_returning(None))
    assert _run(tool, {"citizen_id": "cc"})["ok"] is False
    assert _run(tool, {"task": "x"})["ok"] is False


def test_bridge_exception_never_propagates():
    reg = _reg_with_active()

    class _Boom:
        def __init__(self, recipe):
            pass

        def start(self, task, cwd=""):
            raise RuntimeError("kaboom")

    tool = make_external_agent_tool(citizen_registry=reg, bridge_factory=lambda r: _Boom(r))
    out = _run(tool, {"citizen_id": "cc", "task": "x"})
    assert out["ok"] is False and "起不来" in out["reason"]
