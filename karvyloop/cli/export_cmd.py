"""karvyloop export — "你的实例是个文件夹"从口号变按钮(docs/42 痛点矩阵 △)。

把 ~/.karvyloop(你长出来的一切:技能/知识/偏好/历史)打成**一个可携带压缩包**,
换机器解开即续命 —— 这是打 SaaS 锁死痛点的实体按钮:实例属于你,不属于任何服务器。

**刻意排除的秘密**(安全是地基,[CLAUDE.md 安全/凭证]):
- `config.yaml` —— 你的 API key 住在这里,**留在原机**;新机器上重新填(或拷你自己的备份)。
- `console.runtime.json` —— 本机运行时的访问 token,跨机无意义且是凭证。
- `*.lock` —— 进程锁文件,带走只会碍事。

其余全带:skills/、atoms.json、roles/、domains.json、beliefs、trace 库、tokens.db、
decision*、taste_predictions.json、pending_proposals.json……包根附 MANIFEST.txt 说明
"这是什么/里面有什么/为什么少了 config.yaml/怎么恢复"。

注:输出文案暂为英文硬编码(i18n en/zh 表由并行工作占用,后续补键;t() 缺键回退不救英文)。
"""

from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path, PurePosixPath
from typing import List, Optional, Tuple

# 根级排除(精确名):凭证与运行时秘密,绝不入包
_EXCLUDE_ROOT_NAMES = ("config.yaml", "console.runtime.json")

# 已知顶层条目 → MANIFEST 一句话说明(未知条目走通用说明,不漏)
_KNOWN_ENTRIES = {
    "skills": "your crystallized skill library (one folder per SKILL.md)",
    "atoms.json": "atom registry — reusable execution capabilities",
    "roles": "your roles (identity, commitments, cognition)",
    "domains.json": "business domains and their membership",
    "beliefs": "your knowledge base (beliefs / cognition)",
    "beliefs.json": "your knowledge base (beliefs / cognition)",
    "trace.db": "Trace — the run history every evaluation derives from",
    "tokens.db": "token spend ledger (who burned what, when)",
    "taste_predictions.json": "taste model predictions (decision pre-alignment)",
    "pending_proposals.json": "decision cards still waiting for your call",
    "consolidate_tick.json": "daily knowledge-consolidation watermark state",
    "skill_tags_tick.json": "daily skill-tagging watermark state",
}


def _is_excluded(rel: PurePosixPath) -> bool:
    """一个相对路径(POSIX)是否属于秘密/锁,不入包。"""
    if rel.name.endswith(".lock"):
        return True
    if len(rel.parts) == 1 and rel.name in _EXCLUDE_ROOT_NAMES:
        return True
    return False


def _collect(root: Path, out_path: Path) -> Tuple[List[Tuple[Path, PurePosixPath]], List[str]]:
    """扫 ~/.karvyloop → (入包文件列表, 被排除的相对路径列表)。跳过输出包自身(防自吞)。"""
    included: List[Tuple[Path, PurePosixPath]] = []
    excluded: List[str] = []
    out_resolved = out_path.resolve()
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        try:
            if p.resolve() == out_resolved:
                continue  # 输出包落在实例目录里 → 别把自己打进自己
        except OSError:
            pass
        rel = PurePosixPath(p.relative_to(root).as_posix())
        if _is_excluded(rel):
            excluded.append(str(rel))
        else:
            included.append((p, rel))
    return included, excluded


def _manifest_text(included: List[Tuple[Path, PurePosixPath]], excluded: List[str],
                   when: datetime) -> str:
    """生成包根 MANIFEST.txt:这是什么 / 顶层各是什么 / 刻意少了什么 / 怎么恢复。"""
    # 顶层条目表(目录归并成一行)
    top: dict = {}
    for _, rel in included:
        head = rel.parts[0]
        top[head] = top.get(head, 0) + 1
    lines = [
        "KarvyLoop instance export",
        "=========================",
        "",
        "What this archive is",
        "--------------------",
        "Your KarvyLoop instance — skills, knowledge, preferences, history.",
        "Everything you grew by using it. Your instance is a folder; this is that",
        f"folder, packed on {when:%Y-%m-%d %H:%M} so you can take it anywhere.",
        "",
        "What's inside (top-level entries)",
        "---------------------------------",
    ]
    for head in sorted(top):
        desc = _KNOWN_ENTRIES.get(head, "instance data")
        count = top[head]
        suffix = f" ({count} files)" if count > 1 else ""
        lines.append(f"  {head:<28} {desc}{suffix}")
    lines += [
        "",
        "Deliberately excluded (secrets stay put)",
        "----------------------------------------",
        "  config.yaml            your API keys — they never leave the old machine.",
        "                         Re-add your keys on the new machine (the setup",
        "                         wizard or `karvyloop init` will ask).",
        "  console.runtime.json   local runtime access token — machine-specific.",
        "  *.lock                 process lock files — meaningless elsewhere.",
    ]
    if excluded:
        lines.append("  (excluded this time: " + ", ".join(sorted(excluded)) + ")")
    lines += [
        "",
        "How to restore",
        "--------------",
        "  1. On the new machine, unpack this archive into ~/.karvyloop",
        "     (create the folder if it doesn't exist; MANIFEST.txt may stay or go).",
        "  2. Add your model API key (run `karvyloop init` or edit",
        "     ~/.karvyloop/config.yaml — see the README's minimal config).",
        "  3. Run `karvyloop console` — your skills, knowledge, preferences and",
        "     history are exactly where you left them.",
        "",
    ]
    return "\n".join(lines)


def _write_archive(out_path: Path, included: List[Tuple[Path, PurePosixPath]],
                   manifest: str) -> None:
    """按扩展名写 zip(默认)或 tar.gz;MANIFEST.txt 置于包根。"""
    name = out_path.name.lower()
    if name.endswith(".tar.gz") or name.endswith(".tgz"):
        import io
        import tarfile
        with tarfile.open(out_path, "w:gz") as tf:
            data = manifest.encode("utf-8")
            info = tarfile.TarInfo("MANIFEST.txt")
            info.size = len(data)
            tf.addfile(info, io.BytesIO(data))
            for src, rel in included:
                tf.add(src, arcname=str(rel))
    else:
        import zipfile
        with zipfile.ZipFile(out_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("MANIFEST.txt", manifest)
            for src, rel in included:
                zf.write(src, arcname=str(rel))


def _human_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{n} B"
        n /= 1024.0
    return f"{n} B"  # pragma: no cover


def cmd_export(out: Optional[str] = None) -> int:
    """`karvyloop export [--out PATH]` 的实现。返回 exit code。"""
    root = Path.home() / ".karvyloop"
    now = datetime.now()
    out_path = Path(out) if out else Path.cwd() / f"karvyloop-instance-{now:%Y%m%d}.zip"

    if not root.is_dir():
        sys.stderr.write(
            "Nothing to export yet — ~/.karvyloop doesn't exist.\n"
            "Run `karvyloop console` first; your instance grows there.\n")
        return 1

    included, excluded = _collect(root, out_path)
    if not included:
        sys.stderr.write(
            "Nothing to export yet — ~/.karvyloop has no instance data\n"
            "(only secrets/locks, which are deliberately left behind).\n"
            "Use KarvyLoop a bit first, then export.\n")
        return 1

    manifest = _manifest_text(included, excluded, now)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    _write_archive(out_path, included, manifest)

    total = sum(src.stat().st_size for src, _ in included)
    from karvyloop.i18n import t as _t
    print(_t("cli.export.done", n=len(included), size=_human_size(total), path=str(out_path)))
    print(_t("cli.export.excluded"))
    print(_t("cli.export.restore"))
    return 0


__all__ = ["cmd_export"]
