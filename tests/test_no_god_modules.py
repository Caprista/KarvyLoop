"""Single-file line-count gate — 防"上帝模块"复发。

背景:`console/routes.py` 曾被拆过一次(抽出 workflow/distill/roundtable 引擎),但之后
被 ~30 个 feature commit 重新堆回 5000+ 行。再拆一次治标不治本 —— 真正的长期价值是**配一道
CI 门**:任何单文件 .py 超过阈值就红,让"上帝模块"刚开始长回来时就被拦住,不用等到又攒成 5000 行
才有人翻代码发现。

阈值 = 2000 行(拆完后最大文件远低于此,留足头寸)。若某文件确有暂时无法拆的理由,把它加进
`_WHITELIST` 并**写清理由 + 计划**——白名单是"欠债登记表"不是"免罪符",评审时要能看到为什么。

这道门只数行数,不碰任何运行时行为——纯 dev-infra,不违"少脚手架多信模型"(见 docs/58 §4)。
"""
from __future__ import annotations

import pathlib

# 单文件行数上限。拆分后最大的领域文件应显著低于此;设 2000 留头寸,
# 让"再往大文件里堆一层"这个 god-module 复发机制在攒到 ~2000 行时就被 CI 拦下。
MAX_LINES = 2000

# 暂时豁免的文件(相对 karvyloop/ 包根)。每条**必须**带理由 + 计划,否则等于放任复发。
# 格式:相对路径 -> 理由。空 = 目前无豁免(拆分后 routes.py 已降到阈值内)。
_WHITELIST: dict[str, str] = {
    # 例:"foo/bar.py": "生成代码/第三方 vendored,拆分无意义;计划:随 xxx 一并替换",
}

_PKG_ROOT = pathlib.Path(__file__).resolve().parent.parent / "karvyloop"


def _iter_py_files():
    for p in _PKG_ROOT.rglob("*.py"):
        if "__pycache__" in p.parts:
            continue
        yield p


def _count_lines(path: pathlib.Path) -> int:
    # 与 `wc -l` 语义一致:数换行符(尾行无换行也算,按 splitlines 计)。
    text = path.read_text(encoding="utf-8", errors="replace")
    return len(text.splitlines())


def test_no_god_modules() -> None:
    """karvyloop/ 下无单文件 .py 超过 MAX_LINES(白名单除外)。"""
    offenders: list[str] = []
    for path in _iter_py_files():
        rel = path.relative_to(_PKG_ROOT).as_posix()
        n = _count_lines(path)
        if n <= MAX_LINES:
            continue
        if rel in _WHITELIST:
            continue
        offenders.append(f"{rel}: {n} 行 (> {MAX_LINES})")

    assert not offenders, (
        "发现超过单文件行数上限的模块(god-module 复发风险)。请按领域拆分,"
        f"或若确有理由暂时无法拆分,把它加进 tests/test_no_god_modules.py 的 _WHITELIST "
        f"并写清理由 + 计划。超限文件:\n  " + "\n  ".join(sorted(offenders))
    )


def test_whitelist_entries_still_oversized() -> None:
    """白名单卫生:白名单里的文件若已降回阈值内,应从名单移除(别让白名单变成僵尸豁免)。"""
    stale: list[str] = []
    for rel in _WHITELIST:
        path = _PKG_ROOT / rel
        if not path.exists():
            stale.append(f"{rel}: 文件不存在(已删/改名?),从 _WHITELIST 移除")
            continue
        if _count_lines(path) <= MAX_LINES:
            stale.append(f"{rel}: 已降到 {MAX_LINES} 行以内,从 _WHITELIST 移除(债已还)")
    assert not stale, "白名单有僵尸条目,请清理:\n  " + "\n  ".join(stale)
