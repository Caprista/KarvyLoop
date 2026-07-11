"""tag 系统验收(#3b 第一步:role 打标 + 双语 + 筛选数据形态)。

覆盖三块:
  1) roles/registry.normalize_tags —— 归一成双语 dict,吃旧英文串 / {en,zh} / 单键 dict。
  2) RoleRegistry.create/get/update —— tags 落 profile.json、跨实例读回、默认来源标签、update 保留其它字段。
  3) atoms/self_create._norm_tags / _tag_overlap —— 双语 "en|zh" 编码、按 en 匹配键 overlap、向后兼容旧英文串。

守 [[matching-is-grep-overlap-tags-no-vectors]]:标签是 LLM 打一次的归一化概念,匹配走 en 键的集合重叠,**无向量**。
"""
from __future__ import annotations

from karvyloop.atoms.registry import AtomRegistry
from karvyloop.atoms.self_create import _norm_tags, _tag_en, _tag_overlap
from karvyloop.roles.registry import RoleRegistry, normalize_tags


# ---------- 1) roles/registry.normalize_tags:双语归一 ----------

def test_normalize_tags_bilingual_dict():
    out = normalize_tags([{"en": "Search", "zh": "检索"}])
    assert out == [{"en": "search", "zh": "检索"}]   # en 小写归一,zh 原样


def test_normalize_tags_legacy_string_falls_back_to_en():
    """旧纯英文串 → en=串,zh 回退 en(向后兼容,缺 zh 显 en)。"""
    assert normalize_tags(["web"]) == [{"en": "web", "zh": "web"}]


def test_normalize_tags_single_key_dict_backfills():
    assert normalize_tags([{"en": "translate"}]) == [{"en": "translate", "zh": "translate"}]
    assert normalize_tags([{"zh": "翻译"}]) == [{"en": "翻译", "zh": "翻译"}]


def test_normalize_tags_dedup_by_en_and_caps():
    out = normalize_tags([{"en": "web", "zh": "网页"}, {"en": "WEB", "zh": "网络"}]
                         + [{"en": f"t{i}"} for i in range(10)])
    ens = [t["en"] for t in out]
    assert ens[0] == "web" and ens.count("web") == 1   # 按 en 去重(首见胜)
    assert len(out) <= 8                                # 封顶 8 个


def test_normalize_tags_drops_empty():
    assert normalize_tags([{"en": "", "zh": ""}, "", None]) == []


# ---------- 2) RoleRegistry:tags 落库 / 读回 / 默认 / update ----------

def _atoms_with(*ids):
    reg = AtomRegistry()
    for i in ids:
        reg.create(i, "task", f"{i} 干活")
    return reg


def test_role_create_persists_bilingual_tags(tmp_path):
    reg = RoleRegistry(tmp_path / "roles")
    v = reg.create("pm", identity="PM", tags=[{"en": "product", "zh": "产品"}])
    assert v.tags == [{"en": "product", "zh": "产品"}]
    # 落 profile.json
    import json
    prof = json.loads((v.path / "profile.json").read_text(encoding="utf-8"))
    assert prof["tags"] == [{"en": "product", "zh": "产品"}]


def test_role_create_default_source_tag_when_none(tmp_path):
    """手动新建缺 tags → 默认给个基础「来源」标签,不裸奔。"""
    reg = RoleRegistry(tmp_path / "roles")
    v = reg.create("pm", identity="PM")
    assert v.tags == [{"en": "custom", "zh": "自建"}]


def test_role_tags_read_back_across_instances(tmp_path):
    """tags 是持久态 → 新 registry 实例读得到(跨电脑拷贝不丢)。"""
    root = tmp_path / "roles"
    RoleRegistry(root).create("pm", identity="PM", tags=[{"en": "ops", "zh": "运维"}])
    v = RoleRegistry(root).get("pm")
    assert v is not None and v.tags == [{"en": "ops", "zh": "运维"}]


def test_role_legacy_string_tags_read_back_bilingual(tmp_path):
    """旧 profile.json 里 tags 是纯英文串 → 读回归一成双语 dict(缺 zh 显 en)。"""
    root = tmp_path / "roles"
    reg = RoleRegistry(root)
    reg.create("pm", identity="PM")
    # 手动把 profile.json 改成旧英文串形态(模拟历史数据)
    import json
    pf = (root / "pm" / "profile.json")
    prof = json.loads(pf.read_text(encoding="utf-8"))
    prof["tags"] = ["search", "web"]
    pf.write_text(json.dumps(prof, ensure_ascii=False), encoding="utf-8")
    v = RoleRegistry(root).get("pm")
    assert v.tags == [{"en": "search", "zh": "search"}, {"en": "web", "zh": "web"}]


def test_role_update_tags_preserves_other_profile(tmp_path):
    """update(tags=...) 只改 tags,不丢花名/职务/模型。"""
    reg = RoleRegistry(tmp_path / "roles")
    reg.create("pm", identity="PM", nickname="张三", title="产品经理", model="m1",
               tags=[{"en": "product", "zh": "产品"}])
    reg.update("pm", tags=[{"en": "growth", "zh": "增长"}])
    v = reg.get("pm")
    assert v.tags == [{"en": "growth", "zh": "增长"}]
    assert v.nickname == "张三" and v.title == "产品经理" and v.model == "m1"


def test_role_to_dict_exposes_tags(tmp_path):
    reg = RoleRegistry(tmp_path / "roles")
    v = reg.create("pm", identity="PM", tags=[{"en": "product", "zh": "产品"}])
    d = v.to_dict()
    assert d["tags"] == [{"en": "product", "zh": "产品"}]


# ---------- 3) self_create:双语 "en|zh" 编码 + en 键 overlap + 向后兼容 ----------

def test_self_create_norm_tags_encodes_bilingual():
    out = _norm_tags([{"en": "Search", "zh": "检索"}, {"en": "web", "zh": "网页"}])
    assert out == ["search|检索", "web|网页"]   # en 小写,"en|zh" 紧凑串


def test_self_create_norm_tags_legacy_string():
    """旧英文串 → "en|en"(缺 zh 回退 en,向后兼容)。"""
    assert _norm_tags(["translate"]) == ["translate|translate"]


def test_self_create_norm_tags_reencodes_pipe():
    assert _norm_tags(["Search|检索"]) == ["search|检索"]


def test_tag_en_extracts_match_key():
    assert _tag_en("search|检索") == "search"
    assert _tag_en("web") == "web"                       # 旧串
    assert _tag_en({"en": "Translate", "zh": "翻译"}) == "translate"


def test_tag_overlap_matches_by_en_across_languages():
    """双语编码不影响匹配:只看 en 键;共享 ≥2 才算同义。"""
    a = ["search|检索", "web|网页"]
    b = ["search|查找", "web|网络"]        # zh 不同,en 相同
    assert _tag_overlap(a, b) == 1.0
    # 只共享 1 个 → 不算(避免单个宽标签误判)
    assert _tag_overlap(["search|检索", "pdf|文档"], ["search|查找", "csv|表格"]) == 0.0


def test_tag_overlap_backward_compat_old_strings():
    """旧纯英文串 tags 与新双语编码混用,overlap 仍按 en 键工作。"""
    assert _tag_overlap(["search", "web"], ["search|检索", "web|网页"]) == 1.0


def test_atom_registry_holds_pipe_encoded_tags(tmp_path):
    """registry 把 tags 存成 str → "en|zh" 编码原样存住(不被 str() 破坏),读回可用。"""
    reg = AtomRegistry()
    a = reg.create("web_search", "task", "搜网页", tags=["search|检索", "web|网页"])
    assert a.tags == ["search|检索", "web|网页"]
