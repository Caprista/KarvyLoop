"""test_capability_seams — 能力缝(capability seam)三相:注册/解析/迁移/向后兼容。

A 方向(capability/seams):后端能力 = 定义槽 + 可换 provider + 消费者。
核心红利 = **provider 一换整体迁移**(换沙箱后端只改一处注册,不逐工具改注入)。

AC:
- AC1: 注册/解析 round-trip;未注册槽 resolve → None(fail-soft)。
- AC2: selector.default_sandbox 选出的实现登记为 sandbox provider(fs/shell 同源)。
- AC3: make_coding_tools 不传 sandbox → 从缝解析;缝里没有 → 兜底 default_sandbox。
- AC4: override 后新解析的工具拿到新 provider(整体迁移,本设计核心)。
- AC5: 向后兼容 —— 显式传 sandbox → 走旧路径(现状零回归)。
"""
from __future__ import annotations

import time

from karvyloop.capability.seams import (
    SEAMS,
    SLOT_FS,
    SLOT_SANDBOX,
    SLOT_SHELL,
    CapabilitySlot,
    SeamRegistry,
)
from karvyloop.coding.filestate import FileState
from karvyloop.coding.tools import make_coding_tools
from karvyloop.schemas import Capability, CapabilityToken


def _tok() -> CapabilityToken:
    return CapabilityToken(
        task_id="t",
        grants=[Capability(resource="fs:/tmp", ops=["read", "write"])],
        expiry=time.time() + 3600,
    )


class _FakeSandbox:
    """最小假 provider:能 read/write/exec(满足 Sandbox Protocol 的 duck-type)。"""

    def __init__(self, tag: str = "fake"):
        self.tag = tag

    async def exec(self, argv, *, token, cwd, stdin=b"", timeout_s=120.0, max_output_bytes=30_000):
        from karvyloop.sandbox.exec_result import ExecResult
        return ExecResult(exit_code=0, stdout=self.tag.encode(), stderr=b"",
                          timed_out=False, truncated=False)

    async def write_file(self, path, content, token):
        return None

    async def read_file(self, path, token):
        return self.tag.encode()


# ---- AC1: 注册/解析 + fail-soft ----
def test_register_resolve_roundtrip():
    reg = SeamRegistry()
    reg.register_slot(CapabilitySlot("sandbox"))
    reg.register_provider("sandbox", _FakeSandbox("a"))
    assert reg.resolve("sandbox").tag == "a"
    # 未注册槽 → None(fail-soft)
    assert reg.resolve("nonexistent") is None


def test_register_idempotent_override():
    reg = SeamRegistry()
    reg.register_provider("sandbox", _FakeSandbox("old"))
    reg.register_provider("sandbox", _FakeSandbox("new"))
    assert reg.resolve("sandbox").tag == "new"   # 幂等覆盖 = provider 迁移


def test_global_seams_has_core_slots():
    for name in ("sandbox", "fs", "shell"):
        assert SEAMS.slot(name) is not None


# ---- AC2: selector 注册 ----
def test_selector_registers_provider():
    from karvyloop.sandbox.selector import default_sandbox
    SEAMS.clear()
    sb = default_sandbox(override=_FakeSandbox("via-override"))
    assert sb.tag == "via-override"
    # override 路径也登记了 provider(替代散落 override 参数)
    assert SEAMS.resolve(SLOT_SANDBOX.name) is sb
    assert SEAMS.resolve(SLOT_FS.name) is sb
    assert SEAMS.resolve(SLOT_SHELL.name) is sb


def test_selector_default_path_registers(tmp_path):
    from karvyloop.sandbox.selector import default_sandbox
    SEAMS.clear()
    sb = default_sandbox()   # 平台探测选实现
    assert SEAMS.resolve(SLOT_SANDBOX.name) is sb


# ---- AC3: make_coding_tools 从缝解析 ----
def test_make_tools_resolves_sandbox_from_seam():
    reg = SeamRegistry()
    fake = _FakeSandbox("from-seam")
    reg.register_provider("sandbox", fake)
    tools = make_coding_tools(file_state=FileState(), workspace_root="/tmp",
                              token=_tok(), seams=reg)
    # 工具内部拿到的是缝里的 provider(不是直传)
    assert tools["read_file"].sandbox is fake
    assert tools["run_command"].sandbox is fake


def test_make_tools_global_seam_when_no_param():
    fake = _FakeSandbox("global")
    SEAMS.register_provider("sandbox", fake)
    try:
        tools = make_coding_tools(file_state=FileState(), workspace_root="/tmp", token=_tok())
        assert tools["read_file"].sandbox is fake
    finally:
        SEAMS.clear()   # 不污染全局(别的测试依赖 default)


def test_make_tools_fallback_to_default_sandbox_when_seam_empty():
    reg = SeamRegistry()   # 空注册表
    tools = make_coding_tools(file_state=FileState(), workspace_root="/tmp",
                              token=_tok(), seams=reg)
    # 缝里没有 → 兜底 default_sandbox()(仍会返回一个可用沙箱,不 None)
    assert tools["read_file"].sandbox is not None


# ---- AC4: 整体迁移红利 ----
def test_provider_swap_migrates_tools():
    """provider 一换,后建的工具拿新 provider —— 换后端只改一处注册。"""
    reg = SeamRegistry()
    reg.register_provider("sandbox", _FakeSandbox("v1"))
    tools_v1 = make_coding_tools(file_state=FileState(), workspace_root="/tmp",
                                 token=_tok(), seams=reg)
    assert tools_v1["read_file"].sandbox.tag == "v1"
    # 换 provider(如换远程沙箱后端)
    reg.register_provider("sandbox", _FakeSandbox("v2-remote"))
    tools_v2 = make_coding_tools(file_state=FileState(), workspace_root="/tmp",
                                 token=_tok(), seams=reg)
    assert tools_v2["read_file"].sandbox.tag == "v2-remote"
    assert tools_v2["run_command"].sandbox.tag == "v2-remote"


# ---- AC5: 向后兼容 ----
def test_explicit_sandbox_wins_over_seam():
    reg = SeamRegistry()
    reg.register_provider("sandbox", _FakeSandbox("seam"))
    explicit = _FakeSandbox("explicit")
    tools = make_coding_tools(sandbox=explicit, file_state=FileState(),
                              workspace_root="/tmp", token=_tok(), seams=reg)
    # 显式传 sandbox → 旧路径,优先(现状调用全传,零回归)
    assert tools["read_file"].sandbox is explicit


def test_explicit_sandbox_positional_unchanged():
    """老调用形态 make_coding_tools(sb, fs, root, token=...) 一字不变。"""
    explicit = _FakeSandbox("positional")
    tools = make_coding_tools(explicit, FileState(), "/tmp", token=_tok())
    assert tools["read_file"].sandbox is explicit
    # read_only 语义不变:不给 write/edit
    ro = make_coding_tools(explicit, FileState(), "/tmp", token=_tok(), read_only=True)
    assert "write_file" not in ro and "edit_file" not in ro
    assert "read_file" in ro and "run_command" in ro
