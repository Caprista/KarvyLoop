"""karvyloop.i18n — 双语表现层(M3+ 拍 9.4-A2)。

设计:用户原话"系统安装后默认英文,可以选择切换中文,这不影响代码逻辑"。
i18n 是**纯表现层** —— 只决定"用户看到哪种语言的字符串",不碰任何业务逻辑/控制流。

参照(Q5 借通用基建,不闭门造车):gettext / i18next 的标准三件套
  ① locale 解析(显式 set > env > 默认);② key→字符串查表;③ {占位} 插值 + 缺失回退。
这里**自造**仅一个 ~40 行薄查表(无第三方依赖,符合"少脚手架"),**不**引 gettext .po 工具链。

约定:
- **默认 en**(GitHub README 默认英文 ⇄ 系统默认英文,同一策略)。
- 解析顺序:`set_locale()` 显式 > `KARVYLOOP_LANG` 环境变量 > `_DEFAULT`(en)。
- 支持 "en" / "zh"(zh-CN / zh_TW / zh... 一律归一到 "zh";其余归一到 "en")。
- `t(key, **kw)`:当前 locale 查不到 → 回退 en → 再回退 key 本身(永不抛、永不空)。

用法:
    from karvyloop import i18n
    i18n.set_locale("zh")            # 或留空走 env/默认
    print(i18n.t("console.opening", url="http://127.0.0.1:8766"))
"""
from __future__ import annotations

import os
from typing import Optional

from ._strings import TABLES as _TABLES

_DEFAULT = "en"
_SUPPORTED = ("en", "zh")

# 进程级当前 locale(None = 未显式设置,走 env/默认)。
# 单用户本地 console 场景 → 进程全局足够;测试可 set_locale(None) 复位。
_current: Optional[str] = None


def _normalize(lang: Optional[str]) -> Optional[str]:
    """把任意语言标签归一到受支持 locale,无法识别返回 None。

    "zh" / "zh-CN" / "zh_TW" / "ZH" → "zh";"en" / "en-US" → "en";其余 → None。
    """
    if not lang:
        return None
    low = lang.strip().lower().replace("_", "-")
    if low.startswith("zh"):
        return "zh"
    if low.startswith("en"):
        return "en"
    return None


def resolve_locale() -> str:
    """解析当前生效 locale:显式 set > KARVYLOOP_LANG > 默认(en)。"""
    if _current is not None:
        return _current
    env = _normalize(os.environ.get("KARVYLOOP_LANG"))
    return env or _DEFAULT


def set_locale(lang: Optional[str]) -> str:
    """显式设置进程 locale。传 None / 无法识别 → 复位为走 env/默认。

    Returns:
        设置后实际生效的 locale。
    """
    global _current
    _current = _normalize(lang)  # 无法识别(含 None)→ None → 走 env/默认
    return resolve_locale()


def set_startup_locale(*, explicit: Optional[str] = None, config_lang: Optional[str] = None) -> str:
    """启动时按优先级定 locale:**显式(--lang)> KARVYLOOP_LANG env > config.yaml lang > en**。

    `config_lang` 由 console/CLI 层从 `~/.karvyloop/config.yaml` 的 `lang` 字段读出后传入
    (i18n 模块保持 path-agnostic,不自己读配置)。这让"语言偏好"成为**记录在案的本地设置**:
    设过一次,之后启动自动生效,不必每次 --lang。
    """
    winner = (
        _normalize(explicit)
        or _normalize(os.environ.get("KARVYLOOP_LANG"))
        or _normalize(config_lang)
    )
    set_locale(winner)  # winner=None → 复位走 env/默认(等价)
    return resolve_locale()


def get_locale() -> str:
    """当前生效 locale(等同 resolve_locale,语义更直观)。"""
    return resolve_locale()


def available_locales() -> tuple[str, ...]:
    """受支持的 locale 列表(供 UI 语言切换器列项)。"""
    return _SUPPORTED


def t(key: str, /, **kwargs) -> str:
    """查表取当前 locale 的字符串;缺失逐级回退(当前→en→key 本身)。

    `kwargs` 用于 `str.format` 占位插值;插值失败时返回未插值串(永不抛)。
    """
    loc = get_locale()
    table = _TABLES.get(loc, _TABLES[_DEFAULT])
    s = table.get(key)
    if s is None:
        s = _TABLES[_DEFAULT].get(key, key)  # 回退 en,再回退 key
    if kwargs:
        try:
            return s.format(**kwargs)
        except (KeyError, IndexError, ValueError):
            return s
    return s


__all__ = [
    "set_locale",
    "set_startup_locale",
    "get_locale",
    "resolve_locale",
    "available_locales",
    "t",
]
