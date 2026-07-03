"""test_data_analyst_recall — data-analyst 系统技能接线第一刀(召回可达性 + 文件桥)。

背景(审计):data-analyst 方法技能一直在包内、在索引,但零生产路径可达 ——
① 无 tags + recall 分词不吃中文 → 中文意图永不命中;
② SKILL.md scope:user,业务域聊天走 scope=domain → recall 的 scope 过滤把它挡在门外。

本文件锁三件事(全走真 recall 路径,不 mock):
1. 中文/英文意图 → recall 真命中 data-analyst(tags + when_to_use 中文触发词 + CJK bigram 分词);
   无关意图不命中。
2. scope 放行:source=system 的出厂方法技能在 domain 场也可见(镜像资产,人人一样,跨场安全);
   用户 user-scope 技能的隔离语义**不变**(两侧都锁,索引路径 + 扫盘兜底路径)。
3. 文件面板 →「让TA分析」→ 聊天的桥(后端半):intent 文本里的 workspace 相对路径,
   ReadTool 真能读到(相对路径按"console 从工作区启动(workspace_root=cwd)"的生产默认解析;
   绝对路径不依赖 cwd)。
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.crystallize.recall import recall  # noqa: E402
from karvyloop.crystallize.skill_index import SkillIndex  # noqa: E402


CN_DATA_INTENTS = [
    "帮我分析一下 sales.csv 这份数据",
    "分析这个表格的销售趋势",
    "帮我统计一下这个月的报表数据",
]
EN_DATA_INTENTS = [
    "analyze the data in sales.csv",
    "help me with some statistics on this spreadsheet",
]
IRRELEVANT_INTENTS = ["写首诗", "帮我写一首关于春天的诗", "write me a poem"]


def _index(user_dir) -> SkillIndex:
    """真索引:扫 bundled system_skills(data-analyst 在此)+ 用户 skills 目录。"""
    idx = SkillIndex()
    idx.rebuild_from_disk(pathlib.Path(user_dir))
    return idx


def _write_user_skill(dir_, name, *, desc, when, tags=(), scope="user", extra=""):
    d = pathlib.Path(dir_) / name
    d.mkdir(parents=True, exist_ok=True)
    tag_line = f"tags: [{', '.join(tags)}]\n" if tags else ""
    (d / "SKILL.md").write_text(
        f"---\nname: {name}\ndescription: {desc}\nwhen_to_use: {when}\n"
        f"signature: sig-{name}\nscope: {scope}\n{tag_line}{extra}---\n# body\n",
        encoding="utf-8")
    return d


# ---- 1. 中文/英文意图真命中(索引路径) ----

def test_chinese_intents_hit_data_analyst_user_scope(tmp_path):
    idx = _index(tmp_path)
    for intent in CN_DATA_INTENTS:
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is not None and hit.name == "data-analyst", \
            f"中文意图没召回 data-analyst: {intent!r} -> {hit and hit.name}"


def test_english_intents_hit_data_analyst(tmp_path):
    idx = _index(tmp_path)
    for intent in EN_DATA_INTENTS:
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is not None and hit.name == "data-analyst", \
            f"英文意图没召回 data-analyst: {intent!r} -> {hit and hit.name}"


def test_irrelevant_intents_do_not_hit(tmp_path):
    idx = _index(tmp_path)
    for intent in IRRELEVANT_INTENTS:
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is None or hit.name != "data-analyst", \
            f"无关意图误召回 data-analyst: {intent!r}"


# ---- 2. scope 放行:system 全场可见;用户 user 技能隔离不变 ----

def test_domain_scope_sees_system_skill(tmp_path):
    """业务域聊天(scope=domain)是最该用 data-analyst 的场 —— scope:user 不再挡它。"""
    idx = _index(tmp_path)
    for intent in CN_DATA_INTENTS + EN_DATA_INTENTS:
        hit = recall(intent, skills_dir=tmp_path, scope="domain", skill_index=idx)
        assert hit is not None and hit.name == "data-analyst", \
            f"domain 场没召回 system 技能: {intent!r} -> {hit and hit.name}"


def test_user_scope_user_skill_stays_out_of_domain(tmp_path):
    """放行只给 source=system;用户 user-scope 技能绝不漏进 domain 场(隔离语义不变)。"""
    _write_user_skill(tmp_path, "dog-walk", desc="安排遛狗",
                      when="需要安排遛狗计划时", tags=("遛狗", "宠物"))
    idx = _index(tmp_path)
    intent = "帮我安排一下遛狗计划"
    # user 场可见(0 回归)
    hit_user = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
    assert hit_user is not None and hit_user.name == "dog-walk"
    # domain 场不可见
    hit_domain = recall(intent, skills_dir=tmp_path, scope="domain", skill_index=idx)
    assert hit_domain is None or hit_domain.name != "dog-walk", \
        "user-scope 用户技能漏进了 domain 场(隔离被破)"


def test_fallback_disk_path_same_scope_semantics(tmp_path):
    """扫盘兜底路径(无 SkillIndex)同门:system 来源跨场可见,user 技能隔离不变。"""
    _write_user_skill(tmp_path, "dog-walk", desc="安排遛狗",
                      when="需要安排遛狗计划时", tags=("遛狗", "宠物"))
    _write_user_skill(tmp_path, "sys-method", desc="出厂方法模板",
                      when="需要分析数据统计报表时", tags=("数据", "统计"),
                      extra="source: system\n")
    # user 技能:user 场命中、domain 场不命中
    assert recall("帮我安排一下遛狗计划", skills_dir=tmp_path, scope="user").name == "dog-walk"
    hd = recall("帮我安排一下遛狗计划", skills_dir=tmp_path, scope="domain")
    assert hd is None or hd.name != "dog-walk"
    # system 来源:domain 场也命中
    hs = recall("帮我统计分析这些数据", skills_dir=tmp_path, scope="domain")
    assert hs is not None and hs.name == "sys-method", \
        "扫盘兜底路径没放行 source=system 技能进 domain 场"


# ---- 3. 文件面板 → 聊天的桥(后端半):intent 里的相对路径真能被 read_file 读到 ----

def _mk_workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / "data").mkdir(parents=True)
    csv = ws / "data" / "sales.csv"
    csv.write_text("month,revenue\n2026-01,100\n2026-02,150\n2026-03,210\n",
                   encoding="utf-8")
    return ws, csv


def _read_tool(ws):
    from karvyloop.capability.token import mint
    from karvyloop.coding.filestate import FileState
    from karvyloop.coding.tools.read import ReadTool
    from karvyloop.platform.linux.bubblewrap import BubblewrapSandbox
    from karvyloop.schemas import Capability
    # BubblewrapSandbox.read_file 是纯 Python(不走 bwrap 子进程)→ 全平台可测真沙箱语义
    tok = mint("t-files-bridge", [Capability(resource=f"fs:{ws}", ops=["read", "write"])])
    return ReadTool(BubblewrapSandbox(), FileState(), str(ws), token=tok)


def test_intent_relative_path_readable(tmp_path, monkeypatch):
    """files 面板按钮注入的原文:`帮我分析一下 <相对路径> 这份数据` —— agent 从 intent
    里抠出相对路径调 read_file,必须读得到。相对路径按生产默认解析:console 从工作区
    启动(workspace_root=cwd),故 chdir 到工作区。"""
    ws, _csv = _mk_workspace(tmp_path)
    intent = "帮我分析一下 data/sales.csv 这份数据"   # files_panel.js 注入的原文形状
    m = re.search(r"\S+\.csv", intent)
    assert m, "intent 里抠不出文件路径"
    rel = m.group(0)
    monkeypatch.chdir(ws)
    res = asyncio.run(_read_tool(ws)({"file_path": rel}))
    assert res.ok, f"相对路径 read_file 失败: {res.error_message}"
    assert "revenue" in str(res.payload) and "210" in str(res.payload)


def test_intent_absolute_path_readable_regardless_of_cwd(tmp_path):
    """工作区内绝对路径不依赖 cwd(前端若注入绝对路径也通)。"""
    ws, csv = _mk_workspace(tmp_path)
    res = asyncio.run(_read_tool(ws)({"file_path": str(csv)}))
    assert res.ok, f"绝对路径 read_file 失败: {res.error_message}"
    assert "revenue" in str(res.payload)


def test_outside_workspace_path_still_denied(tmp_path):
    """桥不放宽边界:工作区外路径照拒(0 回归)。"""
    ws, _ = _mk_workspace(tmp_path)
    outside = tmp_path / "secret.csv"
    outside.write_text("x\n", encoding="utf-8")
    res = asyncio.run(_read_tool(ws)({"file_path": str(outside)}))
    assert not res.ok
