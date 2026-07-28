"""console/scene_preexec — 「猜你想干 v2」刀2:预测定向预执行(docs/94 刀2 灵魂刀)。

唤醒后不说"你可能想做 X",而是**空闲把 X 先干到可呈现**,出「已备好」卡。流程:

  刀1 场景信号(schedule_due / task_failed,scene_gate.scene_tick 采到)
    → 本模块消费面 `maybe_consume_signal`(刀1 门先行只判不记账;不适用/不值得 → 回落普通卡)
    → relevance 判断(scene_relevance,一次便宜 LLM;结果按指纹缓存,同信号只判一次)
    → 空闲预执行(复用既有 atom 执行底座:ml.drive(fresh=True) + 独立验收,
       abort_scope + run_scope + token_task,任务卡 kind="scene_preexec" 可 ⏹ 停)
    → 完成且过验证 → 产物落 staging → 经 scene_gate.emit_gated 出「已备好」卡
       (**此刻才**扣刀1 同一份日预算 + 记指纹;失败/放弃不扣不出)。

硬纪律(docs/94 刀2):
- **烧钱面**:并发=1(A1:同指纹/跨指纹都靠 inflight **占坑先于任何 await**,relevance
  判断窗内绝不双判双起);有正在跑的用户任务不起(空闲才干);**单次界 = max_turns(轮数)
  非 token**(scene_preexec_max_turns 默认 8,单次尝试 1 次不 replan);token 维度是每日上限
  **预留制**(A3:今日已花 + SCENE_PREEXEC_RUN_RESERVE ≤ scene_preexec_max_tokens 才起,
  按 token_task 归因从账本实数累计,异常路径也记账);launch 前显式过全局花费刹车
  check_spend_budget("scene_preexec")。诚实边界:drive 内部 forge 的 LLM 调用把
  token_source 盖成 "forge"(既有咽喉行为不动)—— per-run 成本由 token_task 归因 +
  本模块日账兜住,relevance 调用则真标 "scene_relevance"。
- **污染面**:**staging 专属 token**(B1:staging_scoped_token —— 原 fs 写权全剥、用户
  工作区降只读,staging work 授读写/exec;沙箱可写面由 token 定,bash 也写不出 staging;
  铸不出 token → fail-closed 不跑)+ workspace_root 指向 staging(文件工具 path_allowed
  的根);drive 用 fresh=True(跳过召回/observe/结晶 —— **零技能库写入**);不挂
  create_atom(零原子铸造);本模块**绝不**调 mem.write / 经验沉淀(零主动记忆写入);
  漏斗事件带 source="scene_preexec",习惯蒸馏侧过滤(B2:机器诊断 goal 不进"你的习惯")。
  已知留痕(有意,跑评分离账):Trace run 记录与 token 账照常 —— 它们是运行流水,不是记忆态。
  ACCEPT 后产物才落地(会话/工作区);REJECT/过期 → staging 删除,零残留。
- **错时面**:场景带 expiry(schedule_due=触发点+1h;task_failed=+24h);维护 tick
  `scene_ready_maintenance` 扫过期 scene_ready 卡 → registry 撤卡 + 弃产物 + 日志一行。
- **失败静默弃**:预执行失败 fail-loud 到日志/任务卡,不弹卡不扣预算;同指纹只试一次
  (attempted 持久),之后回落刀1 普通卡,绝不无限烧钱重试。

旁路纪律:本模块任何函数异常都吞掉(消费面返 False = 回落普通卡),绝不冒泡到维护 tick。
"""
from __future__ import annotations

import asyncio
import json
import logging
import pathlib
import shutil
import time
import uuid
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 第一版触发面收窄(宁窄勿滥):只这两个场景开预执行;manual_repeat/big_job_done 仍走普通卡。
PREEXEC_SCENES = ("schedule_due", "task_failed")

# 单次预算界 = **max_turns(轮数)不是 token**:小 max_turns(可配 app.state.
# scene_preexec_max_turns)+ 单次尝试不 replan;token 维度只有每日上限(下面,预留制)。
DEFAULT_MAX_TURNS = 8
# 每日 token 上限(可配 app.state.scene_preexec_max_tokens;按 token_task 归因实数累计)。
DEFAULT_DAILY_MAX_TOKENS = 30_000
# A3 预留制:launch 门 = 今日已花 + 本预留 ≤ 每日上限才起(可配 app.state.
# scene_preexec_run_reserve)—— 单次没有 token 硬闸(界在 max_turns),预留挡住
# "上限只剩一丝仍放行整个 run"的溢出。
SCENE_PREEXEC_RUN_RESERVE = 10_000
# staging 有界:最多保 5 个目录,超龄 48h 清(待决卡引用的不清 —— 卡还挂着产物不能丢)。
# docs/94 刀3 ④:可配 app.state.scene_staging_max / scene_staging_ttl_s(待标定清单见
# scene_gate.py 头注总表;默认不动)。
STAGING_MAX = 5
STAGING_TTL_S = 48 * 3600
# 错时 expiry:schedule_due = 触发点 + 1h(可配 app.state.scene_schedule_due_expiry_slack_s);
# task_failed = 24h(当下场景视界)。
SCHEDULE_DUE_EXPIRY_SLACK_S = 3600
TASK_FAILED_EXPIRY_S = 24 * 3600
# 产物/诊断文本进卡摘要与重跑注入的封顶。
_PRODUCT_GIST_CAP = 120
_RERUN_DIAGNOSIS_CAP = 4000
# 持久状态有界(attempted/relevance 指纹表,超了按 ts 砍最老)。
_MAX_ENTRIES = 500
# relevance 供料的习惯行数封顶(docs/94 刀3 ③:防 prompt 膨胀;judge 侧另有同级截断双关)。
_HABIT_LINES_MAX = 5

TOKEN_SOURCE = "scene_preexec"


def _day_str(ts: float) -> str:
    return time.strftime("%Y-%m-%d", time.localtime(ts))


# ---------------------------------------------------------------- 持久状态
class ScenePreexecStore:
    """预执行的持久状态:attempted 指纹(同信号只试一次)+ relevance 缓存(同信号只判一次)
    + 每日 token 日账(双重上限的"每日"半)。path=None = 纯内存(测试不污染真实 home)。
    宁空勿毒:坏文件当空、落盘失败不阻断。"""

    def __init__(self, path: Optional[pathlib.Path] = None) -> None:
        self._path = pathlib.Path(path) if path else None
        self._attempted: dict[str, float] = {}
        self._relevance: dict[str, dict] = {}
        self._day = ""
        self._tokens_spent = 0
        self._load()

    def _load(self) -> None:
        if self._path is None or not self._path.exists():
            return
        try:
            raw = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            logger.warning("[scene_preexec] 状态文件损坏,当空(最多重判一次 relevance)")
            return
        if not isinstance(raw, dict):
            return
        try:
            att = raw.get("attempted")
            if isinstance(att, dict):
                for fp, ts in att.items():
                    try:
                        self._attempted[str(fp)] = float(ts)
                    except (TypeError, ValueError):
                        continue
            rel = raw.get("relevance")
            if isinstance(rel, dict):
                for fp, d in rel.items():
                    if isinstance(d, dict):
                        self._relevance[str(fp)] = dict(d)
            self._day = str(raw.get("day") or "")
            self._tokens_spent = max(0, int(raw.get("tokens_spent") or 0))
        except Exception:
            pass

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._path.with_suffix(self._path.suffix + ".tmp")
            tmp.write_text(json.dumps({
                "attempted": self._attempted, "relevance": self._relevance,
                "day": self._day, "tokens_spent": self._tokens_spent,
            }, ensure_ascii=False, indent=2), encoding="utf-8")
            tmp.replace(self._path)
        except Exception as e:
            logger.warning("[scene_preexec] 状态落盘失败(不阻断): %s", e)

    def _prune(self) -> None:
        for table in (self._attempted, self._relevance):
            if len(table) > _MAX_ENTRIES:
                key = (lambda kv: kv[1]) if table is self._attempted else \
                    (lambda kv: float((kv[1] or {}).get("ts") or 0.0))
                keep = sorted(table.items(), key=key, reverse=True)[:_MAX_ENTRIES]
                table.clear()
                table.update(dict(keep))

    # ---- attempted(同指纹只预执行一次,成败均记;之后回落普通卡)----
    def attempted(self, fp: str) -> bool:
        return (fp or "").strip() in self._attempted

    def mark_attempted(self, fp: str, *, now: Optional[float] = None) -> None:
        f = (fp or "").strip()
        if not f:
            return
        self._attempted[f] = time.time() if now is None else float(now)
        self._prune()
        self._save()

    # ---- relevance 缓存(同指纹只烧一次判断 LLM)----
    def relevance(self, fp: str) -> Optional[dict]:
        d = self._relevance.get((fp or "").strip())
        return dict(d) if d else None

    def put_relevance(self, fp: str, verdict: dict, *, now: Optional[float] = None) -> None:
        f = (fp or "").strip()
        if not f:
            return
        self._relevance[f] = {**dict(verdict or {}),
                              "ts": time.time() if now is None else float(now)}
        self._prune()
        self._save()

    # ---- 每日 token 日账(从 token_ledger.task_total 实数累计;本地日历日重置)----
    def _roll_day(self, now: float) -> None:
        day = _day_str(now)
        if day != self._day:
            self._day = day
            self._tokens_spent = 0

    def tokens_spent_today(self, *, now: Optional[float] = None) -> int:
        try:
            self._roll_day(time.time() if now is None else float(now))
            return self._tokens_spent
        except Exception:
            return 0

    def add_tokens(self, n: int, *, now: Optional[float] = None) -> None:
        try:
            self._roll_day(time.time() if now is None else float(now))
            self._tokens_spent += max(0, int(n))
            self._save()
        except Exception:
            pass


def preexec_store(app: Any) -> ScenePreexecStore:
    """app.state 上懒加载(scene_preexec.json 与 scene_gate.json 同家)。"""
    st = getattr(app.state, "scene_preexec_store", None)
    if st is not None:
        return st
    cfgp = getattr(app.state, "config_path", "") or ""
    path = (pathlib.Path(cfgp).parent / "scene_preexec.json") if cfgp else None
    st = ScenePreexecStore(path)
    app.state.scene_preexec_store = st
    return st


# ---------------------------------------------------------------- staging(产物隔离铁律)
def staging_root(app: Any) -> pathlib.Path:
    root = getattr(app.state, "scene_staging_root", None)
    return pathlib.Path(root) if root else (pathlib.Path.home() / ".karvyloop" / "scene_staging")


def _pending_staging_ids(app: Any) -> set:
    """仍被待决 scene_ready 卡引用的 staging id(生命周期保护:卡挂着产物不清)。"""
    out: set = set()
    reg = getattr(app.state, "proposal_registry", None)
    if reg is None:
        return out
    try:
        from karvyloop.karvy.proposal_registry import KIND_SCENE_READY
        for pr in reg.pending():
            if getattr(pr, "kind", "") == KIND_SCENE_READY:
                sid = str((getattr(pr, "payload", {}) or {}).get("staging_id") or "")
                if sid:
                    out.add(sid)
    except Exception:
        pass
    return out


def create_staging(app: Any, *, now: Optional[float] = None) -> Optional[pathlib.Path]:
    """建一个新 staging 目录 <root>/<sid>/(含 work/ 子目录 = drive 的 workspace)。

    有界:已有 ≥ 上限(默认 STAGING_MAX,可配 scene_staging_max)个 → 先清最老的
    **未被待决卡引用**目录;还挤不出位 → None(本次不预执行,宁可少干)。"""
    try:
        cap = _staging_max(app)   # docs/94 刀3 ④:上限可配(默认不变)
        root = staging_root(app)
        root.mkdir(parents=True, exist_ok=True)
        dirs = sorted([d for d in root.iterdir() if d.is_dir()],
                      key=lambda d: d.stat().st_mtime)
        if len(dirs) >= cap:
            keep = _pending_staging_ids(app)
            for d in dirs:
                if d.name not in keep:
                    shutil.rmtree(d, ignore_errors=True)
                    dirs.remove(d)
                    break
        if len(dirs) >= cap:
            logger.info("[scene_preexec] staging 已满(%s 个都被待决卡引用)→ 本次不预执行",
                        cap)
            return None
        sid = uuid.uuid4().hex[:12]
        d = root / sid
        (d / "work").mkdir(parents=True, exist_ok=True)
        return d
    except Exception as e:
        logger.debug("[scene_preexec] 建 staging 失败(本次不预执行): %s", e)
        return None


def discard_staging(app: Any, staging_id: str) -> bool:
    """丢弃一个 staging 目录(REJECT / 过期 / 失败 / 预算拒)。幂等,失败只 debug。"""
    sid = (staging_id or "").strip()
    if not sid or "/" in sid or "\\" in sid or ".." in sid:
        return False
    try:
        d = staging_root(app) / sid
        if d.is_dir():
            shutil.rmtree(d, ignore_errors=True)
            return True
        return False
    except Exception as e:
        logger.debug("[scene_preexec] 弃 staging %s 失败(48h 巡检兜底): %s", sid, e)
        return False


def write_product(app: Any, staging_dir: pathlib.Path, meta: dict) -> None:
    (staging_dir / "product.json").write_text(
        json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")


def load_product(app: Any, staging_id: str) -> Optional[dict]:
    """读产物元数据;不在/坏 → None(卡的 ACCEPT 据此诚实回执"已过期清理")。"""
    sid = (staging_id or "").strip()
    if not sid or "/" in sid or "\\" in sid or ".." in sid:
        return None
    try:
        p = staging_root(app) / sid / "product.json"
        if not p.exists():
            return None
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def move_files_to_workspace(app: Any, staging_id: str) -> int:
    """ACCEPT 落地①的文件半:staging/<sid>/work/* → <workspace_root>/karvy_prepared/<sid>/。

    只在 ACCEPT 后被调(产物隔离铁律:staging 外零写入只在这一步破例)。返回移动的文件数;
    work 为空/失败 → 0(文本半照常送达)。"""
    sid = (staging_id or "").strip()
    if not sid or "/" in sid or "\\" in sid or ".." in sid:
        return 0
    try:
        work = staging_root(app) / sid / "work"
        if not work.is_dir():
            return 0
        entries = [p for p in work.iterdir()]
        if not entries:
            return 0
        rk = getattr(app.state, "runtime_kwargs", None) or {}
        ws = (rk.get("workspace_root") or "").strip()
        if not ws or ws == "/":
            return 0
        dest = pathlib.Path(ws) / "karvy_prepared" / sid
        dest.mkdir(parents=True, exist_ok=True)
        n = 0
        for p in entries:
            try:
                shutil.move(str(p), str(dest / p.name))
                n += 1
            except Exception:
                continue
        return n
    except Exception as e:
        logger.debug("[scene_preexec] 文件移入工作区失败(文本仍送达): %s", e)
        return 0


# ---------------------------------------------------------------- 空闲/预算判定(确定性)
def _inflight(app: Any) -> dict:
    d = getattr(app.state, "_scene_preexec_inflight", None)
    if d is None:
        d = app.state._scene_preexec_inflight = {}
    return d


def user_busy(app: Any) -> bool:
    """有正在跑的任务(用户的活 / 别的系统活)→ True:预执行绝不抢资源,空闲才干。
    读不到任务板 → True(判不了空闲当忙,保守不干)。"""
    reg = getattr(app.state, "task_registry", None)
    if reg is None:
        return True
    try:
        return any(t.get("status") == "running" for t in reg.list())
    except Exception:
        return True


def _max_turns(app: Any) -> int:
    try:
        n = int(getattr(app.state, "scene_preexec_max_turns", DEFAULT_MAX_TURNS))
        return n if n >= 1 else DEFAULT_MAX_TURNS
    except (TypeError, ValueError):
        return DEFAULT_MAX_TURNS


def _daily_token_cap(app: Any) -> int:
    try:
        n = int(getattr(app.state, "scene_preexec_max_tokens", DEFAULT_DAILY_MAX_TOKENS))
        return n if n >= 1 else DEFAULT_DAILY_MAX_TOKENS
    except (TypeError, ValueError):
        return DEFAULT_DAILY_MAX_TOKENS


def _run_reserve(app: Any) -> int:
    """A3:单次预留量(launch 门用;0 合法=退回纯水位判,但默认保守 10k)。"""
    try:
        n = int(getattr(app.state, "scene_preexec_run_reserve", SCENE_PREEXEC_RUN_RESERVE))
        return n if n >= 0 else SCENE_PREEXEC_RUN_RESERVE
    except (TypeError, ValueError):
        return SCENE_PREEXEC_RUN_RESERVE


# docs/94 刀3 ④:以下三个拍脑袋常数补 app.state 覆盖(模式照 scene_big_job_s:读不到/
# 坏值退默认;不改默认值,内测真数据来了照 scene_gate.py 头注的待标定清单表标定)。
def _staging_max(app: Any) -> int:
    """staging 目录数上限(可配 app.state.scene_staging_max;<1 视为默认)。"""
    try:
        n = int(getattr(app.state, "scene_staging_max", STAGING_MAX))
        return n if n >= 1 else STAGING_MAX
    except (TypeError, ValueError):
        return STAGING_MAX


def _staging_ttl_s(app: Any) -> float:
    """staging 目录超龄清理阈值(可配 app.state.scene_staging_ttl_s;<=0 视为默认)。"""
    try:
        v = float(getattr(app.state, "scene_staging_ttl_s", STAGING_TTL_S))
        return v if v > 0 else float(STAGING_TTL_S)
    except (TypeError, ValueError):
        return float(STAGING_TTL_S)


def _due_expiry_slack_s(app: Any) -> float:
    """schedule_due 场景过期宽限(触发点 + slack;可配 app.state.
    scene_schedule_due_expiry_slack_s;<0 视为默认,0 合法=触发点即过期)。"""
    try:
        v = float(getattr(app.state, "scene_schedule_due_expiry_slack_s",
                          SCHEDULE_DUE_EXPIRY_SLACK_S))
        return v if v >= 0 else float(SCHEDULE_DUE_EXPIRY_SLACK_S)
    except (TypeError, ValueError):
        return float(SCHEDULE_DUE_EXPIRY_SLACK_S)


# ---------------------------------------------------------------- relevance 供料
def _conversation_gist(app: Any) -> str:
    """当前对话摘要(最近几轮 user 说了什么;纯读,fail-soft 空)。"""
    try:
        mgr = getattr(app.state, "conversation_manager", None)
        cur = mgr.current() if mgr is not None else None
        if cur is None:
            return ""
        lines = [(t.user_intent or "").strip().replace("\n", " ")
                 for t in cur.context_view(5)]
        return " / ".join(x[:80] for x in lines if x)
    except Exception:
        return ""


def _habit_lines(app: Any) -> list:
    """(供料)HabitStore 习惯 pattern(纯读,fail-soft 空;测试可注 app.state.habit_store)。

    docs/94 刀3 ③ 供料质量:空/纯空白 pattern **不注水**(干净滤掉,不是 "(无)" 占位;
    没有习惯 → 返 [],judge 侧整段干净省略);行数封顶 _HABIT_LINES_MAX、单行截 80
    (防 prompt 膨胀 —— relevance 是便宜调用,料要小)。
    """
    try:
        store = getattr(app.state, "habit_store", None)
        if store is None:
            pump = getattr(app.state, "proposal_pump", None)
            analyst = getattr(pump, "_analyst", None) if pump is not None else None
            store = getattr(analyst, "habit_store", None) if analyst is not None else None
        if store is None:
            return []
        out = []
        for h in store.list_habits(_HABIT_LINES_MAX):
            line = str(getattr(h, "pattern", "") or "").strip()
            if not line:
                continue   # 空/坏习惯行:干净省略,绝不占位注水
            out.append(line[:80])
            if len(out) >= _HABIT_LINES_MAX:
                break
        return out
    except Exception:
        return []


# ---------------------------------------------------------------- 消费面(scene_tick 调)
async def maybe_consume_signal(app: Any, sig: Any, *, now: Optional[float] = None) -> bool:
    """刀2 消费面:这条场景信号要不要走"预执行 → 已备好卡"路线。

    返回 True = 已消费(预执行已在跑/刚起,压住普通卡);False = 不适用/不值得/资源不空闲
    → 调用方(scene_tick)照旧走刀1 emit_gated 普通卡。**任何异常返 False**(刀1 行为 0 回归)。
    """
    try:
        n_ts = time.time() if now is None else float(now)
        sk = getattr(sig, "scene_kind", "") or ""
        fp = getattr(sig, "fingerprint", "") or ""
        if sk not in PREEXEC_SCENES or not fp:
            return False
        if getattr(app.state, "scene_preexec_enabled", True) is False:
            return False
        ml = getattr(app.state, "main_loop", None)
        rk = getattr(app.state, "runtime_kwargs", None) or {}
        if ml is None or rk.get("gateway") is None:
            return False   # --no-llm / 未接:刀1 原样
        inflight = _inflight(app)
        if fp in inflight:
            return True    # 这条信号的预执行正在跑 → 压住普通卡(完成时它自己出已备好卡)
        store = preexec_store(app)
        if store.attempted(fp):
            return False   # 同指纹只试一次(成败均是);之后回落普通卡
        # 刀1 门先行(只判不记账):dup/预算尽/REJECT 冷却 → 不烧 relevance,原路走
        from karvyloop.karvy.scene_gate import scene_budget, scene_gate
        if scene_gate(app).check(fp, sk, budget=scene_budget(app), now=n_ts) != "allow":
            return False
        if inflight:
            return False   # 并发=1:已有别的预执行在跑 → 这条回落普通卡(不排队)
        if user_busy(app):
            return False   # 有活跃任务:不抢资源,回落普通卡(刀1 行为)
        # A3(预留制):launch 门 = 今日已花 + 单次预留(RUN_RESERVE)仍在每日上限内才起 ——
        # 防"已花 29.9k/上限 30k 仍放行一整个 run"把日预算溢出一大截。
        if store.tokens_spent_today(now=n_ts) + _run_reserve(app) > _daily_token_cap(app):
            logger.debug("[scene_preexec] 今日预执行 token 预算(含单次预留)不够 → 回落普通卡")
            return False
        # 全局花费刹车(唯一咽喉同一判定;达 100%+pause → 抛 → 回落普通卡,普通卡零 LLM)
        try:
            from karvyloop.llm.spend_budget import check_spend_budget
            check_spend_budget(TOKEN_SOURCE)
        except Exception:
            logger.info("[scene_preexec] 全局花费预算拦下预执行 → 回落普通卡")
            return False
        # ---- A1(并发窗):从这里起有 await —— **先占坑再判**。上面的 in-flight 检查到
        # relevance await 之间若不占坑,两个挂点(维护 tick + drive 收尾旁路)会在窗口里
        # 双 relevance + 双预执行。sentinel 占住 inflight[fp];没真起(不值得/异常)finally 摘坑。
        sentinel = object()
        inflight[fp] = sentinel
        launched = False
        try:
            # relevance 判断(一次便宜 LLM;按指纹缓存,同信号只判一次)
            rel = store.relevance(fp)
            if rel is None:
                rel = await _judge(app, sig)
                store.put_relevance(fp, rel, now=n_ts)
            if not rel.get("worth"):
                return False   # 不值得 → 回落刀1 普通建议卡(设计:不预执行)
            # 值得 → 记 attempted(先记后干:失败不无限重试)+ 后台预执行(占坑换成真任务)
            store.mark_attempted(fp, now=n_ts)
            loop = asyncio.get_running_loop()
            task = loop.create_task(_run_preexec(app, sig, rel, now=n_ts))
            inflight[fp] = task
            launched = True

            def _done(t: Any) -> None:
                inflight.pop(fp, None)
                try:
                    exc = t.exception()
                except BaseException:
                    return
                if exc is not None:
                    logger.warning("[scene_preexec] 预执行后台任务异常(静默弃,不弹卡): %s", exc)

            task.add_done_callback(_done)
            logger.info("[scene_preexec] 场景 %s 值得先干(%s)→ 空闲预执行已起",
                        sk, (rel.get("action_intent") or "")[:60])
            return True
        finally:
            if not launched and inflight.get(fp) is sentinel:
                inflight.pop(fp, None)   # 没真起 → 摘坑(别把指纹永久占死)
    except Exception as e:  # noqa: BLE001 —— 消费面绝不外溢,回落刀1 普通卡
        logger.debug("[scene_preexec] 消费面异常(回落普通卡): %s", e)
        return False


async def _judge(app: Any, sig: Any) -> dict:
    """relevance 判断(包一层:LLM 坏输出/调不通 → {"worth": False}=回落普通卡,宁空勿毒)。"""
    try:
        from karvyloop.karvy.scene_relevance import judge_relevance
        rk = getattr(app.state, "runtime_kwargs", None) or {}
        prop = getattr(sig, "proposal", None)
        desc = " ".join(x for x in (getattr(prop, "summary", ""),
                                    getattr(prop, "basis", "")) if x)
        got = await judge_relevance(
            rk.get("gateway"), rk.get("model_ref", "") or "",
            scene_desc=desc, conversation_gist=_conversation_gist(app),
            habits=_habit_lines(app))
        return got if got is not None else {"worth": False, "action_intent": "", "reason": "parse_failed"}
    except Exception as e:  # noqa: BLE001
        logger.debug("[scene_preexec] relevance 判断异常(当不值得): %s", e)
        return {"worth": False, "action_intent": "", "reason": "error"}


# ---------------------------------------------------------------- 预执行本体
def _expiry_for(app: Any, sig: Any, *, now: float) -> float:
    """场景过期时刻:schedule_due = 下次触发点 + 1h(算不出 → now+2h 兜底);task_failed = now+24h。"""
    sk = getattr(sig, "scene_kind", "") or ""
    if sk == "schedule_due":
        try:
            payload = getattr(getattr(sig, "proposal", None), "payload", {}) or {}
            sid = str(payload.get("schedule_id") or "")
            from karvyloop.console.routes_schedules import _scheduler_store
            from karvyloop.karvy.scheduler import next_run_after
            for t in _scheduler_store(app).all():
                if str(getattr(t, "id", "")) == sid:
                    nxt = next_run_after(getattr(t, "cron", "") or "", now)
                    if nxt is not None:
                        return float(nxt) + _due_expiry_slack_s(app)   # docs/94 刀3 ④:宽限可配
        except Exception:
            pass
        return now + 2 * 3600
    return now + TASK_FAILED_EXPIRY_S


def _build_goal(sig: Any, rel: dict) -> tuple:
    """(drive 目标, action, what)。schedule_due=提前试跑原意图(deliver);task_failed=
    诊断失败原因+修法草稿、绝不重跑原任务(rerun:ACCEPT 才带着草稿沿 run_task 语义重跑)。"""
    prop = getattr(sig, "proposal", None)
    payload = getattr(prop, "payload", {}) or {}
    intent = (payload.get("intent") or "").strip()
    action_intent = (rel.get("action_intent") or "").strip()
    sk = getattr(sig, "scene_kind", "") or ""
    if sk == "schedule_due":
        return intent, "deliver", intent
    basis = (getattr(prop, "basis", "") or "").strip()
    goal = (
        f"任务「{intent}」上次执行失败。请诊断失败原因并起草修复方案:\n"
        f"1. 分析最可能的失败原因(可用工具核查环境/文件/网络,只读核查);\n"
        f"2. 若是确定性可修的问题,给出具体修法草稿(步骤/命令/改动);\n"
        f"3. **绝不重跑原任务本身**(这是诊断,不是执行)。\n"
        + (f"已知线索:{basis[:300]}\n" if basis else "")
        + (f"参考方向:{action_intent}" if action_intent else "")
    ).strip()
    return goal, "rerun", intent


def _execute_sync(app: Any, goal: str, work_dir: str, tid: str) -> dict:
    """线程内跑一次预执行(复用既有 atom 执行底座,同 pursuit_tick._advance 款式):

    - **staging 专属 token**(B1 修):沙箱可写面由 token 决定(mounts_from_token→rw_bind),
      不是 workspace_root —— staging_scoped_token 剥离原 fs 写权(用户工作区降只读)+
      授 staging work 读写/exec;铸不出 → fail-closed 不跑(隔离铁律优先于功能);
    - workspace_root 覆写成 staging work 目录(文件工具 path_allowed 的根,隔离的另一半);
    - drive(fresh=True):跳过召回/observe/**结晶**(零技能库写入);不挂 create_atom;
    - abort_scope(tid):/api/task/cancel 可停正跑的预执行(任务卡有 ⏹);
    - run_scope + token_task:Trace / per-run token 归因照常(跑评分离账不缺);
    - 单次界 = **max_turns(轮数)**,不是 token(token 维度由每日上限+预留制管)。
    返回 {ok, text, error, terminal, infra_dead, unfinished, verdict_passed, verdict_inconclusive}。
    """
    ml = getattr(app.state, "main_loop", None)
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    rk2 = dict(rk)
    rk2["workspace_root"] = work_dir
    rk2.pop("max_turns", None)
    from karvyloop.sandbox.mounts import staging_scoped_token
    st_token = staging_scoped_token(rk.get("token"), work_dir)
    if st_token is None:
        # 铸不出隔离 token(测试桩/坏 token)→ 绝不复用原 token 跑(那等于放开用户工作区写权)
        return {"ok": False, "text": "", "error": "no_staging_token", "terminal": "",
                "infra_dead": False, "unfinished": True,
                "verdict_passed": False, "verdict_inconclusive": True}
    rk2["token"] = st_token

    from karvyloop.console.decision_wire import assemble_governance
    from karvyloop.runtime.main_loop import forge_slow_brain_factory

    persona = None
    try:
        from karvyloop.coding.persona import build_karvy_persona_prompt
        persona = build_karvy_persona_prompt(cwd=work_dir)
    except Exception:
        persona = None
    gov = assemble_governance(app, intent=goal, domain="", role="", base="")
    slow_brain = forge_slow_brain_factory(governance=gov, persona=persona,
                                          max_turns=_max_turns(app), **rk2)

    from karvyloop.atoms.abort import abort_scope
    from karvyloop.atoms.terminal import is_infra_dead
    from karvyloop.cognition.trace import run_scope
    from karvyloop.llm.token_ledger import token_source, token_task

    # token_source 外层裹 "scene_preexec"(B2):drive 层的漏斗事件(_emit_funnel_event 在
    # slow_brain 返回后落,forge 内层 with 已退出)会带上这个 source 标 → 习惯蒸馏侧凭它把
    # 机器诊断 goal 挡在"你的习惯"之外(自我反馈环)。诚实边界:forge 的 LLM 调用在其内层
    # with 里仍标 "forge"(既有咽喉行为不动)——per-run 成本靠 token_task 归因 + 本模块日账。
    with abort_scope(tid or ""), run_scope(), token_task(tid or ""), \
            token_source(TOKEN_SOURCE):
        result = ml.drive(goal, slow_brain=slow_brain, fresh=True)
    text = (getattr(result, "text", "") or "")
    error = (getattr(result, "error", "") or "")
    terminal = (getattr(result, "terminal", "") or "")
    out = {"ok": False, "text": text, "error": error, "terminal": terminal,
           "infra_dead": bool(is_infra_dead(terminal)), "unfinished": False,
           "verdict_passed": False, "verdict_inconclusive": True}
    if out["infra_dead"]:
        return out
    if terminal and terminal != "completed":
        # 没跑完(max_turns/aborted…)= 预执行自己的单次预算没兜住 —— **不是**场景信息,
        # 静默弃(标 unfinished,别把 "max_turns" 当"错误诊断"产物端给用户)。
        out["unfinished"] = True
        out["error"] = out["error"] or terminal   # 任务卡照实记(_humanize_bare_terminal 出人话)
        return out
    if error:
        return out
    # 独立验收(「完成且过验证」;缺验收能力 → 诚实 inconclusive,不假装验过)
    try:
        if all(rk2.get(k) is not None for k in ("token", "sandbox", "gateway")):
            from karvyloop.coding.checker import independent_check
            verdict = asyncio.run(independent_check(
                goal, text, token=rk2["token"], sandbox=rk2["sandbox"],
                gateway=rk2["gateway"], workspace_root=work_dir,
                model_ref=rk2.get("model_ref", "")))
            out["verdict_passed"] = bool(getattr(verdict, "passed", False))
            out["verdict_inconclusive"] = bool(getattr(verdict, "inconclusive", True))
    except Exception as e:  # noqa: BLE001 —— 验收炸了当 inconclusive(不假装验过)
        logger.debug("[scene_preexec] 独立验收异常(当 inconclusive): %s", e)
    out["ok"] = True
    return out


async def _run_preexec(app: Any, sig: Any, rel: dict, *, now: float) -> None:
    """后台预执行一条已判"值得"的场景信号。成功且过验证 → 产物落 staging + 经 emit_gated
    出「已备好」卡(此刻才扣预算);失败/过验证不过/场景已过期/预算被抢 → 静默弃。"""
    sk = getattr(sig, "scene_kind", "") or ""
    fp = getattr(sig, "fingerprint", "") or ""
    prop = getattr(sig, "proposal", None)
    payload = getattr(prop, "payload", {}) or {}
    staging = create_staging(app, now=now)
    if staging is None:
        return
    sid = staging.name
    goal, action, what = _build_goal(sig, rel)
    if not goal:
        discard_staging(app, sid)
        return
    expiry_ts = _expiry_for(app, sig, now=now)

    task_reg = getattr(app.state, "task_registry", None)
    tid = ""
    if task_reg is not None:
        try:
            from karvyloop import i18n
            tid = task_reg.start(
                who="小卡", domain_id=payload.get("domain_id", "l0") or "l0",
                role=payload.get("role", "") or "",
                intent=i18n.t("scene_preexec.task_intent", what=(what or goal)[:60]),
                kind="scene_preexec")
        except Exception:
            tid = ""
    def _account_tokens() -> None:
        # 每日 token 日账(per-run 成本按 token_task 归因,从账本实数累计;
        # 异常路径也记 —— 烧了一半的钱同样吃每日上限,别让炸掉的 run 逃账)。
        try:
            from karvyloop.llm.token_ledger import get_ledger
            led = get_ledger()
            if led is not None and tid:
                preexec_store(app).add_tokens(led.task_total(tid))
        except Exception:
            pass

    try:
        res = await asyncio.to_thread(_execute_sync, app, goal, str(staging / "work"), tid)
    except Exception as e:  # noqa: BLE001 —— 失败静默弃:任务卡/日志响,不弹卡不扣预算
        _account_tokens()
        if task_reg is not None and tid:
            try:
                task_reg.finish(tid, error=str(e))
            except Exception:
                pass
        discard_staging(app, sid)
        logger.warning("[scene_preexec] 预执行异常(静默弃): %s", e)
        return
    _account_tokens()
    if task_reg is not None and tid:
        try:
            task_reg.finish(tid, result=(res.get("text") or "")[:2000],
                            error=(res.get("error") or ""))
        except Exception:
            pass
    # ---- 产物判定(完成且过验证才出卡)----
    product_text = ""
    if res.get("infra_dead"):
        pass   # 基础能力失效:静默弃(不是场景信息)
    elif sk == "schedule_due":
        if res.get("ok") and not (res.get("verdict_passed") is False
                                  and res.get("verdict_inconclusive") is False):
            product_text = (res.get("text") or "").strip()      # 试跑结果
        elif res.get("error") and not res.get("unfinished"):
            # 设计定值:试跑仍失败 → 产物 = 错误诊断(「会再挂,原因是这个」正是场景价值)
            from karvyloop import i18n
            product_text = i18n.t("scene_preexec.product_error_diag",
                                  err=(res.get("error") or "")[:400],
                                  text=(res.get("text") or "").strip()[:800])
    else:  # task_failed 诊断草稿:必须干净完成且验收非明确失败
        if res.get("ok") and not (res.get("verdict_passed") is False
                                  and res.get("verdict_inconclusive") is False):
            product_text = (res.get("text") or "").strip()
    n2 = time.time()
    if not product_text.strip() or (expiry_ts and n2 > expiry_ts):
        discard_staging(app, sid)
        logger.info("[scene_preexec] 预执行未产出可呈现产物/场景已过期 → 静默弃(%s)", fp)
        return
    write_product(app, staging, {
        "text": product_text, "scene_kind": sk, "action": action,
        "fingerprint": fp, "intent": (payload.get("intent") or "").strip(),
        "goal": goal, "created_ts": n2, "expiry_ts": expiry_ts, "task_id": tid,
        "verdict_passed": bool(res.get("verdict_passed")),
        "verdict_inconclusive": bool(res.get("verdict_inconclusive", True)),
    })
    # ---- 出「已备好」卡(经刀1 同一扇门:此刻才 check+charge;门拒 → 弃产物)----
    from karvyloop.karvy.proposal_registry import proposal_for_scene_ready
    from karvyloop.karvy.scene_gate import emit_gated
    gist = product_text.replace("\n", " ")[:_PRODUCT_GIST_CAP]
    card = proposal_for_scene_ready(
        scene_kind=sk, action=action, what=what or goal, gist=gist, staging_id=sid,
        intent=(payload.get("intent") or "").strip(), ts=n2,
        domain_id=payload.get("domain_id", "l0") or "l0",
        role=payload.get("role", "") or "",
        schedule_id=str(payload.get("schedule_id") or ""),
        expiry_ts=expiry_ts, task_id=tid,
        scene_basis=(getattr(prop, "basis", "") or ""))
    sent = await emit_gated(app, card, fingerprint=fp, scene_kind=sk)
    if sent is None:
        # 完成期间预算被别的卡用光/指纹已被别路出卡 → 诚实弃产物(预算铁律:不出卡不落地)
        discard_staging(app, sid)
        logger.info("[scene_preexec] 已备好但唤醒门拒(预算/指纹)→ 弃产物(%s)", fp)
        return
    logger.info("[scene_preexec] 「已备好」卡已出(%s → %s,staging=%s)", sk, action, sid)


# ---------------------------------------------------------------- 错时撤卡 + staging 巡检
def scene_ready_maintenance(app: Any, *, now: Optional[float] = None) -> dict:
    """维护 tick(确定性零 LLM):①错时撤卡 —— 过期 scene_ready 卡 registry 撤卡+弃产物+
    日志一行(不打扰用户);②staging 有界 —— 超龄 48h 且不被待决卡引用的目录清掉。"""
    counts = {"expired": 0, "pruned": 0}
    n_ts = time.time() if now is None else float(now)
    try:
        reg = getattr(app.state, "proposal_registry", None)
        if reg is not None:
            from karvyloop.karvy.proposal_registry import KIND_SCENE_READY
            for pr in list(reg.pending()):
                if getattr(pr, "kind", "") != KIND_SCENE_READY:
                    continue
                payload = getattr(pr, "payload", {}) or {}
                try:
                    exp = float(payload.get("expiry_ts") or 0.0)
                except (TypeError, ValueError):
                    exp = 0.0
                if exp > 0 and n_ts > exp:
                    reg.remove(getattr(pr, "proposal_id", ""))
                    discard_staging(app, str(payload.get("staging_id") or ""))
                    counts["expired"] += 1
                    logger.info("[scene_preexec] 场景已过 → 撤「已备好」卡 %s 并弃产物",
                                getattr(pr, "proposal_id", ""))
    except Exception as e:  # noqa: BLE001
        logger.debug("[scene_preexec] 错时撤卡巡检异常(下轮再来): %s", e)
    try:
        ttl = _staging_ttl_s(app)   # docs/94 刀3 ④:超龄阈值可配(默认 48h 不变)
        root = staging_root(app)
        if root.is_dir():
            keep = _pending_staging_ids(app)
            for d in root.iterdir():
                if not d.is_dir() or d.name in keep:
                    continue
                try:
                    if n_ts - d.stat().st_mtime > ttl:
                        shutil.rmtree(d, ignore_errors=True)
                        counts["pruned"] += 1
                except Exception:
                    continue
    except Exception as e:  # noqa: BLE001
        logger.debug("[scene_preexec] staging 巡检异常(下轮再来): %s", e)
    return counts


# ---------------------------------------------------------------- ACCEPT 落地①的会话半
def deliver_to_private_chat(app: Any, user_line: str, text: str) -> bool:
    """把备好的产物作为一条聊天消息发进**私聊小卡**的会话(ACCEPT 落地①的文本半)。

    当前就在私聊 → record_turn(热路径同款);当前在别的场(群/域)→ 不动用户当前 peer,
    直接往私聊 peer 最近一段对话**落盘追加**(下次打开私聊即见)。失败返 False(调用方诚实回执)。"""
    mgr = getattr(app.state, "conversation_manager", None)
    if mgr is None:
        return False
    try:
        cur = mgr.current()
        if cur is not None and getattr(cur, "is_private", False):
            mgr.record_turn(user_line, text, brain="slow")
            return True
        store = getattr(mgr, "_store", None)
        if store is None:
            return False
        from karvyloop.cognition.conversation import Turn, karvy_world_peer
        peer = karvy_world_peer()
        conv = store.most_recent(peer) or store.new(peer)
        store.append_turn(conv, Turn(user_intent=user_line, agent_response=text, brain="slow"))
        return True
    except Exception as e:  # noqa: BLE001
        logger.warning("[scene_preexec] 产物发进会话失败: %s", e)
        return False


__all__ = [
    "PREEXEC_SCENES", "DEFAULT_MAX_TURNS", "DEFAULT_DAILY_MAX_TOKENS",
    "STAGING_MAX", "STAGING_TTL_S", "TOKEN_SOURCE",
    "ScenePreexecStore", "preexec_store", "staging_root",
    "create_staging", "discard_staging", "load_product", "write_product",
    "move_files_to_workspace", "deliver_to_private_chat", "user_busy",
    "maybe_consume_signal", "scene_ready_maintenance",
]
