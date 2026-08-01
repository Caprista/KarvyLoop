"""capability/computer_gate.py — docs/99 刀1:computer use 会话同意门 + 确定性高危弹卡骨架.

**病根 / 为什么需要独立一层**:直控本机的 computer use = 手里的真枪 —— 鼠标键盘打到**真桌面**,
**进程沙箱管不住输入**(输入本就要到真屏,沙箱只关得住工具进程自己读写的东西,关不住它发给
真桌面的点击;docs/99 §一 写死)。所以 computer-control 的收口**不能靠进程沙箱**,靠这层
结构闸 —— 与 outbound_gate 同「执行唯一咽喉截住」哲学(atoms/executor,run_tools 之前)。

**两道结构闸(确定性,零 LLM,绝不 fail-open 放行控机)**:

  1. **会话级显式同意(默认拒)**:没人显式开过「允许控制我的机器」→ computer-control 类工具
     一律**不执行**,合成诚实回执让模型改道(不是崩,是「这台机器的控制权没交给你」)。
     顺序铁律(docs/99 §五):刀1 **没有 UI 开同意** → 端用户开不了 → 能力默认全暗;
     知情同意面 / 火灾停止键 / 会话级细化在刀3-刀4。开同意 = `enable_computer_control()`
     (刀1 只有 dev/测试调得到 = 「留 flag 后开发用」)。

  2. **确定性高危分类(会话已同意后仍拦)**:键入密码/凭证域、危险组合键(永久删除/清空类)、
     疑似不可逆 —— 结构性该升 H2A 决策卡由你拍板。刀1 骨架:高危 → deny-pending-confirm 诚实
     回执(不执行);刀3 升成真决策卡 + 对抗验收。**诚实难点**(docs/99 §六):一次 click 危不危险
     取决于点的是什么(要看懂 UI),不像 outbound 有工具名 —— **不吹每动作完美拦**,首发只拦
     确定性可判的高危 + 会话同意 + 模型自标,内测标定。

**判定面覆盖谁**:`computer`(第 7 真工具编排器,实现在刀2)+ `mcp_computer_use_*`(消费的
computer-use plumbing 的原始工具:screenshot/click/type_text/press_key/set_value/…)。两者
都过这层;plumbing 的 capability 下限另在 policy.required_mode 提到 FULL(默认拒)。

**异常方向(写死别翻,同 outbound_gate)**:分类失败(input 形态诡异)→ **fail-safe 当高危**
(要确认),绝不 fail-open 放行控机。会话同意判定是纯布尔读,不抛。
"""
from __future__ import annotations

import re
from typing import Any

# 消费的 computer-use plumbing 的 server 名(= mcp_presets 里的预设 id;config.yaml server name)。
# MCP 工具名 = f"mcp_{server}_{tool}"(coding/tools/mcp_tool.py)→ 前缀 mcp_computer_use_ 。
#
# **已知局限(刀1,对抗验收 FINDING B)**:两道闸都按这个 server 名认(policy.required_mode 复用
# 本模块 is_computer_control_tool 同一谓词,单一事实源、不漂移)。但若**同一** computer-use server
# 被人工在 config.yaml 里改名接入,其工具 mcp_<别名>_* 认不出 → 两道闸跳过。属人工误配(角色/atom
# 无权加 server,只有人能改),不在基线威胁模型内;签名式识别(按动作名认、不认 server 名)留刀3+
# (避免与浏览器类 MCP 的 click/type 误撞)。预设路径强制 name=computer_use,正常接入永远对齐。
COMPUTER_SERVER = "computer_use"
_TOOL_PREFIX = f"mcp_{COMPUTER_SERVER}_"
# 第 7 真工具编排器名(实现在刀2;刀1 先让门认得它,capability 已提 FULL)。
REAL_COMPUTER_TOOL = "computer"


def is_computer_control_tool(tool: str) -> bool:
    """这个工具是否属「控制本机桌面」类:编排器 computer,或 computer-use plumbing 的 mcp 工具。"""
    t = str(tool or "").strip()
    return t == REAL_COMPUTER_TOOL or t.startswith(_TOOL_PREFIX)


# ---- 闸 1:会话级显式同意(默认拒)-------------------------------------------------
# 全局开关(刀1 骨架:进程级即可,会话级细化 + UI 在刀4 —— 那时按 session 键控;现在没 UI
# 开 = 端用户天然开不了 = 默认全暗)。与 outbound_gate 的 _curated_servers 同款全局注册表模式。
_consent_enabled: bool = False


def enable_computer_control() -> None:
    """显式打开「允许控制我的机器」(刀1:只有 dev/测试/未来 UI 调得到)。"""
    global _consent_enabled
    _consent_enabled = True


def disable_computer_control() -> None:
    global _consent_enabled
    _consent_enabled = False


def is_computer_control_enabled() -> bool:
    return _consent_enabled


def clear_computer_gate() -> None:
    """测试/重载:复位到默认拒(会话同意关)。"""
    global _consent_enabled
    _consent_enabled = False


# ---- 知情授权(会话级显式同意的"怎么开")----------------------------------------
# enable_computer_control() 是**开关**;真实用户绝不该被**静默**替他开。开它必须走知情授权:
# 明确告诉用户在授权什么(看你的屏 + 控你的鼠标键盘)+ 让用户当场选。Windows 尤其关键 —— OS 本身
# **不弹权限框**,这是唯一"用户知不知道"的关卡。UI 无关(prompt_fn 由调用方给:CLI 终端问 /
# 未来控制台弹卡),逻辑单一事实源在这。

CONSENT_NOTICE = (
    "computer use 会让这个 agent **看你的屏幕**、并**代你操作鼠标和键盘**(本次会话)。\n"
    "这是最高信任级能力:进程沙箱管不住打到真桌面的输入。高危/不可逆动作(往密码框键入、"
    "永久删除类组合键)仍会被扣下要你二次确认。跑完即止,不常驻。"
)


def request_consent(*, assume_yes: bool = False, prompt_fn=None) -> bool:
    """知情授权:决定要不要开机器控制,获授权时 enable_computer_control。返回是否已授权。

    - assume_yes(用户**显式预授权**,如 CLI --yes,或未来"本会话记住")→ 直接开(用户已明示)。
    - 否则有 prompt_fn(交互:CLI 终端 / 控制台卡)→ 展示 CONSENT_NOTICE 问,只在**明确同意**才开。
    - 否则(非交互 + 无预授权)→ **拒**(安全默认,绝不静默开)。
    prompt_fn 崩了 → 当拒(fail-safe,绝不因异常放开控机)。
    """
    if assume_yes:
        enable_computer_control()
        return True
    if prompt_fn is not None:
        try:
            ok = bool(prompt_fn(CONSENT_NOTICE))
        except Exception:
            ok = False
        if ok:
            enable_computer_control()
        return ok
    return False


# ---- 闸 2:确定性高危分类 --------------------------------------------------------

# 键入凭证域的信号 token(目标字段名 / 选择器 / 角色里出现即高危)。归一小写。
_CRED_HINTS = frozenset({
    "password", "passwd", "pwd", "secret", "credential", "credentials",
    "pin", "otp", "2fa", "totp", "mfa", "cvv", "cvc", "ccv",
    "ssn", "passphrase", "mnemonic", "seedphrase", "privatekey", "apikey", "token",
})
# 键入 / 写值类动作(computer-use plumbing 短名 + 常见同义)。
_TYPE_ACTIONS = frozenset({
    "type_text", "type", "set_value", "setvalue", "fill", "insert_text", "input_text", "write_text",
})
# 按键 / 组合键类动作。
_KEY_ACTIONS = frozenset({
    "press_key", "presskey", "key", "press", "hotkey", "keychord", "chord", "send_keys", "sendkeys",
})
# 动作名**含**这些 token 即当按键类(不写死枚举)—— 上游若把工具叫 keyboard/shortcut/
# key_combo,或编排器 computer 没带 action 字段,危险 chord 也别从 gate-2 溜过(对抗验收 FINDING A)。
_KEY_ACTION_HINTS = ("key", "press", "hotkey", "chord", "shortcut", "combo", "keyboard")
# 危险按键组合(不可逆 / 清空类)——按归一后的 modifier+key token 集合做「子集命中」。
# 刀1 只列几乎无歧义的毁灭性 chord;刀3 内测标定扩展。
_DANGEROUS_CHORDS: tuple[frozenset[str], ...] = (
    frozenset({"shift", "delete"}),            # Windows 永久删除(绕过回收站)
    frozenset({"shift", "del"}),
    frozenset({"ctrl", "shift", "delete"}),    # 清浏览数据 / 某些应用「清空」
    frozenset({"ctrl", "shift", "del"}),
    frozenset({"cmd", "delete"}),              # macOS 移到废纸篓
    frozenset({"cmd", "shift", "delete"}),     # macOS 清空废纸篓
    frozenset({"meta", "shift", "delete"}),
    frozenset({"super", "delete"}),
)
# 归一:小写 + 常见别名折叠(control→ctrl / option→alt / command|win|super→cmd/meta 保留两写)。
_KEY_ALIASES = {
    "control": "ctrl", "option": "alt", "opt": "alt", "command": "cmd",
    "win": "super", "windows": "super", "return": "enter",
}


def _norm(s: Any) -> str:
    return str(s or "").strip().lower()


def _word_tokens(s: str) -> set[str]:
    """字符串 → 归一词 token 集(camelCase 拆开 + 非字母数字切分)。"""
    x = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", str(s or ""))
    return {t for t in re.split(r"[^a-z0-9]+", x.lower()) if t}


def _chord_keys(s: str) -> set[str]:
    """一个 chord 串(如 'Ctrl+Shift+Delete' / 'cmd shift del')→ 归一 key token 集。"""
    parts = re.split(r"[+\-\s,]+", str(s or "").lower())
    out: set[str] = set()
    for p in parts:
        p = p.strip()
        if p:
            out.add(_KEY_ALIASES.get(p, p))
    return out


def _all_strings(obj: Any, _depth: int = 0) -> list[str]:
    """收集结构里**所有**字符串(dict 键 + dict 值 + list/tuple 元素,递归)——高危扫描面。
    **列表元素也收**(如 press_key 的 chord 传成 ["ctrl","shift","delete"]、selector 传成
    ["#password"],或 {modifiers:[...], key:"delete"} 分开传)——否则高危键会从列表里溜过。
    有界深度防病态嵌套;绝不抛。"""
    out: list[str] = []
    if _depth > 6:
        return out
    try:
        if isinstance(obj, str):
            if obj:
                out.append(obj)
        elif isinstance(obj, dict):
            for k, v in obj.items():
                if k:
                    out.append(str(k))
                out.extend(_all_strings(v, _depth + 1))
        elif isinstance(obj, (list, tuple)):
            for v in obj:
                out.extend(_all_strings(v, _depth + 1))
    except Exception:
        pass
    return out


def _short_action(tool: str, tool_input: dict) -> str:
    """把工具名规约成动作短名:mcp_computer_use_press_key → press_key;
    编排器 computer → input['action']。"""
    t = str(tool or "").strip()
    if t.startswith(_TOOL_PREFIX):
        return t[len(_TOOL_PREFIX):].strip().lower()
    if t == REAL_COMPUTER_TOOL and isinstance(tool_input, dict):
        return _norm(tool_input.get("action") or tool_input.get("type"))
    return t.lower()


def classify_action(tool: str, tool_input: Any) -> tuple[bool, str]:
    """确定性高危分类。返回 (is_high_risk, reason)。非 computer-control 工具 → (False,"")。

    分类失败(input 形态诡异抛错)→ **fail-safe 当高危**(要确认),绝不 fail-open。
    """
    if not is_computer_control_tool(tool):
        return False, ""
    inp = tool_input if isinstance(tool_input, dict) else {}
    try:
        action = _short_action(tool, inp)
        strings = _all_strings(inp)
        # 危险组合键:把 input 里**所有** key token 并成一个集合 —— chord 传成
        # "ctrl+shift+delete" 串、["ctrl","shift","delete"] 列表、或 {modifiers:[...],
        # key:"delete"} 分字段传,并集后都判得出 —— 再看是否 ⊇ 某个危险 chord(防列表/分字段规避)。
        is_key = (action in _KEY_ACTIONS
                  or any(h in action for h in _KEY_ACTION_HINTS))
        if is_key or not action:
            key_tokens: set[str] = set()
            for s in strings:
                key_tokens |= _chord_keys(s)
            if any(danger <= key_tokens for danger in _DANGEROUS_CHORDS):
                return True, "dangerous_key_combo"
        # 键入凭证域:type/set_value/click 类,任一字符串(字段名/选择器/角色/值,含列表/嵌套)
        # 含凭证 token → 高危。
        if action in _TYPE_ACTIONS or action == "click" or not action:
            for s in strings:
                if _word_tokens(s) & _CRED_HINTS:
                    return True, "credential_field"
        return False, ""
    except Exception:
        # 判定崩了:宁可当高危拦下要确认,也绝不放行一次不明的控机动作。
        return True, "unclassifiable"


def is_high_risk_action(tool: str, tool_input: Any) -> bool:
    return classify_action(tool, tool_input)[0]


__all__ = [
    "COMPUTER_SERVER", "REAL_COMPUTER_TOOL", "is_computer_control_tool",
    "enable_computer_control", "disable_computer_control", "is_computer_control_enabled",
    "clear_computer_gate", "classify_action", "is_high_risk_action",
    "request_consent", "CONSENT_NOTICE",
]
