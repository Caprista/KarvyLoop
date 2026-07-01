"""Stage 2/4/5 OnboardingPolicy —— show / suppress / inject / persist。

**核心不变量**(doc §4):
- I1 每条 hint 只显示一次(per-flag, per-install)
- I2 投递不打扰当前 turn(delivery_fn 包 try,异常不外传)
- I3 endpoint 不可用时静默跳过
- I5 强制 deontic.guardrails(本拍用最小集 + 留给 M2 接入 Paradigm Loader L0)
- I6 用户接受/拒绝/忽略写进 seen.yaml

设计:docs/13 §3.1 + §3.2。
"""
from __future__ import annotations

import dataclasses
import logging
import pathlib
from typing import Optional

import yaml

from .hints import ALL_FLAGS, HINTS
from .registry import EndpointEntry, endpoint_registry

logger = logging.getLogger(__name__)


@dataclasses.dataclass(frozen=True)
class PolicyDecision:
    """show() 的决策结果。"""
    flag: str
    show: bool
    reason: str
    hint_text: Optional[str] = None


# deontic.guardrails(I5):拍 3 用最小集,沿用 Paradigm Loader L0 字段名
DEFAULT_GUARDRAILS: tuple[str, ...] = (
    "no rm -rf",
    "user data 加密",
    "deontic 优先于 util",
    "用户保留最终否决权",
    "常驻引导不打扰当前 turn",
)


class OnboardingPolicy:
    """常驻引导策略 —— 控制"哪条 hint 该不该显示"。

    seen:per-flag seen 表(I1)
    guardrails:deontic 集(I5)
    registry:endpoint 注册表(注入,默认全局单例)
    """

    def __init__(
        self,
        seen_path: Optional[str] = None,
        guardrails: tuple[str, ...] = DEFAULT_GUARDRAILS,
        registry: Optional[object] = None,   # EndpointRegistry,type hint 避免循环
    ) -> None:
        self._seen_path = seen_path
        self._guardrails = guardrails
        self._registry = registry if registry is not None else endpoint_registry
        self._seen: dict[str, str] = {}    # flag → "shown" / "accepted" / "rejected" / "ignored"
        if seen_path:
            self._load_seen(seen_path)

    # ---- I1:seen 持久化 ----

    def _load_seen(self, path: str) -> None:
        p = pathlib.Path(path)
        if not p.exists():
            return
        try:
            data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
            self._seen = dict(data)
        except Exception as e:
            logger.warning("onboarding: load seen.yaml failed: %s, start empty", e)

    def _save_seen(self) -> None:
        if not self._seen_path:
            return
        try:
            pathlib.Path(self._seen_path).write_text(
                yaml.safe_dump(self._seen, allow_unicode=True),
                encoding="utf-8",
            )
        except Exception as e:
            logger.warning("onboarding: save seen.yaml failed: %s", e)

    def is_seen(self, flag: str) -> bool:
        return flag in self._seen

    # ---- I5:guardrails 校验 ----

    def has_guardrails(self) -> bool:
        """AC6:5 条 guardrails 都在。"""
        required = {
            "no rm -rf", "user data 加密", "deontic 优先于 util",
            "用户保留最终否决权", "常驻引导不打扰当前 turn",
        }
        return required.issubset(set(self._guardrails))

    # ---- Stage 2/4:should_show + show ----

    def should_show(self, flag: str) -> bool:
        """AC2:第一次 True,标记后第二次 False。"""
        if flag not in ALL_FLAGS:
            return False
        if flag not in HINTS:
            return False
        return flag not in self._seen

    def show(self, flag: str, endpoint_name: str = "cli") -> PolicyDecision:
        """主入口:返回决策 + 投递 hint(若该 show)。

        I1 seen 标记 + I2 异常吞 + I3 不可用跳过 + I5 guardrails 校验。
        """
        # I5:guardrails 校验
        if not self.has_guardrails():
            return PolicyDecision(flag=flag, show=False, reason="guardrails missing")

        # flag 合法性
        if flag not in ALL_FLAGS:
            return PolicyDecision(flag=flag, show=False, reason="unknown flag")

        # I1:已见 → 跳过
        if not self.should_show(flag):
            return PolicyDecision(
                flag=flag, show=False, reason="already seen",
            )

        # I3:endpoint 不可用 → 静默跳过
        entry = self._registry.get(endpoint_name)  # type: ignore[attr-defined]
        if entry is None:
            return PolicyDecision(flag=flag, show=False, reason="endpoint not registered")
        if not entry.is_available_fn():
            return PolicyDecision(flag=flag, show=False, reason="endpoint offline")

        # 投递(I2:异常被吞)
        text = HINTS[flag]
        try:
            formatted = entry.format_hint_fn(text)
            entry.delivery_fn(endpoint_name, formatted)
        except Exception as e:
            logger.warning("onboarding: delivery failed for flag=%s: %s", flag, e)
            return PolicyDecision(flag=flag, show=False, reason=f"delivery failed: {e}")

        # 标记 seen + 持久化
        self._seen[flag] = "shown"
        self._save_seen()
        return PolicyDecision(flag=flag, show=True, reason="delivered", hint_text=text)

    # ---- I6:用户响应持久化 ----

    def record_response(self, flag: str, response: str) -> None:
        """记录用户响应:accepted / rejected / ignored(AC7)。"""
        if response not in ("accepted", "rejected", "ignored", "shown"):
            return
        self._seen[flag] = response
        self._save_seen()

    def get_response(self, flag: str) -> Optional[str]:
        return self._seen.get(flag)


def default_policy(seen_path: Optional[str] = None) -> OnboardingPolicy:
    """便捷构造。"""
    return OnboardingPolicy(seen_path=seen_path)
