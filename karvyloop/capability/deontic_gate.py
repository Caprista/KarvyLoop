"""capability/deontic_gate.py — 域治理 deontic forbid 的**确定性工具闸**(docs/54 B1 Top1)。

病灶:域的 deontic.forbid 此前只是塞进 system prompt 的一段 markdown(软护栏)——
finance 模板 forbid「直接执行任何交易或转账操作」在执行路径上只是祈祷模型不做。
业界共识(NeMo Guardrails / policy-as-code 一系):prompt 内嵌规则 ≠ 控制边界,
确定性策略求值器必须放在 context window 之外。本模块补上这一层。

**分层诚实**(deontic 是自然语言,不假装全能确定性匹配):
  ① 能确定性映射到工具/命令模式的 forbid 条目(交易/转账、删除、对外发送)
     → 本闸真拦:authorize 链 step 6.5(与 fs_grants 敏感地板同层,免疫 FULL/bypass)。
  ② 纯语义 forbid(如「隐瞒下行风险只报收益」「不要用傲慢的语气」)
     → 保持 system prompt 软护栏 + 建时 LLM 冲突检测(现状),classify_forbid 里
     诚实归为 soft,**绝不声称软的变硬了**。

**误拦防护**(fail-safe 方向对齐 fs_grants:宁可漏拦,不可错拦正常事):
  - 只读工具(read_file/web_search/web_fetch/list_dir/search_code)永不被本闸拦;
  - 工具名按 token 边界匹配(不做子串,"seller_report" 不会命中 "sell");
  - 读语义前缀(get_/list_/query_…)的工具豁免(查订单≠下订单);
  - 闸内任何异常 → 当无匹配放行(authorize 绝不抛,AC9)。

**作用域**:contextvar per-run scope —— forge 在一次 run 前从 persona 上的
`deontic_forbid`(paradigm_prompt 编译时挂的机器可读属性)武装,run 结束复位。
不武装(私聊/CLI/无域)= 本闸整体 no-op,0 回归。

**防双注入**:本闸不产出任何 prompt 文本 —— deontic 编进 persona(covers_domain_governance)
管的是"说给模型听"那份;本闸管"模型说了也不算"那份。同一 persona 对象同时携带两者,
单一事实源,结构上不可能双注入。
"""
from __future__ import annotations

import contextlib
import contextvars
import dataclasses
import re
from typing import Iterable, Optional

# ---- forbid 条目 → 类别(确定性关键词分类;命不中 = soft,诚实留软约束)----

CATEGORY_TRANSACTION = "transaction"      # 交易/转账/付款类高危动作
CATEGORY_DELETE = "delete"                # 删除/清空类破坏动作
CATEGORY_EXTERNAL_SEND = "external_send"  # 对外发送(邮件/群发/发布)

# 关键词表:小写子串匹配 forbid **原文**(中文无分词,子串即语义;英文选长词避免误碰)。
_CATEGORY_KEYWORDS: dict[str, tuple[str, ...]] = {
    CATEGORY_TRANSACTION: (
        "交易", "转账", "下单", "付款", "支付", "汇款", "打款", "转钱",
        "买入", "卖出", "调仓", "建仓",
        "trade", "trading", "transfer", "payment", "remit", "place order",
        "buy or sell", "transact", "withdraw", "wire money", "move money",
    ),
    CATEGORY_DELETE: (
        "删除", "删掉", "清空", "擦除", "销毁",
        "delete", "wipe", "erase", "remove file", "rm -rf", "destroy",
    ),
    CATEGORY_EXTERNAL_SEND: (
        "外发", "对外发送", "发送邮件", "发邮件", "群发", "对外发布", "发布到", "对外公开",
        "send email", "send e-mail", "send mail", "send any email",
        "publish", "post to social", "email anyone",
    ),
}


@dataclasses.dataclass(frozen=True)
class ForbidSplit:
    """classify_forbid 的诚实分层结果。"""
    enforceable: tuple[tuple[str, str], ...]  # (category, forbid 原文)
    soft: tuple[str, ...]                     # 纯语义 → 仍走 prompt 软护栏


def classify_forbid(forbid: Iterable[str]) -> ForbidSplit:
    """把 forbid 条目分成「确定性可拦」和「纯语义(软)」两层。

    一条 forbid 可命中多个类别(如「不许转账或删除数据」→ transaction + delete)。
    命不中任何类别 = soft —— 这不是缺陷,是诚实:自然语言规则里只有映射得到
    工具/命令模式的那部分才配叫"硬闸",其余声称硬了就是假接线。
    """
    enforceable: list[tuple[str, str]] = []
    soft: list[str] = []
    for entry in forbid or ():
        text = str(entry or "").strip()
        if not text:
            continue
        low = text.lower()
        hit = False
        for category, kws in _CATEGORY_KEYWORDS.items():
            if any(k in low for k in kws):
                enforceable.append((category, text))
                hit = True
        if not hit:
            soft.append(text)
    return ForbidSplit(enforceable=tuple(enforceable), soft=tuple(soft))


# ---- per-run scope(contextvar;forge 武装,run 完复位)----

@dataclasses.dataclass(frozen=True)
class DeonticScope:
    domain: str                               # 域名(诚实 reason 用)
    entries: tuple[tuple[str, str], ...]      # 确定性可拦的 (category, 原文)
    soft: tuple[str, ...] = ()                # 软约束条目(可观测,不拦)


_SCOPE: contextvars.ContextVar[Optional[DeonticScope]] = contextvars.ContextVar(
    "deontic_scope", default=None
)


def build_scope(forbid: Iterable[str], *, domain: str = "") -> Optional[DeonticScope]:
    """forbid 原文 → scope;没有确定性可拦条目 → None(闸不武装,软护栏照旧)。"""
    split = classify_forbid(forbid)
    if not split.enforceable:
        return None
    return DeonticScope(domain=domain or "", entries=split.enforceable, soft=split.soft)


def active_scope() -> Optional[DeonticScope]:
    return _SCOPE.get()


@contextlib.contextmanager
def deontic_scope(scope: Optional[DeonticScope]):
    """在 with 块内武装 scope(None = no-op)。随块退出复位,绝不跨 run 泄漏。"""
    if scope is None:
        yield
        return
    token = _SCOPE.set(scope)
    try:
        yield
    finally:
        with contextlib.suppress(Exception):   # 跨 context 复位失败不冒泡(fail-safe)
            _SCOPE.reset(token)


def scope_from_system(system: object) -> Optional[DeonticScope]:
    """从 persona/system prompt 对象读机器可读 deontic 属性(paradigm_prompt 挂的)。

    没挂(私聊小卡/默认 coding 提示/轻量 persona)→ None = 闸不武装,0 回归。
    """
    try:
        forbid = tuple(getattr(system, "deontic_forbid", ()) or ())
        if not forbid:
            return None
        domain = str(getattr(system, "deontic_domain", "") or "")
        return build_scope(forbid, domain=domain)
    except Exception:
        return None


# ---- 确定性匹配器(工具级/命令级;宁漏勿错)----

@dataclasses.dataclass(frozen=True)
class DeonticHit:
    category: str
    source: str      # forbid 原文(诚实 reason)
    domain: str
    detail: str      # 命中了什么(工具名/命令模式)


# 永不拦的只读工具(与 policy.DEFAULT_TOOL_REQUIREMENTS 的 READ_ONLY 面一致)
_READ_ONLY_TOOLS = frozenset({
    "read_file", "list_dir", "search_code", "web_search", "web_fetch",
})
# 工具名首 token 是读语义 → 豁免(查订单/看行情 ≠ 下订单)
_READ_VERB_PREFIXES = frozenset({
    "get", "list", "read", "view", "fetch", "search", "query", "show",
    "describe", "history", "status", "check", "watch", "monitor",
})

# transaction:工具名 token 集(强 token 单独即命中;名词需配执行动词)
_TX_STRONG = frozenset({
    "transfer", "transfers", "payout", "payouts", "withdraw", "withdrawal",
    "withdrawals", "remit", "remittance", "buy", "sell", "purchase", "checkout",
})
_TX_NOUNS = frozenset({
    "trade", "trades", "trading", "order", "orders", "payment", "payments",
    "transaction", "transactions", "fund", "funds", "position", "positions", "pay",
})
_TX_VERBS = frozenset({
    "create", "place", "execute", "submit", "make", "send", "initiate",
    "confirm", "cancel", "open", "close",
})
# transaction:命令层 —— 支付类 CLI / HTTP 写方法 + 交易词
_TX_PROGRAMS = frozenset({"stripe", "paypal"})
_HTTP_PROGRAMS = frozenset({"curl", "wget", "http", "https", "httpie", "xh"})

# delete:命令层程序名(POSIX + Windows + PowerShell)
_DELETE_PROGRAMS = frozenset({
    "rm", "del", "rmdir", "rd", "erase", "shred", "unlink", "trash", "remove-item", "ri",
})

# external_send:工具名 token / 命令层邮件程序
_SEND_STRONG = frozenset({"email", "emails", "smtp", "sendmail", "mail"})
_SEND_VERB = "send"
_SEND_NOUNS = frozenset({"message", "messages", "msg", "sms", "dm", "tweet", "mail", "email"})
_MAIL_PROGRAMS = frozenset({"mail", "sendmail", "mutt", "msmtp", "mailx", "swaks"})


def _tool_tokens(tool: str) -> list[str]:
    # 对抗验收 Gap1:camelCase(placeOrder/transferFunds)先按大小写边界切开再归一,
    # 否则整串成单 token 绕过匹配(FULL 模式下会漏)。全小写连写(buyshares)无词典切不了,
    # 是诚实的保守漏拦(未知工具本就 FULL 下限 fail-closed 兜底)。
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", tool or "")
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def _command_program(command: str) -> str:
    """取命令真正执行的程序名(跳过 env 赋值/sudo,去路径与 .exe)。"""
    for raw in (command or "").strip().split():
        t = raw.strip().strip('"').strip("'")
        if not t or re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", t):   # env 赋值前缀跳过
            continue
        low = t.lower()
        if low in ("sudo", "env", "nohup", "command"):
            continue
        base = low.replace("\\", "/").rsplit("/", 1)[-1]
        if base.endswith(".exe"):
            base = base[:-4]
        return base
    return ""


def _command_tokens(command: str) -> set[str]:
    return {t for t in re.split(r"[^a-z0-9]+", (command or "").lower()) if t}


def _match_transaction(tool: str, inp: dict) -> Optional[str]:
    tokens = _tool_tokens(tool)   # tool 是**原始名**(camelCase 切分要大小写信息)
    if tokens and tokens[0] not in _READ_VERB_PREFIXES:
        tset = set(tokens)
        if tset & _TX_STRONG:
            return f"tool:{tool} 含交易执行 token {sorted(tset & _TX_STRONG)}"
        if (tset & _TX_NOUNS) and (tset & _TX_VERBS):
            return (f"tool:{tool} 含交易名词 {sorted(tset & _TX_NOUNS)}"
                    f" + 执行动词 {sorted(tset & _TX_VERBS)}")
    if tool.lower() == "run_command":
        cmd = str((inp or {}).get("command", "") or "")
        prog = _command_program(cmd)
        if prog in _TX_PROGRAMS:
            return f"command 调用支付 CLI:{prog}"
        if prog in _HTTP_PROGRAMS:
            low = cmd.lower()
            # 写方法检测:curl(-X/--request/-d/--data)+ wget(--method/--post-data/--body-data)
            # (对抗验收 Gap2:wget --method=POST 此前不识别)
            writes = bool(re.search(r"(?:^|\s)(?:-x|--request|--method)[\s=]+['\"]?(post|put)\b", low)) \
                or " --data" in low or " --post-data" in low or " --post-file" in low \
                or " --body-data" in low or " --body-file" in low \
                or re.search(r"(?:^|\s)-d\s", low)
            if writes and (_command_tokens(cmd) & (_TX_STRONG | _TX_NOUNS)):
                return f"command:HTTP 写请求携带交易词({prog})"
    return None


def _match_delete(tool: str, inp: dict) -> Optional[str]:
    if tool.lower() == "delete_file":
        return "tool:delete_file"
    if tool.lower() == "run_command":
        cmd = str((inp or {}).get("command", "") or "")
        prog = _command_program(cmd)
        if prog in _DELETE_PROGRAMS:
            return f"command 删除程序:{prog}"
        toks = cmd.lower().split()
        if prog == "git" and "rm" in toks[1:3]:
            return "command:git rm"
        if prog in ("powershell", "pwsh") and ("remove-item" in cmd.lower() or " ri " in cmd.lower()):
            return "command:powershell remove-item"
    return None


def _match_external_send(tool: str, inp: dict) -> Optional[str]:
    tokens = _tool_tokens(tool)
    low = tool.lower()
    if tokens and tokens[0] not in _READ_VERB_PREFIXES and low not in _READ_ONLY_TOOLS:
        tset = set(tokens)
        if tset & _SEND_STRONG and low != "web_fetch":
            return f"tool:{tool} 含外发 token {sorted(tset & _SEND_STRONG)}"
        if _SEND_VERB in tset and tset & _SEND_NOUNS:
            return f"tool:{tool} 含 send + {sorted(tset & _SEND_NOUNS)}"
    if low == "run_command":
        prog = _command_program(str((inp or {}).get("command", "") or ""))
        if prog in _MAIL_PROGRAMS:
            return f"command 邮件程序:{prog}"
    return None


_MATCHERS = {
    CATEGORY_TRANSACTION: _match_transaction,
    CATEGORY_DELETE: _match_delete,
    CATEGORY_EXTERNAL_SEND: _match_external_send,
}


def check_active(tool: str, inp: dict) -> Optional[DeonticHit]:
    """决策链 step 6.5 的入口:当前 scope 下这次工具调用是否命中确定性 forbid。

    未武装 scope / 只读工具 / 匹配器异常 → None(放行;宁漏勿错,authorize 绝不抛)。
    """
    try:
        scope = _SCOPE.get()
        if scope is None or not scope.entries:
            return None
        raw = (tool or "").strip()   # 保留原始大小写(camelCase 切分需要);比较处各自 lower
        if not raw or raw.lower() in _READ_ONLY_TOOLS:
            return None
        for category, source in scope.entries:
            matcher = _MATCHERS.get(category)
            if matcher is None:
                continue
            detail = matcher(raw, inp or {})
            if detail:
                return DeonticHit(category=category, source=source,
                                  domain=scope.domain, detail=detail)
        return None
    except Exception:
        return None


__all__ = [
    "CATEGORY_TRANSACTION", "CATEGORY_DELETE", "CATEGORY_EXTERNAL_SEND",
    "ForbidSplit", "classify_forbid",
    "DeonticScope", "DeonticHit", "build_scope", "active_scope",
    "deontic_scope", "scope_from_system", "check_active",
]
