"""config_budget — 花费预算(spend budget)的 config.yaml 解析(`budget:` 块）。

**为什么这个模块存在**:token 计量是全项目最完整的子系统之一(token_ledger buckets/by_source、
gateway 单咽喉记账),但**只有仪表盘没有刹车** —— 一个跑歪的后台 workflow 能无声烧到 key 额度
耗尽。这是"有你就够了"的信任硬门槛:用户不敢把 key 交给不会自己踩刹车的系统。这里解析用户配置的
硬上限(每日/每月,按钱或按 token),`llm/spend_budget.py` 在 gateway 咽喉执行。

**与 `paradigm/budget.py` 的 Budget 区分**:那个是 **context-token 预算**(单次组装上下文不超模型窗口,
与花钱无关)。这里是 **spend / 花费预算**(累计烧了多少钱/token,踩刹车)。名字刻意用 spend / budget:
块区分开,别混。

config.yaml 结构(默认不配 = 无限,零回归):
    budget:
      daily_usd: 5.0          # 每日花费上限(美元);与 daily_tokens 二选一(都配则各自独立生效,取先触发)
      daily_tokens: 2000000   # 每日 token 上限(无价模型或想按量控时用)
      monthly_usd: 100.0      # 每月花费上限(美元)
      monthly_tokens: 50000000
      on_limit: pause         # 达 100% 后:warn(只告警不拦) | pause(拦后台自动路径)。默认 warn。

铁律:
- **默认不配 = 无限**(零负担 / 零回归):`budget:` 块缺失 → 返回 disabled 的预算,check 永远放行。
- **软护栏,不是安全边界**:达限暂停要**大声说**(出卡 + 日志),绝不静默停。
- 本模块**只读不写** config,不动别人的 config 读写路径。
"""
from __future__ import annotations

import dataclasses
import logging
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# 达限行为(on_limit):warn=只告警不拦;pause=拦后台自动路径(前台永不拦)。
ON_LIMIT_WARN = "warn"
ON_LIMIT_PAUSE = "pause"
VALID_ON_LIMIT = (ON_LIMIT_WARN, ON_LIMIT_PAUSE)
DEFAULT_ON_LIMIT = ON_LIMIT_WARN  # 保守默认:不配 on_limit 时只告警,绝不无声罢工

# 告警阈值(占上限的比例):到 75% / 90% 各出一次同级提醒卡(一天同级不重复,去重由 spend_budget 管)。
WARN_THRESHOLDS = (0.75, 0.90)


def _pos_float(v) -> Optional[float]:
    """解析为正 float;None/非数/<=0 → None(视为该维度不设限)。"""
    if v is None:
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return f if f > 0 else None


def _pos_int(v) -> Optional[int]:
    """解析为正 int;None/非数/<=0 → None(视为该维度不设限)。"""
    if v is None:
        return None
    try:
        n = int(v)
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


@dataclasses.dataclass(frozen=True)
class SpendBudgetConfig:
    """花费预算配置(不可变)。任一 *_usd / *_tokens 为 None = 该维度不设限。"""
    daily_usd: Optional[float] = None
    daily_tokens: Optional[int] = None
    monthly_usd: Optional[float] = None
    monthly_tokens: Optional[int] = None
    on_limit: str = DEFAULT_ON_LIMIT

    @property
    def enabled(self) -> bool:
        """至少配了一个维度的上限 = 启用。全空 = 无限(零回归)。"""
        return any(v is not None for v in (
            self.daily_usd, self.daily_tokens,
            self.monthly_usd, self.monthly_tokens))

    @property
    def blocks_on_limit(self) -> bool:
        """on_limit=pause 才在 100% 拦后台;warn 只告警不拦。"""
        return self.on_limit == ON_LIMIT_PAUSE


def spend_budget_config_from_dict(cfg: dict) -> SpendBudgetConfig:
    """从整份 config dict 解析 `budget:` 块。块缺失/非法 → 全空(disabled,零回归)。

    on_limit 非法值 → 回落 DEFAULT_ON_LIMIT + 日志(fail-soft:配错一个字不该让预算整个失效,
    但也不静默当 pause —— 保守回到 warn)。"""
    budget = (cfg or {}).get("budget") if isinstance(cfg, dict) else None
    if not isinstance(budget, dict) or not budget:
        return SpendBudgetConfig()

    on_limit = str(budget.get("on_limit") or DEFAULT_ON_LIMIT).strip().lower()
    if on_limit not in VALID_ON_LIMIT:
        logger.warning(
            "[budget] on_limit=%r 非法(须是 %s 之一)—— 回落 %s(只告警不拦)",
            budget.get("on_limit"), VALID_ON_LIMIT, DEFAULT_ON_LIMIT)
        on_limit = DEFAULT_ON_LIMIT

    return SpendBudgetConfig(
        daily_usd=_pos_float(budget.get("daily_usd")),
        daily_tokens=_pos_int(budget.get("daily_tokens")),
        monthly_usd=_pos_float(budget.get("monthly_usd")),
        monthly_tokens=_pos_int(budget.get("monthly_tokens")),
        on_limit=on_limit,
    )


def _default_config_path() -> Path:
    return Path.home() / ".karvyloop" / "config.yaml"


def load_spend_budget_config(config_path=None) -> SpendBudgetConfig:
    """读 config.yaml 的 `budget:` 块。文件缺失/读不出/块缺失 → disabled(无限,零回归)。"""
    p = Path(config_path) if config_path else _default_config_path()
    if not p.exists():
        return SpendBudgetConfig()
    try:
        import yaml
        cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception:
        logger.warning("[budget] config.yaml 读取失败 —— 预算不启用(无限)")
        return SpendBudgetConfig()
    if not isinstance(cfg, dict):
        return SpendBudgetConfig()
    return spend_budget_config_from_dict(cfg)


__all__ = [
    "SpendBudgetConfig",
    "spend_budget_config_from_dict",
    "load_spend_budget_config",
    "ON_LIMIT_WARN",
    "ON_LIMIT_PAUSE",
    "VALID_ON_LIMIT",
    "DEFAULT_ON_LIMIT",
    "WARN_THRESHOLDS",
]
