"""karvy/workflow.py — 群内协作的 **workflow(工作流)模式**(ch4,Hardy 2026-06)。

群内协作两模式:圆桌(roundtable.py,开放讨论收敛)+ **workflow(本模块,结构化 角色→任务 DAG)**。
@多人(≥2)走 workflow:小卡按语义/岗位职责 + 你的目标设计一张 DAG → 你拍板 → 执行(依赖满足
的步骤并发、上游产出喂下游)→ 稳定成功后结晶给快脑匹配复用。

**IR(工作流能表达什么)**——薄执行引擎,但够表达真多步工作(借业界 workflow 语义、**不借**可视化
拖拽编辑器:作者是 LLM 按意图设计、人拍板,不是人连节点):
- `depends_on`:控制流依赖(上游做完下游才动);并行无依赖的步骤。
- `inputs`:这步真正吃哪几个上游的产出(默认=depends_on)。决定**喂什么**给它,也决定**分支合并**。
- `when`:**条件分支**——只在上游满足条件时才跑(否则跳过)。让"评审通过就发布 / 不通过就返工"成为可能。
- `on_fail`:**节点级容错**——"skip"(默认,失败不挡下游)/ "retry"(重试 max_retries 次)/ "abort"(中止全流程)。

跳过(skipped)≠ 失败(failed):**失败**的下游照跑(失败隔离,拿空产出);**分支没选中而跳过**的会
**级联跳过**下游(剪掉没走的分支),但**合并步**只要还有任一上游产出就照跑。

本模块只做**纯执行引擎**(可测):`run_workflow(plan, run_step=...)`;真驱动(按角色人格 drive)由
console 注入,规划(LLM 设计 DAG)+ 结晶在上层。不做:迭代/循环(for-each 动态展开)= 诚实 P1。
"""
from __future__ import annotations

import asyncio
from typing import Any, Awaitable, Callable

_TERMINAL = ("done", "failed", "skipped")


def _topo_ok(steps: dict) -> bool:
    """DAG 无环且依赖都存在(规划产出的 plan 先过这关,别把环喂进执行)。"""
    ids = set(steps)
    for s in steps.values():
        for d in s.get("depends_on", []):
            if d not in ids:
                return False
    # Kahn 检测环
    indeg = {sid: 0 for sid in steps}
    for s in steps.values():
        for d in s.get("depends_on", []):
            indeg[s["id"]] += 1
    q = [sid for sid, n in indeg.items() if n == 0]
    seen = 0
    while q:
        cur = q.pop()
        seen += 1
        for s in steps.values():
            if cur in s.get("depends_on", []):
                indeg[s["id"]] -= 1
                if indeg[s["id"]] == 0:
                    q.append(s["id"])
    return seen == len(steps)


def _data_deps(step: dict) -> list:
    """这步真正吃的上游(`inputs` 优先,否则=depends_on)。决定喂什么 + 分支合并/级联跳过。"""
    inp = step.get("inputs")
    if isinstance(inp, list) and inp:
        return [d for d in inp if isinstance(d, str)]
    return list(step.get("depends_on", []))


def _eval_when(step: dict, status: dict, output: dict) -> bool:
    """条件分支:`when` 满足才跑(无 when=恒真)。键 step 指一个上游;判它的 status / 产出文本。

    形如 {"step":"review","status":"done"} / {"step":"review","contains":"通过"} /
    {"step":"review","equals":"yes"}。引用的上游没解析/格式坏 → 默认真(宁跑勿静默剪枝)。"""
    w = step.get("when")
    if not isinstance(w, dict) or not w:
        return True
    ref = w.get("step")
    if not ref or ref not in status:
        return True
    st = status.get(ref, "")
    out = (output.get(ref, "") or "")
    if "status" in w:
        return st == w.get("status")
    if "contains" in w:
        return str(w.get("contains", "")).lower() in out.lower()
    if "equals" in w:
        return out.strip() == str(w.get("equals", "")).strip()
    return True


def _fail_policy(step: dict) -> tuple[str, int]:
    """(policy, max_retries):policy ∈ skip(默认)/retry/abort;retry 默认重 2 次。"""
    pol = step.get("on_fail")
    name = pol if isinstance(pol, str) and pol in ("skip", "retry", "abort") else "skip"
    retries = 0
    if name == "retry":
        try:
            retries = max(1, int(step.get("max_retries", 2)))
        except (TypeError, ValueError):
            retries = 2
    return name, retries


async def run_workflow(
    plan: dict,
    *,
    run_step: Callable[[dict, dict], Awaitable[dict]],
    max_parallel: int = 6,
    should_cancel: Callable[[], bool] | None = None,
) -> dict:
    """按 DAG 执行 workflow:依赖满足的步骤**并发**跑,**上游产出喂下游**(data flow),
    支持**条件分支**(when)、**容错策略**(on_fail: skip/retry/abort)、**选择性输入/分支合并**(inputs)。

    plan: {"goal": str, "steps": [{"id","display","task","depends_on":[ids],
            "inputs":[ids]?, "when":{...}?, "on_fail":"skip|retry|abort"?, "max_retries":N?}]}。
    run_step(step, upstream) -> awaitable dict。upstream={dep_id: dep_output}(只含该步 data-inputs 里
      **已产出**的上游)。返回约定(#54):
        - {"output": str}                成功
        - {"output": "", "error": str}   失败(带**原因**,写进最终文档的 ✗ 后)
        - {"infra_dead": True, "error"}  基础能力失效(token/网/沙箱)→ **fail-loud 中止全流程**,
                                          不盲 retry(自家"infra-dead 不重规划同一条路"原则)。
      抛错/None/空 output(无 error)→ 视为失败(error 兜底)。

    should_cancel():每轮起新步前查一次 —— True 就**不再起新步**(§0.7 逃生门:人踩刹车),
      剩余未决步标 skipped,run 标 cancelled=True 返回。

    状态语义:done(有产出)/ failed(跑了没产出,失败隔离:下游照跑拿空)/ skipped(分支没选中或
      其全部输入都被跳过 → 级联剪枝;但合并步只要还有任一输入产出就跑)。on_fail=abort 或 infra-dead →
      标 failed 并**中止全流程**(剩余未决步标 skipped)。

    返回 {"goal", "steps":[{...step,"output","status","error"}], "ok", "ran":[done 序],
          "aborted":bool, "cancelled":bool, "infra_dead":bool}。
    - ok = **真实成败**:所有非 skipped 的步都 done(不再是"跑成任意一步就 ok")。有 failed / 被中止 /
      被取消 / infra-dead → ok=False。空/有环/悬空依赖 → ok=False(规划层该拦,这里兜底)。
    """
    steps = {s["id"]: dict(s) for s in plan.get("steps", []) if s.get("id")}
    goal = plan.get("goal", "")
    if not steps or not _topo_ok(steps):
        return {"goal": goal, "steps": [], "ok": False, "ran": [], "aborted": False,
                "cancelled": False, "infra_dead": False, "reason": "空 workflow 或依赖有环/悬空"}

    status: dict[str, str] = {}          # id -> done/failed/skipped
    output: dict[str, str] = {}          # id -> 产出文本(失败/跳过=空)
    errors: dict[str, str] = {}          # id -> 失败原因(✗ 后写它)
    ran_order: list = []
    remaining = set(steps)
    aborted = False
    cancelled = False
    infra_dead = False

    while remaining and not aborted:
        # §0.7 逃生门:每轮起新步前查刹车 —— 已在跑的这批让它跑完(尽力而止),但不再起新步。
        if should_cancel is not None:
            try:
                if should_cancel():
                    cancelled = True
                    break
            except Exception:
                pass
        ready = [sid for sid in remaining
                 if all(d in status for d in steps[sid].get("depends_on", []))]
        if not ready:
            break   # 兜底:_topo_ok 已保无环,正常不会卡
        # 先结算"条件分支/级联跳过"(纯判定,不调 run_step)——跳过的腾出名额给真正要跑的
        to_run = []
        for sid in ready:
            step = steps[sid]
            data = _data_deps(step)
            # 全部输入都被跳过 → 这条分支没走到,级联跳过(合并步:只要有一个没跳就照跑)
            if data and all(status.get(d) == "skipped" for d in data):
                status[sid] = "skipped"; output[sid] = ""; remaining.discard(sid)
                continue
            if not _eval_when(step, status, output):
                status[sid] = "skipped"; output[sid] = ""; remaining.discard(sid)
                continue
            to_run.append(sid)
        if not to_run:
            continue   # 这一轮全是跳过,回去看下一层是否解锁
        batch = to_run[:max_parallel]

        async def _one(sid):
            step = steps[sid]
            up = {d: output.get(d, "") for d in _data_deps(step) if status.get(d) == "done"}
            policy, retries = _fail_policy(step)
            attempts = 1 + (retries if policy == "retry" else 0)
            out, err, dead = "", "", False
            for _ in range(attempts):
                try:
                    r = await run_step(step, up) or {}
                    out = (r.get("output") or "").strip()
                    err = (r.get("error") or "").strip()
                    dead = bool(r.get("infra_dead"))
                except Exception as e:   # noqa: BLE001 — 步内任何异常都收成失败原因,不吞
                    out, err, dead = "", (str(e) or "step raised").strip(), False
                if out:
                    err = ""
                    break
                # infra-dead(基础能力没了)→ 别浪费剩余 retry,同一条路重试没意义(fail-loud)
                if dead:
                    break
            # 失败但没给原因 → 兜个底,别在文档里留空白 ✗(违背"失败带原因")
            if not out and not err:
                err = "step produced no output"
            fail_policy = ("abort" if dead else policy) if not out else ""
            return sid, out, err, dead, fail_policy

        results = await asyncio.gather(*[_one(sid) for sid in batch])
        for sid, out, err, dead, failed_policy in results:
            remaining.discard(sid)
            output[sid] = out
            if out:
                status[sid] = "done"
                ran_order.append(sid)
            else:
                status[sid] = "failed"
                errors[sid] = err
                if dead:
                    infra_dead = True
                    aborted = True   # infra-dead:fail-loud 中止全流程,剩余步下面标 skipped
                elif failed_policy == "abort":
                    aborted = True

    # 中止/取消/卡死后仍未决的步骤 → skipped(老实标,不假装做了)
    for sid in remaining:
        status.setdefault(sid, "skipped")
        output.setdefault(sid, "")

    out_steps = [{**steps[sid], "output": output.get(sid, ""),
                  "status": status.get(sid, "skipped"),
                  "error": errors.get(sid, "")} for sid in steps]
    # ok = 真实成败:没有 failed、没被中止/取消/infra-dead,且真跑过(有 done)。
    any_failed = any(s["status"] == "failed" for s in out_steps)
    ok = bool(ran_order) and not any_failed and not aborted and not cancelled and not infra_dead
    return {"goal": goal, "steps": out_steps, "ok": ok, "ran": ran_order,
            "aborted": aborted, "cancelled": cancelled, "infra_dead": infra_dead}


__all__ = ["run_workflow"]
