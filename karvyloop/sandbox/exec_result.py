"""执行结果（sandbox/exec_result.py）。

规格：docs/modules/sandbox.md §3。
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class ExecResult:
    stdout: bytes
    stderr: bytes
    exit_code: int
    interrupted: bool = False
    timed_out: bool = False
    truncated: bool = False
