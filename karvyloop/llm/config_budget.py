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


# ---- 写 `budget:` 块(config_models 同款 load/save 骨架;只碰 budget,别人的键不动)----


def save_spend_budget_config(spec: dict, config_path=None) -> tuple[bool, str]:
    """把 UI 提交的预算改写进 config.yaml 的 `budget:` 块(其余配置一字不动)。

    spec 字段语义(审计 #87 §3-②:区分"未承载 vs 显式清零"):
    - **键缺席** → 保留 config 里该维度已有值(前端只管 USD 时,手配的 token 上限不被抹掉)。
    - **键在但 None/空/<=0** → 清掉该维度上限(显式清零)。
    维度:daily_usd / daily_tokens / monthly_usd / monthly_tokens;on_limit(warn|pause,缺席则保留)。
    - 合并后四维全空 → 删掉整个 `budget:` 块(= 关刹车 = 无限,零回归)。
    - on_limit 非法 → 回落 DEFAULT_ON_LIMIT(不 4xx,fail-soft;与解析侧同口径)。
    返回 (ok, reason)。绝不 log/print 任何密钥(本模块只碰 budget 块,不碰 models)。
    """
    p = Path(config_path) if config_path else _default_config_path()
    try:
        import yaml
        if p.exists():
            cfg = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
        else:
            cfg = {}
    except Exception as e:
        return False, f"config.yaml 读取失败:{type(e).__name__}"
    if not isinstance(cfg, dict):
        cfg = {}

    # 当前已配的四维 + on_limit,作为"未承载维度"的保留基线。
    existing = spend_budget_config_from_dict(cfg)
    du = _pos_float(spec["daily_usd"]) if "daily_usd" in spec else existing.daily_usd
    dt = _pos_int(spec["daily_tokens"]) if "daily_tokens" in spec else existing.daily_tokens
    mu = _pos_float(spec["monthly_usd"]) if "monthly_usd" in spec else existing.monthly_usd
    mt = _pos_int(spec["monthly_tokens"]) if "monthly_tokens" in spec else existing.monthly_tokens
    if "on_limit" in spec:
        on_limit = str(spec.get("on_limit") or DEFAULT_ON_LIMIT).strip().lower()
        if on_limit not in VALID_ON_LIMIT:
            on_limit = DEFAULT_ON_LIMIT
    else:
        on_limit = existing.on_limit

    if not any(v is not None for v in (du, dt, mu, mt)):
        cfg.pop("budget", None)   # 全清 = 无刹车(无限,零回归)
    else:
        block: dict = {}
        if du is not None:
            block["daily_usd"] = du
        if dt is not None:
            block["daily_tokens"] = dt
        if mu is not None:
            block["monthly_usd"] = mu
        if mt is not None:
            block["monthly_tokens"] = mt
        block["on_limit"] = on_limit
        cfg["budget"] = block

    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    except Exception as e:
        return False, f"config.yaml 写入失败:{type(e).__name__}"
    return True, ""


def budget_status(cfg: SpendBudgetConfig, *, ledger=None, model_cost=None, clock=None) -> dict:
    """今日/本月已用 vs 上限(给 GET /api/budget)。**纯只读**,不出卡、不拦、不改记账。

    即使 budget 未配置(disabled)也返回真实用量(上限=None)→ 用户能先看花了多少再设限。
    复用 spend_budget.SpendBudget 的成本换算(_window_spend),不重写钱的算法。

    返回 {enabled, on_limit, dimensions:[{key,unit,used,limit,ratio}], asof}。
    ledger=None → 用量全 0(优雅空,与 /api/tokens 同口径)。
    """
    import time as _time
    from .spend_budget import SpendBudget, _day_start, _month_start

    now = (clock or _time.time)()
    probe = SpendBudget(cfg, ledger_getter=(lambda: ledger),
                        model_cost=model_cost, clock=(clock or _time.time))
    day_usd, day_tok = probe._window_spend(_day_start(now), now)
    mon_usd, mon_tok = probe._window_spend(_month_start(now), now)

    def _dim(key: str, unit: str, used, limit):
        used_v = float(used) if unit == "usd" else int(used)
        ratio = (used_v / limit) if (limit and limit > 0) else 0.0
        return {"key": key, "unit": unit, "used": used_v, "limit": limit, "ratio": ratio}

    dims = [
        _dim("daily_usd", "usd", day_usd, cfg.daily_usd),
        _dim("daily_tokens", "tokens", day_tok, cfg.daily_tokens),
        _dim("monthly_usd", "usd", mon_usd, cfg.monthly_usd),
        _dim("monthly_tokens", "tokens", mon_tok, cfg.monthly_tokens),
    ]
    return {"enabled": cfg.enabled, "on_limit": cfg.on_limit,
            "dimensions": dims, "asof": now}


__all__ = [
    "SpendBudgetConfig",
    "spend_budget_config_from_dict",
    "load_spend_budget_config",
    "save_spend_budget_config",
    "budget_status",
    "ON_LIMIT_WARN",
    "ON_LIMIT_PAUSE",
    "VALID_ON_LIMIT",
    "DEFAULT_ON_LIMIT",
    "WARN_THRESHOLDS",
]
