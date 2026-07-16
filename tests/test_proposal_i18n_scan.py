"""test_proposal_i18n_scan — 提案工厂 summary/basis 不得硬编码中文(源码扫描契约门)。

病根(2026-07-15 实锤):decision_wire.py 等提案工厂把整句中文 summary/basis 直接写进
Proposal,英文界面照吐中文(违双语纪律)。修法 = 模板走 `i18n.t("proposal.*", …)`
(en/zh 双表,zh 保持原文,出卡时按当前 locale 定稿);LLM 产出的动态文本是数据不受限。

本测试防复发(纯 Python AST,不 shell grep,三平台一致):
① 任何 `Proposal(...)` 调用的 `summary=` / `basis=` 实参表达式里不得出现含 CJK 的字符串
   字面量(含 f-string 的字面段);
② 任何 `summary = …` / `basis = …` 简单赋值(提案工厂常先攒变量再传参)同样不得含 CJK
   字面量。
豁免走 ALLOWLIST("相对路径::函数名";数据/非卡文案确需中文时加条目并写明理由)。
注:非 Proposal 调用的 summary= 关键字(如 decision_log.record)是台账数据,不在此门内。
"""
from __future__ import annotations

import ast
import pathlib

ROOT = pathlib.Path(__file__).resolve().parents[1]
PKG = ROOT / "karvyloop"

# 豁免名单:"相对路径::函数名(或 <module>)"。加条目必须注明理由。
ALLOWLIST: set[str] = set()

_FIELDS = {"summary", "basis"}


def _has_cjk(s: str) -> bool:
    return any("一" <= ch <= "鿿" for ch in s)


def _cjk_literals(node: ast.AST) -> list[str]:
    """收集表达式树里所有含 CJK 的字符串字面量(Constant + f-string 字面段)。"""
    out: list[str] = []
    for sub in ast.walk(node):
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str) and _has_cjk(sub.value):
            out.append(sub.value)
    return out


class _Scanner(ast.NodeVisitor):
    def __init__(self, relpath: str) -> None:
        self.relpath = relpath
        self.func_stack: list[str] = ["<module>"]
        self.violations: list[str] = []

    # -- 维护"当前在哪个函数里"(豁免颗粒度) --
    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.func_stack.append(node.name)
        self.generic_visit(node)
        self.func_stack.pop()

    visit_AsyncFunctionDef = visit_FunctionDef  # type: ignore[assignment]

    def _flag(self, field: str, lineno: int, samples: list[str]) -> None:
        where = f"{self.relpath}::{self.func_stack[-1]}"
        if where in ALLOWLIST:
            return
        gist = samples[0][:40].replace("\n", "\\n")
        self.violations.append(
            f"{self.relpath}:{lineno} [{self.func_stack[-1]}] {field}= 含中文字面量:「{gist}…」"
            f" —— 提案卡文案必须走 i18n.t(proposal.*)(en/zh 双表)"
        )

    # ① Proposal(...) 调用的 summary=/basis= 关键字
    def visit_Call(self, node: ast.Call) -> None:
        func = node.func
        name = func.id if isinstance(func, ast.Name) else (
            func.attr if isinstance(func, ast.Attribute) else "")
        if name == "Proposal":
            for kw in node.keywords:
                if kw.arg in _FIELDS:
                    bad = _cjk_literals(kw.value)
                    if bad:
                        self._flag(kw.arg, node.lineno, bad)
        self.generic_visit(node)

    # ② summary = … / basis = … 简单赋值(工厂常先攒变量再传参)
    def visit_Assign(self, node: ast.Assign) -> None:
        targets = [t.id for t in node.targets if isinstance(t, ast.Name)]
        hit = [t for t in targets if t in _FIELDS]
        if hit:
            bad = _cjk_literals(node.value)
            if bad:
                self._flag("/".join(hit), node.lineno, bad)
        self.generic_visit(node)


def _scan_file(path: pathlib.Path) -> list[str]:
    try:
        rel = path.relative_to(ROOT).as_posix()
    except ValueError:  # tmp 样本(扫描器自检)
        rel = path.name
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return []  # 语法坏交给别的门;本门不误伤
    sc = _Scanner(rel)
    sc.visit(tree)
    return sc.violations


def _all_py() -> list[pathlib.Path]:
    return [p for p in sorted(PKG.rglob("*.py")) if "__pycache__" not in p.parts]


def test_proposal_factories_have_no_hardcoded_cjk_summary_basis():
    violations: list[str] = []
    for p in _all_py():
        violations.extend(_scan_file(p))
    assert not violations, (
        "提案工厂 summary/basis 出现硬编码中文(英文界面会漏中文)。"
        "模板请改走 i18n.t + en/zh 双表;确属数据/豁免请进 ALLOWLIST 并注明理由:\n"
        + "\n".join(f"  - {v}" for v in violations)
    )


def test_scanner_catches_seeded_violation(tmp_path):
    """扫描器自检:真种一个违规样本必须被抓到(防扫描器静默失效 = 假绿)。"""
    sample = tmp_path / "seed.py"
    sample.write_text(
        "def f():\n"
        "    summary = f\"记成你的默认偏好吗?{x}\"\n"
        "    return Proposal(summary=summary, basis=\"中文依据\")\n",
        encoding="utf-8",
    )
    got = _scan_file(sample)
    # 赋值 + 关键字两条路径都要能抓
    joined = "\n".join(got)
    assert "summary" in joined and "basis" in joined and len(got) >= 2


# ---- 接线抽查:同一工厂 en/zh 双态出对话言(锁 i18n 真被调用,不是表建了没接) ----

def test_route_proposal_localizes_en_zh():
    from karvyloop import i18n
    from karvyloop.karvy.proposal_registry import proposal_for_route
    kw = dict(domain_id="d1", role="写手", agent_id="a1", domain_name="工作室",
              requirement="写周报", ts=1.0)
    try:
        i18n.set_locale("en")
        p_en = proposal_for_route(**kw)
        assert "Hand" in p_en.summary and "写周报" in p_en.summary
        i18n.set_locale("zh")
        p_zh = proposal_for_route(**kw)
        assert "转给业务域" in p_zh.summary and "写周报" in p_zh.summary
    finally:
        i18n.set_locale(None)
