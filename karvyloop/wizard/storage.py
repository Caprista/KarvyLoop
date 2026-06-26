"""Wizard storage —— 写文件 IO(单独隔离,便于测试)。

**AC5** 不变量:Ctrl+C 中断后已写文件**不**回滚(用户可能想先看看)。
本模块**不**做回滚(本来就没有回滚语义);它是"按 step_id 写 1 个文件"的薄包装。

设计:docs/11-wizard.md §3.3 不变量 3(可中断)。
"""

from __future__ import annotations

import dataclasses
import os
import pathlib
from typing import Optional


def role_dir(domain_id: str, role_id: str) -> pathlib.Path:
    """role 的存储目录:domain_dir/roles/role_id/"""
    return pathlib.Path(domain_id) / "roles" / role_id


def write_step_file(
    *,
    domain_id: str,
    role_id: str,
    file_basename: str,
    content: str,
    base_dir: Optional[pathlib.Path] = None,
) -> pathlib.Path:
    """把 1 个 step 的内容写到 domain/roles/role_id/file_basename。

    返回写入的文件路径。
    base_dir 注入用于测试(避免污染真实工作目录)。
    """
    base = base_dir or pathlib.Path.cwd()
    rd = base / role_dir(domain_id, role_id)
    rd.mkdir(parents=True, exist_ok=True)
    p = rd / file_basename
    p.write_text(content, encoding="utf-8")
    return p


@dataclasses.dataclass
class WizardWriteResult:
    """7 步写文件的累积状态(AC5:可中断)。"""
    domain_id: str
    role_id: str
    written: dict[str, pathlib.Path]   # step_id → 实际写入的路径
    skipped: list[str]                  # 跳过的 step_id
    interrupted: bool = False           # AC5: 是否中断


def initialize_role_dir(
    *,
    domain_id: str,
    role_id: str,
    base_dir: Optional[pathlib.Path] = None,
) -> pathlib.Path:
    """初始化 role 目录(创建空目录,后续 7 步往里写)。"""
    base = base_dir or pathlib.Path.cwd()
    rd = base / role_dir(domain_id, role_id)
    rd.mkdir(parents=True, exist_ok=True)
    return rd
