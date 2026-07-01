"""coding — Forge — coding 执行器（read-before-write / NDJSON / 会话脱敏）

规格（函数级实现架构 + 签名级接口 + 验收标准）：docs/modules/forge.md
里程碑：M0。状态：实现 + 通过 self-acceptance。
**纪律**:Forge 不另起 ReAct 循环,直接复用 atoms.executor.run;
它只提供 coding 工具集 / 提示词 / 会话 / NDJSON 薄封装(HR + spec §2.1 边界)。
"""

from __future__ import annotations

from .filestate import (
    CHANGED_SINCE_READ,
    READ_REQUIRED,
    FileState,
    ReadBeforeWriteError,
    Snapshot,
)
from .forge import RunResult, generate_and_run
from .ndjson import FORMAT_VERSION, SCHEMA as NDJSON_SCHEMA, NdjsonEmitter
from .prompt import (
    BOUNDARY_MARKER,
    GIT_DIFF_MAX,
    INSTRUCTION_FILE_MAX,
    INSTRUCTION_TOTAL_MAX,
    CodingPrompt,
    build_coding_prompt,
    collect_instruction_files,
)
from .session import (
    FORMAT_VERSION as SESSION_FORMAT_VERSION,
    MAX_FIELD_CHARS,
    MAX_FILE_BYTES,
    ROTATE_KEEP,
    SESSION_SCHEMA,
    ForgeSession,
    SessionMeta,
    _redact,
    _scrub_for_disk,
)
from .tools import (
    BashTool,
    CodingResult,
    EditTool,
    ReadTool,
    WriteTool,
    make_coding_tools,
)

__all__ = [
    # 主入口
    "generate_and_run", "RunResult",
    # 工具
    "ReadTool", "WriteTool", "EditTool", "BashTool", "make_coding_tools", "CodingResult",
    # 文件状态机
    "FileState", "Snapshot", "ReadBeforeWriteError", "READ_REQUIRED", "CHANGED_SINCE_READ",
    # NDJSON
    "NdjsonEmitter", "NDJSON_SCHEMA",
    # Session
    "ForgeSession", "SessionMeta", "SESSION_SCHEMA", "SESSION_FORMAT_VERSION",
    "MAX_FIELD_CHARS", "MAX_FILE_BYTES", "ROTATE_KEEP",
    "_redact", "_scrub_for_disk",
    # Prompt
    "CodingPrompt", "build_coding_prompt", "BOUNDARY_MARKER",
    "INSTRUCTION_FILE_MAX", "INSTRUCTION_TOTAL_MAX", "GIT_DIFF_MAX",
    "collect_instruction_files",
]
