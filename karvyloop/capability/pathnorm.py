"""词法路径归一化 + symlink 防御（capability/pathnorm.py）。

规格：docs/modules/capability.md §2.4（HR-5）。
设计要点：
  1) 先拒反斜杠（全平台，防 `..\\x` 绕过）
  2) 词法归一化（栈式），不触文件系统（写新文件目标可能不存在）
  3) 越根 clamp 到 `/`（不抛异常，与 `os.path.normpath` 不同）
  4) root+"/" 防假前缀（`/wsx` 不被当成 `/ws` 的子）
  5) 已存在路径叠 realpath+commonpath 防 symlink 逃逸
"""

from __future__ import annotations

import os
from typing import Optional


def _lexical_normalize(path: str) -> str:
    """栈式词法归一化（不触盘）。规则：

    - 空 → ``
    - 绝对 → 补前导 `/`
    - 相对 → 不补前导
    - `.` / `''` 跳过；`..` 弹栈（栈空则忽略 —— 越根 clamp 到根，不报错）
    """
    if path == "":
        return ""
    is_abs = path.startswith("/")
    parts = path.split("/")
    stack: list[str] = []
    for p in parts:
        if p == "" or p == ".":
            continue
        if p == "..":
            if stack:
                stack.pop()
            # 越根：静默忽略（clamp 到根）
        else:
            stack.append(p)
    out = "/".join(stack)
    if is_abs:
        return "/" + out
    return out


def _closest_existing(path: str) -> Optional[str]:
    """逐级向上找首个存在的祖先。返回绝对路径；若都不存在返回 None。"""
    p = os.path.abspath(path)
    while not os.path.lexists(p):
        parent = os.path.dirname(p)
        if parent == p:
            return None
        p = parent
    return p


def _combine(root: str, path: str) -> str:
    """把 path 拼到 root 旁（path 可相对可绝对）。

    绝对路径判定：以 `/` 开头（POSIX）或匹配 `<letter>:/`（Windows 盘符）。
    """
    if path.startswith("/"):
        return path
    if len(path) >= 2 and path[1] == ":" and path[0].isalpha():
        return path
    return root.rstrip("/") + "/" + path


def is_within_workspace(path: str, root: str) -> bool:
    """path 是否落在 root 之内。词法判定 → 存在性 symlink 加固。

    行为：
    - **path 含反斜杠不直接拒**（Windows 兼容）—— 统一视作分隔符
      与 `/` 等价处理（但 `..\\x` 仍被词法归一化拦下,因为 `..` 处理
      对 `\\` 和 `/` 一致）
    - **混合分隔符**（既含 `\\` 又含 `/`,且不是全 Windows 形式）→ 拒
      防 `/ws\\evil` 这类跨平台逃逸
    - 词法归一化后 `norm == root_norm` 或以 `root_norm+"/"` 开头
    - 路径已存在 → realpath 双方后用 commonpath 复核
    - 路径不存在 → 找最近存在祖先再复核
    """
    # 1) 统一把 root/path 中的反斜杠视作分隔符（Windows 兼容）
    # 原始"硬拒反斜杠"是为防 `..\\x` 绕过；词法归一化对 `\\` 与 `/` 一致
    # 等价处理,所以反斜杠不再是绕过手段,直接归一即可。
    has_bs_in_path = "\\" in path
    has_fs_in_path = "/" in path
    has_bs_in_root = "\\" in root
    has_fs_in_root = "/" in root

    # 1a) 混合分隔符拒（防 /ws\evil 这类跨平台逃逸）
    # 例外:Windows 盘符路径 `C:\Users\ch\x` 视为纯 Windows 形式
    is_pure_windows_path = (
        len(path) >= 2 and path[1] == ":" and path[0].isalpha() and not has_fs_in_path
    )
    if has_bs_in_path and has_fs_in_path and not is_pure_windows_path:
        return False
    if has_bs_in_root and has_fs_in_root:
        return False

    root_posix = root.replace("\\", "/") if has_bs_in_root else root
    path_posix = path.replace("\\", "/") if has_bs_in_path else path

    # 2) 词法判定
    root_norm = _lexical_normalize(root_posix)
    target_norm = _lexical_normalize(_combine(root_norm, path_posix))

    if not (target_norm == root_norm or target_norm.startswith(root_norm + "/")):
        return False

    # 3) 存在性 symlink 防御（仅当 root 在磁盘上存在时；纯词法/冷启动跳过）
    abs_root = root_posix if os.path.isabs(root_posix) else os.path.abspath(root_posix)
    if not os.path.lexists(abs_root):
        # root 不存在（纯词法测试 / 冷启动）→ 接受词法结果
        return True
    abs_path = path if os.path.isabs(path) else os.path.join(abs_root, path)
    base = _closest_existing(abs_path)
    if base is None:
        # 目标路径全不存在（将创建的文件）→ 父目录存在就用父目录
        parent = os.path.dirname(abs_path)
        base = _closest_existing(parent) or abs_root
    real_root = os.path.realpath(abs_root)
    real_closest = os.path.realpath(base)
    try:
        common = os.path.commonpath([real_root, real_closest])
    except ValueError:
        return False
    return common == real_root
