"""test_file_butler_recall — 📁 文件管家:随包资产 + 召回可达 + fs 白名单边界(docs/60)。

照 test_data_analyst_recall 的形制锁四件事(真 recall 路径,不 mock):
1. 中文/英文整理意图 → recall 真命中 file-butler;无关意图不命中;
   数据分析意图仍归 data-analyst(两个系统技能不互相抢)。
2. scope 放行:source=system → domain 场也可见(镜像资产,人人一样)。
3. 随包原住民镜像完整(resident.json + 5 灵魂文件正文 + human-owned 偏好模板),
   引荐 ACCEPT 用真包资产走通:角色 7 文件 + 契约 + COMPOSITION 引 file-butler 技能。
4. fs 边界:入住授的三目录白名单内放行,**越白名单被 Deny**(path_allowed 咽喉 +
   真 WriteTool 全链路),敏感路径免疫一切授权(硬地板)。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.crystallize.recall import recall  # noqa: E402
from karvyloop.crystallize.skill_index import SkillIndex  # noqa: E402

CN_TIDY_INTENTS = [
    "帮我整理一下下载文件夹",
    "收拾一下我桌面上乱七八糟的文件",
    "帮我把这些旧文件归档到文件夹里",
    "帮我清理重复文件",
]
EN_TIDY_INTENTS = [
    "tidy up my downloads folder",
    "organize the files on my desktop",
    "help me find duplicate files and clean up",
]
IRRELEVANT_INTENTS = ["写首诗", "write me a poem about spring"]


def _index(user_dir) -> SkillIndex:
    idx = SkillIndex()
    idx.rebuild_from_disk(pathlib.Path(user_dir))
    return idx


# ---- 1. 召回可达 ----

def test_chinese_tidy_intents_hit_file_butler(tmp_path):
    idx = _index(tmp_path)
    for intent in CN_TIDY_INTENTS:
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is not None and hit.name == "file-butler", \
            f"中文整理意图没召回 file-butler: {intent!r} -> {hit and hit.name}"


def test_english_tidy_intents_hit_file_butler(tmp_path):
    idx = _index(tmp_path)
    for intent in EN_TIDY_INTENTS:
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is not None and hit.name == "file-butler", \
            f"英文整理意图没召回 file-butler: {intent!r} -> {hit and hit.name}"


def test_irrelevant_intents_do_not_hit(tmp_path):
    idx = _index(tmp_path)
    for intent in IRRELEVANT_INTENTS:
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is None or hit.name != "file-butler", \
            f"无关意图误召回 file-butler: {intent!r}"


def test_data_intents_still_go_to_data_analyst(tmp_path):
    """两个系统技能不互相抢:分析类意图仍归 data-analyst(0 回归)。"""
    idx = _index(tmp_path)
    for intent in ("分析这个表格的销售趋势", "analyze the data in sales.csv"):
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is not None and hit.name == "data-analyst", \
            f"数据意图被抢走: {intent!r} -> {hit and hit.name}"


def test_domain_scope_sees_file_butler(tmp_path):
    idx = _index(tmp_path)
    hit = recall("帮我整理一下下载文件夹", skills_dir=tmp_path, scope="domain", skill_index=idx)
    assert hit is not None and hit.name == "file-butler", "domain 场没放行 system 技能"


# ---- 2. 随包资产完整 + 引荐 ACCEPT 走真包资产 ----

def test_shipped_mirror_and_skill_assets_complete():
    from karvyloop.karvy.residents import load_resident, system_residents_dir
    from karvyloop.registry.skills import parse_frontmatter, system_skills_dir
    res = load_resident("file-butler")
    assert res is not None, "包内缺 file-butler 原住民镜像"
    assert (system_residents_dir() / "file-butler" / "resident.json").exists()
    for key in ("identity", "soul", "user", "memory", "verify"):
        assert res[key], f"镜像灵魂文件 {key} 是空的(打样=把文件写满)"
    assert res["skills"] == ["file-butler"]
    assert res["grant_dirs"] == ["Desktop", "Downloads", "Documents"]
    assert res["pitch"].get("en") and res["pitch"].get("zh"), "引荐话术必须双语"
    # 技能:frontmatter 合法 + system 来源 + 方法不是答案(dynamic 重跑)
    fm, body = parse_frontmatter(system_skills_dir() / "file-butler" / "SKILL.md")
    assert fm.name == "file-butler"
    assert (fm.raw or {}).get("source") == "system"
    assert fm.result_reuse == "dynamic"
    assert fm.tags, "召回靠 tags(中英双语)"
    # 喂方法不喂罐头:正文点名方法论 + human-owned 模板存在
    for marker in ("PARA", "inbox-zero", "Johnny.Decimal", "dry-run"):
        assert marker in body, f"SKILL.md 缺方法要素: {marker}"
    refs = system_skills_dir() / "file-butler" / "references"
    assert (refs / "filing-methods.md").exists()
    tpl = (refs / "filing-preferences.template.md").read_text(encoding="utf-8")
    assert "human-owned" in tpl, "偏好模板必须标明 human-owned(实例长在用户空间)"


def test_referral_accept_with_shipped_package(tmp_path):
    """真包镜像 + 真 registry:引荐卡 ACCEPT → 文件管家入住(7 文件 + 契约 + 技能引用 + 白名单)。"""
    from karvyloop.capability.fs_grants import FsGrantsStore
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry
    from karvyloop.karvy.residents import KIND_RESIDENT_REFERRAL, residents_referral_tick
    from karvyloop.roles.registry import RoleRegistry

    # skills_dir 存在但为空 → 走扫盘兜底校验:file-butler 必须靠 system_skills 放行
    user_skills = tmp_path / "skills"
    user_skills.mkdir()
    state = types.SimpleNamespace(
        role_registry=RoleRegistry(tmp_path / "roles", skills_dir=user_skills),
        proposal_registry=PendingProposalRegistry(),
        fs_grants=FsGrantsStore(tmp_path / "fs_grants.json"),
        proposal_handlers={}, ws_clients=set(), runtime_kwargs={},
        taste_predictions=None, memory=None,
        residents_state_path=tmp_path / "referral_state.json",
        residents_home=tmp_path / "home",
        silence_grants_path=tmp_path / "silence_grants.json",
    )
    app = types.SimpleNamespace(state=state)
    got = asyncio.run(residents_referral_tick(app))
    assert got["offered"] is True
    card = [p for p in state.proposal_registry.pending()
            if p.kind == KIND_RESIDENT_REFERRAL][0]
    assert "file-butler" in card.payload["resident_ids"]
    res = state.proposal_registry.decide(card.proposal_id, "ACCEPT",
                                         handlers=state.proposal_handlers)
    assert res is not None and res.ok, f"真包 ACCEPT 失败: {res and res.detail}"
    view = state.role_registry.get("file-butler")
    assert view is not None
    assert view.skill_ids == ["file-butler"], "COMPOSITION 该引用随包技能(用不拥有)"
    commitment = (tmp_path / "roles" / "file-butler" / "COMMITMENT.md").read_text(encoding="utf-8")
    assert "resourceful subordinate" in commitment, "尽责契约没 seed"
    verify = (tmp_path / "roles" / "file-butler" / "VERIFY.md").read_text(encoding="utf-8")
    assert "dry-run" in verify, "VERIFY 门没从镜像写满"
    # 三目录白名单落台账(按角色)
    home = tmp_path / "home"
    for d in ("Desktop", "Downloads", "Documents"):
        assert state.fs_grants.allows(str(home / d / "x.txt"), "write", role="file-butler"), \
            f"{d} 白名单没生效"


# ---- 3. fs 边界:越三目录被 Deny(咽喉 + 真工具全链路)----

def _seed_butler_grants(tmp_path):
    from karvyloop.capability.fs_grants import FsGrantsStore
    from karvyloop.karvy.residents import instantiate_resident, load_resident
    from karvyloop.roles.registry import RoleRegistry
    fs = FsGrantsStore(tmp_path / "fs_grants.json")
    res = load_resident("file-butler")
    instantiate_resident(res, role_registry=RoleRegistry(tmp_path / "roles"),
                         fs_grants=fs, home=tmp_path / "home")
    return fs


def test_fs_grants_boundary_at_chokepoint(tmp_path):
    from karvyloop.capability import fs_grants as fg
    fs = _seed_butler_grants(tmp_path)
    fg.register_store(fs)
    try:
        ws = str(tmp_path / "ws")
        home = tmp_path / "home"
        # 白名单内(前缀语义):放行
        assert fg.path_allowed(str(home / "Downloads" / "a" / "b.txt"), "write",
                               workspace_root=ws)
        # 越三目录:Deny
        assert not fg.path_allowed(str(home / "Pictures" / "x.png"), "read",
                                   workspace_root=ws)
        assert not fg.path_allowed(str(home / "secret-project" / "y.txt"), "write",
                                   workspace_root=ws)
        # 敏感路径免疫一切授权(硬地板):就算长在白名单目录下也不放
        fs.record(str(home / "Desktop"), ["read", "write"], role="file-butler")
        assert not fg.path_allowed(str(home / "Desktop" / ".env"), "read",
                                   workspace_root=ws)
        # 敏感路径连授权都拒记
        assert fs.record(str(pathlib.Path.home() / ".ssh"), ["read"]) is None
    finally:
        fg.register_store(None)


def test_write_tool_denies_outside_whitelist(tmp_path):
    """真 WriteTool 全链路:工作区外 + 白名单外 → 拒,且记一笔"想要"(升授权卡的信使)。"""
    from karvyloop.capability import fs_grants as fg
    from karvyloop.capability.token import mint
    from karvyloop.coding.filestate import FileState
    from karvyloop.coding.tools.write import WriteTool
    from karvyloop.platform.linux.bubblewrap import BubblewrapSandbox
    from karvyloop.schemas import Capability

    fs = _seed_butler_grants(tmp_path)
    fg.register_store(fs)
    try:
        ws = tmp_path / "ws"
        ws.mkdir()
        tok = mint("t-butler", [Capability(resource=f"fs:{ws}", ops=["read", "write"])])
        tool = WriteTool(BubblewrapSandbox(), FileState(), str(ws), token=tok)
        outside = tmp_path / "home" / "Pictures" / "hijack.txt"
        res = asyncio.run(tool({"file_path": str(outside), "content": "nope"}))
        assert not res.ok, "越白名单写入竟然成功(边界破了)"
        assert not outside.exists()
        denied = fs.pop_denied()
        assert any(d["path"] == str(outside) for d in denied), "碰壁没记'想要'(授权卡链断)"
        # 工作区内写照常(0 回归)
        inside = ws / "ok.txt"
        res2 = asyncio.run(tool({"file_path": str(inside), "content": "fine"}))
        assert res2.ok and inside.read_text(encoding="utf-8") == "fine"
    finally:
        fg.register_store(None)
