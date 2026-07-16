"""spend_budget — 花费预算的**刹车**(gateway 咽喉执行,三级控制)。

**为什么**:token 计量已经很完整(仪表盘),但没有刹车 —— 跑歪的后台 workflow 能无声烧穿 key 额度。
这里在**唯一咽喉**(GatewayClient.complete 的调用前)累计校验当日/当月花费,分三级:

  ① 达 75% / 90% 阈值 → 出一张 H2A 提醒卡("今天已花 X,接近上限 Y")+ 日志(一天同级不重复);
  ② 达 100% 且 on_limit=pause → **拦后台自动路径**(workflow/daily/静音/收件箱等),fail-loud 抛
     `SpendBudgetExceeded`;**人正在等的前台交互永不拦**(否则体验="我正问着话它罢工了");
  ③ on_limit=warn → 只告警不拦。

**与 token_ledger / cost 记账的关系(咽喉纪律)**:记账逻辑一字不动。本模块只**读** token_ledger 的
只读窗口查询(window_by_model)算累计花费,是记账旁边**新增**的一个"调用前 check",不改任何写路径。

**成本换算**:每模型 config 若有 `cost`(USD/百万 token,含 input/output/cache_read/cache_write)则
算真钱;没有价的模型按 token 计(只对 *_tokens 上限生效,对 *_usd 上限该模型贡献 0 美元 —— 诚实:
不猜没配的价)。

**foreground vs background**:靠 token_source contextvar 区分。**默认放行**(未知/前台 source 永不拦),
只拦**已知的后台自动 source**(见 AUTOMATIC_SOURCES)—— 这样"前台永不被拦"是结构保证:新加的
source 默认当前台,绝不会误伤用户正在等的那次 drive。

**软护栏,不是安全边界**:达限暂停大声说(抛错 + 出卡 + 日志),绝不静默停。
"""
from __future__ import annotations

import logging
import threading
import time
from typing import Callable, Optional

from .config_budget import SpendBudgetConfig, WARN_THRESHOLDS

logger = logging.getLogger(__name__)


class SpendBudgetExceeded(Exception):
    """花费预算已达上限,且 on_limit=pause + 调用来自后台自动路径 → 咽喉 fail-loud 拒发。

    前台(用户正在等的 drive)**永不**抛这个 —— 只拦后台自动烧钱。"""


# 已知的**后台自动** token_source —— 只有这些在预算耗尽时被拦(pause)。
# 其余(含默认 "unknown" = 主聊天 drive、forge、fuzzy_dispatch = 正在处理用户当前消息、
# roundtable_host = 用户显式开的圆桌)一律当**前台**,永不拦。
# 依据:全仓 grep token_source(...) 字面量分类(2026-07);新 source 默认前台(放行),不误伤。
AUTOMATIC_SOURCES = frozenset({
    "consolidate",          # 知识整理(daily 慢侧)
    "consolidate_auto",     # 自动 consolidation tick
    "atom_consolidate",     # 原子语义合并
    "paradigm_complete",    # 范式补全(后台)
    "agent_import",         # 导入外部 agent 拆解(批量,非用户逐条等)
    "atom_quality",         # atom 质量评审(异步跑评)
    "checker",              # 独立验收 checker(异步)
    "cocreation",           # 共创后台管道
    "inbox_pipe",           # 收件箱分诊(轮询)
    "lesson_distill",       # 经验蒸馏
    "silence_predict",      # 静音预测
    "skill_revision",       # 技能修订
    "skill_tags",           # 技能打标(异步)
    "taste_predict",        # 品味/预判(后台)
    "weekly_digest",        # 周报
    "凝习惯",                # trace 凝习惯(后台)
    "ops_diagnose",         # 运维诊断(后台自愈)
})


def is_automatic_source(source: str) -> bool:
    """该 source 是否算"后台自动"(可被预算拦)。未知/前台 → False(放行)。"""
    return (source or "") in AUTOMATIC_SOURCES


# 三级判定结果的 action。
ACTION_ALLOW = "allow"    # 未启用/未达阈值 → 放行
ACTION_WARN = "warn"      # 达 75%/90% 阈值 → 出提醒卡 + 放行
ACTION_BLOCK = "block"    # 达 100% + pause + 后台 → 拒发


class _WarnDedup:
    """告警去重:同一(day, dimension, threshold)一天只出一次卡。跨调用线程安全。"""

    def __init__(self) -> None:
        self._seen: set[tuple] = set()
        self._lock = threading.Lock()

    def should_emit(self, key: tuple) -> bool:
        with self._lock:
            if key in self._seen:
                return False
            self._seen.add(key)
            return True

    def reset(self) -> None:
        with self._lock:
            self._seen.clear()


def _day_start(ts: float) -> float:
    """ts 所在**本地日**的零点 ts(与 token_ledger 的 day 列同口径:本地日历日)。"""
    lt = time.localtime(ts)
    return time.mktime((lt.tm_year, lt.tm_mon, lt.tm_mday, 0, 0, 0, 0, 0, -1))


def _month_start(ts: float) -> float:
    """ts 所在**本地月**的 1 号零点 ts。"""
    lt = time.localtime(ts)
    return time.mktime((lt.tm_year, lt.tm_mon, 1, 0, 0, 0, 0, 0, -1))


class SpendBudget:
    """花费预算刹车。持 config + ledger + 模型价格解析器,在 gateway 咽喉被 check。

    参数:
        cfg: SpendBudgetConfig(解析自 config.yaml `budget:` 块)。
        ledger_getter: () -> TokenLedger | None。延迟取全局账本(接线顺序无关;账本没接 → 放行)。
        model_cost: (model_id) -> dict | None。每模型 cost 表(USD/百万 token,含 input/output/
            cache_read/cache_write);无价 → None/{}(该模型按 token 计,不贡献美元)。
        clock: () -> float(可注入,测试用)。
        emit_card: (card: dict) -> None(可选)。达阈值时调,传结构化提醒卡 payload;接线由主线做
            (本模块**不**碰 routes/broadcast)。None = 只日志不出卡。
    """

    def __init__(self, cfg: SpendBudgetConfig, *,
                 ledger_getter: Callable[[], object] = None,
                 model_cost: Callable[[str], Optional[dict]] = None,
                 clock: Callable[[], float] = time.time,
                 emit_card: Callable[[dict], None] = None) -> None:
        self.cfg = cfg
        self._ledger_getter = ledger_getter
        self._model_cost = model_cost
        self._clock = clock
        self._emit_card = emit_card
        self._dedup = _WarnDedup()

    # ---- 累计花费查询(只读 ledger)----

    def _window_spend(self, start_ts: float, now: float) -> tuple[float, int]:
        """[start_ts, now] 窗内累计 (usd, tokens)。usd 只对有价模型累加(无价贡献 0);
        tokens = input+output 总量(不含 cache 读命中折算,与 ledger.total 同口径)。**只读**。"""
        led = self._ledger_getter() if self._ledger_getter else None
        if led is None:
            return 0.0, 0
        try:
            rows = led.window_by_model(start_ts=start_ts, end_ts=now)
        except Exception:
            return 0.0, 0
        usd = 0.0
        tokens = 0
        for r in rows:
            tokens += int(r.get("total") or 0)
            usd += self._row_usd(r)
        return usd, tokens

    def _row_usd(self, row: dict) -> float:
        """一行 per-model 用量 → USD(按该模型 cost 表;无价 → 0.0)。"""
        if not self._model_cost:
            return 0.0
        cost = self._model_cost(str(row.get("model") or "")) or {}
        if not cost:
            return 0.0
        return (
            int(row.get("input") or 0) * float(cost.get("input", 0) or 0)
            + int(row.get("output") or 0) * float(cost.get("output", 0) or 0)
            + int(row.get("cache_read") or 0) * float(cost.get("cache_read", 0) or 0)
            + int(row.get("cache_write") or 0) * float(cost.get("cache_write", 0) or 0)
        ) / 1_000_000.0

    # ---- 三级判定 ----

    def evaluate(self, source: str) -> dict:
        """**只读判定**(不出卡、不抛错):给定当前 source,返回本次调用该怎么处理。

        返回 {action, dimension, used, limit, ratio, unit, source, automatic}。
        action ∈ {allow, warn, block}。多维度(daily_usd/tokens/monthly_*)取**最严**:
        任一维度 block → block;否则任一 warn → warn(带触发的最高阈值维度);全 allow → allow。

        block 仅在:达 100% + on_limit=pause + 该 source 是后台自动。前台恒不 block(至多 warn)。
        """
        if not self.cfg.enabled:
            return {"action": ACTION_ALLOW, "dimension": "", "used": 0, "limit": 0,
                    "ratio": 0.0, "unit": "", "source": source, "automatic": False}

        now = self._clock()
        day_usd, day_tok = self._window_spend(_day_start(now), now)
        mon_usd, mon_tok = self._window_spend(_month_start(now), now)

        automatic = is_automatic_source(source)
        dims = [
            ("daily_usd", self.cfg.daily_usd, day_usd, "usd"),
            ("daily_tokens", self.cfg.daily_tokens, day_tok, "tokens"),
            ("monthly_usd", self.cfg.monthly_usd, mon_usd, "usd"),
            ("monthly_tokens", self.cfg.monthly_tokens, mon_tok, "tokens"),
        ]

        best_block = None   # (ratio, dim tuple)
        best_warn = None
        for name, limit, used, unit in dims:
            if limit is None or limit <= 0:
                continue
            ratio = used / limit
            info = {"action": None, "dimension": name, "used": used, "limit": limit,
                    "ratio": ratio, "unit": unit, "source": source, "automatic": automatic}
            if ratio >= 1.0:
                # 100%:pause + 后台 → block;否则(warn 或前台)→ 当作 warn 级提醒
                if self.cfg.blocks_on_limit and automatic:
                    info["action"] = ACTION_BLOCK
                    if best_block is None or ratio > best_block["ratio"]:
                        best_block = info
                else:
                    info["action"] = ACTION_WARN
                    if best_warn is None or ratio > best_warn["ratio"]:
                        best_warn = info
            elif ratio >= WARN_THRESHOLDS[0]:
                info["action"] = ACTION_WARN
                if best_warn is None or ratio > best_warn["ratio"]:
                    best_warn = info

        if best_block is not None:
            return best_block
        if best_warn is not None:
            return best_warn
        return {"action": ACTION_ALLOW, "dimension": "", "used": 0, "limit": 0,
                "ratio": 0.0, "unit": "", "source": source, "automatic": automatic}

    def _warn_tier(self, ratio: float) -> str:
        """触发的告警档标签(用于去重 key + 卡文案):100/90/75。"""
        if ratio >= 1.0:
            return "100"
        if ratio >= WARN_THRESHOLDS[1]:
            return "90"
        return "75"

    def _fmt_amount(self, value, unit: str) -> str:
        if unit == "usd":
            return f"${value:.2f}"
        return f"{int(value):,} tokens"

    def build_card(self, verdict: dict) -> dict:
        """把 warn/block 判定 → 结构化提醒卡 payload(主线接 broadcast_proposal 用)。

        **不**构造 Proposal 对象、**不**碰 routes —— 只给数据(kind=spend_budget_alert)。
        proposal_id 含 day + dimension + tier → 同级一天稳定同 id(前端去重/幂等登记)。"""
        from karvyloop import i18n
        used, limit, unit = verdict["used"], verdict["limit"], verdict["unit"]
        dim, ratio = verdict["dimension"], verdict["ratio"]
        tier = self._warn_tier(ratio)
        blocked = verdict["action"] == ACTION_BLOCK
        period = (i18n.t("proposal.spend.period_month") if dim.startswith("monthly")
                  else i18n.t("proposal.spend.period_day"))
        day_label = time.strftime("%Y-%m-%d", time.localtime(self._clock()))
        used_s, limit_s = self._fmt_amount(used, unit), self._fmt_amount(limit, unit)
        pct = int(ratio * 100)
        if blocked:
            summary = i18n.t("proposal.spend.summary_blocked", period=period,
                             used=used_s, limit=limit_s, pct=pct)
        else:
            summary = i18n.t("proposal.spend.summary_warn", period=period,
                             used=used_s, limit=limit_s, pct=pct, tier=tier)
        return {
            "kind": "spend_budget_alert",
            "summary": summary,
            "proposal_id": f"spend_budget_alert-{day_label}-{dim}-{tier}",
            "payload": {
                "dimension": dim, "period": period, "tier": tier,
                "used": used, "limit": limit, "unit": unit,
                "ratio": ratio, "blocked": blocked,
            },
        }

    def check(self, source: str) -> None:
        """**咽喉执行点**:gateway.complete 调用前调一次。

        - allow → 静默返回。
        - warn  → 出提醒卡(一天同级去重)+ 日志,**放行**。
        - block → 出卡 + 日志 + 抛 SpendBudgetExceeded(fail-loud,仅后台自动路径命中)。

        绝不静默:warn/block 都会日志 + (若接了 emit_card)出卡。异常隔离:判定/出卡自身崩了
        绝不误拦(fail-open —— 预算是软护栏,不该因自己 bug 挡住真调用)。"""
        try:
            verdict = self.evaluate(source)
        except Exception:
            logger.debug("[budget] evaluate 异常,放行(软护栏 fail-open)", exc_info=True)
            return

        action = verdict.get("action")
        if action == ACTION_ALLOW:
            return

        tier = self._warn_tier(verdict["ratio"])
        day_label = time.strftime("%Y-%m-%d", time.localtime(self._clock()))
        dedup_key = (day_label, verdict["dimension"], tier, action)
        first_time = self._dedup.should_emit(dedup_key)

        if first_time:
            # 大声说:日志一定打(不带敏感字段:只有金额/token 数/百分比,无 key)。
            level = logging.WARNING if action == ACTION_BLOCK else logging.INFO
            logger.log(level, "[budget] %s dim=%s used=%s limit=%s ratio=%.2f source=%s",
                       action, verdict["dimension"], verdict["used"],
                       verdict["limit"], verdict["ratio"], source)
            if self._emit_card:
                try:
                    self._emit_card(self.build_card(verdict))
                except Exception:
                    logger.debug("[budget] emit_card 失败(不影响判定)", exc_info=True)

        if action == ACTION_BLOCK:
            # 后台自动路径 + pause + 达 100% → fail-loud。前台永远走不到这里(evaluate 已挡)。
            v = verdict
            raise SpendBudgetExceeded(
                f"花费预算已达上限({v['dimension']}:{v['used']} / {v['limit']},{int(v['ratio']*100)}%)"
                f"—— 后台自动任务 source={source} 暂停。前台交互不受影响;要继续请提高 budget 上限。"
            )


# ---- 全局注册(gateway 咽喉延迟取;None = 无预算,放行)----

_BUDGET: Optional[SpendBudget] = None


def register_spend_budget(budget: Optional[SpendBudget]) -> None:
    """注册全局花费预算(entry 接线时调;None = 关刹车 = 无限)。"""
    global _BUDGET
    _BUDGET = budget


def get_spend_budget() -> Optional[SpendBudget]:
    return _BUDGET


def wire_spend_budget(*, registry: object = None, config_path=None,
                      emit_card: Callable[[dict], None] = None,
                      clock: Callable[[], float] = time.time) -> Optional[SpendBudget]:
    """接线便捷入口:读 config.yaml `budget:` 块 → 建 SpendBudget → register(全局)→ 返回它。

    - 未配 budget(disabled)→ **不注册**(保持无刹车 = 无限,0 回归),返回 None。
    - model_cost 从 registry 的模型定义取 `cost`(USD/百万 token);registry=None → 无价(按 token 计)。
    - ledger 延迟从 token_ledger.get_ledger() 取(接线顺序无关)。
    - emit_card:主线接 broadcast_proposal 的回调(可选;不接 = 只日志)。

    调用方(cli/console entry)在 gateway/ledger 接线后调一次即可。已注册则覆盖(重入安全)。"""
    from .config_budget import load_spend_budget_config
    cfg = load_spend_budget_config(config_path)
    if not cfg.enabled:
        register_spend_budget(None)
        return None

    def _model_cost(model_id: str) -> Optional[dict]:
        if registry is None:
            return None
        try:
            m = registry.get(model_id)
            return dict(getattr(m, "cost", None) or {})
        except Exception:
            return None

    from .token_ledger import get_ledger
    budget = SpendBudget(
        cfg, ledger_getter=get_ledger, model_cost=_model_cost,
        clock=clock, emit_card=emit_card,
    )
    register_spend_budget(budget)
    logger.info("[budget] 花费刹车已启用:on_limit=%s daily_usd=%s daily_tokens=%s "
                "monthly_usd=%s monthly_tokens=%s", cfg.on_limit, cfg.daily_usd,
                cfg.daily_tokens, cfg.monthly_usd, cfg.monthly_tokens)
    return budget


def check_spend_budget(source: str) -> None:
    """gateway 咽喉调用前的模块级入口:未注册 → no-op(放行);已注册 → 委托 check。

    抛 SpendBudgetExceeded 时**向上传播**(gateway 让它冒出去 = fail-loud 拒发)。"""
    b = _BUDGET
    if b is None:
        return
    b.check(source)


__all__ = [
    "SpendBudget",
    "SpendBudgetExceeded",
    "AUTOMATIC_SOURCES",
    "is_automatic_source",
    "register_spend_budget",
    "get_spend_budget",
    "check_spend_budget",
    "wire_spend_budget",
    "ACTION_ALLOW",
    "ACTION_WARN",
    "ACTION_BLOCK",
]
