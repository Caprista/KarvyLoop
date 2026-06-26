"""skill_import — 导入第三方/外部技能进本地技能库(P0-b)。

Hardy(2026-06-21):"我们要能直接用第三方 skill 库的 skill;从 0 自造一套生态和找死没区别。"
Agent Skills 开放标准(2025-12 起,32 工具 / 490k+ 技能)里**技能 = 一个含 SKILL.md 的文件夹**;
各来源(anthropics/skills 官方仓库 / SkillsMP·Skills.sh 市场 / 本地)最终都收敛成
**git 仓库 / zip(.zip/.skill)/ 本地文件夹** —— 一个 importer,三个源适配器。

安全(这是把**外部代码**引进系统的入口,地基级 —— #0 安全是地基不是招牌):
- **路径穿越**:技能名 + 压缩包内每个成员都消毒,拒 `..` / 绝对路径 / 盘符 / symlink;
  一律落在 skills_dir/<safe-name>/ 之内,绝不逃逸。
- **体量护栏**:解压总大小 / 单文件大小 / 文件数封顶(防 zip bomb)。
- **导入只写文件、绝不执行**:脚本执行走 P0-c 沙箱;第三方技能写 `trust: untrusted`
  (没有我方 verify_proof)—— 执行层据此给最小能力。
- **宁空勿毒**:SKILL.md 缺 name/description → 拒绝整包(不半吊子写进库,护城河不投毒)。

只依赖标准库 + httpx(已是项目依赖);网络可注入(测试不触网)。
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import shutil
import tempfile
import time
import zipfile
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional

from .skills import parse_frontmatter

# 体量护栏(防 zip bomb / 失控大仓)
MAX_FILES = 300
MAX_TOTAL_BYTES = 25 * 1024 * 1024   # 25 MB 解压上限
MAX_FILE_BYTES = 10 * 1024 * 1024    # 单文件 10 MB

_SKILL_FILENAME = "SKILL.md"
# 安全技能名:kebab/下划线;其余字符压成连字符,首尾削
_NAME_CLEAN_RE = re.compile(r"[^\w\-]+", re.UNICODE)


Fetch = Callable[[str], bytes]


@dataclass
class ImportResult:
    ok: bool
    name: str = ""
    path: str = ""
    reason: str = ""
    untrusted: bool = True       # 第三方默认不可信(无我方 verify_proof)
    has_scripts: bool = False     # 带 scripts/ → 执行需走 P0-c 沙箱
    files: int = 0
    origin: str = ""


def _default_fetch(url: str) -> bytes:
    import httpx  # 延迟导入:不触网就不需要
    r = httpx.get(url, timeout=30.0, follow_redirects=True,
                  headers={"User-Agent": "karvyloop-skill-import"})
    r.raise_for_status()
    return r.content


def safe_skill_name(raw: str) -> str:
    """把任意名字消毒成安全目录名(无路径分隔/穿越)。空 → ''。"""
    s = (raw or "").strip().replace("/", "-").replace("\\", "-")
    s = _NAME_CLEAN_RE.sub("-", s).strip("-._")
    # 防 '..' / 纯点 / 盘符残留
    if s in ("", ".", "..") or set(s) <= {".", "-", "_"}:
        return ""
    return s[:64]


def _is_safe_member(rel: str) -> bool:
    """压缩包/拷贝时,成员相对路径是否安全(无穿越/绝对/盘符)。"""
    if not rel or rel.startswith(("/", "\\")):
        return False
    p = rel.replace("\\", "/")
    if ":" in p:  # 盘符 C:
        return False
    parts = p.split("/")
    return ".." not in parts and not any(seg.strip() in ("", ".") and i < len(parts) - 1
                                         for i, seg in enumerate(parts))


def _find_skill_root(base: Path) -> Optional[Path]:
    """在 base(或其一/二层子目录)里找含 SKILL.md 的技能根目录。"""
    if (base / _SKILL_FILENAME).is_file():
        return base
    for depth1 in sorted(p for p in base.iterdir() if p.is_dir()):
        if (depth1 / _SKILL_FILENAME).is_file():
            return depth1
        for depth2 in sorted(p for p in depth1.iterdir() if p.is_dir()):
            if (depth2 / _SKILL_FILENAME).is_file():
                return depth2
    return None


def _inject_provenance(skill_md: Path, origin: str) -> None:
    """给导入的 SKILL.md frontmatter 补来源/信任标记 + signature(只补缺失的键,不毁原内容)。

    写入:source: third-party / origin: <url> / trust: untrusted / imported_at: <ts> /
    signature(内容哈希)—— 第三方 SKILL.md 通常无 signature,而 SkillIndex 只收有 sig 的;
    不补就**进不了索引、recall 看不见**(等于导了用不上)。刻意**不写 verify_proof** ——
    第三方没过我方验证门,执行层据 trust 给最小能力。
    """
    text = skill_md.read_text(encoding="utf-8")
    m = re.match(r"^(---\s*\n)(.*?)(\n---\s*\n)(.*)$", text, re.DOTALL)
    sig = "imp-" + hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]
    add = {
        "source": "third-party",
        "origin": origin or "",
        "trust": "untrusted",
        "imported_at": str(int(time.time())),
        "signature": sig,
    }
    if not m:
        # 没 frontmatter(理论上 validate 已挡;兜底)→ 包一层
        fm = "\n".join(f"{k}: {v}" for k, v in add.items())
        skill_md.write_text(f"---\n{fm}\n---\n{text}", encoding="utf-8")
        return
    head, body_fm, close, body = m.groups()
    present = set()
    for line in body_fm.splitlines():
        key = line.partition(":")[0].strip()
        if key:
            present.add(key)
    extra = "".join(f"{k}: {v}\n" for k, v in add.items() if k not in present)
    skill_md.write_text(head + body_fm + "\n" + extra.rstrip("\n") + close + body, encoding="utf-8")


def install_skill_dir(src_root: Path, *, skills_dir: Path, origin: str = "",
                      overwrite: bool = False) -> ImportResult:
    """把一个已落地的技能目录(含 SKILL.md)校验 + 安全拷贝进技能库。"""
    skills_dir = Path(skills_dir)
    skills_dir.mkdir(parents=True, exist_ok=True)
    root = _find_skill_root(Path(src_root))
    if root is None:
        return ImportResult(False, reason="包里找不到 SKILL.md(不是合法 Agent Skill)")
    # 宁空勿毒:必须有 name + description
    try:
        fm, _body = parse_frontmatter(root / _SKILL_FILENAME)
    except OSError as e:
        return ImportResult(False, reason=f"读 SKILL.md 失败:{e}")
    name = safe_skill_name(fm.name)
    if not name:
        return ImportResult(False, reason="SKILL.md 缺合法 name(消毒后为空)")
    if not (fm.description or "").strip():
        return ImportResult(False, reason="SKILL.md 缺 description(拒绝半吊子技能)")
    dest = skills_dir / name
    if dest.exists():
        if not overwrite:
            return ImportResult(False, name=name, reason=f"技能「{name}」已存在(overwrite=true 覆盖)")
        shutil.rmtree(dest)
    # 安全拷贝:逐文件查穿越 + 体量护栏
    total = 0
    count = 0
    has_scripts = False
    staging = Path(tempfile.mkdtemp(prefix="karvyskill-"))
    try:
        for f in sorted(root.rglob("*")):
            if f.is_dir():
                continue
            if f.is_symlink():  # 不跟符号链接(逃逸风险)
                continue
            rel = f.relative_to(root).as_posix()
            if not _is_safe_member(rel):
                shutil.rmtree(staging, ignore_errors=True)
                return ImportResult(False, name=name, reason=f"不安全的路径成员:{rel}")
            sz = f.stat().st_size
            if sz > MAX_FILE_BYTES:
                shutil.rmtree(staging, ignore_errors=True)
                return ImportResult(False, name=name, reason=f"文件过大:{rel}")
            total += sz
            count += 1
            if total > MAX_TOTAL_BYTES or count > MAX_FILES:
                shutil.rmtree(staging, ignore_errors=True)
                return ImportResult(False, name=name, reason="超体量护栏(疑似 zip bomb / 失控大包)")
            if rel.split("/")[0] == "scripts":
                has_scripts = True
            target = staging / rel
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, target)
        # 校验通过 → 原子落库
        shutil.move(str(staging), str(dest))
    except Exception as e:
        shutil.rmtree(staging, ignore_errors=True)
        return ImportResult(False, name=name, reason=f"拷贝失败:{e}")
    _inject_provenance(dest / _SKILL_FILENAME, origin)
    return ImportResult(True, name=name, path=str(dest), untrusted=True,
                        has_scripts=has_scripts, files=count, origin=origin)


# ---------- 源适配器:github / zip / 本地 ----------

_GITHUB_URL_RE = re.compile(
    r"github\.com/(?P<owner>[^/]+)/(?P<repo>[^/]+)(?:/tree/(?P<ref>[^/]+)/(?P<path>.+))?")


def _parse_github(spec: str) -> Optional[tuple[str, str, str, str]]:
    """解析 github 来源 → (owner, repo, path, ref)。支持:
      - https://github.com/owner/repo/tree/<ref>/<path>
      - owner/repo/<path>[@ref]   (省略 ref → main)
    """
    spec = (spec or "").strip()
    m = _GITHUB_URL_RE.search(spec)
    if m:
        return (m.group("owner"), m.group("repo").removesuffix(".git"),
                (m.group("path") or "").strip("/"), m.group("ref") or "main")
    ref = "main"
    if "@" in spec:
        spec, _, ref = spec.rpartition("@")
    parts = [p for p in spec.split("/") if p]
    if len(parts) < 2:
        return None
    owner, repo = parts[0], parts[1].removesuffix(".git")
    path = "/".join(parts[2:])
    return (owner, repo, path, ref)


def import_from_github(spec: str, *, skills_dir: Path, fetch: Optional[Fetch] = None,
                       overwrite: bool = False) -> ImportResult:
    """从 GitHub 仓库的一个技能子目录导入(用 contents API,只取那个技能,不下整仓)。"""
    fetch = fetch or _default_fetch
    parsed = _parse_github(spec)
    if parsed is None:
        return ImportResult(False, reason=f"无法解析 GitHub 来源:{spec}")
    owner, repo, path, ref = parsed
    origin = f"github:{owner}/{repo}/{path}@{ref}"
    staging = Path(tempfile.mkdtemp(prefix="karvygh-"))
    state = {"count": 0, "bytes": 0}

    def _walk(api_path: str, local: Path) -> Optional[str]:
        url = f"https://api.github.com/repos/{owner}/{repo}/contents/{api_path}?ref={ref}"
        try:
            data = json.loads(fetch(url).decode("utf-8"))
        except Exception as e:
            return f"取 {api_path} 失败:{e}"
        if isinstance(data, dict) and data.get("type") == "file":
            data = [data]
        if not isinstance(data, list):
            return f"{api_path} 返回非目录"
        for item in data:
            name = item.get("name", "")
            itype = item.get("type")
            rel = item.get("path", "").split(path, 1)[-1].strip("/") or name
            if not _is_safe_member(rel):
                return f"不安全成员:{rel}"
            if itype == "dir":
                err = _walk(item.get("path", ""), local)
                if err:
                    return err
            elif itype == "file":
                dl = item.get("download_url")
                if not dl:
                    continue
                state["count"] += 1
                if state["count"] > MAX_FILES:
                    return "文件数超护栏"
                blob = fetch(dl)
                state["bytes"] += len(blob)
                if len(blob) > MAX_FILE_BYTES or state["bytes"] > MAX_TOTAL_BYTES:
                    return "超体量护栏"
                tgt = local / rel
                tgt.parent.mkdir(parents=True, exist_ok=True)
                tgt.write_bytes(blob)
        return None

    try:
        err = _walk(path, staging)
        if err:
            return ImportResult(False, reason=err, origin=origin)
        return install_skill_dir(staging, skills_dir=skills_dir, origin=origin, overwrite=overwrite)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def import_from_zip(source: str, *, skills_dir: Path, fetch: Optional[Fetch] = None,
                    overwrite: bool = False) -> ImportResult:
    """从 .zip/.skill 归档导入(本地路径或 http(s) URL)。市场下载 + 手动导入都走这。"""
    origin = source
    try:
        if re.match(r"^https?://", source):
            raw = (fetch or _default_fetch)(source)
        else:
            raw = Path(source).read_bytes()
    except Exception as e:
        return ImportResult(False, reason=f"取 zip 失败:{e}", origin=origin)
    staging = Path(tempfile.mkdtemp(prefix="karvyzip-"))
    try:
        with zipfile.ZipFile(io.BytesIO(raw)) as zf:
            total = 0
            for info in zf.infolist():
                if info.is_dir():
                    continue
                if not _is_safe_member(info.filename):
                    return ImportResult(False, reason=f"zip 含不安全成员:{info.filename}", origin=origin)
                if info.file_size > MAX_FILE_BYTES:
                    return ImportResult(False, reason=f"zip 内文件过大:{info.filename}", origin=origin)
                total += info.file_size
                if total > MAX_TOTAL_BYTES:
                    return ImportResult(False, reason="zip 解压超护栏(疑似 bomb)", origin=origin)
            zf.extractall(staging)
        return install_skill_dir(staging, skills_dir=skills_dir, origin=origin, overwrite=overwrite)
    except zipfile.BadZipFile:
        return ImportResult(False, reason="不是合法 zip", origin=origin)
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def import_from_local(source: str, *, skills_dir: Path, overwrite: bool = False) -> ImportResult:
    """从本地文件夹导入(已含 SKILL.md)。"""
    p = Path(source)
    if not p.is_dir():
        return ImportResult(False, reason=f"本地路径不是目录:{source}", origin=source)
    return install_skill_dir(p, skills_dir=skills_dir, origin=f"local:{source}", overwrite=overwrite)


def import_skill(source: str, *, skills_dir: Path, kind: str = "auto",
                 fetch: Optional[Fetch] = None, overwrite: bool = False) -> ImportResult:
    """统一入口:按 kind(auto 自动嗅探)分派到三源适配器。"""
    source = (source or "").strip()
    if not source:
        return ImportResult(False, reason="来源为空")
    if kind == "auto":
        if "github.com" in source or _GITHUB_URL_RE.search(source) or re.match(r"^[\w.-]+/[\w.-]+(/|@|$)", source):
            if source.endswith(".zip") or source.endswith(".skill"):
                kind = "zip"
            else:
                kind = "github"
        elif source.endswith(".zip") or source.endswith(".skill"):
            kind = "zip"
        elif re.match(r"^https?://", source):
            kind = "zip"
        else:
            kind = "local"
    if kind == "github":
        return import_from_github(source, skills_dir=skills_dir, fetch=fetch, overwrite=overwrite)
    if kind == "zip":
        return import_from_zip(source, skills_dir=skills_dir, fetch=fetch, overwrite=overwrite)
    if kind == "local":
        return import_from_local(source, skills_dir=skills_dir, overwrite=overwrite)
    return ImportResult(False, reason=f"未知来源类型:{kind}")


__all__ = [
    "ImportResult", "import_skill", "import_from_github", "import_from_zip",
    "import_from_local", "install_skill_dir", "safe_skill_name",
]
