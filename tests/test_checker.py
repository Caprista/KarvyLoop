"""test_checker — loop step3:独立验收者(maker→checker→修)。

AC:
- AC1 parse_verdict:PASS / FAIL+原因 / 无判定(inconclusive,不阻塞)
- AC2 verify_and_fix:一次过 → rounds=0,不触发修正
- AC3 verify_and_fix:首验 FAIL → 回灌修一轮 → 再验 PASS,rounds=1,最终 passed
- AC4 verify_and_fix:始终 FAIL → 修正轮数封顶 max_fix_rounds,最终 not passed
- AC5 verify_and_fix:maker 自己 error → 不调 checker,verdict not passed
- AC6 inconclusive(无明确判定)→ 不触发修正循环
- AC7 verify_and_fix_with_rk:缺验收能力(无 gateway)→ 诚实退回单跑,不调 checker
- AC8 make_coding_tools(read_only=True)→ 只有 read_file + run_command(无 write/edit)
- AC9 build_checker_prompt:静态段强调"验收者/只读/VERDICT";复用动态上下文
"""
from __future__ import annotations

import types

import pytest

from karvyloop.coding import checker as C


# ---- AC1 parse_verdict ----
def test_parse_pass():
    v = C.parse_verdict("我读了文件也跑了测试,都过。\nVERDICT: PASS")
    assert v.passed and not v.inconclusive


def test_parse_fail_with_reason():
    v = C.parse_verdict("跑测试 2 个失败。\nVERDICT: FAIL — 没处理空输入,test_empty 挂了")
    assert (not v.passed) and not v.inconclusive
    assert "空输入" in v.feedback


def test_parse_no_verdict_is_inconclusive_and_not_blocking():
    v = C.parse_verdict("我看了一下,大概没问题吧")
    assert v.inconclusive and v.passed  # 不阻塞


def test_parse_fullwidth_colon_and_dash():
    v = C.parse_verdict("VERDICT：FAIL：缺了入口文件")
    assert (not v.passed) and "缺了入口文件" in v.feedback


def test_parse_last_match_wins_against_echoed_claim():
    # 验收者先复述作者自述(含 VERDICT: PASS),再下自己的 FAIL 结论 → 必须取最后那个
    text = ("作者声称:VERDICT: PASS,说都跑通了。\n"
            "但我实际跑测试,test_empty 挂了。\n"
            "VERDICT: FAIL — 没处理空输入")
    v = C.parse_verdict(text)
    assert (not v.passed) and "空输入" in v.feedback   # 不是被作者的 PASS 骗到


def test_parse_last_match_reasoning_then_pass():
    # 验收者先担心 FAIL,核完改判 PASS → 取最后的 PASS(不误触发修正)
    v = C.parse_verdict("一开始担心 VERDICT: FAIL,但重跑后都过了。\nVERDICT: PASS")
    assert v.passed and not v.inconclusive


# ---- verify_and_fix:用 monkeypatch 把 independent_check 换成可控异步桩 ----
def _outcome(text="", error=""):
    return types.SimpleNamespace(text=text, error=error)


def _patch_check(monkeypatch, verdicts):
    """independent_check 依次返回 verdicts 里的 Verdict(异步桩)。"""
    seq = list(verdicts)
    calls = {"n": 0}

    async def fake(intent, maker_text, **kw):
        calls["n"] += 1
        return seq.pop(0) if seq else seq_last

    seq_last = verdicts[-1] if verdicts else C.Verdict(passed=True)
    monkeypatch.setattr(C, "independent_check", fake)
    return calls


def test_verify_pass_first_try(monkeypatch):
    calls = _patch_check(monkeypatch, [C.Verdict(passed=True)])
    drove = []
    res = C.verify_and_fix("做 X", drive_fn=lambda i: drove.append(i) or _outcome("done"),
                           check_kwargs={}, max_fix_rounds=1)
    assert res.rounds == 0 and res.verdict.passed
    assert len(drove) == 1 and calls["n"] == 1  # 只跑一次 maker、验一次


def test_verify_fail_then_fix_passes(monkeypatch):
    _patch_check(monkeypatch, [C.Verdict(passed=False, feedback="缺测试"),
                               C.Verdict(passed=True)])
    drove = []
    res = C.verify_and_fix("做 X", drive_fn=lambda i: drove.append(i) or _outcome("v" + str(len(drove))),
                           check_kwargs={}, max_fix_rounds=1)
    assert res.rounds == 1 and res.verdict.passed
    assert len(drove) == 2                       # maker 跑了 2 次(原 + 修)
    assert "验收意见" in drove[1] and "缺测试" in drove[1]  # 回灌了验收意见


def test_verify_fail_caps_at_budget(monkeypatch):
    _patch_check(monkeypatch, [C.Verdict(passed=False, feedback="还是不行"),
                               C.Verdict(passed=False, feedback="还是不行2")])
    drove = []
    res = C.verify_and_fix("做 X", drive_fn=lambda i: drove.append(i) or _outcome("x"),
                           check_kwargs={}, max_fix_rounds=1)
    assert res.rounds == 1 and not res.verdict.passed   # 封顶 1 轮,仍未过
    assert len(drove) == 2


def test_verify_maker_error_field_skips_check(monkeypatch):
    # 防御性分支:outcome 带 error 字段 → 不验失败产物(虽生产里 DriveResult 无此字段)
    calls = _patch_check(monkeypatch, [C.Verdict(passed=True)])
    res = C.verify_and_fix("做 X", drive_fn=lambda i: _outcome("", error="boom"),
                           check_kwargs={}, max_fix_rounds=1)
    assert not res.verdict.passed and res.rounds == 0
    assert calls["n"] == 0     # 作者就出错 → 不调 checker


def test_verify_maker_exception_propagates_without_check(monkeypatch):
    # 生产真实路径:maker 失败 = 抛异常(ml.drive 不吞)→ verify_and_fix 不吞 → 冒泡给
    # handler 外层 try;关键:**异常发生在调 checker 之前**,绝不在失败产物上浪费验收 pass。
    calls = _patch_check(monkeypatch, [C.Verdict(passed=True)])

    def boom(i):
        raise RuntimeError("maker 炸了")

    with pytest.raises(RuntimeError, match="maker 炸了"):
        C.verify_and_fix("做 X", drive_fn=boom, check_kwargs={}, max_fix_rounds=1)
    assert calls["n"] == 0


def test_inconclusive_does_not_loop(monkeypatch):
    _patch_check(monkeypatch, [C.Verdict(passed=True, inconclusive=True)])
    drove = []
    res = C.verify_and_fix("做 X", drive_fn=lambda i: drove.append(i) or _outcome("done"),
                           check_kwargs={}, max_fix_rounds=2)
    assert res.rounds == 0 and len(drove) == 1   # inconclusive 不触发修正


# ---- AC7 verify_and_fix_with_rk:缺能力诚实退回 ----
def test_with_rk_no_capability_falls_back(monkeypatch):
    called = {"n": 0}

    async def boom(*a, **k):
        called["n"] += 1
        raise AssertionError("不该调 checker")

    monkeypatch.setattr(C, "independent_check", boom)
    ml = types.SimpleNamespace(drive=lambda i, slow_brain=None: _outcome("ran"))
    rk = {"token": object(), "sandbox": object()}   # 缺 gateway
    res = C.verify_and_fix_with_rk("做 X", ml=ml, slow_brain=object(), rk=rk)
    assert res.verdict.inconclusive and res.result.text == "ran"
    assert called["n"] == 0


def test_with_rk_has_capability_runs_check(monkeypatch):
    seen = {}

    async def fake(intent, maker_text, **kw):
        seen.update(kw)
        return C.Verdict(passed=True)

    monkeypatch.setattr(C, "independent_check", fake)
    ml = types.SimpleNamespace(drive=lambda i, slow_brain=None: _outcome("ran"))
    rk = {"token": "T", "sandbox": "S", "gateway": "G", "workspace_root": "/w", "model_ref": "m"}
    res = C.verify_and_fix_with_rk("做 X", ml=ml, slow_brain=object(), rk=rk)
    assert res.verdict.passed
    assert seen["gateway"] == "G" and seen["workspace_root"] == "/w"


# ---- AC8 read-only 工具集 ----
def test_read_only_tools_exclude_write():
    from karvyloop.coding.tools import make_coding_tools
    from karvyloop.coding.filestate import FileState
    tok = types.SimpleNamespace()
    ro = make_coding_tools(sandbox=object(), file_state=FileState(),
                           workspace_root="/", token=tok, read_only=True)
    # 只读 = 不给 write/edit;read/run + 联网只读(web_search/web_fetch)都给
    assert set(ro.keys()) == {"read_file", "run_command", "web_search", "web_fetch"}
    assert "write_file" not in ro and "edit_file" not in ro
    full = make_coding_tools(sandbox=object(), file_state=FileState(),
                             workspace_root="/", token=tok)
    assert {"write_file", "edit_file"} <= set(full.keys())


# ---- AC9 checker prompt ----
def test_checker_prompt_is_verifier(tmp_path):
    p = C.build_checker_prompt(str(tmp_path))
    text = p.to_text()
    assert "验收者" in text and "VERDICT" in text
    assert "绝不修改" in text or "不修改" in text


# ---- verdict_suffix ----
def test_verdict_suffix():
    assert C.verdict_suffix(C.CheckedResult(result=None, verdict=C.Verdict(passed=True), rounds=0)) == "(独立验收✓)"
    assert "修正1轮" in C.verdict_suffix(C.CheckedResult(result=None, verdict=C.Verdict(passed=True), rounds=1))
    assert C.verdict_suffix(C.CheckedResult(result=None, verdict=C.Verdict(passed=True, inconclusive=True), rounds=0)) == ""
    s = C.verdict_suffix(C.CheckedResult(result=None, verdict=C.Verdict(passed=False, feedback="挂了"), rounds=1))
    assert "仍未过" in s and "挂了" in s
