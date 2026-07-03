"""karvyloop import — `karvyloop export` 的回程:一键迁移落地(docs/43 碎碎念⑤)。

"你不可以让用户被绑定在某台机器上" —— export 把实例打成一个包,import 把它在新机器
解回 ~/.karvyloop。两个命令合起来才是"你的实例是个文件夹"的完整按钮。

安全决策(和 export 同一根地基,[CLAUDE.md 安全/凭证]):
- **路径穿越防御**:包成员出现绝对路径 / 盘符 / `..` 分段 / 链接(tar symlink|hardlink)
  → 整包拒收(fail-loud),一个字节都不落地。恶意包不值得"跳过坏成员继续"。
- **秘密永不落地**:包根的 config.yaml / console.runtime.json / channel_secret 及一切
  *.lock 即使出现在包里(export 不会打进去,但手工包可能有)也**跳过不写**——
  既保证"绝不覆盖本机 config.yaml(密钥是本机的)",也防外来包注入别人的凭证。
- **MANIFEST.txt 不落地**:它是包的说明书,不是实例数据;落进 ~/.karvyloop 只会在
  下次 export/doctor 里变成"不认识的条目"。(export 的 manifest 也写明 may stay or go,
  我们选 go —— 实例目录保持纯净。)
- **原子性策略**:先把全部成员解到同卷临时目录(~/.karvyloop.import-tmp-<pid>),
  任何解包错误(截断/坏包)都在这一步炸掉 → 临时目录整删,目标目录一个字节没动;
  全部解出后再逐文件 os.replace 搬进目标(同卷 rename,单文件原子)。
  搬运阶段理论上可中断(同卷 rename 失败概率极低),但"坏包导致半写"被完整挡在第一阶段。

冲突策略:
- 目标已有**实例数据**(~/.karvyloop 里存在秘密/锁之外的文件)→ 默认拒绝,
  列出会被覆盖的顶层项;`--force` 才合并覆盖(逐文件覆盖,本机独有文件不删)。
- 只有 config.yaml(刚 `karvyloop init` 完的新机器)不算实例数据,不挡道。
- `--dry-run` 只读包列清单,零写盘。
"""

from __future__ import annotations

import os
import re
import shutil
import sys
from pathlib import Path, PurePosixPath
from typing import List, Optional, Tuple

# 单一真理来源:什么算"秘密/锁"由 export_cmd 定义,import 原样沿用(同包内刻意共享)。
from .export_cmd import _is_excluded

# 包根说明书 —— 不是实例数据,不落地(理由见模块 docstring)。
_MANIFEST_NAME = "MANIFEST.txt"

_DRIVE_RE = re.compile(r"^[A-Za-z]:")


# ---- 成员名安全 ----

def _member_parts(name: str) -> Tuple[str, ...]:
    """把包成员名拆成分段;'/' 和 '\\' 都当分隔符(恶意包可能混用)。"""
    return tuple(p for p in re.split(r"[/\\]+", name) if p and p != ".")


def _is_unsafe(name: str) -> bool:
    """绝对路径 / 盘符 / `..` 分段 → 不安全,整包拒收。"""
    if name.startswith(("/", "\\")) or _DRIVE_RE.match(name):
        return True
    return ".." in _member_parts(name)


# ---- 读包(zip / tar.gz 两种,和 export 对称)----

class _ArchiveReader:
    """统一 zip/tar 读取:members() 列文件名,extract_to() 把一个成员解到指定路径。"""

    def __init__(self, path: Path):
        self._path = path
        self._zf = None
        self._tf = None

    def __enter__(self) -> "_ArchiveReader":
        import tarfile
        import zipfile
        p = str(self._path)
        if zipfile.is_zipfile(p):
            self._zf = zipfile.ZipFile(p)
        else:
            # r:* 自动识别 gz/bz2/plain;不是 tar → ReadError → 上层报"读不了"
            self._tf = tarfile.open(p, "r:*")
        return self

    def __exit__(self, *exc) -> None:
        if self._zf is not None:
            self._zf.close()
        if self._tf is not None:
            self._tf.close()

    def members(self) -> List[str]:
        """所有**文件**成员名(目录条目剔除)。tar 里出现链接 → ValueError(fail-loud)。"""
        if self._zf is not None:
            return [i.filename for i in self._zf.infolist() if not i.is_dir()]
        names: List[str] = []
        for m in self._tf.getmembers():
            if m.issym() or m.islnk():
                raise ValueError(m.name)   # 链接成员 = 穿越攻击面,整包拒收
            if m.isfile():
                names.append(m.name)
        return names

    def extract_to(self, name: str, dest: Path) -> None:
        """把成员 name 的**内容**写到 dest(不用 extractall —— 路径完全由我们拼)。"""
        dest.parent.mkdir(parents=True, exist_ok=True)
        if self._zf is not None:
            src = self._zf.open(name)
        else:
            src = self._tf.extractfile(name)
            if src is None:  # pragma: no cover — members() 已过滤非文件
                raise ValueError(name)
        with src, open(dest, "wb") as out:
            shutil.copyfileobj(src, out)


# ---- 分拣:落地 / 刻意跳过 ----

def _partition(names: List[str]) -> Tuple[List[str], List[str]]:
    """成员名 → (要落地的, 刻意跳过的:MANIFEST + 秘密 + 锁)。名字已过安全检查。"""
    restore: List[str] = []
    skipped: List[str] = []
    for name in names:
        parts = _member_parts(name)
        if not parts:
            continue   # 空名/"." 之类的怪成员:无实体,静默忽略
        rel = PurePosixPath(*parts)
        if str(rel) == _MANIFEST_NAME or _is_excluded(rel):
            skipped.append(str(rel))
        else:
            restore.append(str(rel))
    return restore, skipped


def _existing_instance_files(root: Path) -> List[str]:
    """目标目录里已有的**实例数据**文件(秘密/锁不算 —— 刚 init 完的新机器不挡道)。"""
    if not root.is_dir():
        return []
    found: List[str] = []
    for p in root.rglob("*"):
        if p.is_file():
            rel = PurePosixPath(p.relative_to(root).as_posix())
            if not _is_excluded(rel):
                found.append(str(rel))
    return found


# ---- 主命令 ----

def cmd_import(archive: str, force: bool = False, dry_run: bool = False,
               root: Optional[Path] = None) -> int:
    """`karvyloop import <archive> [--force] [--dry-run]` 的实现。返回 exit code。

    `root` 仅测试注入用;生产恒为 ~/.karvyloop。
    """
    from karvyloop.i18n import t as _t
    root = root if root is not None else Path.home() / ".karvyloop"
    src = Path(archive)

    if not src.is_file():
        sys.stderr.write(_t("cli.import.not_found", path=str(src)) + "\n")
        return 1

    # 1) 读成员表(此时只读包,零写盘)。坏包/截断包在这里诚实报错。
    try:
        with _ArchiveReader(src) as reader:
            names = reader.members()
    except ValueError as bad:   # tar 链接成员
        sys.stderr.write(_t("cli.import.unsafe", name=str(bad)) + "\n")
        return 1
    except Exception:
        sys.stderr.write(_t("cli.import.unreadable", path=str(src)) + "\n")
        return 1

    # 2) 路径穿越:一个坏成员 = 整包拒收,绝不"跳过继续"。
    for name in names:
        if _is_unsafe(name):
            sys.stderr.write(_t("cli.import.unsafe", name=name) + "\n")
            return 1

    restore, skipped = _partition(names)
    if not restore:
        sys.stderr.write(_t("cli.import.nothing", path=str(src)) + "\n")
        return 1

    collisions = [rel for rel in restore if (root / rel).is_file()]

    # 3) 冲突策略:目标已有实例数据 → 默认拒绝(--dry-run 放行,反正零写盘)。
    if _existing_instance_files(root) and not force and not dry_run:
        heads = sorted({PurePosixPath(rel).parts[0] for rel in collisions})
        sys.stderr.write(_t("cli.import.refuse", root=str(root)) + "\n")
        if heads:
            sys.stderr.write(_t("cli.import.refuse.collisions", items=", ".join(heads)) + "\n")
        else:
            sys.stderr.write(_t("cli.import.refuse.no_collisions") + "\n")
        sys.stderr.write(_t("cli.import.refuse.hint") + "\n")
        return 1

    # 4) 干跑:列清单就走,零写盘。
    if dry_run:
        print(_t("cli.import.dry_run.header", n=len(restore), root=str(root)))
        for rel in sorted(restore):
            marker = "  (overwrite)" if rel in collisions else ""
            print(f"  {rel}{marker}")
        if skipped:
            print(_t("cli.import.skipped", items=", ".join(sorted(skipped))))
        return 0

    # 5) 两阶段落地:先全解到同卷临时目录(坏包在这炸,目标零污染),再逐文件原子搬。
    tmp = root.parent / f".karvyloop.import-tmp-{os.getpid()}"
    if tmp.exists():
        shutil.rmtree(tmp, ignore_errors=True)
    try:
        with _ArchiveReader(src) as reader:
            for rel in restore:
                reader.extract_to(rel, tmp / PurePosixPath(rel))
    except Exception:
        shutil.rmtree(tmp, ignore_errors=True)
        sys.stderr.write(_t("cli.import.unreadable", path=str(src)) + "\n")
        return 1

    root.mkdir(parents=True, exist_ok=True)
    for rel in restore:
        dest = root / PurePosixPath(rel)
        dest.parent.mkdir(parents=True, exist_ok=True)
        os.replace(tmp / PurePosixPath(rel), dest)   # 同卷 rename,单文件原子
    shutil.rmtree(tmp, ignore_errors=True)

    # 6) 收尾:恢复了什么 / 刻意跳过什么 / 下一步(配 key → console)。
    print(_t("cli.import.done", n=len(restore), root=str(root)))
    if collisions:
        print(_t("cli.import.overwrote", n=len(collisions)))
    if skipped:
        print(_t("cli.import.skipped", items=", ".join(sorted(skipped))))
    print(_t("cli.import.config_kept"))
    print(_t("cli.import.next"))
    return 0


__all__ = ["cmd_import"]
