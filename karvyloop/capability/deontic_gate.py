"""capability/deontic_gate.py — 域治理 deontic forbid 的**确定性工具闸**(docs/54 B1 Top1)。

病灶:域的 deontic.forbid 此前只是塞进 system prompt 的一段 markdown(软护栏)——
finance 模板 forbid「直接执行任何交易或转账操作」在执行路径上只是祈祷模型不做。
业界 guardrails / policy-as-code 一系的共识:prompt 内嵌规则 ≠ 控制边界,
确定性策略求值器必须放在 context window 之外。本模块补上这一层。

**分层诚实**(deontic 是自然语言,不假装全能确定性匹配)——「能映射才硬」现在含两路:
  ① 能确定性映射到工具/命令模式的 forbid 条目
     a. 三类语义(交易/转账、删除、对外发送)→ 类别匹配器拦;
     b. **点名了真实工具名**的条目(内测 C-03,如「禁止调用 edit_file」)→ per-tool 精确
        名字阻断。工具名是自然语言里最能确定性映射的东西:精确 token 匹配当前真实
        工具目录(atoms/tool_catalog 内置 ∪ 武装时传入的运行时工具集),绝不子串/模糊
        (「editor」不命中 edit_file)。点名 + 又含三类关键词 = 两种硬闸都挂(不互斥)。
     两路都走 authorize 链 step 6.5(与 fs_grants 敏感地板同层,免疫 FULL/bypass)。
  ② 纯语义 forbid(如「隐瞒下行风险只报收益」「不要用傲慢的语气」)、点名了**不存在**
     工具的条目、以及**唯一许可句**(「只准用 X」「仅允许 X」「除了 X 其他都不许」
     「don't use anything except X」——定向成语正则识别,**不是**通用否定语义分析;
     这类句里点名的工具是用户想**留**的,拦它=拦错方向,故不进阻断集)
     → 保持 system prompt 软护栏 + 建时 LLM 冲突检测(现状),classify_forbid 里
     诚实归为 soft,**绝不声称软的变硬了**。成语覆盖不到的边缘形态退回误硬(安全侧,
     宁紧勿松);注意「除 X 外随便用」是禁 X 本身(禁用语义),照拦不豁免。

**误拦防护**(fail-safe 方向对齐 fs_grants:宁可漏拦,不可错拦正常事):
  - 只读工具(read_file/web_search/web_fetch/list_dir/search_code)永不被**三类闸**拦;
    **例外(取舍锁死)**:被 forbid **点名**的工具哪怕只读也拦 —— 三类闸靠语义推断,
    只读豁免防的是推断误伤;点名闸零推断(用户原文点的就是这个名字),用户意图明确
    优先,不存在"误拦正常事"。
  - 工具名按 token 边界匹配(不做子串,"seller_report" 不会命中 "sell");
  - 读语义前缀(get_/list_/query_…)的工具豁免(查订单≠下订单;同样不适用于点名闸);
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
CATEGORY_NAMED_TOOL = "named_tool"        # forbid 原文点名了真实工具名 → per-tool 精确阻断(C-03)

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
    # C-03 点名工具:(forbid 原文, (被点名的真实工具名(归一后), ...))。
    # 点名条目同时也以 (CATEGORY_NAMED_TOOL, 原文) 出现在 enforceable 里(武装判定共用一处)。
    named: tuple[tuple[str, tuple[str, ...]], ...] = ()


# 拉丁 token:字母开头,后接字母/数字/_/-(\b 词边界语义;中文语境下工具名本就是拉丁串,
# 中文字符天然是分隔符 →「不许edit_file」照样切出 edit_file)。
_LATIN_TOKEN_RE = re.compile(r"[A-Za-z][A-Za-z0-9_\-]*")


def _norm_tool_name(name: str) -> str:
    """小写 + 连字符→下划线('web-search' 认出 web_search,产品既有归一,不是模糊匹配)。
    **不做空格归一**(对抗验收清死代码):forbid 文本 token 按空格断词、运行时工具名受
    LLM API 约束([a-zA-Z0-9_-])都不可能带空格 —— 多词/空格写法不算点名,要写注册名。"""
    return (name or "").strip().lower().replace("-", "_")


def _known_tool_names(extra: Iterable[str] = ()) -> frozenset[str]:
    """真实工具名全集(归一后)= 内置目录 ∪ 调用方传入的运行时工具集(如 MCP 注入)。

    目录唯一事实源 = atoms/tool_catalog.BUILTIN_TOOL_NAMES(**绝不在这里硬编码清单**);
    运行时以 authorize 时真实工具集为准 —— forge 武装 scope 时把本次 run 的 tools.keys()
    传进来,MCP/registry 注入的工具名一并可点名。延迟导入防循环(atoms.executor →
    capability);导入失败 → 只用 extra(fail-safe:点名闸退化为少武装,三类闸不受影响)。
    """
    names: set[str] = set()
    try:
        from karvyloop.atoms.tool_catalog import BUILTIN_TOOL_NAMES
        names.update(BUILTIN_TOOL_NAMES)
    except Exception:
        pass
    names.update(str(n) for n in (extra or ()))
    return frozenset(_norm_tool_name(n) for n in names if str(n).strip())


def _named_tools_in(text: str, known: frozenset[str]) -> tuple[str, ...]:
    """条目文本里点名的真实工具名(精确 token 匹配;绝不子串/模糊:「editor」不命中 edit_file,
    「edit」也不命中(edit_file 才是工具名)。点名不存在的工具 = 不算(诚实降 soft)。"""
    found: list[str] = []
    for tok in _LATIN_TOKEN_RE.findall(text or ""):
        n = _norm_tool_name(tok)
        if n in known and n not in found:
            found.append(n)
    return tuple(found)


# ---- 唯一许可句定向豁免(对抗验收非阻塞项 1)----
# **这是定向成语豁免,不是自然语言否定语义分析**:「只准用 X」「仅允许 X」「除了 X 其他
# 都不许」「don't use anything except X」里点名的工具是用户想**留**的,把它进阻断集 =
# 拦错工具、掐断域功能。只识别下面这一小撮成语(中英各几条,正则);命中 → 该条目里
# 点名的工具不进阻断集,条目照旧走 soft/类别闸。成语覆盖不到的边缘形态(如
# 「never use edit_file except …」「do not call any tool except X」的变体)退回误硬 =
# 安全侧,宁紧勿松。普通禁用句(「禁止 X」「别用 X」「除 X 外随便用」)绝不命中。
_WHITELIST_IDIOMS: tuple[re.Pattern, ...] = (
    # zh 唯一许可动词:只准(用)/只允许/仅允许/只许/仅限/只能用…
    re.compile(r"只准|只允许|仅允许|只许|仅限|只能(?:用|使用|调用)"),
    # zh「除(了) X (之)外,其他/其余…不许/禁止」——与「除 X 外随便用」的区分键在后半句:
    # 禁的是「其他」→ X 是要留的(豁免);后半句是许可(随便用)→ 禁的是 X 本身(照拦)。
    # 负向后顾防「删除/清除/解除/排除…」词内的「除」字误触发。
    re.compile(r"(?<![删清扫拆解免排])除了?[^。;;]{0,30}?(?:其他|其它|其余|别的)"
               r"[^。;;]{0,15}?(?:不许|不准|不得|禁止|禁用|禁)"),
    # en:only use X / use only X
    re.compile(r"\bonly\s+(?:use|call|run|invoke|allow)\b|\b(?:use|call)\s+only\b",
               re.IGNORECASE),
    # en:don't/never use anything|any (other) tools|everything … except X
    # (except 后面才是要留的工具;「never use edit_file except …」禁的是点名工具本身,
    # 不豁免 —— 退回误硬即安全侧)
    re.compile(r"\b(?:don'?t|do\s+not|never)\s+(?:use\s+|call\s+|run\s+|touch\s+)?"
               r"(?:anything|any\s+(?:other\s+)?tools?|any\s+other|other\s+tools?|everything)"
               r"[^.;]{0,40}?\bexcept\b", re.IGNORECASE),
)


def _is_whitelist_idiom(text: str) -> bool:
    """条目是否为「唯一许可句」(定向成语,见 _WHITELIST_IDIOMS 注释;非语义分析)。"""
    return any(p.search(text or "") for p in _WHITELIST_IDIOMS)


def classify_forbid(forbid: Iterable[str], *, known_tools: Iterable[str] = ()) -> ForbidSplit:
    """把 forbid 条目分成「确定性可拦」和「纯语义(软)」两层。

    确定性可拦两路(不互斥,一条 forbid 可同时挂多个):
      - 三类语义关键词(如「不许转账或删除数据」→ transaction + delete);
      - 点名真实工具名(「禁止调用 edit_file」→ named_tool,per-tool 精确阻断;C-03)。
        known_tools = 本次运行时真实工具集(forge 传 tools.keys()),与内置目录取并集。
    命不中任何一路 = soft —— 这不是缺陷,是诚实:自然语言规则里只有映射得到
    工具/命令模式的那部分才配叫"硬闸",其余声称硬了就是假接线。

    **唯一许可句豁免**(定向,非通用否定语义分析):条目命中 _WHITELIST_IDIOMS
    (「只准用 X」「仅允许 X」「除了 X 其他都不许」「don't use anything except X」)
    → 该条目点名的工具**不进**阻断集(那是用户要留的工具),条目照旧走 soft/类别闸。
    注意豁免按**整条目**生效:一条里既写显式禁用又写唯一许可(「不许 A,只准用 B」)
    的复合句,点名闸整体退软(类别闸不受影响)——一条规则写一件事,禁用请单列条目。
    成语覆盖不到的形态退回误硬(安全侧);「除 X 外随便用」是禁 X,照拦。
    """
    enforceable: list[tuple[str, str]] = []
    soft: list[str] = []
    named: list[tuple[str, tuple[str, ...]]] = []
    known = _known_tool_names(known_tools)
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
        named_hits = _named_tools_in(text, known)
        if named_hits and _is_whitelist_idiom(text):
            named_hits = ()   # 唯一许可句:点名的是要**留**的工具 → 不进阻断集(定向豁免)
        if named_hits:
            enforceable.append((CATEGORY_NAMED_TOOL, text))
            named.append((text, named_hits))
            hit = True
        if not hit:
            soft.append(text)
    return ForbidSplit(enforceable=tuple(enforceable), soft=tuple(soft), named=tuple(named))


# ---- per-run scope(contextvar;forge 武装,run 完复位)----

@dataclasses.dataclass(frozen=True)
class DeonticScope:
    domain: str                               # 域名(诚实 reason 用)
    entries: tuple[tuple[str, str], ...]      # 确定性可拦的 (category, 原文)
    soft: tuple[str, ...] = ()                # 软约束条目(可观测,不拦)
    # C-03:点名工具阻断集 (forbid 原文, (归一工具名, ...))——check_active 精确名匹配用
    named: tuple[tuple[str, tuple[str, ...]], ...] = ()


_SCOPE: contextvars.ContextVar[Optional[DeonticScope]] = contextvars.ContextVar(
    "deontic_scope", default=None
)


def build_scope(forbid: Iterable[str], *, domain: str = "",
                known_tools: Iterable[str] = ()) -> Optional[DeonticScope]:
    """forbid 原文 → scope;没有确定性可拦条目 → None(闸不武装,软护栏照旧)。

    known_tools:本次运行时真实工具集(点名闸以它 ∪ 内置目录为准;不传 = 只认内置)。"""
    split = classify_forbid(forbid, known_tools=known_tools)
    if not split.enforceable:
        return None
    return DeonticScope(domain=domain or "", entries=split.enforceable,
                        soft=split.soft, named=split.named)


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


def scope_from_system(system: object, *,
                      known_tools: Iterable[str] = ()) -> Optional[DeonticScope]:
    """从 persona/system prompt 对象读机器可读 deontic 属性(paradigm_prompt 挂的)。

    没挂(私聊小卡/默认 coding 提示/轻量 persona)→ None = 闸不武装,0 回归。
    known_tools:武装方(forge)传本次 run 的真实工具集 —— 点名闸「运行时以 authorize 时
    真实工具集为准」的接线口(MCP/registry 注入的工具名靠它可点名)。
    """
    try:
        forbid = tuple(getattr(system, "deontic_forbid", ()) or ())
        if not forbid:
            return None
        domain = str(getattr(system, "deontic_domain", "") or "")
        return build_scope(forbid, domain=domain, known_tools=known_tools)
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
    "reconcile_receipt",   # 纯算术、无副作用 → 永不拦
    "recall_memory",       # 只读召回个人记忆(karvy/tools.py)、无副作用 → 永不拦
    "list_external_agents",  # 只读列举已接入外部同事(karvy/tools.py)、无副作用 → 永不拦
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

    未武装 scope / 只读工具(仅对三类闸;被点名的工具只读也拦)/ 匹配器异常
    → None(放行;宁漏勿错,authorize 绝不抛)。
    """
    try:
        scope = _SCOPE.get()
        if scope is None or not scope.entries:
            return None
        raw = (tool or "").strip()   # 保留原始大小写(camelCase 切分需要);比较处各自 lower
        if not raw:
            return None
        # C-03 点名工具阻断:精确名匹配,**放在只读豁免之前**。取舍(锁死):用户指名道姓
        # 禁一个工具,哪怕它只读(read_file/web_search)也拦 —— 三类闸靠语义推断,只读豁免
        # 防的是推断误伤;点名闸零推断(forbid 原文点的就是这个名字),用户意图明确优先,
        # 不存在"误拦正常事"。同理读语义前缀豁免(get_/list_…)也不适用于点名闸。
        if scope.named:
            norm = _norm_tool_name(raw)
            for source, names in scope.named:
                if norm in names:
                    return DeonticHit(category=CATEGORY_NAMED_TOOL, source=source,
                                      domain=scope.domain,
                                      detail=f"tool:{raw} 被该 forbid 条目点名禁止(精确名匹配)")
        if raw.lower() in _READ_ONLY_TOOLS:
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
    "CATEGORY_TRANSACTION", "CATEGORY_DELETE", "CATEGORY_EXTERNAL_SEND", "CATEGORY_NAMED_TOOL",
    "ForbidSplit", "classify_forbid",
    "DeonticScope", "DeonticHit", "build_scope", "active_scope",
    "deontic_scope", "scope_from_system", "check_active",
]
