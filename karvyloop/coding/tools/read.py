"""Read 工具（coding/tools/read.py）。

规格：docs/modules/forge.md §2.2 Read 行 + HR-4/HR-5。
调用方在 make_coding_tools 时注入 sandbox + file_state + workspace_root;
token 在工具被调时通过 call() 传入(不存进实例,避免过期 token 残留)。
"""

from __future__ import annotations

import os
from typing import Any

from karvyloop.capability import is_within_workspace
from karvyloop.schemas import CapabilityToken

from ..filestate import FileState
from ._result import CodingResult


class ReadTool:
    name = "read_file"
    description = "Read a file with line numbers (HR-4: records snapshot for read-before-write)"
    parameters = {
        "type": "object",
        "properties": {
            "file_path": {"type": "string"},
            "offset": {"type": "integer", "default": 0},
            "limit": {"type": "integer", "default": 2000},
        },
        "required": ["file_path"],
    }

    def __init__(self, sandbox, file_state: FileState, workspace_root: str, *, token: CapabilityToken):
        self.sandbox = sandbox
        self.fs = file_state
        self.workspace_root = workspace_root
        self.token = token  # 单任务生命周期内不变

    def is_concurrency_safe(self, inp: dict) -> bool:
        return True  # read 只读,always safe

    async def __call__(self, inp: dict) -> CodingResult:
        path = inp.get("file_path", "")
        from karvyloop.capability.fs_grants import note_denied, path_allowed
        if not path or not path_allowed(path, "read", workspace_root=self.workspace_root):
            # 授权台账:工作区外且未授权 → 记"想要"(console 会升 H2A 授权卡),这次仍拒
            if path:
                note_denied(path, "read")
            return CodingResult(ok=False, payload=None, error_code=1,
                                error_message=f"路径 {path} 越出工作区(可授权:等待你在决策卡上放行)")
        offset = int(inp.get("offset", 0))
        limit = int(inp.get("limit", 2000))
        try:
            content = await self.sandbox.read_file(path, self.token)
        except PermissionError as e:
            return CodingResult(ok=False, payload=None, error_code=5,
                                error_message=f"未授权: {e}")
        except FileNotFoundError:
            return CodingResult(ok=False, payload=None, error_code=6,
                                error_message=f"文件不存在: {path}")
        except Exception as e:
            return CodingResult(ok=False, payload=None, error_code=4,
                                error_message=f"读取失败: {type(e).__name__}: {e}")
        # 记快照(HR-4 状态机)
        self.fs.record_read(path, content)
        if not content:
            return CodingResult(ok=True,
                                payload={"system_reminder": "文件为空"},
                                truncated=False)
        # 附件真解析:PDF/docx/xlsx → 文本、音频(mp3/wav/m4a)→ 本地 ASR 文字稿,
        # 之后与 CSV 走同一条产线(行号/offset/limit/truncated)。
        # 解析器纪律(宁空勿毒):坏文件/缺依赖 → 明确报错,绝不把二进制垃圾灌进上下文。
        from karvyloop.file_extract import extract_kind, extract_text
        kind = extract_kind(path)
        extract_truncated = False
        if kind:
            # 转写/解析可能秒级~分钟级(长录音),丢线程池 —— 不卡 loop 上其他任务的事件流
            import asyncio
            res = await asyncio.to_thread(extract_text, content, kind)
            if not res.ok:
                if res.error == "missing_dependency":
                    # hint 自带对应 extra 的安装命令([files] / [asr]),别硬编码成同一个
                    return CodingResult(ok=False, payload=None, error_code=7,
                                        error_message=(f"无法解析 {kind} 附件:缺可选依赖 —— "
                                                       f"{res.hint},装好后重试"))
                if res.error == "asr_failed":
                    return CodingResult(ok=False, payload=None, error_code=7,
                                        error_message=f"无法转写音频:{res.hint}")
                return CodingResult(ok=False, payload=None, error_code=7,
                                    error_message=(f"无法解析 {kind} 附件:文件损坏或与扩展名不符"
                                                   f"({res.hint})—— 拒绝注入二进制垃圾"))
            if not res.text:
                return CodingResult(ok=True,
                                    payload={"system_reminder":
                                             f"{kind} 解析成功但无可提取文本"
                                             f"(可能是扫描件/纯图像/无人声音频)"},
                                    truncated=False)
            text = res.text
            extract_truncated = res.truncated
        else:
            # sandbox 返回 bytes,统一 decode 为 str 再分行
            text = content.decode("utf-8", errors="replace")
        lines = text.splitlines()
        sliced = lines[offset:offset + limit]
        body = "\n".join(sliced)
        truncated = extract_truncated or (offset + limit) < len(lines)
        out = "\n".join(f"{i+1+offset:>6}\t{line}" for i, line in enumerate(body.splitlines()))
        return CodingResult(ok=True, payload=out, truncated=truncated)
