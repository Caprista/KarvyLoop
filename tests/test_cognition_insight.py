"""test_cognition_insight — task_insight 非任务认知沉淀纯逻辑(docs/82 Slice A)。

不变量:
① 信号门(零 LLM)命中:同名工具≥2次 input 变+最终成功 / terminal 非 COMPLETED 且后续成功
  (error 真因并进材料)/ task_run error→done
② 平静零候选:无模式命中 → [](同 input 重试 / 纯失败 / 顺序不对都不算)
③ 解析宁空勿毒:prose/坏 JSON → [];超长丢;**编造 evidence_ref 整条丢**;带 domain/role 丢
④ 复现关:硬证据(env/correction+硬信号)首见即写;软观察 1 run 背书不写、≥2 run 写
⑤ Belief 形状:source=task_insight/provisional/kind/trace_ref/ts 全字段;env 带 applies.device
⑥ provenance_rank:task_insight = auto 档(distill_extracted),永掀不翻 user_explicit
⑦ prompt 禁区:三类 + 硬禁任务评语/决策规则/一次性细节/开放问题 + evidence_ref 不许编造
"""
from __future__ import annotations

import json
import pathlib
import sys
from types import SimpleNamespace

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition import insight as I  # noqa: E402


def _e(kind, task_id="t1", payload=None, ts=1.0, seq=0):
    return SimpleNamespace(kind=kind, task_id=task_id, payload=payload or {}, ts=ts, seq=seq)


def _retry_run(task_id="t1", *, name="pip_install", success=True, ts=1.0, seq=0,
               inputs=({"index": "pypi"}, {"index": "mirror"}), terminal="completed"):
    calls = [{"id": f"c{i}", "name": name, "input": inp} for i, inp in enumerate(inputs)]
    return _e("atom_run", task_id, {"atom_id": "a1", "input": {}, "output": {"text": "装好了"},
                                    "success": success, "tool_calls": calls,
                                    "trace_ref": f"trace://a1/{seq}", "terminal": terminal},
              ts=ts, seq=seq)


# ============ ① 信号门命中 ============

def test_gate_tool_retry_hit():
    sigs = I.find_insight_signals([_retry_run()])
    assert len(sigs) == 1
    s = sigs[0]
    assert s.pattern == "tool_retry" and s.hard is True
    assert s.trace_ref == "t1:0" and "t1:0" in s.refs
    assert "pip_install" in s.material and "[ref=t1:0]" in s.material


def test_gate_replan_recovery_hit_with_error_cause():
    failed = _e("atom_run", "t1", {"atom_id": "a1", "success": False, "tool_calls": [],
                                   "terminal": "max_turns", "output": None}, ts=1.0, seq=0)
    err = _e("error", "t1", {"error_type": "TypeError", "error": "x is None",
                             "stage": "slow_brain"}, ts=1.5, seq=1)
    ok = _e("atom_run", "t1", {"atom_id": "a2", "success": True, "tool_calls": [],
                               "terminal": "completed", "output": {"text": "成了"}}, ts=2.0, seq=2)
    sigs = I.find_insight_signals([failed, err, ok])
    assert len(sigs) == 1
    s = sigs[0]
    assert s.pattern == "replan_recovery" and s.hard is True
    assert set(s.refs) == {"t1:0", "t1:2"}          # 失败+成功配对都在证据面里
    assert "TypeError" in s.material and "max_turns" in s.material   # error 真因并进材料


def test_gate_task_recovery_hit():
    e1 = _e("task_run", "reg-9", {"registry_id": "r1", "status": "error",
                                  "intent": "发周报", "result": "SMTP 拒了", "who": "小卡"}, ts=1.0, seq=0)
    e2 = _e("task_run", "reg-9", {"registry_id": "r1", "status": "done",
                                  "intent": "发周报", "result": "换端口 587 发出去了", "who": "小卡"}, ts=2.0, seq=1)
    sigs = I.find_insight_signals([e1, e2])
    assert len(sigs) == 1 and sigs[0].pattern == "task_recovery" and sigs[0].hard is True
    assert set(sigs[0].refs) == {"reg-9:1", "reg-9:0"}


# ============ ② 平静零候选(不该命中的都不命中)============

def test_gate_quiet_day_zero_signals():
    calm = [
        # 单次调用成功
        _retry_run(inputs=({"q": "a"},), seq=0),
        # 同名工具两次但 input 没变(轮询不是纠错)
        _retry_run(task_id="t2", inputs=({"q": "same"}, {"q": "same"}), seq=0),
        # input 变了但最终失败(纯失败归 role replan,不归洞察)
        _retry_run(task_id="t3", success=False, terminal="max_turns", seq=0),
        # 失败后没有后续成功
        _e("atom_run", "t4", {"success": False, "terminal": "max_turns", "tool_calls": []}, ts=1.0, seq=0),
        # task_run 顺序不对(done 在前 error 在后 = 没恢复)
        _e("task_run", "t5", {"registry_id": "r", "status": "done"}, ts=1.0, seq=0),
        _e("task_run", "t5", {"registry_id": "r", "status": "error"}, ts=2.0, seq=1),
        # 无关 kind 不入池
        _e("eval_fact", "t6", {"success": False}, ts=1.0, seq=0),
    ]
    assert I.find_insight_signals(calm) == []


def test_gate_empty_terminal_not_wronged():
    # terminal 空 = 不可判(老数据):不当失败,后续成功也不算 replan 恢复
    a = _e("atom_run", "t1", {"success": False, "terminal": "", "tool_calls": []}, ts=1.0, seq=0)
    b = _e("atom_run", "t1", {"success": True, "terminal": "completed", "tool_calls": []}, ts=2.0, seq=1)
    assert I.find_insight_signals([a, b]) == []


def test_gate_bad_payload_no_crash():
    # 坏 payload(tool_calls 不是 list)/ None 事件:跳过不炸(append-only 池里可能有坏数据)
    bad = _e("atom_run", "t1", {"tool_calls": "not-a-list", "success": True}, ts=1.0, seq=0)
    assert I.find_insight_signals([bad, None]) == []


# ============ ③ 解析宁空勿毒(升到指称层)============

_REFS = {"t1:0", "t1:2"}


def test_parse_prose_and_bad_json_refused():
    assert I.parse_insights("这台机器 pip 要走镜像。", _REFS) == []
    assert I.parse_insights('[{"content": "x", broken', _REFS) == []
    assert I.parse_insights("", _REFS) == []
    assert I.parse_insights("42", _REFS) == []


def test_parse_fenced_json_ok():
    body = json.dumps([{"content": "这台机器 pip 装包要走镜像源",
                        "kind": "env", "evidence_ref": "t1:0"}], ensure_ascii=False)
    out = I.parse_insights(f"```json\n{body}\n```", _REFS)
    assert len(out) == 1 and out[0]["kind"] == "env" and out[0]["evidence_ref"] == "t1:0"


def test_parse_fabricated_evidence_ref_drops_whole_item():
    items = [
        {"content": "真的:pip 要走镜像", "kind": "env", "evidence_ref": "t1:0"},
        {"content": "编的:天上会掉馅饼", "kind": "env", "evidence_ref": "t9:99"},  # 编造 ref
        {"content": "漏的:没带证据", "kind": "env"},                                 # 缺 ref
    ]
    out = I.parse_insights(json.dumps(items, ensure_ascii=False), _REFS)
    assert [c["content"] for c in out] == ["真的:pip 要走镜像"]


def test_parse_drops_overlong_and_domain_role_items():
    items = [
        {"content": "长" * 301, "kind": "env", "evidence_ref": "t1:0"},
        {"content": "finance 域查数先核来源", "kind": "correction",
         "evidence_ref": "t1:0", "domain": "finance", "role": "审计师"},   # role_experience 地盘
        {"content": "留下的这条", "kind": "correction", "evidence_ref": "t1:0"},
    ]
    out = I.parse_insights(json.dumps(items, ensure_ascii=False), _REFS)
    assert [c["content"] for c in out] == ["留下的这条"]


def test_parse_unknown_kind_falls_to_observation_and_caps():
    items = [{"content": f"条目{i}", "kind": "environment", "evidence_ref": "t1:0"}
             for i in range(8)]
    out = I.parse_insights(json.dumps(items, ensure_ascii=False), _REFS)
    assert len(out) == I.MAX_CANDIDATES                       # 封顶 5
    assert all(c["kind"] == "observation" for c in out)       # 不认识的类别按最保守软观察走


# ============ ④ 复现关(门2)============

def _sig(ref="t1:0", hard=True):
    return I.InsightSignal(pattern="tool_retry", task_id="t1", trace_ref=ref,
                           refs=(ref,), material="m", hard=hard, ts=1.0)


def test_reproduction_hard_env_first_sight_writes():
    cand = {"content": "这台机器 pip 装包要走镜像源", "kind": "env", "evidence_ref": "t1:0"}
    assert I.passes_reproduction(cand, signal=_sig(), run_texts=[]) is True


def test_reproduction_soft_observation_needs_two_runs():
    cand = {"content": "客户邮件都在周五下午发过来", "kind": "observation", "evidence_ref": "t1:0"}
    one = [("t1:0", "读取 客户邮件 周五下午 收件箱")]
    two = one + [("t2:0", "整理 客户邮件 周五下午 归档")]
    # 软观察即便背书信号是硬的,也要 ≥2 run 词面背书("规律"要复现)
    assert I.passes_reproduction(cand, signal=_sig(), run_texts=one) is False
    assert I.passes_reproduction(cand, signal=_sig(), run_texts=two) is True


def test_reproduction_weak_overlap_run_does_not_count():
    # 单个撞词(overlap < SOFT_MIN_OVERLAP)的 run 不算背书票
    cand = {"content": "客户邮件都在周五下午发过来", "kind": "observation", "evidence_ref": "t1:0"}
    weak = [("t1:0", "邮件"), ("t2:0", "邮件")]   # 每个只命中 1 个 bigram
    assert I.soft_backing_runs(cand["content"], weak) == 0
    assert I.passes_reproduction(cand, signal=_sig(), run_texts=weak) is False


def test_build_insight_beliefs_gate_and_cap():
    signals = [_sig("t1:0")]
    run_texts = [("t1:0", "pip 镜像源 安装"), ("t2:0", "pip 镜像源 升级")]
    cands = [
        {"content": "这台机器 pip 装包要走镜像源", "kind": "env", "evidence_ref": "t1:0"},        # 硬:写
        {"content": "客户邮件都在周五发", "kind": "observation", "evidence_ref": "t1:0"},          # 软 0 背书:不写
        {"content": "pip 镜像源 安装升级都稳", "kind": "observation", "evidence_ref": "t1:0"},     # 软 2 背书:写
        {"content": "装依赖前先确认镜像源可达再 pip 安装", "kind": "correction", "evidence_ref": "t1:0"},  # 硬:写
        {"content": "再来一条镜像源 pip 安装心得", "kind": "correction", "evidence_ref": "t1:0"},   # 超 max_writes:不写
    ]
    out = I.build_insight_beliefs(cands, signals=signals, run_texts=run_texts,
                                  now=100.0, max_writes=3)
    assert [b.content for b in out] == [
        "这台机器 pip 装包要走镜像源", "pip 镜像源 安装升级都稳", "装依赖前先确认镜像源可达再 pip 安装"]


# ============ ⑤ Belief 形状(provenance 全字段;env 带 applies.device)============

def test_belief_shape_env_and_correction():
    b = I.make_insight_belief("这台机器 pip 要走镜像源", "env",
                              trace_ref="t1:0", device="dev-A", now=100.0)
    assert b.scope == "personal" and b.freshness_ts == 100.0
    assert b.provenance["source"] == "task_insight"
    assert b.provenance["provisional"] is True
    assert b.provenance["kind"] == "env"
    assert b.provenance["trace_ref"] == "t1:0" and b.provenance["ts"] == 100.0
    assert b.provenance["applies"] == {"device": "dev-A"}       # env 按设备圈定
    assert I.is_task_insight(b)
    c = I.make_insight_belief("传文件走 base64", "correction", trace_ref="t1:2", now=100.0)
    assert "applies" not in c.provenance                        # 非 env 不带设备圈定
    d = I.make_insight_belief("x", "env", trace_ref="t1:0", now=100.0)
    assert d.provenance["applies"]["device"]                    # 没给 device → 本机名兜底,非空


# ============ ⑥ provenance_rank:auto 档,掀不翻 user_explicit ============

def test_task_insight_rank_is_auto_tier():
    from karvyloop.cognition.conflict import PROVENANCE_RANK, provenance_rank
    r = provenance_rank({"source": "task_insight", "provisional": True})
    assert r == PROVENANCE_RANK["distill_extracted"]
    assert r < provenance_rank({"source": "ingest"})            # 人明说的永远压过它
    # 就算 provenance 忘写 provisional,别名表也把它按 auto 档算(双保险)
    assert provenance_rank({"source": "task_insight"}) == PROVENANCE_RANK["distill_extracted"]


# ============ ⑦ prompt 禁区 ============

def test_prompt_bans_other_axes_and_requires_evidence():
    s = I.INSIGHT_SYSTEM
    for kind in ("env", "correction", "observation"):
        assert kind in s                       # 三类齐
    assert "任务评语" in s                     # 硬禁:任务质量轴(技能线)
    assert "决策规则" in s                     # 硬禁:决策偏好轴
    assert "一次性" in s                       # 硬禁:一次性细节
    assert "开放问题" in s                     # 硬禁:开放问题/猜测
    assert "角色经验" in s                     # (domain,role) 工作方法不抢地盘
    assert "evidence_ref" in s and "编造" in s  # 证据必须原样核回
    assert "[]" in s                           # 没有可抽的 → 空数组(宁空勿滥)
