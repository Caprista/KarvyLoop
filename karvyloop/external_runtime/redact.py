"""external_runtime/redact — 落 Trace/日志前的确定性密钥过滤(二道防线)。

**定位(钉死,别当依赖项)**:redact 是**尽力而为的二道防线(best-effort hygiene),
不是"密钥不外泄"的依赖项**。**一道防线永远是:凭证不进子进程 env(bridge 组 env 白名单 +
剔 `*_API_KEY`/`*_TOKEN`)**——外部执行体根本拿不到 key,就没有能被打进 stdout 的原始 key。
redact 只兜"已经泄进 stdout/stderr 的漏网之鱼"。

**模式表天然不完备**:不同厂商 key 形态各异——某些 provider 的 key 是 JWT(以 `eyJ` 开头、
不带 `sk-` 前缀),只配 `sk-*` 的过滤器会**整条漏掉**它。所以模式表覆盖非 sk- 形态
(JWT `eyJ…` + 通用 `api[_-]?key[:=]` 兜底),但**即便如此也只当二道防线**。

外部执行体(某些一次性入口)会把 API key 前缀打进 stdout —— 这是本模块存在的实证理由:
任何外部 CLI 的 stdout/stderr 在入 Trace/日志前必须先过这道过滤器。
"""
from __future__ import annotations

import re

# 确定性密钥模式(小写不敏感处已带 (?i))。覆盖 sk- 与非 sk-(JWT / api_key= 兜底)两类形态。
_DEFAULT: tuple[re.Pattern, ...] = (
    re.compile(r"sk-[A-Za-z0-9_\-]{8,}"),
    # JWT(某些 provider 的 key 就是 JWT,以 eyJ 开头、不带 sk- 前缀)——只配 sk- 会整条漏
    re.compile(r"eyJ[A-Za-z0-9_\-]{10,}(?:\.[A-Za-z0-9_=\-]{4,}){0,2}"),
    re.compile(r"(?i)authorization\s*:\s*\S+"),
    re.compile(r"(?i)bearer\s+\S+"),
    re.compile(r"(?i)api[_-]?key\s*[:=]\s*\S+"),
)

REDACTED = "[REDACTED]"


def compile_extra(patterns) -> tuple[re.Pattern, ...]:
    """把配方里的 per-runtime 额外正则字符串编译成 Pattern(非法正则跳过,不炸)。"""
    out: list[re.Pattern] = []
    for p in patterns or ():
        try:
            out.append(re.compile(str(p)))
        except re.error:
            continue
    return tuple(out)


def redact(text: str, extra=()) -> str:
    """把 text 里命中的密钥模式替换成 [REDACTED](确定性,无 LLM)。

    - extra:配方级额外 Pattern(compile_extra 产出)或正则字符串;并入默认表。
    - 输入非字符串 → 转 str(兜底,绝不炸)。
    """
    if text is None:
        return ""
    s = str(text)
    extra_pats = extra if extra and isinstance(extra[0], re.Pattern) else compile_extra(extra)
    for pat in (*_DEFAULT, *extra_pats):
        s = pat.sub(REDACTED, s)
    return s


def contains_secret(text: str, extra=()) -> bool:
    """text 里是否还残留任一密钥模式(测试断言/自检用;true=redact 没盖住)。"""
    if not text:
        return False
    s = str(text)
    extra_pats = extra if extra and isinstance(extra[0], re.Pattern) else compile_extra(extra)
    return any(pat.search(s) for pat in (*_DEFAULT, *extra_pats))


__all__ = ["redact", "contains_secret", "compile_extra", "REDACTED"]
