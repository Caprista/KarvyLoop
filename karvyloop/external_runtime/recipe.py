"""external_runtime/recipe — DriveRecipe:接一个外部 runtime = 写一份配方,不改 Python。

一份配方 = 命令模板(argv 数组,**绝不 shell 拼**)+ 输出解析(single_json/ndjson/raw_text)+
退出语义(ok_codes/空成功判 failed)+ 元数据出口(有边车填 sidecar,无则 none)+
密钥过滤规则 + 平台分叉 env + preflight 校验项 + 已知泄 key 入口黑名单。

三种 parse_mode 覆盖 headless CLI 输出形态的绝大多数:
- single_json:stdout 是单个 pretty JSON,回复取 text_path,元数据取 meta_path。
- ndjson:逐行 JSON,翻译成阶段事件 + 最终 assistant text。
- raw_text:整个 stdout(过滤后)就是答案;元数据走 --usage-file 边车(有则填 meta_from_sidecar)。

**配方三平台一致纪律**:路径用 `~` 归一 + os.path.expanduser;平台分叉字段(如 runtime home)
显式给;argv 数组化天然免 shell 引号差异。

安全默认最严:key 只描述"从哪读"(不含 key 值);blocked_entrypoints 里的已知泄 key 入口桥拒调。
"""
from __future__ import annotations

import dataclasses
import os
from typing import Optional


# 支持的解析模式(第 4 个 runtime 起若讲这三种之一即纯配方活)
PARSE_SINGLE_JSON = "single_json"
PARSE_NDJSON = "ndjson"
PARSE_RAW_TEXT = "raw_text"
_PARSE_MODES = frozenset({PARSE_SINGLE_JSON, PARSE_NDJSON, PARSE_RAW_TEXT})


@dataclasses.dataclass(frozen=True)
class ExitSpec:
    """退出语义:哪些码算成功、退 0 但空是否判 failed。"""
    ok_codes: tuple[int, ...] = (0,)
    empty_is_failure: bool = True          # 退 0 但产出空 → 判 failed(假成功坑)
    bad_args: Optional[int] = None         # 坏参数码(诚实记录,不用于判定)


@dataclasses.dataclass(frozen=True)
class ParseSpec:
    """输出解析规则。"""
    mode: str = PARSE_RAW_TEXT
    text_path: str = ""                    # single_json 回复取值路径,如 "payloads[0].text"
    meta_path: str = ""                    # single_json 元数据路径,如 "meta"
    meta_from_sidecar: bool = False        # 元数据走 --usage-file 边车 JSON(有出口=True)


@dataclasses.dataclass(frozen=True)
class DriveRecipe:
    """一个外部 runtime 的完整驱动配方(#71 §3.2 / #72 §1.4)。

    argv_template 用占位符 `{prompt}` / `{session_key}` / `{agent_id}` / `{sidecar_path}`,
    由 bridge 在起进程时填真值(填的也是 argv 元素,绝不进 shell)。
    """
    runtime_kind: str
    bin_path: str
    argv_template: tuple[str, ...]
    parse: ParseSpec = dataclasses.field(default_factory=ParseSpec)
    exit: ExitSpec = dataclasses.field(default_factory=ExitSpec)
    extra_path: tuple[str, ...] = ()          # 起进程时前置进 PATH(如 runtime 依赖的 node bin)
    key_source_kind: str = ""                 # local_config | local_env(只描述从哪读)
    key_source_path: str = ""                 # 本地 config/env 路径(存在性检查,绝不读内容)
    env: tuple[tuple[str, str], ...] = ()     # 平台分叉/沙箱兜底 env(非密;key 绝不进这里)
    preflight: tuple[str, ...] = ()           # 接入向导必须满足的预置项(人读)
    redact_patterns: tuple[str, ...] = ()     # per-runtime 额外密钥过滤正则
    blocked_entrypoints: tuple[str, ...] = ()  # 已知泄 key/危险入口,桥拒调
    timeout_wall_s: int = 900                 # 桥侧 wall-clock 上限(不依赖 CLI 自己的 timeout)
    smoke_prompt: str = "reply with the single word READY"  # 探活冒烟 prompt
    smoke_anchor: str = "READY"               # 冒烟锚(确定性可判,不用 LLM 判)
    #: 这份配方**能真驱动**的外部 runtime 常见可执行名(doctor 按需接入引导按此探 PATH)。
    #: 纪律:只列"我们 ship 了能驱动它的配方"的 bin —— "可接入"是真的,不给没配方的 runtime 画饼。
    #: 中性名纪律(公开仓):这些是各类 headless CLI agent 的确定性 bin 名(PATH 探测键),非依赖/非背书。
    probe_bins: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.parse.mode not in _PARSE_MODES:
            raise ValueError(
                f"DriveRecipe: 未知 parse.mode {self.parse.mode!r} "
                f"(支持 {sorted(_PARSE_MODES)})")
        if not self.runtime_kind:
            raise ValueError("DriveRecipe: runtime_kind 必填")
        if not self.argv_template:
            raise ValueError("DriveRecipe: argv_template 不能为空")

    def resolved_bin(self) -> str:
        return os.path.expanduser(self.bin_path or "")

    def env_map(self) -> dict[str, str]:
        return {k: v for k, v in self.env}


# ---- 三份内置配方(从 VM 实测固化;无真 key)----
# 中性词纪律:公开仓不点参照工程名,用 runtime_kind 归类。配方值(命令 flag/路径)是"理解"来的接入
# 事实,非复制代码;真实 bin 路径由 citizen 探测时填,配方给默认。


def _generic_cli_recipe() -> DriveRecipe:
    """讲 stream-json 的通用 headless CLI(ndjson 逐行 → 阶段事件 + 最终 assistant text)。"""
    return DriveRecipe(
        runtime_kind="generic_cli",
        bin_path="",   # 由 citizen 探测填
        argv_template=("-p", "{prompt}", "--output-format", "stream-json"),
        parse=ParseSpec(mode=PARSE_NDJSON),
        exit=ExitSpec(ok_codes=(0,), empty_is_failure=True),
        redact_patterns=(),
        timeout_wall_s=900,
        # probe_bins 纪律:只列**确知用此确切 CLI**的 bin,别给没验过的 runtime 画饼。
        # `claude -p "<task>" --output-format stream-json` 是公开文档化的 headless 语法(互操作名可留);
        # codex/gemini/opencode 未验此语法 → 不列(用户自己配方里填 bin)。
        probe_bins=("claude",),
    )


def _single_json_recipe() -> DriveRecipe:
    """讲单 pretty JSON 的 headless runtime(回复在 payloads[0].text,元数据在 meta 内嵌)。"""
    return DriveRecipe(
        runtime_kind="single_json_cli",
        bin_path="",
        argv_template=("agent", "--local", "--agent", "{agent_id}",
                       "--session-key", "{session_key}", "--json", "-m", "{prompt}"),
        parse=ParseSpec(mode=PARSE_SINGLE_JSON, text_path="payloads[0].text", meta_path="meta"),
        exit=ExitSpec(ok_codes=(0,), empty_is_failure=True),
        preflight=("exec-policy ask=off(否则 headless 挂死等人审)",),
        timeout_wall_s=900,
        # 此 CLI 形态(`agent --local --json`)= VM 实测过的那个 runtime 的确切语法,但它是我们的
        # 参照工程、按中性名纪律不写进出货代码 → 内置 **shape-only**、不认领具体产品名(不画饼);
        # 用户在自己配方里填这份形态对应的 bin。
        probe_bins=(),
    )


def _raw_text_sidecar_recipe() -> DriveRecipe:
    """讲纯文本(零横幅)的一次性 runtime,usage 走 --usage-file 边车 JSON(有元数据出口)。

    VM 实测:`-z "<task>" --safe-mode --usage-file <out>` 退码 0、stdout 只有答案、边车写出
    usage(input/output/total_tokens + model + provider),文件无 key。桥读边车记进 ext: 账本。
    """
    return DriveRecipe(
        runtime_kind="raw_text_sidecar",
        bin_path="",
        # -z 一次性;--safe-mode + --usage-file 边车;{sidecar_path} 由 bridge 填临时文件路径
        argv_template=("-z", "{prompt}", "--safe-mode", "--usage-file", "{sidecar_path}"),
        parse=ParseSpec(mode=PARSE_RAW_TEXT, meta_from_sidecar=True),
        exit=ExitSpec(ok_codes=(0,), empty_is_failure=True, bad_args=2),
        # -z 自动 bypass 审批 → preflight 要求限只读工具集或沙箱兜底(否则违 H2A)
        preflight=("toolset 限只读 或 沙箱兜底(否则无人审跑危险工具)",),
        # 已知泄 key 的入口:空 provider 打 404 且把 key 前缀打进 stdout → 桥拒调
        blocked_entrypoints=("agent_entrypoint", "run_agent"),
        timeout_wall_s=900,
        # 此 CLI 形态(`-z --safe-mode --usage-file`)= VM 实测过的那个 runtime 的确切语法,同属
        # 参照工程、中性名纪律不写进出货代码 → 内置 **shape-only**、不认领具体产品名(不画饼);
        # 用户在自己配方里填这份形态对应的 bin。
        probe_bins=(),
    )


_BUILTIN = {
    "generic_cli": _generic_cli_recipe,
    "single_json_cli": _single_json_recipe,
    "raw_text_sidecar": _raw_text_sidecar_recipe,
}


def builtin_recipe(runtime_kind: str) -> Optional[DriveRecipe]:
    """按 runtime_kind 取一份内置配方(未知 → None)。"""
    factory = _BUILTIN.get((runtime_kind or "").strip())
    return factory() if factory else None


def builtin_kinds() -> tuple[str, ...]:
    return tuple(_BUILTIN.keys())


def builtin_probe_bins() -> tuple[str, ...]:
    """我们 ship 的所有内置配方**能真驱动**的外部 runtime bin 名(去重、保持稳定顺序)。

    doctor 按需接入引导据此探 PATH —— **只探我们有配方能驱动的 bin**,让"可接入"是真的
    (不给没配方的 runtime 画饼)。派生规则:
      - 配方显式 `probe_bins` → 用它(首选,人读清楚这份配方驱动谁)。
      - 没显式 probe_bins 时,退回 `argv_template[0]` —— 但仅当它不是选项(不以 '-' 起、非占位符),
        否则跳过(argv[0] 是 flag/占位符 = 无 bin 名可派生,不硬塞)。
    """
    seen: dict[str, None] = {}
    for factory in _BUILTIN.values():
        r = factory()
        bins = list(r.probe_bins or ())
        if not bins:
            first = (r.argv_template[0] if r.argv_template else "") or ""
            if first and not first.startswith("-") and "{" not in first:
                bins = [first]
        for b in bins:
            b = (b or "").strip()
            if b:
                seen.setdefault(b, None)
    return tuple(seen.keys())


__all__ = [
    "DriveRecipe", "ParseSpec", "ExitSpec",
    "PARSE_SINGLE_JSON", "PARSE_NDJSON", "PARSE_RAW_TEXT",
    "builtin_recipe", "builtin_kinds", "builtin_probe_bins",
]
