"""onboarding — 「第一个 10 分钟」新手旅程(状态 + 随包样例数据)。

产品目标:把"要用一个月才能感受的飞轮"压缩成装完 10 分钟可亲眼看到 ——
第一步跑一个真演示任务(data-analyst 方法召回是真机制),第二步再跑一次同类任务,
用户亲眼看到**方法复用回执**(drive 返回的 skill_name 来自真 recall 命中)和
成长曲线(/api/skills/curve)上的**第一批 usage 点**。

诚实红线:本模块只提供样例数据与旅程状态,**不预制任何输出** —— 两个演示任务都
真跑用户配置的模型;回执/曲线点全部由真实执行产生。没配模型时旅程如实引导先配模型。

设计(薄状态机):
- 旅程状态持久在 `~/.karvyloop/onboarding.json`(env `KARVYLOOP_ONBOARDING_PATH` 可覆盖,
  测试隔离用);阶段 = fresh → step1(第一任务已发) → step2(第二任务已发) → done / skipped。
- 首启检测:无状态文件 + Trace 无任何 run = 新用户(fresh);无状态文件但 Trace 有 run =
  老实例(升级上来的),旅程视同 done,绝不对老用户突然弹新手旅程。
- 演示任务文案是**锁死的常量**(en/zh):它们的 token 必须命中 data-analyst 系统技能的
  召回(grep+overlap 匹配,无向量),且不含上下文依赖标记词(否则 CV-9 门会跳过召回)。
  测试锁死这两条,改文案必须过测试。
"""
from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Optional

#: 旅程阶段(唯一合法集合;前端/路由都以此为准)
JOURNEY_STAGES = ("fresh", "step1", "step2", "done", "skipped")

#: 状态文件写锁:stage 与 intake 同住 onboarding.json,两个写函数都做 read-modify-write。
#: 无锁则并发写(如采集器提交与旅程推进同请求周期撞上)会 lost-update 掉一个兄弟键。
_STATE_LOCK = threading.Lock()

#: 随包样例数据文件名(虚构数据,几十行,见 sample_data/)
SAMPLE_NAME = "quarterly_sales.csv"

#: 演示任务文案(en/zh)。**改动必须过 tests/test_onboarding_journey.py 的召回/门测试**:
#: - token 必须与 data-analyst 的 tags/when_to_use 有 overlap(csv/分析/数据…);
#: - 不得含 context_gate 的依赖标记词(它/这个/继续/the same/\bit\b…),
#:   否则第二句会被 CV-9 判上下文依赖 → 跳过召回 → 演示看不到方法复用回执。
JOURNEY_TASKS = {
    "en": {
        "task1": ("Analyze the attached quarterly_sales.csv: give me a per-category "
                  "overview and the notable trends."),
        "task2": ("Compare growth across categories in the quarterly_sales.csv data "
                  "and name the fastest-growing category, with evidence."),
    },
    "zh": {
        "task1": "分析附件 quarterly_sales.csv:给我各品类销售概览和值得注意的趋势。",
        "task2": "对比 quarterly_sales.csv 数据里各品类的增长速度,找出增长最快的品类,并给出证据。",
    },
}


def sample_data_dir() -> Path:
    """包内只读样例数据目录(`karvyloop/sample_data/`),与 system_skills 同发版语义。"""
    return Path(__file__).resolve().parent / "sample_data"


def load_sample() -> tuple[str, str]:
    """读随包样例 CSV → (文件名, 文本)。文件缺失(打包丢了)→ ("", "")(fail-soft,
    路由层如实报 ok:false,绝不编造数据)。"""
    p = sample_data_dir() / SAMPLE_NAME
    try:
        return SAMPLE_NAME, p.read_text(encoding="utf-8")
    except OSError:
        return "", ""


def compose_task_intent(task_text: str, *, sample_name: str, sample_text: str) -> str:
    """按前端真实发送格式组装演示任务 intent(镜像 app.js `_attachmentsTextInline` +
    `_submitChat` 的组装:文本附件内联在前、问题在后)。

    **仅供测试/E2E 复现前端路径用** —— 前端自己走 `_submitChat` 真路径组装,不调这里。
    """
    return f"[附件:{sample_name}]\n{sample_text}\n\n[我的问题] {task_text}"


def default_state_path() -> Path:
    """旅程状态文件路径:env `KARVYLOOP_ONBOARDING_PATH` > `~/.karvyloop/onboarding.json`。"""
    env = os.environ.get("KARVYLOOP_ONBOARDING_PATH", "").strip()
    if env:
        return Path(env).expanduser()
    return Path.home() / ".karvyloop" / "onboarding.json"


def read_stage(path: Optional[Path] = None, *, has_runs: bool = False) -> str:
    """读旅程阶段。

    - 状态文件在且合法 → 存的阶段。
    - 无状态文件 + 实例已有 run(Trace 非空)→ "done":老实例升级上来,绝不突然弹新手旅程。
    - 无状态文件 + 零 run → "fresh":真·新用户。
    - 文件损坏 → 按无文件同一逻辑(宁可保守,不炸启动)。
    """
    p = path or default_state_path()
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        stage = str(data.get("stage", "")).strip()
        if stage in JOURNEY_STAGES:
            return stage
    except (OSError, ValueError):
        pass
    return "done" if has_runs else "fresh"


def _read_state_dict(p: Path) -> dict:
    """状态文件整体读成 dict(损坏/缺失 → {},绝不炸)。stage 之外还住着 intake(人格采集器)。"""
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def write_stage(stage: str, path: Optional[Path] = None) -> bool:
    """持久化旅程阶段(合法集合外一律拒,返 False)。写失败 fail-soft 返 False。

    合并写(不整文件覆盖):intake 等兄弟键保留。唯一例外 —— 写 "fresh"(「重看旅程」)
    时**连 intake 一起重置**:可跳过可重来,采集器与旅程重看一致(重答会替换旧种子)。
    """
    if stage not in JOURNEY_STAGES:
        return False
    p = path or default_state_path()
    with _STATE_LOCK:   # RMW 全程持锁,防与 write_intake 并发丢兄弟键
        try:
            state = _read_state_dict(p)
            state.update({"stage": stage, "ts": time.time()})
            if stage == "fresh":
                state.pop("intake", None)   # 重看旅程 = 采集器也重来(一致语义)
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            return True
        except OSError:
            return False


def read_intake(path: Optional[Path] = None) -> dict:
    """人格采集器状态({"done": bool, "answers": {...}, "ts": float} 或 {});与旅程同文件。"""
    p = path or default_state_path()
    d = _read_state_dict(p).get("intake")
    return d if isinstance(d, dict) else {}


def write_intake(intake: dict, path: Optional[Path] = None) -> bool:
    """持久化采集器状态(合并写,stage 等兄弟键保留)。写失败 fail-soft 返 False。"""
    p = path or default_state_path()
    with _STATE_LOCK:   # RMW 全程持锁,防与 write_stage 并发丢兄弟键
        try:
            state = _read_state_dict(p)
            state["intake"] = dict(intake or {})
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            return True
        except OSError:
            return False


__all__ = [
    "JOURNEY_STAGES", "JOURNEY_TASKS", "SAMPLE_NAME",
    "sample_data_dir", "load_sample", "compose_task_intent",
    "default_state_path", "read_stage", "write_stage",
    "read_intake", "write_intake",
]
