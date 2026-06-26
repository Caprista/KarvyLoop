"""test_cognition_domain_layer — 认知两层:域专属认知不跨域漏(docs/00 §2.6 ④)+ 删域清私有层(⑤原语)。"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.schemas.cognition import Belief  # noqa: E402


def _b(content, domain=None):
    prov = {"source": "test", "kind": "fact"}
    if domain is not None:
        prov["applies"] = {"domain": domain, "role": "group"}
    return Belief(content=content, provenance=prov, freshness_ts=1.0, scope="personal")


def test_shared_belief_recalled_everywhere():
    mem = MemoryManager()
    mem.write(_b("共享事实"))               # 无 applies = 通用/共享层
    assert "共享事实" in mem.recall_block("共享事实", domain="")
    assert "共享事实" in mem.recall_block("共享事实", domain="legal")


def test_private_belief_only_in_its_domain():
    mem = MemoryManager()
    mem.write(_b("legal机密", domain="legal"))
    assert "legal机密" in mem.recall_block("legal机密", domain="legal")    # 本域召得到
    assert "legal机密" not in mem.recall_block("legal机密", domain="sales")  # 别的域不漏
    assert "legal机密" not in mem.recall_block("legal机密", domain="")       # 私聊/l0 不漏


def test_domain_recall_is_shared_plus_own_private():
    mem = MemoryManager()
    mem.write(_b("共享层"))
    mem.write(_b("legal私有", domain="legal"))
    mem.write(_b("sales私有", domain="sales"))
    blk = mem.recall_block("共享层 legal私有 sales私有", domain="legal")
    assert "共享层" in blk and "legal私有" in blk      # 共享 + 本域私有
    assert "sales私有" not in blk                       # 别域私有不漏(A 域机密不到 B)


def test_purge_domain_removes_only_that_domains_private():
    mem = MemoryManager()
    mem.write(_b("共享层"))
    mem.write(_b("legal私有", domain="legal"))
    mem.write(_b("legal私有2", domain="legal"))
    mem.write(_b("sales私有", domain="sales"))
    n = mem.purge_domain("legal")
    assert n == 2
    remaining = [b.content for b in mem.index.all("personal")]
    assert "共享层" in remaining and "sales私有" in remaining   # 共享层+别域留着(角色回公共库)
    assert "legal私有" not in remaining and "legal私有2" not in remaining   # 该域私有随域删


def test_purge_empty_domain_noop():
    mem = MemoryManager()
    mem.write(_b("x"))
    assert mem.purge_domain("") == 0
    assert len(list(mem.index.all("personal"))) == 1
