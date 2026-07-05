"""karvy/butler_lesson — 文件管家第一课(入住后的第一单,docs/60 引荐的下半场)。

**wow 时刻的三个"只有"**:只有本地运行时能扫你真实的桌面/下载;只有触到真实文件才建立信任;
只有 H2A 能优雅做到"它先拿出完整方案、你拍板它才动手"。所以第一课 = 引荐卡 ACCEPT 后顺势给的
第一任务:扫一眼桌面/下载(**只读**)→ 产出整理方案预览卡(H2A)→ 你 ACCEPT 才真执行。

**确定性,零 LLM**:扫描 = 白名单内只读遍历 + 读元数据(名字/大小/mtime/扩展名);方案 = 按类型
或按时间分桶(模式由人格采集器 filing 一题**确定性**决定 —— 答案不同,方案当场不同);查重 =
同尺寸再同 sha1(hash 是事实,尺寸只是线索,file-butler SKILL 的方法);占位大户 = 尺寸 Top-N。
没有一个字节来自模型编造 —— 卡上每一行都能在磁盘上核对。

**安全边界(管家既有地基,不是新承诺)**:
- 扫描只进 fs_grants 已授权目录(引荐 ACCEPT 时落的白名单),敏感路径硬地板照拦;
- 执行只做 **move**:绝不删除、绝不覆盖(目标已存在 → 跳过并如实报);隐藏文件/系统文件/
  快捷方式不碰(SKILL "out of scope by definition");
- 每一步 move 记进 `~/.karvyloop/butler_moves.json` 台账(src→dst 全量留痕)—— 没有删除 +
  不覆盖 + 全量台账 = 每一步都可逆,这是第一课版的"回收站兜底";
- "只看看不动"是合法选择:REJECT = 一个字节都不动。

空桌面/空下载 → 诚实说"没什么可整理的"(route 返 empty,前端给替代建议),绝不硬凑方案。
"""
from __future__ import annotations

import hashlib
import json
import logging
import shutil
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

KIND_BUTLER_PLAN = "butler_plan"
BUTLER_ROLE_ID = "file-butler"

#: 第一课扫哪(引荐清单 grant_dirs 的子集;Documents 刻意不碰 —— 第一课只收拾"杂物堆")
FIRST_LESSON_DIR_NAMES = ("Desktop", "Downloads")

MAX_ENTRIES_PER_DIR = 400          # 只读遍历天花板(超大目录只看前 N,卡上如实标注)
HASH_SIZE_CAP = 32 * 1024 * 1024   # 查重 hash 的单文件上限(>32MB 只按尺寸提示,不烧 IO)
HOG_MIN_SIZE = 50 * 1024 * 1024    # "占位大户"门槛
HOG_TOP_N = 5

#: 永不入方案的文件(系统面/桌面秩序;SKILL:hidden/system/app data out of scope)
_SKIP_NAMES = {"desktop.ini", "thumbs.db", ".ds_store"}
_LEAVE_EXTS = {".lnk", ".url"}     # 桌面快捷方式:移走 = 弄乱人家的桌面

#: 按类型分桶(文件夹名按出卡 locale 定稿;顺序即判定顺序)
_TYPE_BUCKETS: tuple = (
    ("images", {"en": "Images", "zh": "图片"},
     {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".bmp", ".heic", ".tiff"}),
    ("documents", {"en": "Documents", "zh": "文档"},
     {".pdf", ".doc", ".docx", ".txt", ".md", ".rtf", ".odt", ".pages", ".epub"}),
    ("sheets", {"en": "Spreadsheets", "zh": "表格"},
     {".xls", ".xlsx", ".csv", ".ods", ".numbers"}),
    ("slides", {"en": "Slides", "zh": "演示"},
     {".ppt", ".pptx", ".key", ".odp"}),
    ("archives", {"en": "Archives", "zh": "压缩包"},
     {".zip", ".rar", ".7z", ".tar", ".gz", ".bz2", ".xz"}),
    ("installers", {"en": "Installers", "zh": "安装包"},
     {".exe", ".msi", ".dmg", ".pkg", ".deb", ".rpm", ".appimage", ".apk"}),
    ("media", {"en": "Media", "zh": "音视频"},
     {".mp3", ".wav", ".flac", ".m4a", ".mp4", ".mkv", ".avi", ".mov", ".webm"}),
)
_OTHER_BUCKET = {"en": "Others", "zh": "其他"}


def default_journal_path() -> Path:
    """move 台账(第一课版"回收站兜底":全量 src→dst 留痕 = 每一步可逆)。"""
    return Path.home() / ".karvyloop" / "butler_moves.json"


def _bucket_for(ext: str, locale: str) -> str:
    for _key, names, exts in _TYPE_BUCKETS:
        if ext in exts:
            return names.get(locale) or names["en"]
    return _OTHER_BUCKET.get(locale) or _OTHER_BUCKET["en"]


def _time_bucket(mtime: float) -> str:
    return time.strftime("%Y-%m", time.localtime(mtime))


def scan_dir(d: Path) -> dict:
    """一个白名单目录的**只读**盘点(顶层文件;子文件夹不深入 —— 已归类的不去翻动)。

    跳过:隐藏文件(dot 开头)/系统文件/快捷方式/敏感路径(硬地板)。只读元数据,不读内容。
    返回 {"files": [{path,name,size,mtime,ext,leave}], "truncated": bool}。
    """
    from karvyloop.capability.fs_grants import is_sensitive_path
    files: list = []
    truncated = False
    try:
        entries = sorted(d.iterdir(), key=lambda p: p.name.lower())
    except OSError:
        return {"files": [], "truncated": False}
    for p in entries:
        if len(files) >= MAX_ENTRIES_PER_DIR:
            truncated = True
            break
        try:
            name = p.name
            if name.startswith(".") or name.lower() in _SKIP_NAMES:
                continue
            if not p.is_file() or p.is_symlink():
                continue
            if is_sensitive_path(str(p)):
                continue
            st = p.stat()
            files.append({
                "path": str(p), "name": name, "size": int(st.st_size),
                "mtime": float(st.st_mtime), "ext": p.suffix.lower(),
                "leave": p.suffix.lower() in _LEAVE_EXTS,
            })
        except OSError:
            continue   # 读元数据失败的单个文件跳过(只读盘点,绝不因一个文件炸整单)
    return {"files": files, "truncated": truncated}


def _sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def find_duplicates(files: list) -> list:
    """查重:同尺寸(线索)→ 同 sha1(事实)。只报告,第一课**绝不删除**(删除永远另行 H2A)。

    返回 [{"names": [...], "size": int}](组内 ≥2 个内容完全相同的文件)。
    """
    by_size: dict = {}
    for f in files:
        if 0 < f["size"] <= HASH_SIZE_CAP:
            by_size.setdefault(f["size"], []).append(f)
    groups: list = []
    for size, cands in by_size.items():
        if len(cands) < 2:
            continue
        by_hash: dict = {}
        for f in cands:
            try:
                by_hash.setdefault(_sha1(Path(f["path"])), []).append(f)
            except OSError:
                continue
        for _h, same in by_hash.items():
            if len(same) >= 2:
                groups.append({"names": [f["name"] for f in same], "size": size})
    groups.sort(key=lambda g: -g["size"])
    return groups


def filing_mode_from_memory(mem: Any) -> str:
    """人格采集器 filing 一题的**确定性**消费:by_time / by_type(默认 by_type)。

    按 provenance.intake_q=="filing" 在认知库定位(onboarding_intake 种的),失效条不算。
    这是"采集的答案改变系统行为"的第一处闭环 —— 答案不同,第一课方案当场不同。
    """
    if mem is None:
        return "by_type"
    try:
        seen: set = set()
        for sc in ("personal", "domain"):
            for b in mem.index.all(sc):
                if id(b) in seen:
                    continue
                seen.add(id(b))
                prov = getattr(b, "provenance", None) or {}
                if prov.get("source") == "decision_pref" and prov.get("intake_q") == "filing" \
                        and getattr(b, "invalid_at", None) is None:
                    return "by_time" if prov.get("intake_opt") == "by_time" else "by_type"
    except Exception:
        pass
    return "by_type"


def build_first_lesson(dirs: list, *, mode: str = "by_type", locale: str = "en") -> dict:
    """扫描 + 出方案(纯确定性,零 LLM)。

    返回 {"scanned": n, "moves": [...], "duplicates": [...], "hogs": [...],
          "dirs": [str...], "mode": mode, "truncated": bool, "empty": bool}。
    move = {"src","dst","name","bucket","dir"}(dst 永远在同一白名单目录**之内**的子文件夹)。
    """
    all_files: list = []
    moves: list = []
    truncated = False
    for d in dirs:
        got = scan_dir(Path(d))
        truncated = truncated or got["truncated"]
        for f in got["files"]:
            f["dir"] = str(d)
            all_files.append(f)
            if f["leave"]:
                continue   # 快捷方式留在原地(桌面秩序不是我们的)
            bucket = _time_bucket(f["mtime"]) if mode == "by_time" \
                else _bucket_for(f["ext"], locale)
            moves.append({
                "src": f["path"],
                "dst": str(Path(d) / bucket / f["name"]),
                "name": f["name"], "bucket": bucket, "dir": str(d),
            })
    duplicates = find_duplicates(all_files)
    hogs = sorted((f for f in all_files if f["size"] >= HOG_MIN_SIZE),
                  key=lambda f: -f["size"])[:HOG_TOP_N]
    return {
        "scanned": len(all_files),
        "moves": moves,
        "duplicates": duplicates,
        "hogs": [{"name": f["name"], "size": f["size"]} for f in hogs],
        "dirs": [str(d) for d in dirs],
        "mode": mode,
        "truncated": truncated,
        "empty": not all_files,
    }


def _fmt_size(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.0f}{unit}" if unit == "B" else f"{size:.1f}{unit}"
        size /= 1024.0
    return f"{size:.1f}GB"


def proposal_for_butler_plan(plan: dict, *, ts: float, mode_from_intake: bool = False,
                             strength: float = 0.85):
    """整理方案 → H2A 预览卡(你拍板才动手;REJECT=只看看不动,同样合法)。

    payload 全字符串(改了再批白名单约定);proposal_id 按 moves 集合稳定派生(同方案幂等)。
    文案走 i18n,出卡时按当前 locale 定稿(residents 同款先例)。
    """
    from karvyloop.i18n import get_locale, t
    from karvyloop.karvy.atoms import Proposal
    loc = get_locale()
    moves, dups, hogs = plan["moves"], plan["duplicates"], plan["hogs"]
    sep = "、" if loc == "zh" else ", "
    dir_names = sep.join(Path(d).name for d in plan["dirs"])
    mode_key = "butler.lesson.mode_by_time" if plan["mode"] == "by_time" \
        else "butler.lesson.mode_by_type"
    mode_line = t(mode_key)
    if mode_from_intake:
        mode_line += " " + t("butler.lesson.mode_from_intake")
    basis_lines = [
        t("butler.lesson.basis_scan", n=plan["scanned"], dirs=dir_names),
        mode_line,
    ]
    if dups:
        basis_lines.append(t("butler.lesson.basis_dups", n=len(dups)))
    if hogs:
        top = sep.join(f"{h['name']}({_fmt_size(h['size'])})" for h in hogs[:3])
        basis_lines.append(t("butler.lesson.basis_hogs", top=top))
    if plan.get("truncated"):
        basis_lines.append(t("butler.lesson.basis_truncated", cap=MAX_ENTRIES_PER_DIR))
    basis_lines.append(t("butler.lesson.basis_safety"))
    sig = hashlib.sha1("\n".join(f"{m['src']}→{m['dst']}" for m in moves)
                       .encode("utf-8")).hexdigest()[:8]
    payload = {
        "plan": json.dumps({"moves": moves, "duplicates": dups, "hogs": plan["hogs"],
                            "dirs": plan["dirs"], "mode": plan["mode"],
                            "truncated": bool(plan.get("truncated"))},
                           ensure_ascii=False),
        "dirs": ",".join(plan["dirs"]),
        "mode": str(plan["mode"]),
    }
    return Proposal(
        summary=t("butler.lesson.summary", n=len(moves), dirs=dir_names),
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=strength,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=ts,
        kind=KIND_BUTLER_PLAN,
        payload=payload,
        proposal_id=f"{KIND_BUTLER_PLAN}-0-{sig}",
        basis="\n".join(basis_lines),
    )


def _within(path: Path, base: Path) -> bool:
    """path 是否在 base 目录之内。**拒绝任何含 `..` 的路径**:relative_to 是词法判定、
    不归一 `..`(`Desktop/x/../../etc` 词法上"在 Desktop 内"实则逃逸)—— fs_grants.allows
    会 resolve 后再拦一道,但这层确定性地板不许依赖台账在不在。"""
    if ".." in path.parts or ".." in base.parts:
        return False
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def execute_plan(plan: dict, *, fs_grants: Any = None,
                 journal_path: Optional[Path] = None) -> dict:
    """执行已获 ACCEPT 的方案(只 move;绝不删除、绝不覆盖)。

    每条 move 执行前再验一遍(方案可能过时/被篡改,执行时以磁盘现状为准):
    src 仍在且是文件、src/dst 都在方案声明的白名单目录内、敏感地板、fs_grants 写权限
    (接了台账才验 —— 台账是引荐 ACCEPT 落的,正常路径必在)。目标已存在 → 跳过如实报。
    全部 move 记台账(journal;第一课版回收站兜底)。返回 {"moved","skipped","errors"}。
    """
    from karvyloop.capability.fs_grants import is_sensitive_path
    allowed = [Path(d) for d in (plan.get("dirs") or [])]
    moved: list = []
    skipped: list = []
    for m in (plan.get("moves") or []):
        src, dst = Path(str(m.get("src", ""))), Path(str(m.get("dst", "")))
        name = m.get("name") or src.name
        base = next((b for b in allowed if _within(src, b)), None)
        if base is None or not _within(dst, base):
            skipped.append({"name": name, "reason": "outside_whitelist"})
            continue
        if is_sensitive_path(str(src)) or is_sensitive_path(str(dst)):
            skipped.append({"name": name, "reason": "sensitive"})
            continue
        if fs_grants is not None and not (
                fs_grants.allows(str(src), "write", role=BUTLER_ROLE_ID)
                and fs_grants.allows(str(dst), "write", role=BUTLER_ROLE_ID)):
            skipped.append({"name": name, "reason": "not_granted"})
            continue
        if not src.is_file():
            skipped.append({"name": name, "reason": "gone"})
            continue
        if dst.exists():
            skipped.append({"name": name, "reason": "target_exists"})   # 绝不覆盖(SKILL 铁律)
            continue
        try:
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.move(str(src), str(dst))
            moved.append({"src": str(src), "dst": str(dst)})
        except OSError as e:
            skipped.append({"name": name, "reason": f"os_error: {e}"})
    # 台账(fail-soft:记不上不回滚已成的 move,但要响)
    if moved:
        jp = Path(journal_path) if journal_path else default_journal_path()
        try:
            existing = []
            if jp.exists():
                try:
                    existing = json.loads(jp.read_text(encoding="utf-8")) or []
                except ValueError:
                    existing = []
            if not isinstance(existing, list):   # 台账被改坏成非数组 → 重起一本(旧内容已坏)
                existing = []
            existing.append({"ts": time.time(), "origin": "butler_first_lesson",
                             "moved": moved})
            jp.parent.mkdir(parents=True, exist_ok=True)
            jp.write_text(json.dumps(existing, ensure_ascii=False, indent=2),
                          encoding="utf-8")
        except OSError as e:
            logger.warning("[butler_lesson] move 台账落盘失败(move 已成,可逆性留痕缺一笔): %s", e)
    return {"moved": moved, "skipped": skipped}


def make_butler_plan_handler(app: Any):
    """ACCEPT 兑现 handler:按卡上方案真执行(K5:只在用户 ACCEPT 后被调)。"""
    def handler(proposal) -> tuple:
        from karvyloop.i18n import t
        payload = getattr(proposal, "payload", None) or {}
        try:
            plan = json.loads(payload.get("plan") or "{}")
            if not isinstance(plan, dict):
                raise ValueError("plan not a dict")
        except ValueError:
            return False, t("butler.lesson.bad_plan")   # 宁拒勿猜:方案坏了绝不瞎动文件
        st = getattr(app, "state", None)
        res = execute_plan(
            plan,
            fs_grants=getattr(st, "fs_grants", None),
            journal_path=getattr(st, "butler_journal_path", None),
        )
        n_moved, n_skipped = len(res["moved"]), len(res["skipped"])
        if not n_moved and not n_skipped:
            return True, t("butler.lesson.receipt_none")
        return True, t("butler.lesson.receipt", moved=n_moved, skipped=n_skipped)
    return handler


__all__ = [
    "KIND_BUTLER_PLAN", "BUTLER_ROLE_ID", "FIRST_LESSON_DIR_NAMES",
    "default_journal_path", "scan_dir", "find_duplicates", "filing_mode_from_memory",
    "build_first_lesson", "proposal_for_butler_plan", "execute_plan",
    "make_butler_plan_handler",
]
