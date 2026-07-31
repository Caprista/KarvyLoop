"""docs/99 刀1「computer use 会话同意门 + 确定性高危弹卡骨架」验收测试.

覆盖:
  ① is_computer_control_tool 判定面(computer / mcp_computer_use_* 认得;别的工具不认)
  ② 确定性高危分类:键入凭证域 / 危险组合键 → 高危;普通点击/键入/截屏 → 非高危;fail-safe
  ③ capability 默认拒:required_mode(mcp_computer_use_*)==FULL、computer==FULL(mcp_* 通融档零回归)
  ④ 会话同意门(执行咽喉):默认拒(未启用)→ 截住不执行 + 诚实回执;启用 + benign → 真跑
  ⑤ 启用后高危动作仍被扣下(不执行 + 诚实回执);读类(screenshot)未启用照样被同意门拦
  ⑥ 非 computer-control 工具零回归(consent 关也照跑);computer 门不误拦外发
  ⑦ 预设目录:computer_use 存在、stdio(npx)、category app、outbound_tools 空、风险文案诚实、
     build_server_config 产出真实消费形状
"""
from __future__ import annotations

import time
from typing import Any

import pytest

from karvyloop.atoms import Terminal, TerminalEvent, ToolResultEvent, run
from karvyloop.atoms._scripted_mock import ScriptedMockAdapter, text_round, tool_round
from karvyloop.capability import computer_gate as cg
from karvyloop.capability.policy import Mode, required_mode
from karvyloop.gateway import GatewayClient, ModelRegistry
from karvyloop.schemas import AtomSpec, Capability, CapabilityToken


# ================================================================ ① 判定面(纯)
class TestIsComputerControlTool:
    def test_recognizes_orchestrator_and_plumbing(self):
        assert cg.is_computer_control_tool("computer")
        assert cg.is_computer_control_tool("mcp_computer_use_click")
        assert cg.is_computer_control_tool("mcp_computer_use_screenshot")
        assert cg.is_computer_control_tool("mcp_computer_use_type_text")

    def test_rejects_others(self):
        for name in ("read_file", "run_command", "mcp_gmail_send_message",
                     "mcp_computer_click",       # 错 server 名(不是 computer_use)
                     "computer_use", "", "web_fetch"):
            assert not cg.is_computer_control_tool(name), name


# ================================================================ ② 高危分类(纯)
class TestClassifyAction:
    def test_credential_field_type_is_high_risk(self):
        for tool, inp in [
            ("mcp_computer_use_type_text", {"selector": "input#password", "text": "hunter2"}),
            ("mcp_computer_use_type_text", {"target": "Password field", "text": "x"}),
            ("mcp_computer_use_set_value", {"role": "passwordText", "value": "x"}),
            ("mcp_computer_use_type_text", {"selector": "#otp", "text": "123456"}),
        ]:
            hi, reason = cg.classify_action(tool, inp)
            assert hi is True and reason == "credential_field", (tool, inp)

    def test_dangerous_key_combo_is_high_risk(self):
        for keys in ("ctrl+shift+delete", "shift+delete", "cmd+shift+delete",
                     "Shift+Delete", "Control+Shift+Del", "cmd delete"):
            hi, reason = cg.classify_action("mcp_computer_use_press_key", {"keys": keys})
            assert hi is True and reason == "dangerous_key_combo", keys

    def test_dangerous_chord_evasion_forms_still_caught(self):
        """规避面(对抗验收):chord 不以 '+' 串传,而是列表 / 分字段 —— 仍必须判出高危。"""
        for inp in [
            {"keys": ["ctrl", "shift", "delete"]},              # 列表形
            {"modifiers": ["ctrl", "shift"], "key": "delete"},  # 分字段形
            {"chord": ["cmd", "shift", "delete"]},              # 别的参数名 + 列表
            {"keys": "shift", "extra": ["delete"]},             # 拆到不同字段
        ]:
            hi, reason = cg.classify_action("mcp_computer_use_press_key", inp)
            assert hi is True and reason == "dangerous_key_combo", inp

    def test_dangerous_chord_under_nonallowlisted_action_names(self):
        """FINDING A(对抗验收):危险 chord 藏在非枚举动作名下(keyboard/shortcut/key_combo)
        或编排器 computer 没带 action 字段 —— 仍必须判高危(不写死动作枚举)。"""
        for tool, inp in [
            ("mcp_computer_use_keyboard", {"keys": ["ctrl", "shift", "delete"]}),
            ("mcp_computer_use_shortcut", {"keys": ["ctrl", "shift", "delete"]}),
            ("mcp_computer_use_key_combo", {"keys": ["ctrl", "shift", "delete"]}),
            ("computer", {"keys": "ctrl+shift+delete"}),                    # 编排器无 action 字段
            ("computer", {"action": "keyboard", "keys": ["cmd", "shift", "delete"]}),
        ]:
            hi, reason = cg.classify_action(tool, inp)
            assert hi is True and reason == "dangerous_key_combo", (tool, inp)

    def test_literal_chord_text_in_type_action_not_misjudged(self):
        """反向:type_text 一段**字面**含 'ctrl+shift+delete' 的文本不是按键动作 → 不误判组合键
        (只有按键类动作才跑 chord 扫描,type/click 不跑;防误伤)。"""
        assert cg.classify_action(
            "mcp_computer_use_type_text",
            {"selector": "#notes", "text": "press ctrl+shift+delete to clear"}) == (False, "")

    def test_credential_hint_in_list_or_nested_still_caught(self):
        """凭证提示藏在列表 / 嵌套结构里(不是顶层字符串值)—— 仍判高危。"""
        for tool, inp in [
            ("mcp_computer_use_type_text", {"selectors": ["#password"], "text": "x"}),
            ("mcp_computer_use_set_value",
             {"element": {"attributes": {"role": "password"}}, "value": "x"}),
        ]:
            hi, reason = cg.classify_action(tool, inp)
            assert hi is True and reason == "credential_field", (tool, inp)

    def test_benign_actions_not_high_risk(self):
        for tool, inp in [
            ("mcp_computer_use_click", {"selector": "button.save"}),
            ("mcp_computer_use_type_text", {"selector": "input#search", "text": "hello world"}),
            ("mcp_computer_use_press_key", {"keys": "ctrl+c"}),
            ("mcp_computer_use_press_key", {"keys": "Return"}),
            ("mcp_computer_use_screenshot", {}),
            ("mcp_computer_use_scroll", {"x": 10, "y": 20}),
        ]:
            assert cg.classify_action(tool, inp) == (False, ""), (tool, inp)

    def test_non_cc_tool_is_never_high_risk(self):
        assert cg.classify_action("read_file", {"path": "/etc/passwd"}) == (False, "")
        assert cg.classify_action("mcp_gmail_send_message", {"password": "x"}) == (False, "")

    def test_malformed_input_does_not_crash(self):
        # 非 dict input:规约成 {} → benign,不抛。
        hi, _ = cg.classify_action("mcp_computer_use_type_text", "not-a-dict")
        assert hi in (False, True)   # 只要求不抛(返回 bool 元组)
        assert isinstance(cg.classify_action("mcp_computer_use_click", None), tuple)

    def test_is_high_risk_action_helper(self):
        assert cg.is_high_risk_action("mcp_computer_use_press_key", {"keys": "shift+delete"})
        assert not cg.is_high_risk_action("mcp_computer_use_click", {"selector": "ok"})


# ================================================================ 会话同意门(纯)
class TestConsentGate:
    def setup_method(self):
        cg.clear_computer_gate()

    def teardown_method(self):
        cg.clear_computer_gate()

    def test_default_denied(self):
        assert cg.is_computer_control_enabled() is False   # 默认全暗(顺序铁律)

    def test_enable_disable_clear(self):
        cg.enable_computer_control()
        assert cg.is_computer_control_enabled() is True
        cg.disable_computer_control()
        assert cg.is_computer_control_enabled() is False
        cg.enable_computer_control()
        cg.clear_computer_gate()
        assert cg.is_computer_control_enabled() is False


# ================================================================ ③ capability 默认拒(纯)
class TestCapabilityFloor:
    def test_computer_plumbing_requires_full(self):
        assert required_mode("mcp_computer_use_click") == Mode.FULL
        assert required_mode("mcp_computer_use_screenshot") == Mode.FULL
        assert required_mode("computer") == Mode.FULL

    def test_ordinary_mcp_tools_unchanged(self):
        # 零回归:普通 MCP 工具仍吃 WORKSPACE_WRITE 通融档(否则配了 MCP 也用不了)。
        assert required_mode("mcp_gmail_list_emails") == Mode.WORKSPACE_WRITE
        assert required_mode("mcp_notion_search") == Mode.WORKSPACE_WRITE


# ================================================================ ④⑤⑥ 执行咽喉集成
@pytest.fixture(autouse=True)
def _clean_cc():
    cg.clear_computer_gate()
    yield
    cg.clear_computer_gate()


class _Tool:
    """测试工具:call_log 记录真实调用(没真执行就看它是空的)。"""

    def __init__(self, name: str):
        self.name = name
        self.call_log: list[dict] = []
        self.description = f"test tool {name}"
        self.parameters = {"type": "object", "properties": {}}

    async def __call__(self, input: dict) -> Any:
        self.call_log.append(dict(input))
        return {"echo": dict(input)}

    def is_concurrency_safe(self, input: dict) -> bool:
        return False


def _atom() -> AtomSpec:
    return AtomSpec(id="a-cc", kind="task", prompt="t",
                    input_schema={"type": "object"}, output_schema={"type": "object"},
                    tools=[], model="p/a")


def _tok() -> CapabilityToken:
    return CapabilityToken(task_id="t", grants=[Capability(resource="fs:/tmp", ops=["read"])],
                           expiry=time.time() + 3600)


def _gw(adapter: ScriptedMockAdapter) -> GatewayClient:
    reg = ModelRegistry.from_config({
        "models": {"providers": {"p": {"base_url": "x", "models": [
            {"id": "p/a", "api": "anthropic-messages", "context_window": 1000,
             "max_tokens": 100},
        ]}}},
        "agents": {"defaults": {"model": "p/a"}},
        "embedding": {"model": "p/a"},
    })
    return GatewayClient(reg, adapters={"anthropic-messages": adapter})


async def _drive(tools: dict, *, rounds: list, input: dict | None = None,
                 mode: Mode = Mode.WORKSPACE_WRITE) -> list:
    adapter = ScriptedMockAdapter(rounds=rounds + [text_round("done")])
    gw = _gw(adapter)
    return [ev async for ev in run(_atom(), input or {"intent": "t"}, _tok(),
                                   gateway=gw, tools=tools, default_mode=mode)]


def _results(events: list) -> dict:
    return {ev.result.tool_use_id: ev.result
            for ev in events if isinstance(ev, ToolResultEvent)}


class TestExecutorConsentGate:
    @pytest.mark.asyncio
    async def test_blocked_when_consent_disabled(self):
        """④ 会话同意未开(默认):computer-control 工具在咽喉被截,不执行 + 诚实回执。"""
        from karvyloop import i18n
        tool = _Tool("mcp_computer_use_click")
        events = await _drive({"mcp_computer_use_click": tool},
                              rounds=[tool_round("c1", "mcp_computer_use_click",
                                                 {"selector": "button.ok"})])
        assert tool.call_log == []                       # 没真执行
        r = _results(events)["c1"]
        assert r.is_error is False
        assert r.content == {"ok": False, "error_message": i18n.t(
            "computer.control_not_enabled", tool="mcp_computer_use_click")}
        assert isinstance(events[-1], TerminalEvent)
        assert events[-1].reason == Terminal.COMPLETED   # agent 可继续干别的

    @pytest.mark.asyncio
    async def test_screenshot_read_also_gated_when_disabled(self):
        """⑤ 未启用时连读类(screenshot)也被同意门拦(看屏也是控机的一部分,默认全暗)。"""
        from karvyloop import i18n
        tool = _Tool("mcp_computer_use_screenshot")
        events = await _drive({"mcp_computer_use_screenshot": tool},
                              rounds=[tool_round("c1", "mcp_computer_use_screenshot", {})])
        assert tool.call_log == []
        assert _results(events)["c1"].content == {"ok": False, "error_message": i18n.t(
            "computer.control_not_enabled", tool="mcp_computer_use_screenshot")}

    @pytest.mark.asyncio
    async def test_benign_runs_when_enabled(self):
        """④ 会话同意已开 + benign 动作 → 真跑(mode=FULL 满足 computer 的 FULL 下限)。"""
        cg.enable_computer_control()
        tool = _Tool("mcp_computer_use_click")
        events = await _drive({"mcp_computer_use_click": tool},
                              rounds=[tool_round("c1", "mcp_computer_use_click",
                                                 {"selector": "button.ok"})],
                              mode=Mode.FULL)
        assert tool.call_log == [{"selector": "button.ok"}]     # 真执行了
        r = _results(events)["c1"]
        assert r.is_error is False and r.content == {"echo": {"selector": "button.ok"}}

    @pytest.mark.asyncio
    async def test_high_risk_blocked_even_when_enabled(self):
        """⑤ 已同意但高危(危险组合键)→ 仍被扣下等拍板,不执行。"""
        from karvyloop import i18n
        cg.enable_computer_control()
        tool = _Tool("mcp_computer_use_press_key")
        events = await _drive({"mcp_computer_use_press_key": tool},
                              rounds=[tool_round("c1", "mcp_computer_use_press_key",
                                                 {"keys": "ctrl+shift+delete"})],
                              mode=Mode.FULL)
        assert tool.call_log == []                       # 高危没执行
        assert _results(events)["c1"].content == {"ok": False, "error_message": i18n.t(
            "computer.high_risk_needs_confirm", tool="mcp_computer_use_press_key")}

    @pytest.mark.asyncio
    async def test_credential_type_blocked_even_when_enabled(self):
        """⑤ 已同意但键入密码域 → 仍被扣下(凭证域高危)。"""
        cg.enable_computer_control()
        tool = _Tool("mcp_computer_use_type_text")
        events = await _drive({"mcp_computer_use_type_text": tool},
                              rounds=[tool_round("c1", "mcp_computer_use_type_text",
                                                 {"selector": "input#password", "text": "s3cr3t"})],
                              mode=Mode.FULL)
        assert tool.call_log == []
        assert _results(events)["c1"].content["ok"] is False

    @pytest.mark.asyncio
    async def test_non_cc_tool_zero_regression(self):
        """⑥ 非 computer-control 工具:consent 关也照常执行(computer 门只管控机类)。"""
        read = _Tool("read_file")
        events = await _drive({"read_file": read},
                              rounds=[tool_round("c1", "read_file", {"path": "/tmp/a"})])
        assert read.call_log == [{"path": "/tmp/a"}]
        r = _results(events)["c1"]
        assert r.is_error is False and r.content == {"echo": {"path": "/tmp/a"}}

    @pytest.mark.asyncio
    async def test_cc_and_outbound_coexist(self):
        """⑥ computer 门与 outbound 草稿门同一咽喉互不干扰:一次同时调控机(截住)+ 发信(草稿)。"""
        from karvyloop import i18n
        from karvyloop.capability import outbound_gate as og
        store = og.OutboundDraftStore()
        og.register_store(store)
        try:
            cc = _Tool("mcp_computer_use_click")
            send = _Tool("mcp_gmail_send_message")
            events = await _drive(
                {"mcp_computer_use_click": cc, "mcp_gmail_send_message": send},
                rounds=[tool_round("c1", "mcp_computer_use_click", {"selector": "x"}),
                        tool_round("c2", "mcp_gmail_send_message",
                                   {"to": "a@b.com", "body": "hi"})])
            assert cc.call_log == [] and send.call_log == []     # 两个都没直执行
            res = _results(events)
            # 控机 → 同意门诚实回执;发信 → outbound 草稿回执
            assert res["c1"].content == {"ok": False, "error_message": i18n.t(
                "computer.control_not_enabled", tool="mcp_computer_use_click")}
            assert res["c2"].content == {"text": i18n.t("outbound.draft_submitted",
                                                        tool="mcp_gmail_send_message")}
            drafts = store.pop_drafts()
            assert len(drafts) == 1 and drafts[0]["tool"] == "mcp_gmail_send_message"
        finally:
            og.register_store(None)
            og.clear_curated_outbound()


# ================================================================ ⑦ 预设目录
class TestPreset:
    def test_computer_use_preset_present_and_honest(self):
        from karvyloop.console.mcp_presets import PRESETS
        p = next((x for x in PRESETS if x["id"] == "computer_use"), None)
        assert p is not None, "computer_use 预设缺失"
        assert p["command"] == "npx"            # stdio「一条命令就能跑」
        assert "mcp" in p["args_template"]       # 上游 server 子命令
        assert not p["url"]
        assert p["category"] == "app"
        assert p["needs_secret"] is False
        assert p["outbound_tools"] == []         # 桌面控制不是外发语义(另由 computer_gate 收口)
        # 风险文案诚实:点出「沙箱管不住」+ 默认关 + 高危扣下
        note = p["risk_note"]
        assert "sandbox" in note.lower() or "沙箱" in note
        assert "默认" in note or "off by default" in note.lower()

    def test_build_server_config_shape_consumable(self, tmp_path):
        """build_server_config 产出 read_mcp_server_configs 真实消费的 stdio 形状。"""
        import yaml
        from karvyloop.coding.tools.mcp_tool import read_mcp_server_configs
        from karvyloop.console.mcp_presets import build_server_config
        entry = build_server_config("computer_use", {})
        assert entry == {"name": "computer_use", "command": "npx",
                         "args": ["-y", "@agent-sh/computer-use-linux", "mcp"]}
        cfg = tmp_path / "config.yaml"
        cfg.write_text(yaml.safe_dump({"mcp": {"servers": [entry]}}, allow_unicode=True),
                       encoding="utf-8")
        (got,) = read_mcp_server_configs(str(cfg))
        assert got.name == "computer_use" and got.command == "npx"
