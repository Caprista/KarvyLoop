"""external_runtime 探活测试:doctor 式确定性(bin/key 源/冒烟锚),缺失=诚实报不可用。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from karvyloop.external_runtime import DriveRecipe, ParseSpec, probe  # noqa: E402
from karvyloop.external_runtime.recipe import PARSE_RAW_TEXT  # noqa: E402


def _fake_runner(*, stdout="READY", returncode=0):
    def run(argv, *, env, timeout, cwd):
        class P:
            pass
        P.returncode = returncode
        P.stdout = stdout
        P.stderr = ""
        return P()
    return run


def _recipe(bin_path, *, key_path="", smoke_anchor="READY"):
    return DriveRecipe(runtime_kind="rt", bin_path=bin_path,
                       argv_template=("-z", "{prompt}"),
                       parse=ParseSpec(mode=PARSE_RAW_TEXT),
                       key_source_path=key_path, smoke_anchor=smoke_anchor)


def test_probe_bad_bin_is_unreachable():
    r = _recipe("/nonexistent/bin/does-not-exist-xyz")
    res = probe(r, runner=_fake_runner())
    assert res.status == "unreachable" and "找不到" in res.reason


def test_probe_missing_key_source_unreachable_without_reading(tmp_path):
    # 用一个真实存在的可执行文件(python 本身)让 bin 检查过,再验 key 源缺失被拦
    py = sys.executable
    r = _recipe(py, key_path=str(tmp_path / "no_such_config.json"))
    res = probe(r, runner=_fake_runner())
    assert res.status == "unreachable" and "key 源缺失" in res.reason
    # 诚实:reason 里不含任何文件内容(本就不存在,但确认不读内容语义)
    assert "不读内容" in res.reason


def test_probe_smoke_hit_active():
    py = sys.executable
    r = _recipe(py)
    res = probe(r, runner=_fake_runner(stdout="READY here"))
    assert res.status == "active"
    assert res.manifest_hash and res.capability_card.get("smoke_ok") is True


def test_probe_smoke_miss_anchor_unreachable():
    py = sys.executable
    r = _recipe(py, smoke_anchor="READY")
    res = probe(r, runner=_fake_runner(stdout="something else entirely"))
    assert res.status == "unreachable"


def test_probe_no_smoke_static_only_active():
    py = sys.executable
    r = _recipe(py)
    res = probe(r, smoke=False)  # 只静态检查(bin/key),不冒烟
    assert res.status == "active" and res.capability_card.get("smoke_ok") is None
