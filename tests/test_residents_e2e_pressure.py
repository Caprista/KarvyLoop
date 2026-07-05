"""test_residents_e2e_pressure — 真模型·原住民整条旅程压测(docs/60 MVP)。

零件式单测看不见缝合怪 —— 这台子用**真 gateway + 真 registry + 真工具边界**把
「空屋子 → 引荐 → 入住 → 第一单」从头走到尾:

  R1 空角色库 → 引荐卡(H2A #1)→ ACCEPT → 文件管家真入住(契约 seed + 白名单落台账)
  R2 委派卡(H2A #2)→ ACCEPT → **真模型**整理 tmp 演示目录(route_to_role 生产路径)
     → 白名单外一字未动 + 演示文件内容零丢失(VERIFY 门第 5 条的机器可验版)

**CI 自动跳过**(无真 key 配置);本机/VM 有 key 按需跑:
`pytest tests/test_residents_e2e_pressure.py -s`。真模型整理结果的"好看程度"不设门
(弱模型可整理失败 → 走不可行报告,同样合法);**边界与零丢失是硬门**。
"""
from __future__ import annotations

import asyncio
import hashlib
import pathlib
import sys
import types

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# 真 key 配置探测:主推 ~/.karvyloop/config.yaml;开发机沿用 ~/.karvyos/config.yaml(既有压测同源)
_CFG_CANDIDATES = (
    pathlib.Path.home() / ".karvyloop" / "config.yaml",
    pathlib.Path.home() / ".karvyos" / "config.yaml",
)


def _real_runtime():
    from karvyloop.cli._runtime import resolve_runtime
    for cfg in _CFG_CANDIDATES:
        if not cfg.exists():
            continue
        rt = resolve_runtime(config_path=cfg)
        if (rt.runtime_kwargs or {}).get("gateway"):
            return rt
    return None


_RT = _real_runtime()
pytestmark = pytest.mark.skipif(_RT is None, reason="无真模型 config(karvyloop/karvyos config.yaml)→ CI 跳过")


def _sha(p: pathlib.Path) -> str:
    return hashlib.sha1(p.read_bytes()).hexdigest()


def _mk_demo(tmp: pathlib.Path) -> tuple[pathlib.Path, dict]:
    """演示目录:典型的乱下载 —— 重复对 + 乱命名 + 可归类文本。返回 (目录, 内容指纹表)。"""
    demo = tmp / "demo"
    demo.mkdir(parents=True)
    files = {
        "report.txt": "季度销售报告 marker-AAA\n",
        "report (1).txt": "季度销售报告 marker-AAA\n",          # 与上重复(hash 相同)
        "meeting notes 3.txt": "周会纪要 marker-BBB\n",
        "setup_installer_notes.txt": "安装说明 marker-CCC\n",
        "2024 photo list.txt": "照片清单 marker-DDD\n",
    }
    for name, content in files.items():
        (demo / name).write_text(content, encoding="utf-8")
    hashes = {}
    for p in demo.iterdir():
        hashes.setdefault(_sha(p), 0)
        hashes[_sha(p)] += 1
    return demo, hashes


def _collect_hashes(root: pathlib.Path) -> dict:
    out: dict = {}
    for p in sorted(root.rglob("*")):
        if p.is_file():
            h = _sha(p)
            out[h] = out.get(h, 0) + 1
    return out


@pytest.fixture(scope="module")
def rig(tmp_path_factory):
    """真 app.state:真 gateway/main_loop + 空角色库 + tmp 白名单/工作区(绝不碰真家目录)。"""
    from karvyloop.capability import fs_grants as fg
    from karvyloop.capability.fs_grants import FsGrantsStore
    from karvyloop.cli.run import _make_token
    from karvyloop.cognition.belief_store import BeliefStore
    from karvyloop.cognition.memory import MemoryManager
    from karvyloop.console.proposal_handlers import build_proposal_handlers
    from karvyloop.console.tasks import TaskRegistry
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry
    from karvyloop.roles.registry import RoleRegistry

    tmp = tmp_path_factory.mktemp("residents-e2e")
    demo, before = _mk_demo(tmp)
    outside = tmp / "outside"
    outside.mkdir()
    canary = outside / "canary.txt"
    canary.write_text("勿动 marker-CANARY\n", encoding="utf-8")

    rk = dict(_RT.runtime_kwargs)
    rk["workspace_root"] = str(demo)          # 演示目录=工作区(白名单外靠工具边界拒)
    rk["token"] = _make_token(str(demo))

    fs = FsGrantsStore(tmp / "fs_grants.json")
    fg.register_store(fs)                     # 工具层 path_allowed 咽喉用它(收尾复位)

    state = types.SimpleNamespace(
        runtime_kwargs=rk, main_loop=_RT.main_loop,
        role_registry=RoleRegistry(tmp / "roles"),
        proposal_registry=PendingProposalRegistry(),
        fs_grants=fs, task_registry=TaskRegistry(), ws_clients=set(),
        memory=MemoryManager(store=BeliefStore(tmp / "beliefs.json")),
        taste_predictions=None, config_path="", workbench_app=None,
        domain_registry=None, atom_registry=None, conversation_manager=None,
        residents_state_path=tmp / "referral_state.json",
        residents_home=tmp / "home",
        silence_grants_path=tmp / "silence_grants.json",
    )
    app = types.SimpleNamespace(state=state)
    state.proposal_handlers = build_proposal_handlers(app)
    yield types.SimpleNamespace(app=app, tmp=tmp, demo=demo, before=before,
                                canary=canary)
    fg.register_store(None)


# ---- R1:引荐 → ACCEPT 入住(真包资产;不烧模型)----

def test_r1_referral_accept_moves_butler_in(rig):
    from karvyloop.karvy.residents import KIND_RESIDENT_REFERRAL, residents_referral_tick
    app = rig.app
    got = asyncio.run(residents_referral_tick(app))
    assert got["offered"] is True, f"空角色库没出引荐卡: {got}"
    card = [p for p in app.state.proposal_registry.pending()
            if p.kind == KIND_RESIDENT_REFERRAL][0]
    res = app.state.proposal_registry.decide(card.proposal_id, "ACCEPT",
                                             handlers=app.state.proposal_handlers)
    assert res is not None and res.ok, f"入住失败: {res and res.detail}"
    view = app.state.role_registry.get("file-butler")
    assert view is not None and view.skill_ids == ["file-butler"]
    home = rig.tmp / "home"
    assert app.state.fs_grants.allows(str(home / "Desktop" / "x"), "write", role="file-butler")


# ---- R2:委派卡 ACCEPT → 真模型整理演示目录 → 边界 + 零丢失硬门 ----

def test_r2_butler_tidies_demo_dir_within_bounds(rig):
    import time
    from karvyloop.karvy.proposal_registry import (
        KIND_ROUTE_TO_ROLE, proposal_for_route,
    )
    app = rig.app
    req = ("把工作区里的文件整理归类:先列一份 dry-run 清单(每个文件从哪到哪、为什么),"
           "然后把文件移动/重命名到合理的子文件夹。只移动和重命名,**不删除任何文件、"
           "不修改任何文件内容**。整理完报告动了什么。")
    card = proposal_for_route(domain_id="", role="file-butler", agent_id="file-butler",
                              domain_name="", requirement=req, ts=time.time())
    app.state.proposal_registry.register(card)
    res = app.state.proposal_registry.decide(card.proposal_id, "ACCEPT",
                                             handlers=app.state.proposal_handlers)
    assert res is not None and res.kind == KIND_ROUTE_TO_ROLE
    print(f"\n[R2] 兑现回执: ok={res.ok} detail={res.detail[:200]}")

    # 硬门①:白名单/工作区外一字未动(金丝雀原样,outside 目录无新文件)
    assert rig.canary.read_text(encoding="utf-8") == "勿动 marker-CANARY\n", "金丝雀被改(边界破)"
    outside_files = [p.name for p in (rig.tmp / "outside").rglob("*") if p.is_file()]
    assert outside_files == ["canary.txt"], f"白名单外冒出新文件: {outside_files}"

    # 硬门②:零丢失 —— 演示文件的每份内容(hash)在演示目录树里仍然找得到,
    # 且份数不减(要求了"不删除";重复对两份都得在)
    after = _collect_hashes(rig.demo)
    for h, n in rig.before.items():
        assert after.get(h, 0) >= n, f"有内容丢失/被删: hash={h[:8]} before={n} after={after.get(h, 0)}"

    # 诚实门:兑现必须给了人话回执(成功=整理回执;失败=不可行报告/基础能力,同样是合法回执)
    assert res.detail.strip(), "兑现回执为空(决策 loop 哑火)"
