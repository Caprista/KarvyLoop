"""test_console_files — workspace 文件管理:列/看/下载 + **路径越狱必须拒**(安全核心)。

锁:钉死在 workspace 根;`../` / 绝对路径逃逸一律拒(否则能下 /etc/passwd 或 ~/.karvyloop 的 key)。
"""
from __future__ import annotations

import pathlib
import sys

from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


def _client(workspace=None):
    rk = {"workspace_root": str(workspace)} if workspace else {}
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None, runtime_kwargs=rk)
    return TestClient(app)


def _setup_ws(tmp_path):
    ws = tmp_path / "ws"
    (ws / "sub").mkdir(parents=True)
    (ws / "report.md").write_text("# 月度报表\nhello", encoding="utf-8")
    (ws / "sub" / "data.csv").write_text("a,b\n1,2", encoding="utf-8")
    (tmp_path / "SECRET.txt").write_text("api_key=DO-NOT-LEAK", encoding="utf-8")  # 在 ws 之外
    return ws


def test_list_root_and_subdir(tmp_path):
    ws = _setup_ws(tmp_path)
    c = _client(ws)
    r = c.get("/api/files/list").json()
    assert r["ok"] is True and r["path"] == ""
    names = {e["name"]: e["is_dir"] for e in r["entries"]}
    assert names == {"sub": True, "report.md": False}
    sub = c.get("/api/files/list", params={"path": "sub"}).json()
    assert sub["ok"] and {e["name"] for e in sub["entries"]} == {"data.csv"}


def test_view_and_download(tmp_path):
    ws = _setup_ws(tmp_path)
    c = _client(ws)
    v = c.get("/api/files/view", params={"path": "report.md"}).json()
    assert v["ok"] is True and "月度报表" in v["text"]
    d = c.get("/api/files/download", params={"path": "sub/data.csv"})
    assert d.status_code == 200 and d.text.replace("\r\n", "\n") == "a,b\n1,2"


def test_path_traversal_rejected(tmp_path):
    """越狱:绝不能读到 workspace 之外(SECRET.txt 在 ws 父目录)。"""
    ws = _setup_ws(tmp_path)
    c = _client(ws)
    for bad in ["../SECRET.txt", "../../etc/passwd", "sub/../../SECRET.txt"]:
        assert c.get("/api/files/view", params={"path": bad}).json()["ok"] is False
        assert c.get("/api/files/download", params={"path": bad}).status_code == 404
    assert c.get("/api/files/list", params={"path": ".."}).json()["ok"] is False


def test_upload_writes_into_workspace(tmp_path):
    ws = _setup_ws(tmp_path)
    c = _client(ws)
    r = c.post("/api/files/upload", params={"dir": "sub", "name": "uploaded.txt"}, content=b"hi from user")
    assert r.json()["ok"] is True and r.json()["name"] == "uploaded.txt"
    assert (ws / "sub" / "uploaded.txt").read_bytes() == b"hi from user"


def test_upload_rejects_traversal(tmp_path):
    """上传名/目录都不能逃出 workspace(否则能覆盖 ~/.karvyloop 的 config 或系统文件)。"""
    ws = _setup_ws(tmp_path)
    c = _client(ws)
    # name 含路径分隔 → 只取 basename(落在 ws 内,不逃逸)
    r = c.post("/api/files/upload", params={"dir": "", "name": "../evil.txt"}, content=b"x")
    assert r.json()["ok"] is True   # basename 化为 evil.txt
    assert (ws / "evil.txt").exists() and not (tmp_path / "evil.txt").exists()
    # dir 逃逸 → 拒
    r2 = c.post("/api/files/upload", params={"dir": "../..", "name": "evil.txt"}, content=b"x")
    assert r2.json()["ok"] is False


def test_delete_file_and_empty_dir(tmp_path):
    ws = _setup_ws(tmp_path)
    c = _client(ws)
    assert c.post("/api/files/delete", params={"path": "report.md"}).json()["ok"] is True
    assert not (ws / "report.md").exists()
    # 非空目录拒;清空后可删
    assert c.post("/api/files/delete", params={"path": "sub"}).json()["reason"] == "not_empty"
    (ws / "sub" / "data.csv").unlink()
    assert c.post("/api/files/delete", params={"path": "sub"}).json()["ok"] is True
    assert not (ws / "sub").exists()


def test_delete_rejects_root_and_traversal(tmp_path):
    ws = _setup_ws(tmp_path)
    c = _client(ws)
    assert c.post("/api/files/delete", params={"path": ""}).json()["ok"] is False   # 删根拒
    assert c.post("/api/files/delete", params={"path": "../SECRET.txt"}).json()["ok"] is False
    assert (tmp_path / "SECRET.txt").exists()   # 仓外文件没被删


def test_no_workspace_degrades(tmp_path):
    c = _client(None)   # 没接 workspace
    assert c.get("/api/files/list").json() == {"ok": False, "reason": "no_workspace"}
    assert c.post("/api/files/upload", params={"name": "x.txt"}, content=b"x").json()["ok"] is False
    assert c.post("/api/files/delete", params={"path": "x"}).json()["ok"] is False
