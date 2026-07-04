"""test_file_extract — 附件真解析:PDF / docx / xlsx → 文本,走 CSV 同一条产线。

背景:files 桥(面板预览 + 「让TA分析」交办 + read_file 工具)此前只对 CSV/纯文本"真解析",
PDF 等二进制文档会被 `decode(errors="replace")` 成乱码灌进上下文。本文件锁四层:

1. **解析层**(karvyloop/file_extract.py):PDF 两页真文本、docx 段落+表格、xlsx 按行铺开;
   截断有上限并明示(truncated)。
2. **宁空勿毒**(仓内解析器硬纪律):截断的 PDF / 伪造扩展名 / zip 垃圾 → 空文本 + 明确
   错误码,绝不把二进制垃圾灌进上下文;**这些测试不依赖可选包**(magic 防线在 import 之前)。
3. **优雅降级**:没装 pypdf/python-docx/openpyxl → error="missing_dependency" + 明确的
   `pip install "karvyloop[files]"` 提示,不崩(用 sys.modules[pkg]=None 强制 ImportError,
   装没装都能测)。
4. **产线缝合**:/api/files/view 预览走同一条截断线;ReadTool(真沙箱)读 PDF 出真文本
   (行号产线与 CSV 一致);recall 对 "分析 xxx.pdf" 意图真命中 data-analyst。

fixture:tests/fixtures/two_page.pdf 是手工构造的最小两页 PDF(页1/页2 各一条
"KarvyLoop fixture page …: revenue …" 文本流,Helvetica,xref 偏移精确),约 1KB。
可选依赖测试统一 pytest.importorskip,没装则跳过,不报 collection error。
"""
from __future__ import annotations

import io
import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.file_extract import extract_kind, extract_text  # noqa: E402

FIXTURES = pathlib.Path(__file__).resolve().parent / "fixtures"
PDF_FIXTURE = FIXTURES / "two_page.pdf"


# ---------------------------------------------------------------- 1. 解析层

def test_extract_kind_mapping():
    assert extract_kind("report.pdf") == "pdf"
    assert extract_kind("REPORT.PDF") == "pdf"      # 后缀大小写不敏感
    assert extract_kind("doc.docx") == "docx"
    assert extract_kind("sheet.xlsx") == "xlsx"
    # CSV/文本**不在**解析表里:它们本来就是文本,走原有 decode 产线
    for name in ("data.csv", "notes.txt", "readme.md", "noext", "a.doc", "a.xls"):
        assert extract_kind(name) is None, name


def test_pdf_two_pages_extracted():
    pytest.importorskip("pypdf")
    r = extract_text(PDF_FIXTURE.read_bytes(), "pdf")
    assert r.ok and r.kind == "pdf" and not r.truncated
    assert "KarvyLoop fixture page one" in r.text and "2026-01" in r.text
    assert "KarvyLoop fixture page two" in r.text and "2026-02" in r.text
    assert "--- page 1 ---" in r.text and "--- page 2 ---" in r.text  # 页标记(分析可引用页码)


def test_pdf_truncation_capped_and_flagged():
    pytest.importorskip("pypdf")
    r = extract_text(PDF_FIXTURE.read_bytes(), "pdf", max_chars=40)
    assert r.ok and r.truncated and len(r.text) <= 40


# ------------------------------------------------- 2. 宁空勿毒(无依赖也必须过)

def test_truncated_pdf_yields_empty_not_garbage():
    """截断的 PDF(头还在、结构断了)→ 空文本 + 明确错误,不吐半解析垃圾。
    头部过 magic 后才走解析库,故无 pypdf 的机器上错误是 missing_dependency —— 两种都算"拒了"。"""
    broken = PDF_FIXTURE.read_bytes()[:120]
    r = extract_text(broken, "pdf")
    assert r.text == ""            # 宁空
    assert not r.ok and r.error in ("bad_file", "missing_dependency")


def test_fake_extension_rejected_by_magic():
    """伪造扩展名:文本冒充 .pdf、垃圾冒充 .docx/.xlsx → magic 防线直接拒,不碰解析库。"""
    r = extract_text(b"month,revenue\n2026-01,100\n", "pdf")
    assert not r.ok and r.error == "bad_file" and r.text == ""
    for kind in ("docx", "xlsx"):
        r = extract_text(b"this is not a zip container", kind)
        assert not r.ok and r.error == "bad_file" and r.text == "", kind


def test_zip_garbage_docx_rejected():
    """过了 magic(PK 头)但内容是垃圾的 .docx → 解析异常收敛成 bad_file,空文本。"""
    pytest.importorskip("docx")
    r = extract_text(b"PK\x03\x04" + b"\xde\xad\xbe\xef" * 32, "docx")
    assert not r.ok and r.error == "bad_file" and r.text == ""


# --------------------------------------------- 3. 缺依赖优雅降级(装没装都能测)

@pytest.mark.parametrize("kind,pkg,magic", [
    ("pdf", "pypdf", b"%PDF-1.4 stub"),
    ("docx", "docx", b"PK\x03\x04 stub"),
    ("xlsx", "openpyxl", b"PK\x03\x04 stub"),
])
def test_missing_dependency_clear_error(monkeypatch, kind, pkg, magic):
    monkeypatch.setitem(sys.modules, pkg, None)   # 强制 import 失败(装了也一样测)
    r = extract_text(magic, kind)
    assert not r.ok and r.error == "missing_dependency" and r.text == ""
    assert "karvyloop[files]" in r.hint


# ------------------------------------------------------- 1b. docx / xlsx 真往返

def test_docx_paragraphs_and_table():
    docx = pytest.importorskip("docx")
    d = docx.Document()
    d.add_paragraph("季度营收纪要 Q2 revenue memo")
    tbl = d.add_table(rows=2, cols=2)
    tbl.rows[0].cells[0].text = "month"; tbl.rows[0].cells[1].text = "revenue"
    tbl.rows[1].cells[0].text = "2026-04"; tbl.rows[1].cells[1].text = "300"
    buf = io.BytesIO(); d.save(buf)
    r = extract_text(buf.getvalue(), "docx")
    assert r.ok and "Q2 revenue memo" in r.text
    assert "month\trevenue" in r.text and "2026-04\t300" in r.text  # 表格 → 制表符行


def test_xlsx_rows_as_csv_shape():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook(); ws = wb.active; ws.title = "Sales"
    for row in (("month", "revenue"), ("2026-01", 100), ("2026-02", 150)):
        ws.append(row)
    buf = io.BytesIO(); wb.save(buf)
    r = extract_text(buf.getvalue(), "xlsx")
    assert r.ok and "# sheet: Sales" in r.text
    assert "month,revenue" in r.text and "2026-01,100" in r.text  # 行铺成 CSV 形状


def test_xlsx_truncation_flagged():
    openpyxl = pytest.importorskip("openpyxl")
    wb = openpyxl.Workbook(); ws = wb.active
    for i in range(200):
        ws.append((f"2026-{i:03d}", i))
    buf = io.BytesIO(); wb.save(buf)
    r = extract_text(buf.getvalue(), "xlsx", max_chars=100)
    assert r.ok and r.truncated and len(r.text) <= 100


# ------------------------------------------ 4a. /api/files/view 走同一条预览产线

def _client(workspace):
    from fastapi.testclient import TestClient
    from karvyloop.console import build_console_app
    from karvyloop.karvy.observer import WorkbenchObserver
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None,
                            runtime_kwargs={"workspace_root": str(workspace)})
    return TestClient(app)


def test_view_pdf_extracts_text(tmp_path):
    pytest.importorskip("pypdf")
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "report.pdf").write_bytes(PDF_FIXTURE.read_bytes())
    v = _client(ws).get("/api/files/view", params={"path": "report.pdf"}).json()
    assert v["ok"] is True and v.get("extract") == "pdf"
    assert "KarvyLoop fixture page one" in v["text"] and "page two" in v["text"]
    assert "binary" not in v   # 不再是"二进制,下载来看"


def test_view_fake_pdf_refused_no_garbage(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "evil.pdf").write_bytes(b"\x00\x01\x02 not a pdf at all")
    v = _client(ws).get("/api/files/view", params={"path": "evil.pdf"}).json()
    assert v["ok"] is True and v.get("binary") is True          # 旧前端兼容:仍提示下载
    assert v.get("extract_error") == "bad_file" and "text" not in v  # 新前端:明确坏文件,零垃圾


def test_view_pdf_missing_dep_honest_hint(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", None)
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "report.pdf").write_bytes(PDF_FIXTURE.read_bytes())
    v = _client(ws).get("/api/files/view", params={"path": "report.pdf"}).json()
    assert v.get("extract_error") == "missing_dependency" and "karvyloop[files]" in v.get("hint", "")


def test_view_csv_path_unchanged(tmp_path):
    """CSV 原产线不动:纯文本直出,无 extract 字段(防平行管线/回归)。"""
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "data.csv").write_text("month,revenue\n2026-01,100\n", encoding="utf-8")
    v = _client(ws).get("/api/files/view", params={"path": "data.csv"}).json()
    assert v["ok"] is True and v["text"].startswith("month,revenue") and "extract" not in v


# ----------------------------------------- 4b. ReadTool(真沙箱)= agent 分析入口

def _read_tool(ws):
    """真 ReadTool + 真沙箱(read_file 纯 Python,全平台可测真语义)—— 同 CSV 缝合测。"""
    from karvyloop.capability.token import mint
    from karvyloop.coding.filestate import FileState
    from karvyloop.coding.tools.read import ReadTool
    from karvyloop.platform.linux.bubblewrap import BubblewrapSandbox
    from karvyloop.schemas import Capability
    tok = mint("t-file-extract", [Capability(resource=f"fs:{ws}", ops=["read", "write"])])
    return ReadTool(BubblewrapSandbox(), FileState(), str(ws), token=tok)


async def test_read_tool_pdf_real_text_not_mojibake(tmp_path):
    pytest.importorskip("pypdf")
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "report.pdf").write_bytes(PDF_FIXTURE.read_bytes())
    res = await _read_tool(ws)({"file_path": str(ws / "report.pdf").replace("\\", "/")})
    assert res.ok, res.error_message
    payload = str(res.payload)
    assert "KarvyLoop fixture page one" in payload and "2026-02 150" in payload
    assert "\t" in payload and payload.lstrip().startswith("1")   # CSV 同款行号产线
    assert "%PDF" not in payload and "endobj" not in payload      # 乱码时代的痕迹不得再现


async def test_read_tool_fake_pdf_fail_loud(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "evil.pdf").write_bytes(b"\xff\xfe binary junk pretending to be pdf")
    res = await _read_tool(ws)({"file_path": str(ws / "evil.pdf").replace("\\", "/")})
    assert not res.ok and res.payload is None
    assert "无法解析" in (res.error_message or "")   # 明确报错,而不是把垃圾交给模型


async def test_read_tool_missing_dep_tells_how_to_fix(tmp_path, monkeypatch):
    monkeypatch.setitem(sys.modules, "pypdf", None)
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "report.pdf").write_bytes(PDF_FIXTURE.read_bytes())
    res = await _read_tool(ws)({"file_path": str(ws / "report.pdf").replace("\\", "/")})
    assert not res.ok and "karvyloop[files]" in (res.error_message or "")


async def test_read_tool_csv_path_unchanged(tmp_path):
    """CSV 经 ReadTool 的既有语义不动(行号 + 原文)—— 防这刀改坏老产线。"""
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "sales.csv").write_text("month,revenue\n2026-03,210\n", encoding="utf-8")
    res = await _read_tool(ws)({"file_path": str(ws / "sales.csv").replace("\\", "/")})
    assert res.ok and "month,revenue" in str(res.payload) and "2026-03,210" in str(res.payload)


# --------------------------------------------------- 4c. 召回:pdf/xlsx 意图 → data-analyst

def test_pdf_and_xlsx_intents_recall_data_analyst(tmp_path):
    from karvyloop.crystallize.recall import recall
    from karvyloop.crystallize.skill_index import SkillIndex
    idx = SkillIndex(); idx.rebuild_from_disk(tmp_path)
    for intent in ("帮我分析一下 report.pdf 这份数据",
                   "帮我分析一下 q2.xlsx 这份报表",
                   "analyze the numbers in summary.pdf"):
        hit = recall(intent, skills_dir=tmp_path, scope="user", skill_index=idx)
        assert hit is not None and hit.name == "data-analyst", \
            f"附件分析意图没召回 data-analyst: {intent!r} -> {hit and hit.name}"
