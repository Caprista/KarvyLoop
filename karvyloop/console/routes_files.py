"""routes_files — /api/files/* 端点(workspace 文件管理:列/看/下载/上传/删)。

从 routes.py 纯搬移(P2-② routes god-module 拆分,零逻辑改动)。自带 APIRouter,
由 app.py include_router;符号在 routes.py re-export 以保既有 import/monkeypatch 可达。

安全:**钉死在 workspace 根**(agent 产物在这);.. / 符号链接逃逸一律拒。
config/凭证(~/.karvyloop)在 workspace 之外,天然不可达 → 不会泄密。
LAN 提醒:console 绑 0.0.0.0 时局域网可访问这些文件,沿用"仅在受信网络开"的口径。
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, HTTPException, Request

router = APIRouter(prefix="/api")


def _files_root(request: Request):
    from pathlib import Path
    rk = getattr(request.app.state, "runtime_kwargs", None) or {}
    root = rk.get("workspace_root") or ""
    if not root:
        return None
    try:
        p = Path(root).resolve()
        return p if p.exists() else None
    except Exception:
        return None


def _files_safe(root, rel: str):
    """把相对路径解析进 root;越狱(../ 或符号链接逃出 root)→ None。"""
    if root is None:
        return None
    try:
        target = (root / (rel or "")).resolve()
    except Exception:
        return None
    return target if (target == root or root in target.parents) else None


@router.get("/files/list")
def api_files_list(request: Request, path: str = "") -> dict[str, Any]:
    """列 workspace 下某目录(钉死在 workspace 根)。无 workspace / 越狱 → ok:false。"""
    root = _files_root(request)
    if root is None:
        return {"ok": False, "reason": "no_workspace"}
    target = _files_safe(root, path)
    if target is None or not target.exists() or not target.is_dir():
        return {"ok": False, "reason": "bad_path"}
    entries: list[dict[str, Any]] = []
    try:
        for p in sorted(target.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                st = p.stat()
                entries.append({"name": p.name, "is_dir": p.is_dir(),
                                "size": (st.st_size if p.is_file() else 0), "mtime": st.st_mtime})
            except OSError:
                continue
    except OSError:
        return {"ok": False, "reason": "bad_path"}
    rel = "" if target == root else str(target.relative_to(root)).replace("\\", "/")
    return {"ok": True, "path": rel, "entries": entries, "workspace": str(root)}


@router.get("/files/view")
def api_files_view(request: Request, path: str) -> dict[str, Any]:
    """看文本文件(预览;封顶 100KB;非文本/过大 → 提示下载)。"""
    root = _files_root(request)
    target = _files_safe(root, path) if root else None
    if target is None or not target.is_file():
        return {"ok": False, "reason": "bad_path"}
    try:
        if target.stat().st_size > 100_000:
            return {"ok": True, "too_big": True}
        text = target.read_bytes().decode("utf-8")
        return {"ok": True, "text": text}
    except UnicodeDecodeError:
        return {"ok": True, "binary": True}
    except Exception as e:
        return {"ok": False, "reason": type(e).__name__}


@router.get("/files/download")
def api_files_download(request: Request, path: str):
    """下载 workspace 内的文件(路径越狱/非文件 → 404)。"""
    from fastapi.responses import FileResponse
    root = _files_root(request)
    target = _files_safe(root, path) if root else None
    if target is None or not target.is_file():
        raise HTTPException(status_code=404, detail="not found")
    return FileResponse(str(target), filename=target.name)


@router.post("/files/upload")
async def api_files_upload(request: Request, dir: str = "", name: str = "") -> dict[str, Any]:
    """上传文件进 workspace 的某目录(裸 body=文件字节,name/dir 走 query;免 multipart 依赖)。

    安全:目标目录钉死在 workspace 根;name 只取 basename(防 `../` / 路径分隔逃逸);封顶 100MB。
    """
    import os
    root = _files_root(request)
    if root is None:
        return {"ok": False, "reason": "no_workspace"}
    safe = os.path.basename((name or "").strip())
    if not safe or safe in (".", ".."):
        return {"ok": False, "reason": "bad_name"}
    dir_target = _files_safe(root, dir)
    if dir_target is None or not dir_target.is_dir():
        return {"ok": False, "reason": "bad_path"}
    target = (dir_target / safe).resolve()
    if not (target == root or root in target.parents):   # 双保险:仍在 root 内
        return {"ok": False, "reason": "bad_path"}
    body = await request.body()
    if len(body) > 100 * 1024 * 1024:
        return {"ok": False, "reason": "too_big"}
    try:
        target.write_bytes(body)
    except OSError as e:
        return {"ok": False, "reason": type(e).__name__}
    return {"ok": True, "name": safe, "size": len(body)}


@router.post("/files/delete")
def api_files_delete(request: Request, path: str = "") -> dict[str, Any]:
    """删 workspace 内的文件 / **空**目录(不可逆 → 前端会先确认)。
    钉死在 workspace 根:越狱拒、删根拒;非空目录拒(让用户先清里面,避免误删一整棵树)。"""
    root = _files_root(request)
    if root is None:
        return {"ok": False, "reason": "no_workspace"}
    target = _files_safe(root, path)
    if target is None or target == root or not target.exists():
        return {"ok": False, "reason": "bad_path"}   # 越狱 / 删根 / 不存在 一律拒
    try:
        if target.is_dir():
            if any(target.iterdir()):
                return {"ok": False, "reason": "not_empty"}   # 非空目录不删
            target.rmdir()
        else:
            target.unlink()
    except OSError as e:
        return {"ok": False, "reason": type(e).__name__}
    return {"ok": True}
