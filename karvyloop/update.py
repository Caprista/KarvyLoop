"""update — 版本检测(detect → notify → 你按下,**绝不自动升级**)。

本地主权工具的升级铁律(与产品论题自洽):只**检测 + 提示**,绝不静默自动升级。
检测是一次干净的版本查询 —— **零遥测、不带任何用户数据**、可用 env 关掉、查不到就**静默**
(永不抛、永不阻塞启动)。升级命令交给你自己执行(升级本身就是产品对自己生命周期的一次 H2A)。

数据承诺:升级不动 `~/.karvyloop/`(你的 beliefs/skills/decision_log/config 都在那,跨版本存活)。
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import time
import urllib.request
from pathlib import Path
from typing import Optional

from karvyloop import __version__

GITHUB_REPO = "Caprista/KarvyLoop"
_RELEASES_API = f"https://api.github.com/repos/{GITHUB_REPO}/releases/latest"
_CACHE_PATH = Path.home() / ".karvyloop" / ".update_check.json"
_CACHE_TTL = 86400          # 一天最多查一次(别 hammer GitHub,尊重本地优先)
_ENV_OFF = "KARVYLOOP_NO_UPDATE_CHECK"   # 设任意非空值 = 彻底关闭检测(主权)


def current_version() -> str:
    return __version__


def _parse_semver(v: str) -> tuple:
    """'v1.2.3' / '1.2' / '1.2.3-rc1' → (1,2,3)。丢 pre-release 后缀做主比较;非法段当 0。"""
    core = (v or "").strip().lstrip("vV").split("-")[0].split("+")[0]
    parts: list[int] = []
    for chunk in core.split("."):
        try:
            parts.append(int(chunk))
        except ValueError:
            parts.append(0)
    while len(parts) < 3:
        parts.append(0)
    return tuple(parts[:3])


def is_newer(latest: str, current: str) -> bool:
    return _parse_semver(latest) > _parse_semver(current)


def detect_install_mode() -> str:
    """git(可 `git pull`)还是 pip。包目录上溯找到 .git → git。"""
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        try:
            if (p / ".git").exists():
                return "git"
        except Exception:
            break
    return "pip"


def upgrade_command(mode: Optional[str] = None) -> str:
    mode = mode or detect_install_mode()
    return "git pull && pip install -e ." if mode == "git" else "pip install -U karvyloop"


def check_disabled() -> bool:
    return bool(os.environ.get(_ENV_OFF))


def _read_cache() -> Optional[dict]:
    try:
        d = json.loads(_CACHE_PATH.read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else None
    except Exception:
        return None


def _write_cache(d: dict) -> None:
    try:
        _CACHE_PATH.parent.mkdir(parents=True, exist_ok=True)
        _CACHE_PATH.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
    except Exception:
        pass   # 缓存写失败不致命(下次再查)


def _fetch_latest(timeout: float = 4.0) -> Optional[dict]:
    """查 GitHub Releases 最新 tag。任何失败 → None(网断/限流/还没发过 release 都静默)。"""
    try:
        req = urllib.request.Request(_RELEASES_API, headers={
            "Accept": "application/vnd.github+json",
            "User-Agent": "karvyloop-update-check",   # 仅版本查询;不带任何用户数据
        })
        with urllib.request.urlopen(req, timeout=timeout) as r:
            data = json.loads(r.read().decode())
        tag = data.get("tag_name") or ""
        if not tag:
            return None
        return {"latest": tag, "url": data.get("html_url") or "", "name": data.get("name") or tag}
    except Exception:
        return None


def check_update(*, force: bool = False, now: Optional[float] = None,
                 timeout: float = 4.0) -> dict:
    """返回 {current, latest, newer, command, url, checked, source}。

    缓存一天(force 跳缓存);disabled → 只返 current;查不到 → newer=False。永不抛、永不阻塞。
    """
    cur = current_version()
    base = {"current": cur, "latest": None, "newer": False,
            "command": upgrade_command(), "url": "", "checked": False, "source": "disabled"}
    if check_disabled():
        return base
    now = now if now is not None else time.time()
    if not force:
        c = _read_cache()
        if c and c.get("latest") and (now - float(c.get("ts", 0))) < _CACHE_TTL:
            latest = c["latest"]
            return {"current": cur, "latest": latest, "newer": is_newer(latest, cur),
                    "command": upgrade_command(), "url": c.get("url", ""),
                    "checked": True, "source": "cache"}
    got = _fetch_latest(timeout=timeout)
    if got is None:
        return {**base, "checked": True, "source": "unreachable"}
    _write_cache({"ts": now, "latest": got["latest"], "url": got["url"]})
    latest = got["latest"]
    return {"current": cur, "latest": latest, "newer": is_newer(latest, cur),
            "command": upgrade_command(), "url": got["url"], "checked": True, "source": "live"}


# ---------------------------------------------------------------------------
# 升级前置 + 回滚(preflight / post-install smoke / rollback)
#
# 市场课(docs/42 第四部分):"每次更新都比之前更烂"是 top-3 流失因;OpenClaw 六月静默迁移事故是
# 反面教材。铁律:动手前先留后悔药(记回滚点 + 备份实例状态),装完先自检(能导入才算装成),
# 装坏了**自动回滚**并把原因大声说出来 —— 绝不静默带病重启。
# ---------------------------------------------------------------------------

_ROLLBACK_NAME = "update_rollback.json"
_BACKUP_KEEP = 3          # 备份有界:只留最近 3 份,更老的删

# 备份覆盖的实例状态形态(~/.karvyloop 顶层文件,非递归)。迁移代码可能写坏的状态文件全在这些
# 后缀里(beliefs/domains/atoms/tasks/decision_* 的 json、config.yaml、tokens/habits 的 db、
# trace/usage/verify 的 sqlite)。skills/ 目录本体(SKILL.md 方法库)**不整目录拷** ——
# 可能很大,且升级本就承诺不动 ~/.karvyloop;只拷 skills/ 顶层的 *.json 索引文件。
# 这个"诚实范围"同时写进每份备份的 manifest.json(scope 字段),不假装备份了全部。
BACKUP_PATTERNS = ("*.json", "*.yaml", "*.yml", "*.db", "*.sqlite", "*.sqlite3")

_SMOKE_CODE = "import karvyloop; import karvyloop.console.app"


def _kl_home() -> Path:
    return Path.home() / ".karvyloop"


def _run(argv: list, cwd=None, timeout: float = 900) -> tuple:
    """shell-out 唯一咽喉(git / pip / smoke 都走这;测试在这层 stub)。返回 (rc, 合并输出),永不抛。"""
    try:
        p = subprocess.run([str(a) for a in argv], cwd=str(cwd) if cwd else None,
                           capture_output=True, text=True, timeout=timeout)
        return p.returncode, ((p.stdout or "") + (p.stderr or "")).strip()
    except Exception as e:
        return -1, f"{type(e).__name__}: {e}"


def repo_root() -> Optional[Path]:
    """git 安装时的仓库根(包目录上溯找 .git);pip 安装 → None。"""
    here = Path(__file__).resolve()
    for p in (here, *here.parents):
        try:
            if (p / ".git").exists():
                return p
        except Exception:
            break
    return None


def snapshot_rollback_point(*, home=None, root=None) -> dict:
    """升级前记下"现在在哪":当前 git commit + 版本 → ~/.karvyloop/update_rollback.json。

    这是回滚的唯一依据;记不下来 → ok=False(没有后悔药就别动手,调用方应中止升级)。
    """
    home = Path(home) if home else _kl_home()
    root = Path(root) if root else repo_root()
    if root is None:
        return {"ok": False, "reason": "非 git 安装,找不到仓库根(无法记录回滚点)"}
    rc, out = _run(["git", "rev-parse", "HEAD"], cwd=root)
    commit = out.strip().splitlines()[0].strip() if (rc == 0 and out.strip()) else ""
    if not commit:
        return {"ok": False, "reason": f"git rev-parse HEAD 失败(rc={rc}): {out[:200]}"}
    d = {"prev_commit": commit, "prev_version": current_version(), "ts": time.time()}
    try:
        home.mkdir(parents=True, exist_ok=True)
        (home / _ROLLBACK_NAME).write_text(
            json.dumps(d, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return {"ok": False, "reason": f"写 {_ROLLBACK_NAME} 失败: {e}"}
    return {"ok": True, **d}


def _prune_backups(broot: Path, keep: int = _BACKUP_KEEP) -> None:
    """备份有界:按 mtime 只留最近 keep 份目录,更老的删(拷贝失败/半份也照删)。"""
    try:
        dirs = sorted((d for d in broot.iterdir() if d.is_dir()),
                      key=lambda d: (d.stat().st_mtime, d.name), reverse=True)
        for old in dirs[keep:]:
            shutil.rmtree(old, ignore_errors=True)
    except Exception:
        pass


def backup_instance_state(to_version: str, *, home=None, keep: int = _BACKUP_KEEP) -> dict:
    """升级前把实例状态拷进 ~/.karvyloop/backups/pre-<version>-<ts>/(迁移事故的后悔药)。

    诚实范围(也写进 manifest.json 的 scope):只拷**顶层状态文件**(BACKUP_PATTERNS)+
    skills/ 顶层 *.json 索引;skills/ 本体(SKILL.md 方法库)可能很大、且是纯文本,不整目录拷。
    跳过 `_`/`.` 开头的临时/缓存文件(_upgrade*.json、.update_check.json)。有界:只留最近 keep 份。
    """
    home = Path(home) if home else _kl_home()
    ts = time.strftime("%Y%m%d-%H%M%S")
    safe_ver = "".join(c if (c.isalnum() or c in ".-_") else "_"
                       for c in (to_version or "unknown").strip() or "unknown")
    bdir = home / "backups" / f"pre-{safe_ver}-{ts}"
    copied: list = []
    try:
        bdir.mkdir(parents=True, exist_ok=True)
        if home.exists():
            for pat in BACKUP_PATTERNS:
                for f in sorted(home.glob(pat)):
                    if not f.is_file() or f.name.startswith(("_", ".")):
                        continue          # 临时/缓存不备份
                    shutil.copy2(f, bdir / f.name)
                    copied.append(f.name)
            skills = home / "skills"
            if skills.is_dir():
                sk_out = bdir / "skills"
                for f in sorted(skills.glob("*.json")):   # 只拷索引关键 JSON(见 docstring)
                    if f.is_file():
                        sk_out.mkdir(exist_ok=True)
                        shutil.copy2(f, sk_out / f.name)
                        copied.append(f"skills/{f.name}")
        manifest = {
            "to_version": to_version, "ts": time.time(), "files": copied,
            "scope": ("top-level state files (*.json/*.yaml/*.db/*.sqlite) + skills/*.json index only; "
                      "per-entity directories (skills/ SKILL.md bodies, conversations/, roles/) "
                      "intentionally NOT copied — may be large, and upgrades never touch "
                      "~/.karvyloop by contract"),
        }
        (bdir / "manifest.json").write_text(
            json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as e:
        return {"ok": False, "reason": f"备份失败: {e}", "backup_dir": str(bdir), "files": copied}
    _prune_backups(home / "backups", keep=keep)
    return {"ok": True, "backup_dir": str(bdir), "files": copied}


def preflight(to_version: str, *, home=None, root=None) -> dict:
    """升级前置(动任何东西**之前**跑):① 记回滚点 ② 备份实例状态。

    任一步失败 → ok=False + stage/reason,调用方**中止升级**(fail-loud,别没带降落伞就跳)。
    """
    snap = snapshot_rollback_point(home=home, root=root)
    if not snap.get("ok"):
        return {"ok": False, "stage": "snapshot", "reason": snap.get("reason", ""), "prev_commit": ""}
    bak = backup_instance_state(to_version, home=home)
    if not bak.get("ok"):
        return {"ok": False, "stage": "backup", "reason": bak.get("reason", ""),
                "prev_commit": snap["prev_commit"]}
    return {"ok": True, "prev_commit": snap["prev_commit"], "prev_version": snap["prev_version"],
            "backup_dir": bak["backup_dir"]}


def post_install_smoke(*, python=None, cwd=None) -> tuple:
    """装完的最小自检:新代码能被导入吗(karvyloop + console.app)。抓"装完起不来"于重启之前。"""
    py = python or sys.executable
    rc, out = _run([py, "-c", _SMOKE_CODE], cwd=cwd)
    return (rc == 0), out[-400:]


def perform_rollback(prev_commit: str, *, root=None, python=None) -> tuple:
    """回滚本体:git reset --hard <prev_commit> + pip install -e .(重启由调用方管)。返回 (ok, why)。"""
    root = Path(root) if root else repo_root()
    if not prev_commit or root is None:
        return False, "缺回滚点 commit 或找不到仓库根"
    py = python or sys.executable
    rc, out = _run(["git", "reset", "--hard", prev_commit], cwd=root)
    if rc != 0:
        return False, f"git reset --hard 失败(rc={rc}): {out[-300:]}"
    rc2, out2 = _run([py, "-m", "pip", "install", "-e", "."], cwd=root)
    if rc2 != 0:
        return False, f"回滚后 pip install -e . 失败(rc={rc2}): {out2[-300:]}"
    return True, ""


def finalize_install(install_rc: int, prev_commit: str, *, root=None, python=None, log=None) -> dict:
    """装完后的验证 + 自动回滚决策(upgrade_runner 在重启**之前**调):

    - 升级命令 rc==0 → 跑 post-install smoke;导入失败 = 装出一个起不来的版本 → **自动回滚**。
    - 升级命令 rc!=0 → git pull 可能已把树挪到新版而 pip 半途死 → 同样回滚到已知好 commit。
    - 回滚本身失败 → 不装死,把两层原因都写清(fail-loud,留给人 + upgrade.log)。
    返回 {"ok", "rolled_back", "reason"}(reason 空 = 一切正常)。
    """
    L = log or (lambda m: None)
    if install_rc == 0:
        ok, why = post_install_smoke(python=python, cwd=root)
        if ok:
            L("post-install smoke 通过(karvyloop + console.app 可导入)")
            return {"ok": True, "rolled_back": False, "reason": ""}
        reason = f"装完自检失败(新代码导入不了): {why}"
    else:
        reason = f"升级命令失败(rc={install_rc})"
    L(f"⚠ {reason}")
    if not prev_commit:
        return {"ok": False, "rolled_back": False,
                "reason": f"{reason};且无回滚点(preflight 未记),保持现盘版本"}
    L(f"自动回滚 → git reset --hard {prev_commit[:12]} + pip install -e .")
    ok, why = perform_rollback(prev_commit, root=root, python=python)
    L("回滚完成" if ok else f"⚠ 回滚也失败: {why}")
    return {"ok": False, "rolled_back": ok,
            "reason": reason if ok else f"{reason};自动回滚也失败: {why}"}


def read_rollback_point(*, home=None) -> Optional[dict]:
    """读 update_rollback.json;无 / 坏 / 缺 prev_commit → None。"""
    home = Path(home) if home else _kl_home()
    try:
        d = json.loads((home / _ROLLBACK_NAME).read_text(encoding="utf-8"))
        return d if isinstance(d, dict) and d.get("prev_commit") else None
    except Exception:
        return None


def rollback_status(*, home=None) -> dict:
    """给 /api/update_status 的诚实字段:现在**能不能**一键回去、回去是哪个版本。"""
    info = read_rollback_point(home=home)
    return {"rollback_available": bool(info), "prev_version": (info or {}).get("prev_version")}


def rollback(*, home=None, root=None, python=None) -> dict:
    """手动回滚(端点 / CLI 用):按 update_rollback.json 回到上一个已知好版本。重启由调用方管。"""
    info = read_rollback_point(home=home)
    if not info:
        return {"ok": False, "reason": "没有记录过回滚点(还没做过带 preflight 的升级)"}
    ok, why = perform_rollback(str(info["prev_commit"]), root=root, python=python)
    return {"ok": ok, "reason": why, "prev_commit": str(info["prev_commit"]),
            "prev_version": info.get("prev_version")}


__all__ = [
    "current_version", "is_newer", "detect_install_mode", "upgrade_command",
    "check_disabled", "check_update", "GITHUB_REPO",
    "BACKUP_PATTERNS", "repo_root", "snapshot_rollback_point", "backup_instance_state",
    "preflight", "post_install_smoke", "perform_rollback", "finalize_install",
    "read_rollback_point", "rollback_status", "rollback",
]
