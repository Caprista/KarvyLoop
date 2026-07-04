"""test_residents_referral — 原住民引荐式入住(docs/60 MVP 第一件,空屋子解法)。

锁的合同(全走真机制,不 mock 决策链):
1. 触发条件:**空角色库**才出引荐卡;已有角色不出;一生只出一次(offered 落盘)。
2. 静默规则:REJECT 后永不再提;DEFER 卡留待决表、不重复出新卡;已挂着不重复出。
3. ACCEPT = 真入住:`RoleRegistry.create` 落 7 文件、**尽责契约由 create 统一 seed**
   (镜像里刻意没有 COMMITMENT),VERIFY/MEMORY 种子写入,目录白名单落 fs_grants 台账
   (可撤、按角色),幂等(二次 ACCEPT 不覆写实例)。
4. 卡文案走 i18n(en/zh 同 key 同占位,出卡时按当前 locale 定稿)。

镜像用 tmp 合成(residents_dir 注入)—— 机制与随包资产解耦;随包 file-butler 的
真资产测试在 tests/test_file_butler_recall.py。
"""
from __future__ import annotations

import asyncio
import json
import pathlib
import sys
import types

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.capability.fs_grants import FsGrantsStore  # noqa: E402
from karvyloop.karvy.proposal_registry import PendingProposalRegistry  # noqa: E402
from karvyloop.karvy.residents import (  # noqa: E402
    KIND_RESIDENT_REFERRAL,
    instantiate_resident,
    load_resident,
    residents_referral_tick,
    should_offer_referral,
)
from karvyloop.roles.registry import RoleRegistry  # noqa: E402

# 契约指纹(与 test_commitment_contract_seeding 同款):证明 COMMITMENT 真 seed 了契约
_CONTRACT_FPS = ("resourceful subordinate", "Exhaust your own resourcefulness", "bring evidence")


def _mk_resident_mirror(base: pathlib.Path, rid: str = "tmp-butler",
                        grant_dirs=("Desktop", "Downloads")) -> pathlib.Path:
    d = base / rid
    d.mkdir(parents=True, exist_ok=True)
    (d / "resident.json").write_text(json.dumps({
        "id": rid,
        "emoji": "📁",
        "nickname": {"en": "Test Butler", "zh": "测试管家"},
        "title": {"en": "File steward", "zh": "文件管家"},
        "pitch": {"en": "Keeps your folders tidy, preview first.",
                  "zh": "先预览后动手,替你收拾文件夹。"},
        "skills": [],
        "grant_dirs": list(grant_dirs),
        "grant_ops": ["read", "write"],
    }, ensure_ascii=False), encoding="utf-8")
    (d / "IDENTITY.md").write_text("I am a test resident who tidies folders.", encoding="utf-8")
    (d / "SOUL.md").write_text("Preview first, act second.", encoding="utf-8")
    (d / "USER.md").write_text("Serves the owner of this machine.", encoding="utf-8")
    (d / "MEMORY.md").write_text("Seed: methods live in the filing skill.", encoding="utf-8")
    (d / "VERIFY.md").write_text("Never move without a dry-run preview; delete needs H2A.",
                                 encoding="utf-8")
    return d


def _app(tmp_path: pathlib.Path, *, with_mirror: bool = True):
    residents_dir = tmp_path / "residents"
    if with_mirror:
        _mk_resident_mirror(residents_dir)
    state = types.SimpleNamespace(
        role_registry=RoleRegistry(tmp_path / "roles"),
        proposal_registry=PendingProposalRegistry(persist_path=tmp_path / "pending.json"),
        fs_grants=FsGrantsStore(tmp_path / "fs_grants.json"),
        proposal_handlers={},
        ws_clients=set(),
        runtime_kwargs={},
        taste_predictions=None,
        memory=None,
        residents_dir=residents_dir,
        residents_state_path=tmp_path / "referral_state.json",
        residents_home=tmp_path / "home",
        silence_grants_path=tmp_path / "silence_grants.json",
    )
    return types.SimpleNamespace(state=state)


def _tick(app) -> dict:
    return asyncio.run(residents_referral_tick(app))


def _pending_referrals(app) -> list:
    return [p for p in app.state.proposal_registry.pending()
            if getattr(p, "kind", "") == KIND_RESIDENT_REFERRAL]


# ---- 1. 触发条件 ----

def test_empty_library_offers_card(tmp_path):
    app = _app(tmp_path)
    got = _tick(app)
    assert got["offered"] is True
    cards = _pending_referrals(app)
    assert len(cards) == 1
    card = cards[0]
    assert card.proposal_id.startswith(KIND_RESIDENT_REFERRAL)
    assert card.payload.get("resident_ids") == "tmp-butler"
    assert card.basis, "引荐卡必须带决策依据(为什么/边界/ACCEPT 会发生什么)"
    # 状态落盘:一生只出一次的依据
    state = json.loads((tmp_path / "referral_state.json").read_text(encoding="utf-8"))
    assert state.get("offered") is True


def test_non_empty_library_never_offers(tmp_path):
    app = _app(tmp_path)
    app.state.role_registry.create("已有角色", identity="在住")
    got = _tick(app)
    assert got["offered"] is False
    assert _pending_referrals(app) == []


def test_no_mirror_no_card(tmp_path):
    app = _app(tmp_path, with_mirror=False)
    got = _tick(app)
    assert got["offered"] is False
    assert _pending_referrals(app) == []


def test_pending_card_not_duplicated(tmp_path):
    app = _app(tmp_path)
    _tick(app)
    got2 = _tick(app)
    assert got2["offered"] is False
    assert len(_pending_referrals(app)) == 1


# ---- 2. 静默规则 ----

def test_reject_then_forever_silent(tmp_path):
    app = _app(tmp_path)
    _tick(app)
    pid = _pending_referrals(app)[0].proposal_id
    res = app.state.proposal_registry.decide(pid, "REJECT",
                                             handlers=app.state.proposal_handlers)
    assert res is not None and res.detail == "rejected"
    assert _pending_referrals(app) == []
    # 再 tick(角色库仍空)→ 不纠缠
    got = _tick(app)
    assert got["offered"] is False
    assert _pending_referrals(app) == []


def test_defer_keeps_card_without_new_one(tmp_path):
    app = _app(tmp_path)
    _tick(app)
    pid = _pending_referrals(app)[0].proposal_id
    app.state.proposal_registry.decide(pid, "DEFER", handlers=app.state.proposal_handlers)
    assert len(_pending_referrals(app)) == 1, "DEFER 的卡该留在待决表"
    _tick(app)
    assert len(_pending_referrals(app)) == 1, "DEFER 后不许出第二张"


def test_should_offer_guards():
    assert should_offer_referral(role_registry=None, state={}) is False
    class _Empty:
        def __len__(self):
            return 0
    assert should_offer_referral(role_registry=_Empty(), state={}) is True
    assert should_offer_referral(role_registry=_Empty(), state={"offered": True}) is False
    assert should_offer_referral(role_registry=_Empty(), state={"decision": "accepted"}) is False


# ---- 3. ACCEPT = 真入住 ----

def test_accept_creates_role_with_contract_and_grants(tmp_path):
    app = _app(tmp_path)
    _tick(app)
    pid = _pending_referrals(app)[0].proposal_id
    res = app.state.proposal_registry.decide(pid, "ACCEPT",
                                             handlers=app.state.proposal_handlers)
    assert res is not None and res.ok, f"ACCEPT 兑现失败: {res and res.detail}"
    # 角色真建:7 文件齐 + 契约 seed(create 统一入口,镜像不带 COMMITMENT)
    role_dir = tmp_path / "roles" / "tmp-butler"
    for fname in ("IDENTITY.md", "SOUL.md", "USER.md", "MEMORY.md",
                  "COMMITMENT.md", "VERIFY.md", "COMPOSITION.yaml"):
        assert (role_dir / fname).exists(), f"缺 {fname}"
    commitment = (role_dir / "COMMITMENT.md").read_text(encoding="utf-8")
    for fp in _CONTRACT_FPS:
        assert fp in commitment, f"COMMITMENT 缺契约指纹: {fp}"
    # 镜像种子真写入(不是 stub)
    assert "dry-run preview" in (role_dir / "VERIFY.md").read_text(encoding="utf-8")
    assert "filing skill" in (role_dir / "MEMORY.md").read_text(encoding="utf-8")
    # profile:花名/职务(按当前 locale,默认 en)
    prof = json.loads((role_dir / "profile.json").read_text(encoding="utf-8"))
    assert prof.get("nickname"), "入住该带花名"
    # 目录白名单落台账:白名单内放行、按角色、可撤;台账外拒
    fs = app.state.fs_grants
    home = tmp_path / "home"
    assert fs.allows(str(home / "Desktop" / "a.txt"), "write", role="tmp-butler")
    assert fs.allows(str(home / "Downloads" / "b" / "c.txt"), "read", role="tmp-butler")
    assert not fs.allows(str(home / "Pictures" / "x.png"), "read", role="tmp-butler"), \
        "白名单外目录不该被放行"
    # 状态:decision=accepted → 永不再引荐
    state = json.loads((tmp_path / "referral_state.json").read_text(encoding="utf-8"))
    assert state.get("decision") == "accepted"
    got = _tick(app)
    assert got["offered"] is False


def test_accept_idempotent_does_not_overwrite_instance(tmp_path):
    app = _app(tmp_path)
    res = load_resident("tmp-butler", app.state.residents_dir)
    out1 = instantiate_resident(res, role_registry=app.state.role_registry,
                                fs_grants=app.state.fs_grants,
                                home=app.state.residents_home)
    assert out1["created"] is True
    # 用户改了实例(养出来的);再入住不许覆写
    ident = tmp_path / "roles" / "tmp-butler" / "IDENTITY.md"
    ident.write_text("# IDENTITY\n\n我被用户亲手改过\n", encoding="utf-8")
    out2 = instantiate_resident(res, role_registry=app.state.role_registry,
                                fs_grants=app.state.fs_grants,
                                home=app.state.residents_home)
    assert out2["created"] is False
    assert "亲手改过" in ident.read_text(encoding="utf-8"), "二次入住覆写了用户实例(镜像/实例刀被破)"
    # 白名单仍然在(record 幂等)
    assert app.state.fs_grants.allows(
        str(app.state.residents_home / "Desktop" / "f.txt"), "write", role="tmp-butler")


# ---- 4. 文案 i18n(en/zh 同 key,出卡时按 locale 定稿)----

def test_card_text_localized(tmp_path):
    from karvyloop import i18n
    try:
        i18n.set_locale("zh")
        app_zh = _app(tmp_path / "zh")
        _tick(app_zh)
        card_zh = _pending_referrals(app_zh)[0]
        assert "入住" in card_zh.summary and "测试管家" in card_zh.summary
        i18n.set_locale("en")
        app_en = _app(tmp_path / "en")
        _tick(app_en)
        card_en = _pending_referrals(app_en)[0]
        assert "Move in" in card_en.summary and "Test Butler" in card_en.summary
    finally:
        i18n.set_locale(None)
