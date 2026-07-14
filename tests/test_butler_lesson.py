"""test_butler_lesson — 文件管家第一课(扫描 → 方案预览卡 → ACCEPT 真执行)。

锁的合同(全确定性,零 LLM):
1. 只读扫描:白名单目录顶层文件;隐藏/系统/快捷方式/子目录不入方案;敏感路径硬地板。
2. 方案分桶:by_type(图片/文档/安装包…)/ by_time(YYYY-MM)—— 模式由采集器 filing
   答案确定性决定(与 onboarding_intake 咬合);查重 = 同尺寸+同 hash(只报告不删);
   占位大户只报告。
3. 卡:payload 全字符串、proposal_id 稳定派生(同方案幂等)、basis 交代安全边界。
4. 执行(仅 ACCEPT 后):只 move;绝不覆盖(目标已存在→跳过如实报);白名单外/敏感/
   中途消失→跳过;金丝雀一字不动;全量 move 台账(第一课版回收站兜底)。
5. 端点:无授权→no_grants(不越权偷扫);空目录→empty(诚实,不硬凑);出卡进待决表。
6. 前端接线(静态断言)。
"""
from __future__ import annotations

import asyncio
import json
import time
import types
from pathlib import Path

import pytest

import karvyloop.karvy.butler_lesson as bl
from karvyloop.capability.fs_grants import FsGrantsStore
from karvyloop.karvy.butler_lesson import (
    BUTLER_ROLE_ID,
    KIND_BUTLER_PLAN,
    build_first_lesson,
    execute_plan,
    filing_mode_from_memory,
    find_duplicates,
    make_butler_plan_handler,
    proposal_for_butler_plan,
    scan_dir,
)

REPO = Path(__file__).resolve().parent.parent
STATIC = REPO / "karvyloop" / "console" / "static"


def _mk_home(tmp_path: Path) -> Path:
    """演示家目录:Desktop/Downloads 塞典型杂物 + 该跳过的东西 + 白名单外金丝雀。"""
    home = tmp_path / "home"
    desk, down = home / "Desktop", home / "Downloads"
    desk.mkdir(parents=True)
    down.mkdir(parents=True)
    (desk / "photo.PNG").write_text("img-A", encoding="utf-8")
    (desk / "notes.txt").write_text("notes", encoding="utf-8")
    (desk / "app.lnk").write_text("shortcut", encoding="utf-8")       # 快捷方式:不入方案
    (desk / ".hidden").write_text("secret", encoding="utf-8")         # 隐藏:跳过
    (desk / "desktop.ini").write_text("sys", encoding="utf-8")        # 系统文件:跳过
    (desk / "subdir").mkdir()                                         # 子目录:不深入不移动
    (desk / "subdir" / "inner.txt").write_text("inner", encoding="utf-8")
    (down / "report.pdf").write_text("pdf-content", encoding="utf-8")
    (down / "report (1).pdf").write_text("pdf-content", encoding="utf-8")   # 重复对(同 hash)
    (down / "setup.exe").write_text("installer-bytes", encoding="utf-8")
    (down / "song.mp3").write_text("media-bytes!", encoding="utf-8")
    outside = home / "Documents"                                      # 白名单外(第一课不碰)
    outside.mkdir()
    (outside / "canary.txt").write_text("勿动 marker-CANARY", encoding="utf-8")
    return home


def _grants(tmp_path: Path, home: Path) -> FsGrantsStore:
    fs = FsGrantsStore(tmp_path / "fs_grants.json")
    for name in ("Desktop", "Downloads"):
        fs.record(str(home / name), ["read", "write"], role=BUTLER_ROLE_ID,
                  origin="resident_seed")
    return fs


# ---- 1. 只读扫描 ----

def test_scan_dir_reads_metadata_and_skips_noise(tmp_path):
    home = _mk_home(tmp_path)
    got = scan_dir(home / "Desktop")
    names = {f["name"] for f in got["files"]}
    assert names == {"photo.PNG", "notes.txt", "app.lnk"}   # 隐藏/系统/子目录都不在
    lnk = next(f for f in got["files"] if f["name"] == "app.lnk")
    assert lnk["leave"] is True                              # 快捷方式:盘点可见但永不移动
    png = next(f for f in got["files"] if f["name"] == "photo.PNG")
    assert png["ext"] == ".png" and png["size"] == 5 and png["mtime"] > 0
    assert got["truncated"] is False
    # 扫描是只读的:文件都还在原地
    assert (home / "Desktop" / "photo.PNG").exists()


def test_scan_dir_missing_dir_is_empty(tmp_path):
    assert scan_dir(tmp_path / "nope") == {"files": [], "truncated": False}


# ---- 2. 方案分桶 + 查重 + 大户 ----

def test_build_plan_by_type_buckets(tmp_path):
    home = _mk_home(tmp_path)
    plan = build_first_lesson([home / "Desktop", home / "Downloads"],
                              mode="by_type", locale="en")
    assert plan["empty"] is False and plan["scanned"] == 7
    by_name = {m["name"]: m for m in plan["moves"]}
    assert "app.lnk" not in by_name                          # leave 的不进 moves
    assert by_name["photo.PNG"]["bucket"] == "Images"
    assert by_name["setup.exe"]["bucket"] == "Installers"
    assert by_name["song.mp3"]["bucket"] == "Media"
    assert by_name["report.pdf"]["bucket"] == "Documents"
    # dst 永远在同一白名单目录之内的子文件夹
    m = by_name["report.pdf"]
    assert Path(m["dst"]) == home / "Downloads" / "Documents" / "report.pdf"
    # 查重:同内容 pdf 被 hash 抓到(只报告)
    assert any(set(g["names"]) == {"report.pdf", "report (1).pdf"}
               for g in plan["duplicates"])


def test_build_plan_by_time_buckets(tmp_path):
    home = _mk_home(tmp_path)
    plan = build_first_lesson([home / "Downloads"], mode="by_time", locale="zh")
    for m in plan["moves"]:
        assert len(m["bucket"]) == 7 and m["bucket"][4] == "-", \
            f"by_time 桶应是 YYYY-MM,得到 {m['bucket']!r}"


def test_zh_locale_buckets(tmp_path):
    home = _mk_home(tmp_path)
    plan = build_first_lesson([home / "Desktop"], mode="by_type", locale="zh")
    by_name = {m["name"]: m for m in plan["moves"]}
    assert by_name["photo.PNG"]["bucket"] == "图片"


def test_duplicates_need_same_hash_not_just_size():
    files = [
        {"path": "a", "name": "a", "size": 5},
        {"path": "b", "name": "b", "size": 7},
    ]
    assert find_duplicates(files) == []   # 尺寸不同连候选都不是(不烧 hash)


def test_hogs_reported_not_moved(tmp_path, monkeypatch):
    monkeypatch.setattr(bl, "HOG_MIN_SIZE", 10)   # 测试不写 50MB 真文件
    home = _mk_home(tmp_path)
    plan = build_first_lesson([home / "Downloads"], mode="by_type", locale="en")
    hog_names = {h["name"] for h in plan["hogs"]}
    assert "installer-bytes" not in hog_names   # hogs 是文件名不是内容
    assert {"setup.exe", "song.mp3", "report.pdf", "report (1).pdf"} >= hog_names
    assert hog_names, "≥10B 的文件该进大户榜(报告面)"


def test_empty_dirs_honest_empty(tmp_path):
    d = tmp_path / "Desktop"
    d.mkdir()
    plan = build_first_lesson([d], mode="by_type", locale="en")
    assert plan["empty"] is True and plan["moves"] == []


# ---- 与采集器咬合:filing 答案确定性决定分桶模式 ----

def test_filing_mode_from_memory_interlock(tmp_path):
    from karvyloop.cognition.belief_store import BeliefStore
    from karvyloop.cognition.memory import MemoryManager
    from karvyloop.onboarding_intake import seed_answers
    mem = MemoryManager(store=BeliefStore(tmp_path / "beliefs.json"))
    assert filing_mode_from_memory(None) == "by_type"
    assert filing_mode_from_memory(mem) == "by_type"          # 没答过 → 默认按类型
    seed_answers({"filing": "by_time"}, mem=mem, locale="zh")
    assert filing_mode_from_memory(mem) == "by_time"          # 答案改变系统行为
    seed_answers({"filing": "by_type"}, mem=mem, locale="zh")  # 重答替换
    assert filing_mode_from_memory(mem) == "by_type"


# ---- 3. 卡 ----

def test_proposal_payload_strings_and_stable_id(tmp_path):
    home = _mk_home(tmp_path)
    plan = build_first_lesson([home / "Desktop", home / "Downloads"],
                              mode="by_type", locale="en")
    c1 = proposal_for_butler_plan(plan, ts=1.0)
    c2 = proposal_for_butler_plan(plan, ts=2.0)
    assert c1.kind == KIND_BUTLER_PLAN
    assert c1.proposal_id == c2.proposal_id, "同方案必须幂等收敛成同一张卡"
    assert c1.options == ("ACCEPT", "DEFER", "REJECT")
    for k, v in c1.payload.items():
        assert isinstance(v, str), f"payload.{k} 必须是字符串(改了再批白名单约定)"
    inner = json.loads(c1.payload["plan"])
    assert inner["moves"] and inner["dirs"]
    assert c1.basis and c1.summary


def test_proposal_i18n_both_locales(tmp_path):
    from karvyloop import i18n
    home = _mk_home(tmp_path)
    plan = build_first_lesson([home / "Desktop"], mode="by_type", locale="zh")
    try:
        i18n.set_locale("zh")
        card = proposal_for_butler_plan(plan, ts=1.0, mode_from_intake=True)
        assert "文件管家" in card.summary and "入门问答" in card.basis
        i18n.set_locale("en")
        card_en = proposal_for_butler_plan(plan, ts=1.0)
        assert "File Butler" in card_en.summary
    finally:
        i18n.set_locale(None)


# ---- 4. 执行(仅 ACCEPT 后) ----

def test_execute_plan_moves_within_whitelist_only(tmp_path):
    home = _mk_home(tmp_path)
    fs = _grants(tmp_path, home)
    plan = build_first_lesson([home / "Desktop", home / "Downloads"],
                              mode="by_type", locale="en")
    journal = tmp_path / "journal.json"
    res = execute_plan(plan, fs_grants=fs, journal_path=journal)
    assert len(res["moved"]) == len(plan["moves"]) and not res["skipped"]
    assert (home / "Downloads" / "Documents" / "report.pdf").exists()
    assert not (home / "Downloads" / "report.pdf").exists()
    assert (home / "Desktop" / "Images" / "photo.PNG").exists()
    # 不入方案的都在原地
    assert (home / "Desktop" / "app.lnk").exists()
    assert (home / "Desktop" / ".hidden").exists()
    assert (home / "Desktop" / "subdir" / "inner.txt").exists()
    # 金丝雀(白名单外)一字未动
    assert (home / "Documents" / "canary.txt").read_text(encoding="utf-8") == "勿动 marker-CANARY"
    # 零丢失:重复对两份都在(第一课绝不删除)
    assert (home / "Downloads" / "Documents" / "report (1).pdf").exists()
    # 台账(第一课版回收站兜底):全量 src→dst 留痕
    entries = json.loads(journal.read_text(encoding="utf-8"))
    assert entries[0]["origin"] == "butler_first_lesson"
    assert len(entries[0]["moved"]) == len(plan["moves"])
    assert all(Path(m["dst"]).exists() for m in entries[0]["moved"])


def test_execute_plan_never_overwrites(tmp_path):
    home = _mk_home(tmp_path)
    fs = _grants(tmp_path, home)
    plan = build_first_lesson([home / "Desktop"], mode="by_type", locale="en")
    # 预置目标冲突:Images/photo.PNG 已存在别的内容
    tgt = home / "Desktop" / "Images" / "photo.PNG"
    tgt.parent.mkdir(parents=True)
    tgt.write_text("PRE-EXISTING", encoding="utf-8")
    res = execute_plan(plan, fs_grants=fs, journal_path=tmp_path / "j.json")
    skip = next(s for s in res["skipped"] if s["name"] == "photo.PNG")
    assert skip["reason"] == "target_exists"
    assert tgt.read_text(encoding="utf-8") == "PRE-EXISTING", "覆盖了已存在目标(SKILL 铁律破)"
    assert (home / "Desktop" / "photo.PNG").exists(), "源文件必须原地保留"


def test_execute_plan_rejects_outside_and_tampered(tmp_path):
    home = _mk_home(tmp_path)
    fs = _grants(tmp_path, home)
    canary = home / "Documents" / "canary.txt"
    evil = {
        "dirs": [str(home / "Desktop")],
        "moves": [
            # 源在白名单外(方案被篡改)→ 拒
            {"src": str(canary), "dst": str(home / "Desktop" / "x.txt"), "name": "canary.txt"},
            # 目标逃出白名单目录 → 拒
            {"src": str(home / "Desktop" / "notes.txt"),
             "dst": str(home / "Documents" / "notes.txt"), "name": "notes.txt"},
            # 中途消失 → 跳过
            {"src": str(home / "Desktop" / "gone.txt"),
             "dst": str(home / "Desktop" / "Others" / "gone.txt"), "name": "gone.txt"},
        ],
    }
    res = execute_plan(evil, fs_grants=fs, journal_path=tmp_path / "j.json")
    assert not res["moved"]
    reasons = {s["name"]: s["reason"] for s in res["skipped"]}
    assert reasons["canary.txt"] == "outside_whitelist"
    assert reasons["notes.txt"] == "outside_whitelist"
    assert reasons["gone.txt"] == "gone"
    assert canary.exists() and (home / "Desktop" / "notes.txt").exists()


def test_within_rejects_symlink_escape(tmp_path):
    """_within 必须按 OS 真路径判(symlink 加固):白名单目录里一个指向外部的符号链接,
    词法上"在 base 内"、shutil.move 的真实落点却在白名单外 —— 词法判定放行 = 搬穿白名单。"""
    base = tmp_path / "base"
    base.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = base / "sneaky"
    try:
        link.symlink_to(outside, target_is_directory=True)
    except (OSError, NotImplementedError):
        pytest.skip("本机建不了 symlink(Windows 无 SeCreateSymbolicLinkPrivilege)")
    # 穿 symlink 的目标:词法在 base 内,真路径在 outside → 必须 False
    assert bl._within(link / "evil.txt", base) is False
    assert bl._within(link, base) is False
    # 0 回归:真在 base 内的照常 True;base 外的照常 False;`..` 照拒
    assert bl._within(base / "ok.txt", base) is True
    assert bl._within(outside / "x.txt", base) is False
    assert bl._within(base / ".." / "outside" / "x.txt", base) is False


def test_execute_plan_rejects_dotdot_traversal(tmp_path):
    """`..` 逃逸(词法上"在白名单内"实则越狱)必须被确定性地板拒 —— 不依赖 fs_grants 在不在。"""
    home = _mk_home(tmp_path)
    evil = {
        "dirs": [str(home / "Desktop")],
        "moves": [{
            "src": str(home / "Desktop" / "notes.txt"),
            "dst": str(home / "Desktop" / "Others" / ".." / ".." / ".." / "escaped.txt"),
            "name": "notes.txt",
        }],
    }
    res = execute_plan(evil, fs_grants=None, journal_path=tmp_path / "j.json")   # 故意不带台账
    assert not res["moved"]
    assert res["skipped"][0]["reason"] == "outside_whitelist"
    assert (home / "Desktop" / "notes.txt").exists()
    assert not (tmp_path / "escaped.txt").exists() and not (home / "escaped.txt").exists()


def test_execute_plan_requires_grant(tmp_path):
    """授权被撤(能力总览可撤)→ 执行时以台账现状为准,全部跳过。"""
    home = _mk_home(tmp_path)
    fs = FsGrantsStore(tmp_path / "fs_grants.json")   # 空台账 = 没授权
    plan = build_first_lesson([home / "Desktop"], mode="by_type", locale="en")
    res = execute_plan(plan, fs_grants=fs, journal_path=tmp_path / "j.json")
    assert not res["moved"]
    assert all(s["reason"] == "not_granted" for s in res["skipped"])


def test_handler_refuses_bad_plan():
    handler = make_butler_plan_handler(types.SimpleNamespace(state=types.SimpleNamespace()))
    ok, detail = handler(types.SimpleNamespace(payload={"plan": "{broken"}))
    assert ok is False and detail.strip(), "方案坏了必须拒绝碰文件(宁拒勿猜)"


# ---- 5. 端点 ----

def _app(tmp_path, home=None, fs=None, memory=None):
    from karvyloop.karvy.proposal_registry import PendingProposalRegistry
    state = types.SimpleNamespace(
        fs_grants=fs, proposal_registry=PendingProposalRegistry(),
        proposal_handlers={}, ws_clients=set(), runtime_kwargs={},
        taste_predictions=None, memory=memory,
        residents_home=home, butler_journal_path=tmp_path / "journal.json",
        silence_grants_path=tmp_path / "silence_grants.json",
        main_loop=None,
    )
    return types.SimpleNamespace(state=state)


def _post(app):
    from karvyloop.console.routes_butler import api_butler_first_lesson
    return asyncio.run(api_butler_first_lesson(types.SimpleNamespace(app=app)))


def test_endpoint_no_grants_refuses(tmp_path):
    home = _mk_home(tmp_path)
    app = _app(tmp_path, home=home, fs=FsGrantsStore(tmp_path / "fs.json"))   # 空台账
    r = _post(app)
    assert r == {"ok": False, "reason": "no_grants"}, "没授权必须拒扫(不越权偷看)"


def test_endpoint_empty_home_honest(tmp_path):
    home = tmp_path / "home"
    (home / "Desktop").mkdir(parents=True)
    (home / "Downloads").mkdir(parents=True)
    fs = _grants(tmp_path, home)
    r = _post(_app(tmp_path, home=home, fs=fs))
    assert r["ok"] is True and r["empty"] is True


def test_endpoint_full_chain_card_then_accept_executes(tmp_path):
    """端到端(零 LLM):扫描 → 出卡进待决表 → ACCEPT → 真执行 + 金丝雀不动。"""
    home = _mk_home(tmp_path)
    fs = _grants(tmp_path, home)
    app = _app(tmp_path, home=home, fs=fs)
    r = _post(app)
    assert r["ok"] is True and r["empty"] is False and r["moves"] > 0
    cards = [p for p in app.state.proposal_registry.pending()
             if getattr(p, "kind", "") == KIND_BUTLER_PLAN]
    assert len(cards) == 1 and cards[0].proposal_id == r["proposal_id"]
    assert KIND_BUTLER_PLAN in app.state.proposal_handlers, "handler 没随出卡注入"
    res = app.state.proposal_registry.decide(
        r["proposal_id"], "ACCEPT", handlers=app.state.proposal_handlers)
    assert res is not None and res.ok, f"兑现失败: {res and res.detail}"
    assert (home / "Downloads" / "Documents" / "report.pdf").exists()
    assert (home / "Documents" / "canary.txt").exists()
    assert (tmp_path / "journal.json").exists()
    # 卡兑现后离开待决表
    assert not [p for p in app.state.proposal_registry.pending()
                if getattr(p, "kind", "") == KIND_BUTLER_PLAN]


def test_endpoint_reject_moves_nothing(tmp_path):
    home = _mk_home(tmp_path)
    fs = _grants(tmp_path, home)
    app = _app(tmp_path, home=home, fs=fs)
    r = _post(app)
    res = app.state.proposal_registry.decide(
        r["proposal_id"], "REJECT", handlers=app.state.proposal_handlers)
    assert res.ok and res.detail == "rejected"
    assert (home / "Downloads" / "report.pdf").exists(), "REJECT(只看看不动)必须一字不动"
    assert not (home / "Downloads" / "Documents").exists()
    assert not (tmp_path / "journal.json").exists()


def test_endpoint_rescan_replaces_stale_card(tmp_path):
    home = _mk_home(tmp_path)
    fs = _grants(tmp_path, home)
    app = _app(tmp_path, home=home, fs=fs)
    r1 = _post(app)
    (home / "Downloads" / "newfile.zip").write_text("z", encoding="utf-8")
    r2 = _post(app)
    assert r2["proposal_id"] != r1["proposal_id"]
    cards = [p for p in app.state.proposal_registry.pending()
             if getattr(p, "kind", "") == KIND_BUTLER_PLAN]
    assert len(cards) == 1 and cards[0].proposal_id == r2["proposal_id"], \
        "重扫后旧方案卡必须撤(不留双卡打架)"


def test_endpoint_mode_follows_intake_answer(tmp_path):
    from karvyloop.cognition.belief_store import BeliefStore
    from karvyloop.cognition.memory import MemoryManager
    from karvyloop.onboarding_intake import seed_answers
    home = _mk_home(tmp_path)
    fs = _grants(tmp_path, home)
    mem = MemoryManager(store=BeliefStore(tmp_path / "beliefs.json"))
    seed_answers({"filing": "by_time"}, mem=mem, locale="zh")
    app = _app(tmp_path, home=home, fs=fs, memory=mem)
    r = _post(app)
    assert r["mode"] == "by_time", "采集器答案没有改变第一课行为(咬合断了)"


# ---- 6. 前端接线(静态断言) ----

def test_frontend_wired_to_first_lesson():
    app_js = (STATIC / "app.js").read_text(encoding="utf-8")
    assert "/api/butler/first_lesson" in app_js, "前端没接第一课端点"
    assert "_butlerOfferFirstLesson" in app_js, "引荐 ACCEPT 后没递第一任务 chip"
    assert 'kind === "resident_referral"' in app_js.replace("d.kind", "kind"), \
        "第一课入口没挂在引荐兑现回执上"
    assert '"butler_plan"' in app_js, "方案卡没有专属渲染分支"
    assert "_spotlightEl(notice)" in app_js, "第一课入口没接聚光蒙版待遇"
