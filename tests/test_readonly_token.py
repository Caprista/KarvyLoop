"""test_readonly_token — 独立验收者只读硬化(docs/00 §0.6 安全地基)。

此前 checker 只砍 write/edit 工具、bash 仍能写=loophole。修:read_only_token 在能力层去掉 fs 写
→ mounts_from_token 把工作区算进 ro → bubblewrap --ro-bind → bash 也写不动。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.sandbox.mounts import mounts_from_token, read_only_token  # noqa: E402
from karvyloop.schemas import CapabilityToken  # noqa: E402
from karvyloop.schemas.capability import Capability  # noqa: E402


def _tok(grants):
    return CapabilityToken(task_id="t", grants=grants, expiry=9e9)


def test_strips_fs_write():
    t = _tok([Capability(resource="fs:/ws", ops=["read", "write"])])
    ro = read_only_token(t)
    assert ro.grants[0].ops == ["read"]                 # write 去掉


def test_empty_ops_becomes_read():
    # 空 ops 原本=通配可写 → 显式只读(否则 mounts 仍算 rw)
    t = _tok([Capability(resource="fs:/ws", ops=[])])
    assert read_only_token(t).grants[0].ops == ["read"]


def test_non_fs_grants_untouched():
    t = _tok([Capability(resource="exec:python", ops=["exec"]),
              Capability(resource="net:any", ops=["connect"])])
    ro = read_only_token(t)
    assert ro.grants[0].ops == ["exec"] and ro.grants[1].ops == ["connect"]  # exec/net 不动


def test_mounts_have_no_rw_after_readonly():
    t = _tok([Capability(resource="fs:/ws", ops=["read", "write"]),
              Capability(resource="fs:/ro", ops=["read"])])
    ro_paths, rw_paths = mounts_from_token(read_only_token(t))
    assert rw_paths == []                               # 没有可写挂载了 → bash 写不动
    assert "/ws" in ro_paths and "/ro" in ro_paths      # 都成只读


def test_original_token_unchanged():
    t = _tok([Capability(resource="fs:/ws", ops=["read", "write"])])
    read_only_token(t)
    assert "write" in t.grants[0].ops                   # 派生不改原 token
