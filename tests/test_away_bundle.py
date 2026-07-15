"""test_away_bundle — karvy.chat 接入页 bundle 的**可复现构建**闸门(docs/43 P2 签名收尾)。

签名半步从"人工可比对"升级为"机器常态守着":CI(pytest 三腿)每次**从已提交的 static/
源重新构建** away bundle,断言它与已提交的发布态 static/away-dist 逐字节一致 + SRI 真锁内容。
任何人改了 static/*.js 却没重跑构建更新 away-dist → 这里当场红(committed 源 ⇄ 发布产物脱钩)。

三层断言(全离线、确定性):
1. 复现一致:重构建 → 每文件 sha256 == 已提交 away-dist/MANIFEST.sha256;
2. SRI 真锁:index.html 里每个 integrity 的 sha384 == 它引用的那个 static/*.js 的真哈希;
3. 无绝对引用:index.html 不含 /static/(相对引用,配 CSP script-src 'self')。

另附**可选**上线比对(env KARVYLOOP_VERIFY_DEPLOYED=1 才跑,涉外网):拉 https://karvy.chat/
MANIFEST.sha256 比对已提交 MANIFEST——不一致=线上与 main 脱钩(该重新部署)。默认跳过(CI 无网)。
"""
from __future__ import annotations

import base64
import hashlib
import pathlib

import pytest

from karvyloop.console import build_away_bundle as bab

DIST = pathlib.Path(bab.__file__).resolve().parent / "static" / "away-dist"


def _committed_manifest() -> dict:
    out = {}
    for line in (DIST / "MANIFEST.sha256").read_text(encoding="utf-8").splitlines():
        if line.strip():
            h, name = line.split("  ", 1)
            out[name] = h
    return out


def test_reproducible_build_matches_committed(tmp_path):
    """从已提交 static/ 重构建 → 每文件哈希与已提交 away-dist/MANIFEST 一致(源⇄产物不脱钩)。"""
    rebuilt = bab.build(out_dir=tmp_path)
    committed = _committed_manifest()
    assert rebuilt == committed, (
        "away bundle 与已提交 away-dist 不一致 —— 改了 static/*.js 或 away.html 却没重跑 "
        "`python -m karvyloop.console.build_away_bundle`?差异: "
        + ", ".join(f"{k}:{rebuilt.get(k)}!={committed.get(k)}"
                    for k in set(rebuilt) | set(committed) if rebuilt.get(k) != committed.get(k)))


def test_sri_integrity_locks_real_bytes():
    """index.html 里每个 integrity 的 sha384 == 它引用的 static/*.js 真哈希(换包浏览器拒载)。"""
    import re
    html = (DIST / "index.html").read_text(encoding="utf-8")
    static = pathlib.Path(bab.__file__).resolve().parent / "static"
    pairs = re.findall(r'<script src="\./([\w.]+)" integrity="(sha384-[^"]+)"', html)
    assert len(pairs) == len(bab.JS_FILES), f"SRI 脚本标签数不对: {pairs}"
    for name, integ in pairs:
        # LF 归一后再算(同 build:autocrlf 在 Windows 检出把 .js 变 CRLF,raw 算会与
        # index.html 里基于 LF 的 integrity 不符 —— CI Windows 腿实捕)。SRI 锁的是**上线的
        # LF 字节**(bundle 里的 .js 已归一),测试要对同一基准算。
        data = (static / name).read_bytes().replace(b"\r\n", b"\n")
        real = "sha384-" + base64.b64encode(hashlib.sha384(data).digest()).decode("ascii")
        assert integ == real, f"{name} 的 SRI 与真字节不符(锁失效)"


def test_no_absolute_static_refs():
    html = (DIST / "index.html").read_text(encoding="utf-8")
    assert "/static/" not in html, "index.html 仍有绝对 /static/ 引用(karvy.chat 上会 404)"
    assert 'src="./' in html, "脚本应为相对引用(配 CSP script-src 'self')"


def test_deployed_matches_committed():
    """可选:拉 karvy.chat 线上 MANIFEST 比对已提交(env 门,CI 无网默认跳过)。"""
    import os
    if os.environ.get("KARVYLOOP_VERIFY_DEPLOYED") != "1":
        pytest.skip("上线比对要显式 KARVYLOOP_VERIFY_DEPLOYED=1(涉外网)")
    import urllib.request
    with urllib.request.urlopen("https://karvy.chat/MANIFEST.sha256", timeout=20) as r:
        live = {}
        for line in r.read().decode("utf-8").splitlines():
            if line.strip():
                h, name = line.split("  ", 1)
                live[name] = h
    assert live == _committed_manifest(), "线上 karvy.chat 与已提交 MANIFEST 脱钩 —— 该重新部署"
