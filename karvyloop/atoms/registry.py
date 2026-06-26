"""atoms/registry — 公共原子库(L1 镜像 CRUD + 持久化,M3+ 拍 9.5 #3-P1)。

设计:docs/00 §2.3(L1 原子)+ schemas/atom.py。

**甲(用户 2026-06-16 写进 schema + 2026-06-19「买糖」比喻确认)**:
原子是**公共能力池,不属于任何 role**。角色**用**原子(写进 COMPOSITION 配方),不拥有。
建角色时缺哪个原子就**就地建一个**(买糖)→ 落进**这个公共库** → 以后任何角色都能用。
来源:KarvyLoop 内置 / **用户自建** / 外部导入(MCP,M2+)。

**镜像 vs 实例(§2.1)**:这里管的是**镜像**(AtomSpec 静态定义,可分发);
实例(AtomRun + 记忆)是用出来的,不在这。

**持久化**:`~/.karvyloop/atoms.json` 一个数组(pydantic model_dump/validate);atomic 写。
原子是公共池、可增可删(区别于业务域的 archive-only)—— M1 允许删(P2 再加"被角色引用则拦")。
"""
from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Optional

from karvyloop.schemas.atom import AtomSpec

# 原子 id 必须 COMPOSITION-safe:ethos/auditor 抓 `atom: <name>` 用 [A-Za-z0-9_]+,
# 带连字符/空格的名字在 COMPOSITION.yaml 里引用不到 → 强制下划线命名。
_ATOM_ID_RE = re.compile(r"^[A-Za-z0-9_]+$")


class AtomStore:
    """原子镜像的磁盘存储(JSON 数组,atomic 写)。"""

    def __init__(self, path) -> None:
        self._path = Path(path)
        self._path.parent.mkdir(parents=True, exist_ok=True)

    def load_all(self) -> list[AtomSpec]:
        """读回所有原子镜像。文件不存在 / 坏 → 返空(不阻塞启动)。"""
        if not self._path.exists():
            return []
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError):
            return []
        if not isinstance(data, list):
            return []
        out: list[AtomSpec] = []
        for rec in data:
            if not isinstance(rec, dict):
                continue
            try:
                out.append(AtomSpec.model_validate(rec))
            except Exception:
                continue  # 坏项跳过,不阻塞其它
        return out

    def save_all(self, atoms) -> None:
        """整存(atomic:.tmp → replace)。"""
        payload = json.dumps([a.model_dump() for a in atoms], ensure_ascii=False, indent=2)
        tmp = self._path.with_suffix(self._path.suffix + ".tmp")
        tmp.write_text(payload, encoding="utf-8")
        os.replace(tmp, self._path)


class DuplicateAtomError(ValueError):
    """原子 id(名字)已存在。"""


class AtomRegistry:
    """公共原子库(进程内镜像 + 可选持久化)。

    D7 风格:store 依赖注入(None = 纯内存,测试友好)。
    """

    def __init__(self, *, store: Optional[AtomStore] = None) -> None:
        self._atoms: dict[str, AtomSpec] = {}
        self._store = store
        if store is not None:
            for a in store.load_all():
                self._atoms[a.id] = a

    # ---- 创建(买糖)----
    def create(
        self,
        atom_id: str,
        kind: str,
        prompt: str,
        *,
        tools: Optional[list[str]] = None,
        model: Optional[str] = None,
        required_capabilities: Optional[list] = None,
        is_read_only: bool = False,
        is_concurrency_safe: bool = False,
        input_schema: Optional[dict] = None,
        output_schema: Optional[dict] = None,
    ) -> AtomSpec:
        """建一个原子镜像入公共库。

        atom_id = 人可读的名字(如 "web-search" / "prd-writer"),做唯一标识。
        重名 → DuplicateAtomError(不偷偷覆盖)。input/output schema M1 缺省 object。
        """
        aid = (atom_id or "").strip()
        if not aid:
            raise ValueError("atom_id(名字)不能为空")
        if not _ATOM_ID_RE.match(aid):
            raise ValueError(
                f"atom_id「{aid}」只能含字母/数字/下划线(要能被 COMPOSITION.yaml `atom: x` 引用)"
            )
        if kind not in ("task", "daemon"):
            raise ValueError(f"kind 必须是 task/daemon,得到 {kind!r}")
        if aid in self._atoms:
            raise DuplicateAtomError(f"原子「{aid}」已存在(原子是公共库,换个名字)")
        spec = AtomSpec(
            id=aid,
            kind=kind,
            prompt=prompt or "",
            input_schema=input_schema or {"type": "object"},
            output_schema=output_schema or {"type": "object"},
            tools=list(tools or []),
            required_capabilities=list(required_capabilities or []),
            model=model,
            is_read_only=is_read_only,
            is_concurrency_safe=is_concurrency_safe,
        )
        self._atoms[aid] = spec
        self._persist()
        return spec

    # ---- 读 ----
    def get(self, atom_id: str) -> Optional[AtomSpec]:
        return self._atoms.get(atom_id)

    def list_all(self) -> list[AtomSpec]:
        return list(self._atoms.values())

    def __len__(self) -> int:
        return len(self._atoms)

    # ---- 删(原子是公共池,可删;P2 再加"被角色引用则拦")----
    def remove(self, atom_id: str) -> bool:
        if atom_id in self._atoms:
            del self._atoms[atom_id]
            self._persist()
            return True
        return False

    def _persist(self) -> None:
        if self._store is not None:
            self._store.save_all(self._atoms.values())


__all__ = ["AtomRegistry", "AtomStore", "DuplicateAtomError"]
