"""test_skill_import — 第三方技能导入(Hardy:必须能用开放标准生态)+ 安全护栏。"""
from __future__ import annotations
import io, json, pathlib, sys, zipfile
ROOT = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from karvyloop.registry import skill_import as si  # noqa: E402
from karvyloop.registry.skills import parse_frontmatter  # noqa: E402

STD_SKILL = ("---\nname: pdf-fill\ndescription: Fill PDF forms\n"
             "allowed-tools:\n  - Read\n  - Bash\nwhen-to-use: filling pdf forms\n---\n"
             "# PDF Fill\nrun scripts/fill.py\n")


def _local_skill(tmp, name="pdf-fill", body=STD_SKILL, with_script=True):
    d = tmp / "src" / name; d.mkdir(parents=True)
    (d / "SKILL.md").write_text(body, encoding="utf-8")
    if with_script:
        (d / "scripts").mkdir()
        (d / "scripts" / "fill.py").write_text("print('hi')\n", encoding="utf-8")
    return d


def test_import_local_preserves_scripts_and_marks_untrusted(tmp_path):
    src = _local_skill(tmp_path)
    sk = tmp_path / "skills"
    r = si.import_from_local(str(src), skills_dir=sk)
    assert r.ok and r.name == "pdf-fill" and r.has_scripts and r.untrusted
    dest = sk / "pdf-fill"
    assert (dest / "scripts" / "fill.py").is_file()
    txt = (dest / "SKILL.md").read_text(encoding="utf-8")
    assert "source: third-party" in txt and "trust: untrusted" in txt
    assert "verify_proof" not in txt          # 第三方没过我方验证门


def test_standard_hyphen_allowed_tools_read(tmp_path):
    src = _local_skill(tmp_path)
    sk = tmp_path / "skills"
    si.import_from_local(str(src), skills_dir=sk)
    fm, _ = parse_frontmatter(sk / "pdf-fill" / "SKILL.md")
    # 开放标准 allowed-tools(连字符)被我方解析为 allowed_tools
    assert "Read" in fm.allowed_tools and fm.when_to_use == "filling pdf forms"


def test_refuse_garbage_missing_description(tmp_path):
    src = _local_skill(tmp_path, body="---\nname: bad\n---\n# no desc\n")
    r = si.import_from_local(str(src), skills_dir=tmp_path / "skills")
    assert not r.ok and "description" in r.reason


def test_zip_path_traversal_rejected(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("../evil/SKILL.md", STD_SKILL)
    z = tmp_path / "evil.zip"; z.write_bytes(buf.getvalue())
    r = si.import_from_zip(str(z), skills_dir=tmp_path / "skills")
    assert not r.ok and "不安全" in r.reason


def test_zip_happy_path(tmp_path):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("pdf-fill/SKILL.md", STD_SKILL)
        zf.writestr("pdf-fill/scripts/fill.py", "print(1)\n")
    z = tmp_path / "s.zip"; z.write_bytes(buf.getvalue())
    r = si.import_from_zip(str(z), skills_dir=tmp_path / "skills")
    assert r.ok and r.name == "pdf-fill" and r.has_scripts


def test_safe_skill_name_blocks_traversal():
    assert si.safe_skill_name("../../etc/passwd") == "etc-passwd" or "passwd" in si.safe_skill_name("../../etc/passwd")
    assert si.safe_skill_name("..") == ""
    assert si.safe_skill_name("good-name") == "good-name"


def test_parse_github_forms():
    assert si._parse_github("anthropics/skills/skills/pdf") == ("anthropics", "skills", "skills/pdf", "main")
    assert si._parse_github("anthropics/skills/skills/pdf@v2") == ("anthropics", "skills", "skills/pdf", "v2")
    o, r, p, ref = si._parse_github("https://github.com/anthropics/skills/tree/main/skills/pdf")
    assert (o, r, p, ref) == ("anthropics", "skills", "skills/pdf", "main")


def test_import_from_github_with_injected_fetch(tmp_path):
    # 假 GitHub contents API:目录列 SKILL.md + scripts/;无网络
    def fake_fetch(url: str) -> bytes:
        if "contents/skills/pdf?" in url:
            return json.dumps([
                {"name": "SKILL.md", "type": "file", "path": "skills/pdf/SKILL.md",
                 "download_url": "https://raw/SKILL.md"},
                {"name": "scripts", "type": "dir", "path": "skills/pdf/scripts"},
            ]).encode()
        if "contents/skills/pdf/scripts?" in url:
            return json.dumps([
                {"name": "fill.py", "type": "file", "path": "skills/pdf/scripts/fill.py",
                 "download_url": "https://raw/fill.py"},
            ]).encode()
        if url == "https://raw/SKILL.md":
            return STD_SKILL.encode()
        if url == "https://raw/fill.py":
            return b"print('x')\n"
        raise AssertionError(f"unexpected url {url}")
    r = si.import_from_github("anthropics/skills/skills/pdf", skills_dir=tmp_path / "skills",
                              fetch=fake_fetch)
    assert r.ok and r.name == "pdf-fill" and r.has_scripts
    assert r.origin.startswith("github:anthropics/skills")
