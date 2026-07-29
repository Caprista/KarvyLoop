"""capability/outbound_gate.py — 连接器程刀0:**对外发送永不直发,一律草稿决策卡**(docs/96 刀0)。

病根:external_send 硬闸(deontic_gate)只在域 persona 带 deontic_forbid 时武装 ——
私聊/CLI/无域场景下,MCP 发信类工具(如接上 Gmail 后的 mcp_gmail_send_message)直接
放行执行,无任何拍板,违背「要对外的都拍板」。本模块补上**全局**的一层(不依赖域 forbid):

    agent 调用判定为「对外发送类」的工具
      → 执行唯一咽喉(atoms/executor step 6,run_tools 之前)截住,**不真执行**
      → 这次调用(工具名 + 完整入参)记进全局草稿队列(note_draft,fs_grants 同款注册表模式)
      → agent 收到诚实合成回执「草稿已递交等主人拍板(不会自动发出)」,可继续干别的
      → console 在 drive 收尾把草稿升成 H2A 决策卡(kind=outbound_draft ∈ HIGH_RISK_KINDS,
        绝不被静音,逐张拍)
      → ACCEPT = handler 真调原工具原参数发送;REJECT = 丢弃(零副作用)
    域 deontic_forbid 硬闸照旧**叠加在前**:域里禁了外发 → authorize 直接 Deny,连草稿卡都不出。

**判定面**(is_outbound_send_tool,纯工具名确定性判定,零 LLM):
  - 策展元数据显式标注 > 名字猜测:MCP server 配置/预设可带 `outbound_tools: [...]`
    (register_curated_outbound 注册,归一精确匹配)——显式标注的工具即使名字判不出也拦。
  - 名字猜测走共享 token 表(与 deontic_gate 的 external_send 匹配器**共用这一份**,别写两份):
    强 token(email/smtp/mail…)单独即命中;发送动词(send/reply/post/publish/tweet/dm/notify…)
    需配消息名词(message/sms/dm/tweet/comment…);create/compose/draft + message/email 组合形态也算。
  - **保守边界(宁漏勿误拦读操作)**:名字里出现读语义 token(get/list/search/read…,**任意位置**,
    不只首 token —— MCP 名带 `mcp_<server>_` 前缀,首 token 判定对它失效)→ 绝不判外发;
    出现非发送动作动词(delete/archive/cancel…)且无显式发送动词 → 不判(删推文≠发推文);
    拿不准的名字不拦 —— 漏的兜底是刀1 策展元数据显式标注。
  - 诚实边界:`run_command` 里跑 mail CLI 不在本刀判定面(那半仍由 deontic 域闸 + 沙箱管);
    本刀盖的是**工具级**调用(MCP 工具 + 未来内置发送工具)。

**自有内置工具核对**(BUILTIN_TOOL_NAMES,docs/96 刀0 梳理结论):run_command/read_file/
write_file/edit_file/web_fetch/web_search/reconcile_receipt/create_schedule/remember_fact/
recall_memory/create_role/create_domain/external_agent(起外部子进程,FULL 闸另管)/
attach_/list_/revoke_external_agent —— **没有一个落在"对外发送"语义**(channels 的 email
digest 是系统通道非 agent 工具;web_fetch 是读)。判定表对内置工具全量零命中,有测试锁着。

**fail-closed**:--no-llm/测试桩/无 store(CLI 裸跑)场景,截住但注册不了草稿 →
回执=「外发通道未接,草稿未能递交」,**绝不放行直发**。安全方向只收紧不放松。

凭证纪律:入参进卡(用户自己的数据,可核);**绝不进 log**(本模块日志只记工具名,不记 input)。
"""
from __future__ import annotations

import logging
import re
import time
import uuid
from typing import Iterable, Optional

logger = logging.getLogger(__name__)


# ---- 共享 token 表(deontic_gate.external_send 匹配器与本刀共用,单一事实源)----

# 强 token:单独出现(且无读语义/非发送动作豁免)即判外发。
SEND_STRONG = frozenset({"email", "emails", "smtp", "sendmail", "mail"})
# 发送动词(宽面):需配消息名词(send_message / post_comment / publish_post / createEmail /
# chat.scheduleMessage / chat.meMessage…)。create/compose 属"组合形态"动词:create_message/
# create_email 这类也算;schedule/share/chat 为对抗验收 LEAK 补漏(scheduleMessage=延迟直发、
# meMessage=真发消息)。**draft 不在此列**(对抗验收:draft_email 只建草稿不发,拦了是误拦
# 方向 —— 它进 NON_SEND_ACTION_VERBS 当豁免;send_draft 因显式 send 占先仍拦)。
SEND_VERBS = frozenset({
    "send", "sends", "reply", "replies", "respond", "forward",
    "post", "publish", "tweet", "dm", "notify", "broadcast", "announce",
    "create", "compose", "schedule", "share", "chat",
})
# 消息名词(发送动词的宾语面)。
SEND_NOUNS = frozenset({
    "message", "messages", "msg", "sms", "dm", "dms", "tweet", "tweets",
    "mail", "email", "emails", "reply", "replies", "comment", "comments",
    "notification", "notifications", "post", "posts", "letter", "newsletter",
})
# 内容/载荷名词(对抗验收 LEAK 补漏):只与**真送出动词**(TRANSMIT_VERBS)组合才算外发 ——
# send_file/share_photo/forward_document 拦;create_file/create_invoice(只建不发)不拦。
CONTENT_NOUNS = frozenset({
    "file", "files", "invoice", "invoices", "media", "document", "documents",
    "voice", "attachment", "attachments", "photo", "photos", "video", "videos",
})
# 自足动词(弱):宾语隐含就是"一条消息",单独出现即判外发 —— 但**读语义 token 同现时读
# 占先**(get_send_history 是读不是发)。对抗验收 LEAK 补:**裸 send/broadcast 自足**
# (mcp_gmail_gmail_send / mcp_resend_send / broadcast 都是真实发送名;裸 send 的唯一排除
# = send_keys/send_key 输入模拟类,见 _INPUT_SIM_PAIRS)。
SEND_SELF_SUFFICIENT = frozenset({"tweet", "dm", "sms", "notify", "send", "sends", "broadcast"})
# 自足动词(强):reply 的对象天然是"来信",**压过读语义 token**(对抗验收:
# mcp_gmail_get_thread_and_reply 名含 get 但真会发,必须拦;get_send_history 无 reply 仍是读)。
REPLY_OVERRIDE = frozenset({"reply", "replies"})
# 命令层邮件程序(deontic 域闸的 run_command 半用;本刀不判 run_command,见模块头诚实边界)。
MAIL_PROGRAMS = frozenset({"mail", "sendmail", "mutt", "msmtp", "mailx", "swaks"})

# 读语义 token:名字里**任意位置**出现 → 不判外发(纯读绝不误判;mcp_gmail_list_emails
# 首 token 是 "mcp",按首 token 判读对 MCP 名失效,故这里按"出现即读"保守豁免)。
# 唯一例外 = REPLY_OVERRIDE(见上)。表本体与 deontic_gate._READ_VERB_PREFIXES 同源
# (deontic 从这里 import,首 token 语义不变)。
READ_VERB_PREFIXES = frozenset({
    "get", "list", "read", "view", "fetch", "search", "query", "show",
    "describe", "history", "status", "check", "watch", "monitor",
})
# 非发送动作动词:出现且**无显式发送动词**同现 → 不判外发(delete_tweet 是删不是发;
# archive_mail 是归档;draft_email/update_draft 只建/改草稿)。显式发送动词同现时发送
# 语义占先(forward_email 照拦;send_draft 真发,照拦)。
NON_SEND_ACTION_VERBS = frozenset({
    "delete", "remove", "cancel", "archive", "trash", "unsend",
    "mark", "update", "edit", "modify", "label", "star", "unstar", "mute", "draft",
})
# NON_SEND 豁免里"显式发送动词"= 无歧义的**发送动作本身**(send/forward/publish/share…)。
# 不含 create/compose(语义太宽,压不过 delete/update),也不含 tweet/dm/reply/post
# (它们兼作名词:delete_tweet/delete_post 是删不是发,必须能被豁免)。
_EXPLICIT_SEND_VERBS = frozenset({
    "send", "sends", "forward", "respond", "broadcast", "announce", "publish", "share",
})
# 真送出动词(CONTENT_NOUNS 的配对面):把已有内容**递出去**的动作。
TRANSMIT_VERBS = frozenset({"send", "sends", "forward", "share", "broadcast"})
# "add" 特例:只与核心消息名词组合(korotovsky slack server 真名 conversations_add_message);
# 不进 SEND_VERBS 宽面 —— add_issue_comment/add_reaction 是"往对象上挂东西",不算发送。
_ADD_MESSAGE_NOUNS = frozenset({"message", "messages", "msg"})
# 输入模拟类排除(裸 send 自足的**唯一**排除,对抗验收锁死口径):send_keys/send_key =
# 浏览器/GUI 击键,不是对外发信 —— 按**相邻 token 对**匹配(browser_send_keys 也豁免;
# send_key_message 之类不误免 —— 后续规则该拦照拦)。
_INPUT_SIM_PAIRS = (("send", "keys"), ("send", "key"))


def tool_tokens(tool: str) -> list[str]:
    """工具名 → 归一 token 列(camelCase 先按大小写边界切开再小写;与 deontic_gate 同一实现,
    deontic 从这里 import —— 单一事实源)。全小写连写(buyshares)无词典切不了,诚实保守漏。"""
    s = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", tool or "")
    return [t for t in re.split(r"[^a-z0-9]+", s.lower()) if t]


def _norm_name(name: str) -> str:
    """策展名单/工具名归一:小写 + 连字符→下划线(与 deontic_gate._norm_tool_name 同口径)。"""
    return (name or "").strip().lower().replace("-", "_")


# ---- 策展元数据(显式 outbound_tools 标注;显式 > 名字猜测)----

_curated_outbound: set[str] = set()


def register_curated_outbound(names: Iterable[str]) -> None:
    """登记策展标注的外发工具名(MCP server 配置 `outbound_tools:` / 预设;console 接入时调)。
    归一后存;显式标注的工具即使名字判不出也拦(刀1 会消费同一份标注)。"""
    for n in names or ():
        s = _norm_name(str(n))
        if s:
            _curated_outbound.add(s)


def curated_outbound() -> frozenset[str]:
    return frozenset(_curated_outbound)


def clear_curated_outbound() -> None:
    """测试/重载用。"""
    _curated_outbound.clear()


# ---- 判定函数(确定性,零 LLM,绝不抛)----

def is_outbound_send_tool(tool: str, *, extra_outbound: Iterable[str] = ()) -> bool:
    """这个工具名是否属「对外发送类」(docs/96 刀0 判定面)。

    判定序(短路;对抗验收 65 名真实工具判定表锁着,tests/test_outbound_draft.py):
      1. 策展显式标注(全局登记 ∪ extra_outbound)精确命中 → True(显式 > 猜测)。
      2. 输入模拟排除:相邻 (send, keys|key) token 对且无消息名词 → False(击键≠发信)。
      3. 非发送动作动词(delete/archive/update/draft…)且无显式发送动词 → False(删/改/建草稿≠发)。
      4. reply 压过读语义 → True(get_thread_and_reply 真会发)。
      5. 读语义 token 任意位置出现 → False(纯读绝不误判;get_send_history 是读)。
      6. 强 token(email/smtp/mail…)→ True。
      7. 弱自足动词(send/broadcast/tweet/dm/sms/notify)→ True。
      8. 发送动词 × 消息名词 → True;"add" 特例只配核心消息名词(conversations_add_message)。
      9. 真送出动词 × 内容名词(send_file/share_photo/forward_document)→ True。
      10. 其余 → False(拿不准不拦,兜底=策展标注)。

    **异常方向权衡(对抗验收④,写死别翻)**:主体是 frozenset 集合运算 + 纯字符串处理,
    现实不可达抛;唯一有外部输入形态风险的是 extra_outbound 迭代/字符串化 → 只这段窄 try,
    失败当"无额外标注"继续走名字判定(全局策展照常生效)。**不**给主体包 try-当-外发:
    异常若判 True 会把一切读操作拦死(fail-open→fail-拦死一切=另一种事故);窄 try +
    确定性主体裸跑(真有代码缺陷 fail-loud 上冒),比宽 try 静默吞更诚实。
    """
    raw = (tool or "").strip()
    if not raw:
        return False
    norm = _norm_name(raw)
    if norm in _curated_outbound:
        return True
    if extra_outbound:
        try:   # 窄 try:只兜外部传入的 extra_outbound(形态不受控);失败=当没传,判定继续
            if norm in {_norm_name(str(n)) for n in extra_outbound}:
                return True
        except Exception:
            pass
    tokens = tool_tokens(raw)
    if not tokens:
        return False
    tset = set(tokens)
    # 2) 输入模拟排除(裸 send 自足的唯一排除):相邻 (send, keys|key) 且无消息名词
    if not (tset & SEND_NOUNS):
        for a, b in zip(tokens, tokens[1:]):
            if (a, b) in _INPUT_SIM_PAIRS:
                return False
    # 3) 非发送动作压制(显式发送动词同现时发送占先:send_draft/forward_email 照拦)
    if (tset & NON_SEND_ACTION_VERBS) and not (tset & _EXPLICIT_SEND_VERBS):
        return False
    # 4) reply 压过读语义(对象天然是"来信")
    if tset & REPLY_OVERRIDE:
        return True
    # 5) 读语义豁免(任意位置;纯读绝不误判)
    if tset & READ_VERB_PREFIXES:
        return False
    # 6-9) 发送判定
    if tset & SEND_STRONG:
        return True
    if tset & SEND_SELF_SUFFICIENT:
        return True
    if (tset & SEND_VERBS) and (tset & SEND_NOUNS):
        return True
    if ("add" in tset) and (tset & _ADD_MESSAGE_NOUNS):
        return True
    if (tset & TRANSMIT_VERBS) and (tset & CONTENT_NOUNS):
        return True
    return False


# ---- 草稿队列(fs_grants 同款全局注册表:执行层记待办,console 收尾捞起升卡)----

class OutboundDraftStore:
    """截住的外发调用草稿队列(内存即可 —— drive 收尾就被 console 消费升卡;
    卡本体经 proposal_registry 持久化)。有界防涨(超 50 砍最老)。"""

    _CAP = 50

    def __init__(self) -> None:
        self._drafts: list[dict] = []

    def note_draft(self, tool: str, tool_input: dict, *,
                   atom_id: str = "", intent: str = "") -> str:
        """记一条草稿(完整入参原样;入参绝不进 log)。返回 draft_id。"""
        d = {
            "draft_id": uuid.uuid4().hex[:12],
            "tool": str(tool or ""),
            "tool_input": dict(tool_input or {}),
            "atom_id": str(atom_id or ""),
            "intent": str(intent or "")[:400],
            "ts": time.time(),
        }
        self._drafts.append(d)
        if len(self._drafts) > self._CAP:
            self._drafts = self._drafts[-self._CAP:]
        logger.info("[outbound_gate] 外发调用已截成草稿(tool=%s)→ 等 H2A 拍板", d["tool"])
        return d["draft_id"]

    def pop_drafts(self) -> list[dict]:
        """取走并清空草稿队列(console drive 收尾消费,升 outbound_draft 卡)。"""
        out, self._drafts = self._drafts, []
        return out


_store: Optional[OutboundDraftStore] = None


def register_store(store: Optional[OutboundDraftStore]) -> None:
    global _store
    _store = store


def get_store() -> Optional[OutboundDraftStore]:
    return _store


def note_outbound_draft(tool: str, tool_input: dict, *,
                        atom_id: str = "", intent: str = "") -> bool:
    """便捷:有 store 就记草稿,返回 True;没有(CLI 裸跑/--no-llm/测试桩)→ False
    —— 调用方(executor 截点)据此给「外发通道未接,草稿未能递交」的诚实回执,
    **两种情况都不放行直发**(fail-closed)。"""
    st = get_store()
    if st is None:
        return False
    try:
        st.note_draft(tool, tool_input, atom_id=atom_id, intent=intent)
        return True
    except Exception:
        logger.warning("[outbound_gate] 草稿入队失败(fail-closed,不放行直发)", exc_info=True)
        return False


__all__ = [
    "SEND_STRONG", "SEND_VERBS", "SEND_NOUNS", "SEND_SELF_SUFFICIENT",
    "CONTENT_NOUNS", "TRANSMIT_VERBS", "REPLY_OVERRIDE",
    "MAIL_PROGRAMS", "READ_VERB_PREFIXES", "NON_SEND_ACTION_VERBS",
    "tool_tokens", "is_outbound_send_tool",
    "register_curated_outbound", "curated_outbound", "clear_curated_outbound",
    "OutboundDraftStore", "register_store", "get_store", "note_outbound_draft",
]
