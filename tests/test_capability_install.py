"""test_capability_install — 「能力解锁」一键启用(Hardy 2026-07-09:app 替用户装,别让人自己找门)。

锁四件事:
1. INSTALLABLE 是 pyproject extras 的**忠实镜像**(改了 pyproject 忘了这里 → 立刻红,防漂移)。
2. OCR 进了能力解锁清单(报销识别的图片腿可被发现)。
3. start_install:未知 id 拒;已知 id 起安装 + 写 running 状态;注入 runner 可跑到 done。
4. enable 端点安全门:缺 CSRF 头拒 / 公网来源拒(装东西是控自己机器的事,同一键升级)。
"""
from __future__ import annotations

import tomllib
import types
from pathlib import Path

import karvyloop.console.capability_install as ci
from karvyloop.console.capability_install import INSTALLABLE, read_status, start_install
from karvyloop.console.unlocks import list_unlocks


# ---- 1. INSTALLABLE ↔ pyproject 防漂移 ----
def test_installable_mirrors_pyproject():
    pp = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text(encoding="utf-8"))
    extras = pp["project"]["optional-dependencies"]
    for cid, pkgs in INSTALLABLE.items():
        assert cid in extras, f"INSTALLABLE 有 {cid} 但 pyproject 没这个 extra"
        assert pkgs == extras[cid], f"{cid} 与 pyproject 漂移: map={pkgs} pyproject={extras[cid]}"


# ---- 2. OCR 可被发现 ----
def test_ocr_in_unlock_list():
    ids = {u["id"] for u in list_unlocks("")}
    assert "ocr" in ids, "图片/票据 OCR 必须出现在能力解锁清单(否则用户不知道有这回事)"


# ---- 3. start_install 行为 ----
def test_start_install_rejects_unknown():
    out = start_install("definitely-not-a-capability")
    assert out["ok"] is False and "不是可一键安装" in out["reason"]


def test_start_install_runs_and_records_status(tmp_path, monkeypatch):
    monkeypatch.setattr(ci, "_KL", tmp_path)     # 状态文件写进 tmp(_KL 是模块级,直接改属性)
    seen = {}

    def fake_runner(cap_id, packages, python):
        seen["packages"] = packages
        ci._write(cap_id, {"state": "done", "rc": 0, "extra_step": ""})

    out = start_install("ocr", runner=fake_runner)
    assert out["ok"] is True and out["started"] is True
    assert seen["packages"] == INSTALLABLE["ocr"], "必须装 ocr 的底层包"
    st = read_status("ocr")
    assert st["state"] == "done" and st["id"] == "ocr", "装完状态可读到 done"


# ---- 4. enable 端点安全门(同一键升级)----
def _req(client_host: str, *, header: str | None = "1"):
    headers = {"x-karvyloop-upgrade": header} if header is not None else {}
    return types.SimpleNamespace(
        client=types.SimpleNamespace(host=client_host),
        headers=types.SimpleNamespace(get=lambda k, d=None: headers.get(k.lower(), d)),
        app=types.SimpleNamespace(state=types.SimpleNamespace()))


def test_enable_rejects_missing_csrf_and_public_origin():
    from karvyloop.console.routes import CapabilityEnableRequest, api_capability_enable
    body = CapabilityEnableRequest(id="ocr")
    # 缺 CSRF 头 → 拒,不触发安装
    out = api_capability_enable(body, _req("127.0.0.1", header=None))
    assert out["ok"] is False and "CSRF" in out["reason"]
    # 公网来源 → 拒
    out2 = api_capability_enable(body, _req("8.8.8.8"))
    assert out2["ok"] is False and "局域网" in out2["reason"]
