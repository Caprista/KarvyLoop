"""test_suite_self_contained — 元自检:测试套件不得依赖内部 docs/ 在场。

Hardy 原则:测试是**代码完整度 / 工程可用性**的保障(也是后续应用自检修复的基础),
不是 doc-lint。开源/打包发团队时不会带内部 docs/,测试必须照样能全绿。

本守卫扫描 tests/ 源码,禁止任何测试**读取仓库根的 docs/ 文件**(ROOT/parents[n] / "docs")。
临时夹具 `tmp_path / "docs"` 不在禁止之列(那是自包含的,不碰仓库)。
"""
from __future__ import annotations

import pathlib
import re

_TESTS_DIR = pathlib.Path(__file__).resolve().parent
# 仓库根锚定的 docs 路径:ROOT / "docs"、parents[1] / "docs"、REPO/"docs" 等
_REPO_DOCS = re.compile(r"""(ROOT|REPO|REPO_ROOT|parents\[\d+\])\s*/\s*["']docs["']""")


def test_no_test_reads_repo_docs():
    offenders = []
    for f in _TESTS_DIR.glob("test_*.py"):
        if f.name == pathlib.Path(__file__).name:
            continue
        text = f.read_text(encoding="utf-8", errors="replace")
        if _REPO_DOCS.search(text):
            offenders.append(f.name)
    assert not offenders, (
        "这些测试依赖仓库 docs/ 内容,违反'测试不依赖内部文档'原则(开源/打包后会崩):"
        f"{offenders}。请改成自包含(测代码/夹具),文档审查另走文档审计。"
    )
