"""M1.5 横向补 — crystallize 扩展测试（test_crystallize_ext.py）。

对应 spec:docs/modules/crystallize.md §5.1 M1.5
覆盖:
  AC-M1.5-sig-1  月份名(中/英)归一为 "month" token
  AC-M1.5-sig-2  同义词(中/英)归一
  AC-M1.5-sig-3  停用词过滤
  AC-M1.5-sig-4  值分桶(数字量级/字符串长度/形态)
  AC-M1.5-idx-1  SkillIndex rebuild_from_disk 从 SKILL.md 读 signature
  AC-M1.5-idx-2  lookup_by_name / lookup_by_sig / sig_for_name 一致
  AC-M1.5-rest-1 命中归档技能时 recall 自动 store.restore
  AC-M1.5-rest-2 restore 后 RecallHit.restored=True
  AC-M1.5-sug-1  auto_suggest 按 score 降序排、含 usage_score
  AC-M1.5-sug-2  默认不返回归档;include_archived=True 放开
  AC-M1.5-imp-1  5 类关键词归类正确(增/删/改/偏好/纠正)
  AC-M1.5-imp-2  分类后写回 5 段,不破坏已有段
  AC-M1.5-imp-3  maybe_improve 仍是 5 轮一次;空纠正跳过
"""

from __future__ import annotations

from pathlib import Path

import pytest

from karvyloop.crystallize import (
    InMemoryUsageStore,
    SkillIndex,
    SuggestHit,
    auto_suggest,
    classify_batch,
    classify_correction,
    CorrectionKind,
    RecallHit,
    build_skill_md,
    maybe_improve,
    recall,
    write_skill_md,
)
from karvyloop.crystallize.signature import (
    _intent_cluster,
    _value_bucket,
    compute_signature,
)
from karvyloop.schemas import AtomRun, UsageStats


# ---- 工具 ----

def _atom(intent: str, input_: dict | None = None, tools: list[dict] | None = None) -> AtomRun:
    return AtomRun(
        atom_id="r1",
        input={"intent": intent, **(input_ or {})},
        output={},
        tool_calls=tools or [],
        ts=1000.0,
        success=True,
        trace_ref="trace:r1",
    )


def _write_skill(skills_dir: Path, *, name: str, sig: str, when: str, desc: str,
                 body: str = "## Steps\n\n1. do it", scope: str = "user") -> Path:
    text = build_skill_md(
        name=name, description=desc, body=body,
        signature=sig, when_to_use=when, scope=scope,
        verify_proof={"passed_at": 1.0, "verifier": "auto", "note": "ok"},
        trace_refs=["t1"],
    )
    return write_skill_md(skills_dir / name, text)


# ============ AC-M1.5-sig-1: 月份名归一 ============
def test_sig_normalizes_english_month_names():
    """英文 jan/january/.../dec/december 都归一为 "month" token(intent cluster 维度)。"""
    # 关键:月份名出现在 intent 文本里(intent cluster 走 normalize);
    # schema 里不要塞月份名(那里走 value_bucket,长度不同时仍会分桶,这是有意的)
    a = compute_signature(_atom("summarize jan report"))
    b = compute_signature(_atom("summarize december report"))
    assert a == b, f"intent 中月份名应归一:sig a={a} b={b}"


def test_sig_normalizes_chinese_month_names():
    """中文 一月/.../十二月/月份 都归一为 "month" token。"""
    a = compute_signature(_atom("总结 一月 报告"))
    b = compute_signature(_atom("总结 十二月 报告"))
    c = compute_signature(_atom("总结 月份 报告"))
    assert a == b == c, f"中文月份应归一:a={a} b={b} c={c}"


# ============ AC-M1.5-sig-2: 同义词归一 ============
def test_sig_normalizes_synonyms_english():
    """summarize == summary == report → 同 sig。"""
    a = compute_signature(_atom("summarize this"))
    b = compute_signature(_atom("summary this"))
    c = compute_signature(_atom("report this"))
    assert a == b == c


def test_sig_normalizes_synonyms_chinese():
    """总结/汇报/整理 → 同 sig;翻译/译 → 同 sig。"""
    a = compute_signature(_atom("总结 这个"))
    b = compute_signature(_atom("汇报 这个"))
    c = compute_signature(_atom("整理 这个"))
    assert a == b == c


# ============ AC-M1.5-sig-3: 停用词过滤 ============
def test_sig_filters_stopwords():
    """停用词(请/帮我/的/把/a/the/for...)不参与签名。"""
    a = compute_signature(_atom("summarize the report"))
    b = compute_signature(_atom("summarize report"))
    c = compute_signature(_atom("please summarize report"))
    # 去掉 the / please 后应同 sig
    assert a == b == c


def test_intent_cluster_dedupes_and_caps_at_5():
    """_intent_cluster 归一后去重、截前 5。"""
    out = _intent_cluster("summarize summarize translate search test a b c d e f g")
    tokens = out.split()
    assert len(tokens) <= 5
    assert tokens.count("summarize") == 1  # 去重


# ============ AC-M1.5-sig-4: 值分桶 ============
def test_value_bucket_numbers():
    """数字按量级分桶:<0/<1/<10/<100/<1k/<1m/+。"""
    assert _value_bucket(-5) == "num<0"
    assert _value_bucket(0.5) == "num<1"
    assert _value_bucket(5) == "num<10"
    assert _value_bucket(50) == "num<100"
    assert _value_bucket(500) == "num<1k"
    assert _value_bucket(500_000) == "num<1m"
    assert _value_bucket(5_000_000) == "num+"


def test_value_bucket_strings():
    """字符串按长度 + 形态(isodate/isoweek/url)分桶。"""
    assert _value_bucket("") == "str<>"
    assert _value_bucket("ab") == "str<4"
    assert _value_bucket("hello") == "str<16"
    assert _value_bucket("x" * 20) == "str<64"
    assert _value_bucket("x" * 100) == "str+"
    assert _value_bucket("2026-01-15").startswith("isodate:")
    assert _value_bucket("2026-W03").startswith("isoweek:")
    assert _value_bucket("https://x.com/y").startswith("url:")


def test_sig_value_bucketing_makes_different_dates_same():
    """2026-01 vs 2026-05 同 bucket(都 isodate+str<16) → 候选合并。"""
    a = compute_signature(_atom("summarize report", {"date": "2026-01-15"}))
    b = compute_signature(_atom("summarize report", {"date": "2026-05-20"}))
    assert a == b, f"同 bucket isodate 应同 sig:a={a} b={b}"


# ============ AC-M1.5-idx-1: rebuild_from_disk ============
def test_skill_index_rebuilds_from_disk(tmp_path: Path):
    """SkillIndex 从 SKILL.md frontmatter.signature 重建;无 signature 的不收。"""
    sd = tmp_path / "skills"
    sd.mkdir()
    _write_skill(sd, name="alpha", sig="aaaa1111aaaa1111",
                 when="when alpha", desc="alpha desc")
    _write_skill(sd, name="beta", sig="bbbb2222bbbb2222",
                 when="when beta", desc="beta desc")
    # 旧 SKILL.md(无 signature)—— 手工建一个
    legacy = sd / "legacy"
    legacy.mkdir()
    (legacy / "SKILL.md").write_text(
        "---\nname: legacy\ndescription: d\n---\nold body\n", encoding="utf-8"
    )

    base = SkillIndex().rebuild_from_disk(tmp_path / "_none")  # 包内系统技能基线(动态,不硬编码)
    idx = SkillIndex()
    n = idx.rebuild_from_disk(sd)
    assert n == base + 2  # 用户 2 条(legacy 没 signature 不收)+ 系统基线
    assert idx.name_for_sig("aaaa1111aaaa1111") == "alpha"
    assert idx.name_for_sig("bbbb2222bbbb2222") == "beta"
    assert "legacy" not in idx


# ============ AC-M1.5-idx-2: 双向一致 ============
def test_skill_index_bidirectional_consistency(tmp_path: Path):
    """lookup_by_name 与 lookup_by_sig / sig_for_name / name_for_sig 一致。"""
    sd = tmp_path / "skills"
    sd.mkdir()
    _write_skill(sd, name="foo", sig="f00f00f00f00f00f",
                 when="wf", desc="d")
    idx = SkillIndex()
    idx.rebuild_from_disk(sd)

    e1 = idx.lookup_by_name("foo")
    e2 = idx.lookup_by_sig("f00f00f00f00f00f")
    assert e1 is not None and e2 is not None
    assert e1.sig == e2.sig == "f00f00f00f00f00f"
    assert e1.name == "foo"
    assert idx.sig_for_name("foo") == "f00f00f00f00f00f"
    assert idx.name_for_sig("f00f00f00f00f00f") == "foo"
    assert "foo" in idx
    sys_base = SkillIndex()
    sys_base.rebuild_from_disk(tmp_path / "_none")   # 系统技能基线(动态)
    assert len(idx) == len(sys_base) + 1             # 用户 1 条 + 系统基线


def test_skill_index_register_unregister():
    """register / unregister 走内存;rebuild 会清空后重建。"""
    idx = SkillIndex()
    idx.register(name="a", sig="aaa", scope="user", when_to_use="w",
                 description="d", path="/tmp/a/SKILL.md")
    assert idx.sig_for_name("a") == "aaa"
    idx.unregister("a")
    assert "a" not in idx


# ============ AC-M1.5-rest-1/2: auto-restore ============
def test_recall_auto_restores_archived_skill(tmp_path: Path):
    """命中归档技能时 recall 自动 store.restore,RecallHit.restored=True。"""
    sd = tmp_path / "skills"
    sd.mkdir()
    _write_skill(sd, name="summarize", sig="s1s1s1s1s1s1s1s1",
                 when="summarize text", desc="总结一段文字")

    store = InMemoryUsageStore()
    store.put("s1s1s1s1s1s1s1s1", UsageStats(usage_count=5, success_count=5, last_used_at=0))
    store.archive("s1s1s1s1s1s1s1s1")
    assert store.is_archived("s1s1s1s1s1s1s1s1")

    idx = SkillIndex()
    idx.rebuild_from_disk(sd)
    hit = recall("summarize this text", skills_dir=sd, scope="user",
                 store=store, skill_index=idx)
    assert hit is not None
    assert hit.name == "summarize"
    assert hit.restored is True
    # 归档已翻
    assert not store.is_archived("s1s1s1s1s1s1s1s1")


def test_recall_does_not_set_restored_when_not_archived(tmp_path: Path):
    """未归档技能 RecallHit.restored=False(默认)。"""
    sd = tmp_path / "skills"
    sd.mkdir()
    _write_skill(sd, name="alpha", sig="a1a1a1a1a1a1a1a1",
                 when="alpha text", desc="alpha desc")
    store = InMemoryUsageStore()
    store.put("a1a1a1a1a1a1a1a1", UsageStats(usage_count=3, success_count=3, last_used_at=0))
    idx = SkillIndex()
    idx.rebuild_from_disk(sd)
    hit = recall("alpha text", skills_dir=sd, scope="user",
                 store=store, skill_index=idx)
    assert hit is not None
    assert hit.restored is False


# ============ AC-M1.5-sug-1/2: auto_suggest ============
def test_auto_suggest_returns_top_n_sorted_by_score(tmp_path: Path):
    """auto_suggest 按 match score 降序排,Top-N 截断。"""
    sd = tmp_path / "skills"
    sd.mkdir()
    _write_skill(sd, name="high", sig="1111", when="summarize report",
                 desc="summarize a report")
    _write_skill(sd, name="low", sig="2222", when="nothing matches",
                 desc="totally unrelated skill")
    idx = SkillIndex()
    idx.rebuild_from_disk(sd)
    hits = auto_suggest("summarize report", skills_dir=sd, scope="user",
                        skill_index=idx, top_n=3)
    # 至少 high 在;low 可能在(空 overlap 时被剔,取决于 tokenize)
    names = [h.name for h in hits]
    assert "high" in names
    # high 应排第一(score 最高)
    if len(hits) > 1:
        assert hits[0].score >= hits[1].score


def test_auto_suggest_includes_usage_score(tmp_path: Path):
    """每项含 usage_score(由 store.usage_score 算)。"""
    import time
    sd = tmp_path / "skills"
    sd.mkdir()
    _write_skill(sd, name="alpha", sig="abcabcabcabcabcd",
                 when="alpha text", desc="alpha")
    store = InMemoryUsageStore()
    # usage_count=10, last_used_at=now → score 高
    now = time.time()
    store.put("abcabcabcabcabcd", UsageStats(usage_count=10, success_count=10, last_used_at=now))
    idx = SkillIndex()
    idx.rebuild_from_disk(sd)
    hits = auto_suggest("alpha text", skills_dir=sd, scope="user",
                        store=store, skill_index=idx, top_n=1, now=now)
    assert len(hits) == 1
    assert hits[0].usage_score > 5.0  # usage=10, recency=1 → score=10


def test_auto_suggest_excludes_archived_by_default(tmp_path: Path):
    """归档技能默认不进建议;include_archived=True 才进。"""
    sd = tmp_path / "skills"
    sd.mkdir()
    _write_skill(sd, name="archived_one", sig="aaa", when="alpha text", desc="d")
    store = InMemoryUsageStore()
    store.put("aaa", UsageStats(usage_count=3, success_count=3, last_used_at=1000))
    store.archive("aaa")
    idx = SkillIndex()
    idx.rebuild_from_disk(sd)

    # 默认:不入
    hits = auto_suggest("alpha text", skills_dir=sd, scope="user",
                        store=store, skill_index=idx, top_n=3)
    assert all(h.name != "archived_one" for h in hits)

    # 显式 include:入
    hits2 = auto_suggest("alpha text", skills_dir=sd, scope="user",
                         store=store, skill_index=idx, top_n=3,
                         include_archived=True)
    assert any(h.name == "archived_one" for h in hits2)


# ============ AC-M1.5-imp-1: 5 类关键词归类 ============
def test_classify_correction_5_kinds():
    """关键词命中归类正确(5 类全覆盖)。"""
    assert classify_correction("don't send email") == CorrectionKind.REMOVE
    assert classify_correction("以后别发邮件") == CorrectionKind.REMOVE
    assert classify_correction("I prefer markdown") == CorrectionKind.PREFERENCE
    assert classify_correction("用 markdown 表格") == CorrectionKind.PREFERENCE
    assert classify_correction("change to pdf") == CorrectionKind.MODIFY
    assert classify_correction("改成 pdf") == CorrectionKind.MODIFY
    assert classify_correction("also add footer") == CorrectionKind.ADD
    assert classify_correction("还有补充") == CorrectionKind.ADD
    assert classify_correction("that's wrong") == CorrectionKind.CORRECTION
    assert classify_correction("错了") == CorrectionKind.CORRECTION


def test_classify_correction_priority_correction_over_remove():
    """同时含 correction+remove → 优先归 correction(更正更强烈)。"""
    # "错了 不要" → 既符合"错了"又符合"不要";按优先级归 correction
    assert classify_correction("错了,不要这么做") == CorrectionKind.CORRECTION


def test_classify_correction_empty_falls_back():
    """空/纯空白 → 兜底归 CORRECTION。"""
    assert classify_correction("") == CorrectionKind.CORRECTION
    assert classify_correction("   ") == CorrectionKind.CORRECTION
    # 无关键词命中 → 也归 CORRECTION(保守)
    assert classify_correction("今天天气不错") == CorrectionKind.CORRECTION


def test_classify_batch_returns_list():
    """classify_batch 批量。"""
    out = classify_batch([
        "don't do X",
        "改一下",
        "用 markdown",
    ])
    assert [c.kind for c in out] == [
        CorrectionKind.REMOVE, CorrectionKind.MODIFY, CorrectionKind.PREFERENCE,
    ]


# ============ AC-M1.5-imp-2: 5 段写回不破坏已有 ============
def test_write_corrections_to_skill_md_creates_5_sections(tmp_path: Path):
    """分类后 5 段都正确写入;各段含 [kind] 前缀便于审计。"""
    p = tmp_path / "SKILL.md"
    text = build_skill_md(
        name="foo", description="d", body="## Steps\n\n1. do it",
        signature="sig1234567890ab",
        verify_proof={"passed_at": 0, "verifier": "manual", "note": ""},
        trace_refs=["r1"],
    )
    p.write_text(text, encoding="utf-8")

    classified = classify_batch([
        "don't send email",     # remove
        "用 markdown 表格",     # preference
        "改成 pdf",            # modify
        "还有补充",            # add
        "错了",                # correction
    ])
    from karvyloop.crystallize import write_corrections_to_skill_md
    assert write_corrections_to_skill_md(p, classified, now=2000.0) is True
    out = p.read_text(encoding="utf-8")
    # 5 段都应存在
    for header in ["## Add", "## Remove", "## Modify", "## Preferences", "## Corrections"]:
        assert header in out, f"section {header!r} 缺失"
    # 每段都应有 [kind] 前缀
    assert "[remove]" in out
    assert "[preference]" in out
    assert "[modify]" in out
    assert "[add]" in out
    assert "[correction]" in out


def test_write_corrections_appends_to_existing_section(tmp_path: Path):
    """已有某段时,新纠正追加到该段尾(不重开一段、不破坏下一段)。"""
    p = tmp_path / "SKILL.md"
    p.write_text(
        "---\nname: foo\nsignature: sig000000000000\n---\n"
        "## Steps\n\n1. do it\n\n"
        "## Preferences\n\n- (2026-06-01) [preference] old pref\n\n"
        "## Corrections\n\n- (2026-06-01) [correction] old err\n",
        encoding="utf-8",
    )
    classified = classify_batch([
        "用 json",                       # preference (append to existing)
        "错了",                          # correction (append to existing)
        "以后别 X",                     # remove (new section)
    ])
    from karvyloop.crystallize import write_corrections_to_skill_md
    write_corrections_to_skill_md(p, classified, now=3000.0)
    out = p.read_text(encoding="utf-8")
    # 旧的 pref/correction 还在
    assert "old pref" in out
    assert "old err" in out
    # 新内容追加到了正确段尾
    # Preferences 段: 旧的 (2026-06-01) 还在 + 新的 (2026-06-02) 之后追加
    pref_idx = out.find("## Preferences")
    corr_idx = out.find("## Corrections")
    assert pref_idx < corr_idx
    # 新 pref 出现在 Preferences 段内(在下一段 ## 之前)
    new_pref_pos = out.find("[preference] 用 json")
    new_corr_pos = out.find("[correction] 错了")
    assert pref_idx < new_pref_pos < corr_idx
    assert corr_idx < new_corr_pos
    # Remove 是新段,在文末
    assert "## Remove" in out


# ============ AC-M1.5-imp-3: 5 轮触发 + 空纠正跳过 ============
def test_maybe_improve_triggers_every_5_turns(tmp_path: Path):
    """turn_count 不被 5 整除 → 不触发;整除时即使有纠正也只在指定 sig 有 stats 才触发。"""
    sd = tmp_path / "skills"
    sd.mkdir()
    _write_skill(sd, name="alpha", sig="aa11", when="w", desc="d")
    store = InMemoryUsageStore()
    store.put("aa11", UsageStats(usage_count=3, success_count=3,
                                 last_used_at=0, steered_by_user=["用 json"]))

    # turn 1,2,3,4 → False
    for t in [1, 2, 3, 4]:
        assert maybe_improve("alpha", skills_dir=sd, store=store,
                             sig="aa11", turn_count=t) is False
    # turn 5 → True(有写)
    assert maybe_improve("alpha", skills_dir=sd, store=store,
                         sig="aa11", turn_count=5) is True
    out = (sd / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    assert "## Preferences" in out
    assert "用 json" in out


def test_maybe_improve_no_corrections_skips(tmp_path: Path):
    """turn=5 但 steered_by_user 为空 → 跳过(不写盘)。"""
    sd = tmp_path / "skills"
    sd.mkdir()
    _write_skill(sd, name="alpha", sig="aa22", when="w", desc="d")
    store = InMemoryUsageStore()
    store.put("aa22", UsageStats(usage_count=3, success_count=3, last_used_at=0))
    # text 仍是初始 SKILL.md(没任何 ## Add/## Remove/...)
    initial = (sd / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    assert maybe_improve("alpha", skills_dir=sd, store=store,
                         sig="aa22", turn_count=5) is False
    after = (sd / "alpha" / "SKILL.md").read_text(encoding="utf-8")
    assert initial == after  # 未改


def test_maybe_improve_missing_skill_md_returns_false(tmp_path: Path):
    """SKILL.md 不存在 → 跳过(不抛)。"""
    sd = tmp_path / "skills"
    sd.mkdir()
    store = InMemoryUsageStore()
    store.put("ghost", UsageStats(usage_count=3, success_count=3,
                                  last_used_at=0, steered_by_user=["用 json"]))
    assert maybe_improve("ghost", skills_dir=sd, store=store,
                         sig="ghost", turn_count=5) is False


# ============ 端到端:crystallize 写盘 → SkillIndex 重建 → recall 命中 ============
def test_end_to_end_crystallize_then_index_then_recall(tmp_path: Path):
    """crystallize 写盘 → SkillIndex.rebuild_from_disk → recall 命中同 sig。"""
    from karvyloop.crystallize import crystallize, InMemoryUsageStore, VerifyStore
    from karvyloop.schemas import AtomRun

    sd = tmp_path / "skills"
    sd.mkdir()
    store = InMemoryUsageStore()
    verify = VerifyStore()

    # 喂一次成功(直接走 store.put + verify.mark_verified)
    sig = "endsig12345678ab"
    store.put(sig, UsageStats(usage_count=5, success_count=5, last_used_at=1000.0,
                              param_variants=[{"x": i} for i in range(3)]))
    verify.mark_verified(sig, "trace:1", note="ok", clock=lambda: 1000.0)

    # 结晶
    s = crystallize(
        sig, name="my_skill", description="d", body="## Steps\n1. do",
        when_to_use="my skill", arguments=None,
        store=store, verify=verify, skills_dir=sd, scope="user", now=1000.0,
    )
    assert s.name == "my_skill"

    # 索引重建
    base = SkillIndex().rebuild_from_disk(tmp_path / "_none")  # 系统技能基线(动态)
    idx = SkillIndex()
    n = idx.rebuild_from_disk(sd)
    assert n == base + 1
    assert idx.sig_for_name("my_skill") == sig

    # recall 用同 intent 命中
    hit = recall("my skill", skills_dir=sd, scope="user", skill_index=idx)
    assert hit is not None
    assert hit.name == "my_skill"
    assert hit.sig == sig
