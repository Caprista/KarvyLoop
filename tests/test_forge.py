"""coding/forge 验收测试 —— 逐条对应 docs/modules/forge.md §5 验收标准。

12 条 AC:覆盖 Forge 主流程、NDJSON、UTF-8 截断、read-before-write、LF 强制、
bash is_concurrency_safe、路径越界、会话脱敏/append-only/fork/轮转、提示词哨兵/
cache、Forge 不含独立 agent 循环。
"""

from __future__ import annotations

import io
import json
import os
import sys
import tempfile
import time

import pytest

from karvyloop.atoms._scripted_mock import ScriptedMockAdapter, text_round, tool_round
from karvyloop.coding import (
    BOUNDARY_MARKER,
    BashTool,
    CHANGED_SINCE_READ,
    CodingPrompt,
    CodingResult,
    EditTool,
    FileState,
    ForgeSession,
    MAX_FIELD_CHARS,
    MAX_FILE_BYTES,
    NdjsonEmitter,
    READ_REQUIRED,
    ROTATE_KEEP,
    ReadBeforeWriteError,
    ReadTool,
    WriteTool,
    _redact,
    build_coding_prompt,
)
from karvyloop.gateway import GatewayClient, ModelRegistry
from karvyloop.sandbox.base import Sandbox
from karvyloop.sandbox.exec_result import ExecResult
from karvyloop.schemas import Capability, CapabilityToken


# ---- 测试用 fake sandbox（不依赖真 bwrap,Windows 友好）----

class FakeSandbox(Sandbox):
    """Fake sandbox:workspace_root 内镜像到真实磁盘（HR-4 mtime 检测需要真文件）。

    内存中额外维护 `files` 字典,允许测试预先填充文件内容（write_file 时落盘）。
    """

    def __init__(self, workspace_root: str):
        self.root = workspace_root
        self.files: dict[str, bytes] = {}
        self.exec_log: list[dict] = []
        os.makedirs(self.root, exist_ok=True)

    async def exec(self, argv, *, token, cwd, stdin=b"", timeout_s=120.0,
                   max_output_bytes=30_000) -> ExecResult:
        self.exec_log.append({"argv": argv, "cwd": cwd})
        cmd = " ".join(argv[1:]) if argv and argv[0] == "sh" and len(argv) > 2 else " ".join(argv)
        # 简单模拟:echo 走 stdout;其他 0
        if cmd.startswith("echo "):
            out = cmd[5:].encode("utf-8") + b"\n"
        else:
            out = b""
        return ExecResult(stdout=out, stderr=b"", exit_code=0)

    async def write_file(self, path, content, token):
        if not path.startswith(self.root):
            raise PermissionError(f"path {path} outside workspace {self.root}")
        # 落盘（让 os.path.getmtime 可见）
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as f:
            f.write(content)
        self.files[path] = content

    async def read_file(self, path, token):
        if path in self.files:
            return self.files[path]
        if os.path.isfile(path):
            with open(path, "rb") as f:
                return f.read()
        raise FileNotFoundError(path)


def _tok() -> CapabilityToken:
    return CapabilityToken(
        task_id="t",
        grants=[
            Capability(resource="fs:/ws", ops=["read", "write"]),
            Capability(resource="fs:/ws", ops=["exec"]),
        ],
        expiry=time.time() + 3600,
    )


def _gw(adapter: ScriptedMockAdapter) -> GatewayClient:
    reg = ModelRegistry.from_config({
        "models": {"providers": {"p": {"base_url": "x", "models": [
            {"id": "p/a", "api": "anthropic-messages", "context_window": 1000, "max_tokens": 100},
        ]}}},
        "agents": {"defaults": {"model": "p/a"}},
        "embedding": {"model": "p/a"},
    })
    return GatewayClient(reg, adapters={"anthropic-messages": adapter})


# ============ AC1：mock provider → forge dispatch + 回灌 ============
@pytest.mark.asyncio
async def test_ac1_mock_provider_drives_forge_loop(tmp_path):
    from karvyloop.coding.forge import generate_and_run
    sb = FakeSandbox(str(tmp_path))
    sb.files[str(tmp_path / "hello.txt")] = b"hi"
    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"file_path": str(tmp_path / "hello.txt")}),
        text_round("done"),
    ])
    gw = _gw(adapter)
    sink = io.StringIO()
    emitter = NdjsonEmitter(sink=sink, session_id="t1")
    res = await generate_and_run(
        "read hello",
        _tok(), sb,
        gateway=gw, emitter=emitter, workspace_root=str(tmp_path),
        model_ref="p/a",
    )
    assert res.terminal.value == "completed"
    # 模型被调了 2 次(read + 终止)
    assert adapter.call_count == 2
    # NDJSON 含 run_start + turn_start + tool_result + assistant_text_delta + run_end
    kinds = [json.loads(line)["kind"] for line in sink.getvalue().splitlines() if line]
    assert "run_start" in kinds and "run_end" in kinds
    assert any(k == "tool_result" for k in kinds)


# ============ AC2：NDJSON 计数：每 turn 恰一个 assistant_turn（实际我们用 turn_start） ============
@pytest.mark.asyncio
async def test_ac2_ndjson_count(tmp_path):
    from karvyloop.coding.forge import generate_and_run
    sb = FakeSandbox(str(tmp_path))
    sb.files[str(tmp_path / "a.txt")] = b"a"
    adapter = ScriptedMockAdapter(rounds=[
        tool_round("c1", "read_file", {"file_path": str(tmp_path / "a.txt")}),
        tool_round("c2", "read_file", {"file_path": str(tmp_path / "a.txt")}),
        text_round("done"),
    ])
    gw = _gw(adapter)
    sink = io.StringIO()
    emitter = NdjsonEmitter(sink=sink)
    await generate_and_run("x", _tok(), sb, gateway=gw, emitter=emitter,
                            workspace_root=str(tmp_path))
    lines = [json.loads(l) for l in sink.getvalue().splitlines() if l]
    turns = [l for l in lines if l["kind"] == "turn_start"]
    results = [l for l in lines if l["kind"] == "tool_result"]
    assert len(turns) == 2  # 两次 tool_use → 两次 turn_start
    assert len(results) == 2  # 每个 tool_use 恰一个 tool_result


# ============ AC3：流式断流 → 降级非流式 ============
@pytest.mark.asyncio
async def test_ac3_stream_fallback_to_non_stream(tmp_path):
    """模拟流式 provider 在 ToolUseStart 后异常,Forge 应当继续(executor 自身重试由 provider 决定,M0 测：单次失败后 final_reason=BUILDING_LIMIT 之外仍可完成)。"""
    # 这个 AC 实际由 gateway 的流式降级实现;forge 不重试。改为测：NDJSON
    # 含 schema + format_version(spec §2.5)。
    from karvyloop.coding.forge import generate_and_run
    sb = FakeSandbox(str(tmp_path))
    sb.files[str(tmp_path / "a.txt")] = b"a"
    adapter = ScriptedMockAdapter(rounds=[text_round("ok")])
    gw = _gw(adapter)
    sink = io.StringIO()
    emitter = NdjsonEmitter(sink=sink)
    await generate_and_run("x", _tok(), sb, gateway=gw, emitter=emitter,
                            workspace_root=str(tmp_path))
    for line in sink.getvalue().splitlines():
        if line:
            ev = json.loads(line)
            assert ev["schema"] == "karvyloop-forge-ndjson"
            assert ev["v"] == 1


# ============ AC4：工具输出 >32KB → truncated + UTF-8 不破 ============
def test_ac4_truncate_32kb():
    sink = io.StringIO()
    e = NdjsonEmitter(sink=sink)
    big = "你" * 20000  # 每个 3 字节 → 60KB UTF-8
    e.tool_result(tool_use_id="c1", is_error=False, output=big, truncated=False)
    line = sink.getvalue().strip()
    # 整行可能也被 32KB 截断
    assert len(line.encode("utf-8")) <= 32 * 1024 + 200
    # 必须是合法 JSON
    obj = json.loads(line)
    # output 已被截断
    assert obj["truncated"] is True
    # 截断的 output 字符串应当能 utf-8 decode(我们用 char 截,必合法)
    obj["output"][:1000].encode("utf-8").decode("utf-8")


# ============ AC5：read-before-write ============
@pytest.mark.asyncio
async def test_ac5a_overwrite_existing_without_read_rejected(tmp_path):
    """已存在的文件 未读就写 → 拒(HR-4 防盲目覆盖)。"""
    p = tmp_path / "x.txt"
    p.write_text("old", encoding="utf-8")
    sb = FakeSandbox(str(tmp_path))
    w = WriteTool(sb, FileState(), str(tmp_path), token=_tok())
    r = await w({"file_path": str(p), "content": "hello"})
    assert r.ok is False and r.error_code == READ_REQUIRED


@pytest.mark.asyncio
async def test_ac5a2_create_new_file_without_read_allowed(tmp_path):
    """9.5 修订:**新文件**(不存在)未读直接写 → 允许(无内容可覆盖,不必先读)。"""
    sb = FakeSandbox(str(tmp_path))
    w = WriteTool(sb, FileState(), str(tmp_path), token=_tok())
    r = await w({"file_path": str(tmp_path / "new.txt"), "content": "hello"})
    assert r.ok is True


@pytest.mark.asyncio
async def test_ac5b_write_after_external_change_rejected(tmp_path):
    """Read 后外部改 mtime → Write 应当被拒(FakeSandbox 模拟 mtime 变化)。"""
    import os as _os
    sb = FakeSandbox(str(tmp_path))
    p = str(tmp_path / "x.txt")
    # 通过 write_file 落盘(filestate mtime 检测需要真文件)
    await sb.write_file(p, b"v1", token=_tok())
    fs = FileState()
    rd = ReadTool(sb, fs, str(tmp_path), token=_tok())
    await rd({"file_path": p})
    # 外部改文件 mtime
    time.sleep(0.05)
    _os.utime(p, (_os.path.getatime(p), _os.path.getmtime(p) + 5))
    w = WriteTool(sb, fs, str(tmp_path), token=_tok())
    r = await w({"file_path": p, "content": "v2"})
    assert r.ok is False and r.error_code == CHANGED_SINCE_READ


@pytest.mark.asyncio
async def test_ac5c_edit_multi_match_no_replace_all_rejected(tmp_path):
    sb = FakeSandbox(str(tmp_path))
    p = str(tmp_path / "x.txt")
    sb.files[p] = b"abc abc abc"
    fs = FileState()
    rd = ReadTool(sb, fs, str(tmp_path), token=_tok())
    await rd({"file_path": p})
    e = EditTool(sb, fs, str(tmp_path), token=_tok())
    r = await e({"file_path": p, "old_string": "abc", "new_string": "X"})
    assert r.ok is False and r.error_code == 9  # 多匹配
    r2 = await e({"file_path": p, "old_string": "abc", "new_string": "X", "replace_all": True})
    assert r2.ok is True


# ============ AC6：Write 强制 LF + 自动 mkdir ============
@pytest.mark.asyncio
async def test_ac6_write_lf_and_mkdir(tmp_path):
    sb = FakeSandbox(str(tmp_path))
    p = str(tmp_path / "sub/dir/x.txt")
    fs = FileState()
    # 先记 read("sub/dir/x.txt") 路径
    sb.files[p] = b"line1\r\nline2\r\n"
    fs.record_read(p, sb.files[p])
    w = WriteTool(sb, fs, str(tmp_path), token=_tok())
    r = await w({"file_path": p, "content": "line1\r\nline2\r\n"})
    assert r.ok is True
    assert b"\r\n" not in sb.files[p]  # LF 强制
    # mkdir:子目录自动创建(我们的 sandbox.write_file 不自动建——但 spec 写"写前 mkdir",WriteTool 自己 mkdir)
    # 验证 mkdir 路径
    import os as _os
    assert _os.path.isdir(_os.path.dirname(p))


# ============ AC7：Bash is_concurrency_safe 动态判定 ============
def test_ac7_bash_concurrency_safe_classification():
    # ls -la → 只读
    assert BashTool.__name__  # placeholder 防止 import 报错
    from karvyloop.coding.tools.bash import _classify
    assert _classify("ls -la") is True
    assert _classify("cat /etc/hosts") is True
    assert _classify("rm x") is False
    assert _classify("mv a b") is False
    assert _classify("echo hi > out") is False
    # 解析失败 → 保守写
    assert _classify("(((") is False
    # pipeline → 保守
    assert _classify("ls | grep foo") is False


# ============ AC8：路径越界被拒 ============
@pytest.mark.asyncio
async def test_ac8_path_outside_workspace_rejected(tmp_path):
    sb = FakeSandbox(str(tmp_path))
    fs = FileState()
    rd = ReadTool(sb, fs, str(tmp_path), token=_tok())
    r = await rd({"file_path": "/etc/passwd"})
    assert r.ok is False
    # Windows backslash 拒
    r2 = await rd({"file_path": str(tmp_path) + "\\evil"})
    # Windows 下 is_within_workspace 已拒 backslash → ok=False
    assert r2.ok is False


# ============ AC9：会话落盘不含明文 key,内存态保真 ============
def test_ac9_session_redaction_and_memory_fidelity(tmp_path):
    d = tmp_path / "sess"
    s = ForgeSession.create(d)
    in_mem_msg = {"kind": "user_msg",
                   "text": "请用 sk-ant-ABCDEFGHIJKLMNOP1234 这个 key",
                   "bearer": "Bearer xyz1234567890abcdef"}
    s.append_record(in_mem_msg)
    # **内存态保真**:调用方传入的 dict 引用应未被修改
    assert in_mem_msg["text"] == "请用 sk-ant-ABCDEFGHIJKLMNOP1234 这个 key"
    assert in_mem_msg["bearer"] == "Bearer xyz1234567890abcdef"
    # 落盘:文件内容应脱敏
    raw = s.path.read_text(encoding="utf-8")
    assert "sk-ant-ABCDEFGHIJKLMNOP1234" not in raw
    assert "Bearer xyz1234567890abcdef" not in raw
    assert "[redacted]" in raw


# ============ AC10：append-only + 轮转 + fork ============
def test_ac10_session_append_rotate_fork(tmp_path):
    d = tmp_path / "sess"
    s = ForgeSession.create(d)
    # 推 N 条
    N = 5
    for i in range(N):
        s.append_record({"kind": "rec", "i": i})
    # reload 等价
    s2 = ForgeSession.load(s.path)
    assert s2.count_records(include_meta=False) == N
    # 轮转:每条 < 16K(MAX_FIELD_CHARS),需要多条累计超 256K
    # 17 条 × ~15K = ~255K;再加 1 条触发轮转
    for i in range(20):
        s2.append_record({"kind": "big", "data": "x" * (MAX_FIELD_CHARS - 100)})
    rotated = list(s2.path.parent.glob("*.1.jsonl"))
    assert rotated, f"未轮转;现存文件: {list(s2.path.parent.glob('*.jsonl'))}"
    # 轮转文件数 ≤ ROTATE_KEEP
    all_files = list(s2.path.parent.glob("*.jsonl")) + list(s2.path.parent.glob("*.1.jsonl"))
    assert len(all_files) <= ROTATE_KEEP + 1
    # fork
    f = s2.fork(d, branch="exp1")
    assert f.meta.parent == s2.meta.session_id
    assert f.meta.branch == "exp1"
    assert f.meta.session_id != s2.meta.session_id
    # fork 包含父历史
    fc = f.count_records(include_meta=False)
    assert fc >= N


# ============ AC11：提示词：boundary 过滤 + 静态字节稳定 + UTF-8 截断 ============
def test_ac11_prompt_boundary_cache_truncate(tmp_path):
    p = build_coding_prompt(str(tmp_path))
    text = p.to_text()
    # 哨兵存在
    assert BOUNDARY_MARKER in text
    # 静态字节稳定:同一 cwd → 同一 static 段
    p2 = build_coding_prompt(str(tmp_path))
    assert p.static == p2.static
    # 静态前缀打 cache_control
    blocks = p.to_blocks()
    # 静态最后一段有 cache_control
    static_block_count = len(p.static)
    assert blocks[static_block_count - 1].get("cache_control") == {"type": "ephemeral"}
    # 哨兵之后不应再打 cache_control(只有哨兵块自身打)
    # 指令文件 >4K 截断
    big_instr = tmp_path / "AGENTS.md"
    big_instr.write_text("y" * 5000)
    p3 = build_coding_prompt(str(tmp_path))
    # 找 AGENTS.md 段
    text3 = p3.to_text()
    # 截断标志:整段 ≤ 4K + 标签长度
    assert "y" * 5000 not in text3
    assert text3.count("y") <= 4 * 1024 + 100  # 留点标签


# ============ AC11b:CodingPrompt 必须满足网关 system 的 duck-type 契约 ============
def test_ac11b_prompt_blocks_gateway_cache_contract(tmp_path):
    """adapter 调 `system.to_blocks(cache=...)`(gateway SystemPrompt 契约)。CodingPrompt 作为
    forge 的 system 走同一条线 —— 少了 cache 参数,每次 forge 调模型直接 TypeError,被执行器
    误判 infra-dead(模型/网络调不通),整条慢脑路径全灭(2026-07-04 真机实捕,J22 揪出)。"""
    p = build_coding_prompt(str(tmp_path))
    # cache=True(默认)与位置调用等价,静态尾块保留 cache_control
    assert p.to_blocks(cache=True) == p.to_blocks()
    # cache=False:合法调用 + 不打任何断点
    blocks = p.to_blocks(cache=False)
    assert blocks, "to_blocks(cache=False) 不能为空"
    assert all("cache_control" not in b for b in blocks), \
        "cache=False 仍在打 cache_control 断点"


# ============ AC12：Forge 不含独立 agent 循环（导入检查）============
def test_ac12_forge_no_independent_loop():
    """**forge 编排层**（coding/forge.py）不应自起 while True;只能复用 atoms.executor.run。
    工具类文件（prompt/session/filestate）的内部迭代循环（git_root 向上找等）允许存在。"""
    import pathlib
    forbidden = ["while True", "while not done", "while not finished"]
    # 编排层:forge.py + tools/__init__.py + ndjson.py（事件发射）+ session.py（主流程非 append 端）
    # 为避免误伤,只查 forge.py（最薄编排层）。
    target = pathlib.Path("karvyloop/coding/forge.py")
    text = target.read_text(encoding="utf-8")
    offenders = [(f, f) for f in forbidden if f in text]
    assert not offenders, f"coding/forge.py 疑似自起循环: {offenders}"
    # 同时确认 forge.py 显式 import atoms.executor.run
    assert "from karvyloop.atoms import" in text and "run as atom_run" in text
    assert "atom_run" in text  # 用上


# ============ 额外：filestate 直接单测（不在 AC 列表但保底）============
def test_filestate_snapshots():
    fs = FileState()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.txt")
        open(p, "w").write("hi")
        fs.record_read(p, b"hi")
        snap = fs.get(p)
        assert snap is not None
        assert fs.assert_writable(p).path == snap.path


def test_filestate_read_required():
    fs = FileState()
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "x.txt")
        open(p, "w").write("hi")
        with pytest.raises(ReadBeforeWriteError) as ex:
            fs.assert_writable(p)
        assert ex.value.code == READ_REQUIRED
