"""test_relay_e2e_interop — 浏览器端 E2E(static/e2e.js)与 Python 端(relay/e2e.py)的
**跨实现字节级互操作**锁。

协议 v1 冻结:任何一侧改一个字节(帧头/HKDF info/nonce 方向常量/seq 编码)都会在这里红。
流程:Python 生成定死密钥的向量(全确定,无随机)→ node 加载真构建产物逐字节复现 +
反向互开 + 安全性质(重放/篡改/错指纹拒)。node/构建产物缺 → skip(CI 前端腿保证在)。
"""
from __future__ import annotations

import pathlib
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "karvyloop" / "console" / "frontend"
STATIC_E2E = ROOT / "karvyloop" / "console" / "static" / "e2e.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node 不在 PATH(前端互操作检查需要)")
def test_js_python_e2e_byte_level_interop(tmp_path):
    assert STATIC_E2E.is_file(), "static/e2e.js 构建产物缺失(node scripts/build.mjs)"
    vec = tmp_path / "e2e_vectors.json"
    gen = subprocess.run(
        [sys.executable, str(FRONTEND / "scripts" / "gen_e2e_vectors.py"), str(vec)],
        capture_output=True, text=True, timeout=120)
    assert gen.returncode == 0, f"向量生成失败: {gen.stderr[-500:]}"
    chk = subprocess.run(
        ["node", str(FRONTEND / "scripts" / "e2e_interop_check.mjs"), str(vec)],
        capture_output=True, text=True, timeout=120)
    assert chk.returncode == 0, f"互操作检查红:\n{chk.stdout[-1200:]}\n{chk.stderr[-500:]}"
    assert "INTEROP PASS" in chk.stdout
