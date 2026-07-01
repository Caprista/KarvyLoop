"""test_skills_api — Skill 库面板后端(Hardy 卡点:skill 库找不到)。"""
from __future__ import annotations
import pathlib, sys
from fastapi.testclient import TestClient
ROOT = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from karvyloop.console import build_console_app  # noqa: E402
from karvyloop.karvy.observer import WorkbenchObserver  # noqa: E402


def test_skills_no_llm_graceful():
    c = TestClient(build_console_app(workbench=WorkbenchObserver(), main_loop=None))
    r = c.get("/api/skills").json()
    assert r["skills"] == [] and r.get("no_llm") is True


def test_skills_lists_crystallized(tmp_path):
    from karvyloop.cli.main_loop import MainLoop
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    ml = MainLoop(skills_dir=tmp_path / "skills")
    sd = ml.skills_dir / "daily"; sd.mkdir(parents=True, exist_ok=True)
    (sd / "SKILL.md").write_text("---\nname: daily\n---\n# 日报技能\n", encoding="utf-8")
    ml.skill_index.register(name="daily", sig="s1", scope="user",
                            when_to_use="写日报", description="d", path=str(sd / "SKILL.md"))
    app.state.main_loop = ml
    r = TestClient(app).get("/api/skills").json()
    assert len(r["skills"]) == 1
    s = r["skills"][0]
    assert s["name"] == "daily" and s["when_to_use"] == "写日报"
    assert "日报技能" in s["body"] and s["archived"] is False


def test_skill_restore_no_llm():
    c = TestClient(build_console_app(workbench=WorkbenchObserver(), main_loop=None))
    assert c.post("/api/skill/restore", json={"sig": "x"}).json()["ok"] is False


def test_skill_import_endpoint_local(tmp_path):
    """POST /api/skill/import 从本地导入第三方技能 → 进库 + 进索引 + 可召回。"""
    from karvyloop.cli.main_loop import MainLoop
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    ml = MainLoop(skills_dir=tmp_path / "skills")
    app.state.main_loop = ml
    # 造一个开放标准格式的本地技能(连字符 allowed-tools + scripts)
    src = tmp_path / "ext" / "hello-skill"; (src / "scripts").mkdir(parents=True)
    (src / "SKILL.md").write_text(
        "---\nname: hello-skill\ndescription: say hi\nallowed-tools:\n  - Read\n---\n# Hello\nrun scripts/hi.py\n",
        encoding="utf-8")
    (src / "scripts" / "hi.py").write_text("print('hi')\n", encoding="utf-8")
    c = TestClient(app)
    r = c.post("/api/skill/import", json={"source": str(src), "kind": "local"}).json()
    assert r["ok"] and r["name"] == "hello-skill" and r["has_scripts"] and r["untrusted"]
    # 进库
    dest = ml.skills_dir / "hello-skill"
    assert (dest / "scripts" / "hi.py").is_file()
    # 进索引(stamped signature) → /api/skills 列得出 + 标第三方
    listed = c.get("/api/skills").json()["skills"]
    names = {s["name"]: s for s in listed}
    assert "hello-skill" in names and names["hello-skill"]["third_party"] is True


def test_skill_import_no_llm():
    c = TestClient(build_console_app(workbench=WorkbenchObserver(), main_loop=None))
    assert c.post("/api/skill/import", json={"source": "x", "kind": "local"}).json()["ok"] is False


def test_skill_run_no_llm():
    c = TestClient(build_console_app(workbench=WorkbenchObserver(), main_loop=None))
    r = c.post("/api/skill/run", json={"name": "x", "script": "scripts/a.py"}).json()
    assert r["ok"] is False


def test_skill_run_sandbox_unavailable_on_windows(tmp_path):
    """非 Linux:沙箱不可用 → 明确拒绝,绝不无隔离跑(fail-closed)。"""
    from karvyloop.cli.main_loop import MainLoop
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    ml = MainLoop(skills_dir=tmp_path / "skills")
    d = ml.skills_dir / "demo"; (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text("---\nname: demo\ndescription: d\nsignature: s\n---\n# d\n", encoding="utf-8")
    (d / "scripts" / "a.py").write_text("print(1)\n", encoding="utf-8")
    app.state.main_loop = ml
    r = TestClient(app).post("/api/skill/run", json={"name": "demo", "script": "scripts/a.py"}).json()
    # Windows host:StubSandbox.available()=False → 拒绝(不是 NotImplemented 崩)
    import sys
    if not sys.platform.startswith("linux"):
        assert r["ok"] is False and "沙箱" in r["reason"]


def test_skill_grant_net_endpoint(tmp_path):
    """POST /api/skill/grant 授网 → /api/skills 里 net_granted 反映。"""
    from karvyloop.cli.main_loop import MainLoop
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    ml = MainLoop(skills_dir=tmp_path / "skills")
    d = ml.skills_dir / "demo"; (d / "scripts").mkdir(parents=True)
    (d / "SKILL.md").write_text(
        "---\nname: demo\ndescription: d\nsource: third-party\ntrust: untrusted\nsignature: imp\n---\n# d\n",
        encoding="utf-8")
    (d / "scripts" / "a.py").write_text("print(1)\n", encoding="utf-8")
    ml.skill_index.rebuild_from_disk(ml.skills_dir)   # 进索引(/api/skills 才列得出)
    app.state.main_loop = ml
    c = TestClient(app)
    before = {s["name"]: s for s in c.get("/api/skills").json()["skills"]}["demo"]
    assert before["net_granted"] is False
    assert c.post("/api/skill/grant", json={"name": "demo", "net": True}).json()["ok"]
    after = {s["name"]: s for s in c.get("/api/skills").json()["skills"]}["demo"]
    assert after["net_granted"] is True


def test_skill_grant_no_llm():
    c = TestClient(build_console_app(workbench=WorkbenchObserver(), main_loop=None))
    assert c.post("/api/skill/grant", json={"name": "x", "net": True}).json()["ok"] is False


def test_skill_catalog_endpoint_shape(monkeypatch):
    """GET /api/skill/catalog 返回 entries(mock 掉网络,验形状不验真调)。"""
    import karvyloop.registry.skill_catalog as sc
    from karvyloop.registry.skill_catalog import CatalogEntry
    monkeypatch.setattr(sc, "search_catalog",
        lambda q="", source="all", sources=None, fetch=None: [
            CatalogEntry(name="pdf", description="d", source="anthropics/skills/skills/pdf", origin="official")])
    c = TestClient(build_console_app(workbench=WorkbenchObserver(), main_loop=None))
    r = c.get("/api/skill/catalog?q=pdf&source=official").json()
    assert r["entries"] and r["entries"][0]["name"] == "pdf"
    assert r["entries"][0]["source"] == "anthropics/skills/skills/pdf"


def test_skill_sources_seed_and_save(tmp_path):
    """GET 默认 seed 两源;POST 全关 → 拒(≥1 开)。"""
    from karvyloop.cli.main_loop import MainLoop
    app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
    app.state.main_loop = MainLoop(skills_dir=tmp_path / "skills")
    c = TestClient(app)
    got = c.get("/api/skill/sources").json()
    ids = {s["id"] for s in got["sources"]}
    assert "official" in ids and "skillsmp" in ids
    # 全关 → 拒
    bad = c.post("/api/skill/sources", json={"sources": [
        {"id": "official", "type": "github", "repo": "anthropics/skills", "enabled": False}]}).json()
    assert bad["ok"] is False
    # 留一个开 → 成
    ok = c.post("/api/skill/sources", json={"sources": [
        {"id": "official", "type": "github", "repo": "anthropics/skills", "enabled": True}]}).json()
    assert ok["ok"] is True


def test_skill_sources_no_llm():
    c = TestClient(build_console_app(workbench=WorkbenchObserver(), main_loop=None))
    assert c.get("/api/skill/sources").json().get("no_llm") is True
