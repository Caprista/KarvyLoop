"""capability/fs_grants.py — 工作区外文件访问的**授权台账** + 敏感路径硬地板。

**解的矛盾**(Hardy /btw 2026-07-02):role 关死在工作区 → 本地权限发挥不了;全放开 → 密钥/系统
文件裸奔;把规矩写进每个 agent 的提示词 → 上下文膨胀。解法 = **带闸的按需授权**(机器不进提示词):

    role 碰工作区外路径 → 工具层拒绝(现状)+ 记一笔"想要"(note_denied)
      → console 在 drive 收尾把"想要"升成 H2A 授权卡(KIND_FS_ACCESS)
      → 你拍板 ACCEPT → 台账落盘 → 之后工具边界/沙箱挂载自动放行该路径
      → 能力总览一张表可见、可撤
    敏感路径(密钥/ssh/凭据库)= **硬地板**:谁批都不行,与 rm -rf 检测同级,免疫一切授权。

零上下文成本:全链路是确定性机器;唯一出现在模型/人面前的是那张授权卡(而它恰好又是口味信号)。
全局注册表模式沿用 token_ledger 先例(工具层拿不到 app,经由 register/get 取台账)。
"""
from __future__ import annotations

import json
import logging
import time
import uuid
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# ---- 敏感路径硬地板(免疫一切授权;标准=拿到即约等于拿到你的账号/钱包/机器)----
# 匹配口径:归一化(小写、/ 分隔)后的**子串**;宁可误杀多一点,也不放走一条密钥。
SENSITIVE_MARKERS: tuple[str, ...] = (
    "/.karvyloop/config.yaml",   # 我们自己的 API key(第一个就得是它)
    "/.karvyloop/console.runtime.json",
    "/tokens.db",
    "/.ssh", "/.gnupg", "/.aws", "/.azure", "/.config/gcloud",
    "/.netrc", "id_rsa", "id_ed25519",
    "/.env",                      # 惯例密钥文件
    "/login data", "/logins.json", "/key4.db", "/cookies",   # 浏览器凭据/会话
    "/.kube/config", "/.docker/config.json",
    "/etc/shadow", "/etc/sudoers",
    "/appdata/roaming/karvyloop",  # 预留(Windows 侧数据)
)


def _norm(p: str) -> str:
    return str(p or "").replace("\\", "/").lower()


def is_sensitive_path(path: str) -> bool:
    """敏感路径判定(硬地板)。expanduser 后归一化做子串匹配。"""
    try:
        n = _norm(str(Path(path).expanduser()))
    except Exception:
        n = _norm(path)
    return any(m in n for m in SENSITIVE_MARKERS)


class FsGrantsStore:
    """授权台账(落盘,fail-safe)。一条授权 = {id, path, ops, role, origin, created_at, expires_at}。

    - path 授权是**前缀语义**:授了目录=目录下全部(敏感地板仍然优先)。
    - role 为空 = 任意角色(单用户现实下的 v1;按角色收窄留字段)。
    - expires_at None = 永久(能力总览可撤)。
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else None
        self._grants: list[dict] = []
        self._denied: list[dict] = []   # "想要"队列(内存即可,drive 收尾就消费)
        if self._path is not None and self._path.exists():
            try:
                d = json.loads(self._path.read_text(encoding="utf-8"))
                self._grants = [g for g in (d.get("grants") or []) if isinstance(g, dict)]
            except Exception:
                pass

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps({"grants": self._grants}, ensure_ascii=False, indent=2),
                                  encoding="utf-8")
        except Exception as e:
            logger.warning("[fs_grants] 落盘失败(不阻断): %s", e)

    # ---- 判定 ----

    def allows(self, path: str, op: str = "read", *, role: str = "",
               now: Optional[float] = None) -> bool:
        """该路径该操作是否已被授权。敏感地板绝对优先(授了也不算)。"""
        if is_sensitive_path(path):
            return False
        n = now if now is not None else time.time()
        try:
            target = _norm(str(Path(path).expanduser().resolve()))
        except Exception:
            target = _norm(path)
        for g in self._grants:
            exp = g.get("expires_at")
            if exp is not None and n > float(exp):
                continue
            g_role = (g.get("role") or "").strip()
            if g_role and role and g_role != role:
                continue
            if op not in (g.get("ops") or ["read"]):
                continue
            base = _norm(g.get("path", ""))
            if base and (target == base or target.startswith(base.rstrip("/") + "/")):
                return True
        return False

    # ---- 记账 ----

    def record(self, path: str, ops: list, *, role: str = "", origin: str = "h2a",
               ttl_seconds: Optional[float] = None, now: Optional[float] = None) -> Optional[dict]:
        """落一条授权。敏感路径**拒记**(硬地板;返回 None)。同 path+ops+role 幂等。"""
        if is_sensitive_path(path):
            logger.warning("[fs_grants] 拒绝授权敏感路径(硬地板): %s", path)
            return None
        try:
            norm_path = str(Path(path).expanduser().resolve())
        except Exception:
            norm_path = path
        clean_ops = sorted({o for o in (ops or []) if o in ("read", "write")}) or ["read"]
        n = now if now is not None else time.time()
        for g in self._grants:   # 幂等
            if _norm(g.get("path", "")) == _norm(norm_path) and (g.get("ops") or []) == clean_ops \
                    and (g.get("role") or "") == (role or ""):
                return g
        g = {"id": uuid.uuid4().hex[:12], "path": norm_path, "ops": clean_ops,
             "role": role or "", "origin": origin, "created_at": n,
             "expires_at": (n + ttl_seconds) if ttl_seconds else None}
        self._grants.append(g)
        self._save()
        return g

    def revoke(self, grant_id: str) -> bool:
        before = len(self._grants)
        self._grants = [g for g in self._grants if g.get("id") != grant_id]
        if len(self._grants) != before:
            self._save()
            return True
        return False

    def list(self, *, now: Optional[float] = None) -> list:
        n = now if now is not None else time.time()
        return [dict(g, expired=(g.get("expires_at") is not None and n > float(g["expires_at"])))
                for g in self._grants]

    # ---- "想要"队列(拒绝→升卡的信使)----

    def note_denied(self, path: str, op: str) -> None:
        """工具层碰壁时记一笔"想要"(敏感路径不记 —— 那不是可授权的事)。"""
        if not path or is_sensitive_path(path):
            return
        self._denied.append({"path": str(path), "op": op, "ts": time.time()})
        if len(self._denied) > 50:
            self._denied = self._denied[-50:]

    def pop_denied(self) -> list:
        """取走并清空"想要"队列(console drive 收尾消费,去重后升卡)。"""
        out, self._denied = self._denied, []
        # 去重(同 path+op 只留一条)
        seen: set = set()
        uniq = []
        for d in out:
            k = (_norm(d["path"]), d["op"])
            if k not in seen:
                seen.add(k)
                uniq.append(d)
        return uniq


# ---- 全局注册表(token_ledger 同款先例:工具层拿不到 app,经由这里取台账)----

_store: Optional[FsGrantsStore] = None


def register_store(store: Optional[FsGrantsStore]) -> None:
    global _store
    _store = store


def get_store() -> Optional[FsGrantsStore]:
    return _store


def path_allowed(path: str, op: str = "read", *, workspace_root: str = "",
                 role: str = "") -> bool:
    """工具层/能力链共用的一站判定:工作区内 → 行;台账授过 → 行;其余 → 不行。
    敏感地板在 allows 里绝对优先。没注册台账 = 只有工作区(0 回归)。"""
    from .pathnorm import is_within_workspace
    if is_sensitive_path(path):
        return False
    if workspace_root and is_within_workspace(path, workspace_root):
        return True
    st = get_store()
    return bool(st is not None and st.allows(path, op, role=role))


def note_denied(path: str, op: str) -> None:
    """便捷:有台账就记"想要"(没有=静默,不碍事)。"""
    st = get_store()
    if st is not None:
        st.note_denied(path, op)


__all__ = ["FsGrantsStore", "is_sensitive_path", "SENSITIVE_MARKERS",
           "register_store", "get_store", "path_allowed", "note_denied"]
