"""test_skill_exec — 沙箱执行技能脚本 + 第三方信任收口(P0-c 安全核心)。"""
from __future__ import annotations
import asyncio, pathlib, sys
ROOT = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from karvyloop.capability.skill_grants import capability_for_skill, is_trusted_skill, token_for_skill  # noqa: E402
from karvyloop.registry.skill_exec import run_skill_script, resolve_script  # noqa: E402
from karvyloop.registry.skills import parse_frontmatter  # noqa: E402
from karvyloop.sandbox.exec_result import ExecResult  # noqa: E402
from karvyloop.sandbox.mounts import mounts_from_token, has_net  # noqa: E402


class FakeSandbox:
    """记录最后一次 exec 的 argv/token/cwd;不真跑(Windows 无 bwrap)。"""
    def __init__(self): self.calls = []
    async def exec(self, argv, *, token, cwd, stdin=b"", timeout_s=120.0, max_output_bytes=30_000):
        self.calls.append({"argv": argv, "token": token, "cwd": cwd})
        return ExecResult(stdout=b"ok", stderr=b"", exit_code=0)


def _mk_skill(tmp, *, third_party: bool, allowed=("Read", "WebFetch")):
    d = tmp / "sk" / "demo"; (d / "scripts").mkdir(parents=True)
    at = "".join(f"  - {a}\n" for a in allowed)
    extra = "source: third-party\ntrust: untrusted\nsignature: imp-x\n" if third_party else "signature: cryst-x\n"
    (d / "SKILL.md").write_text(
        f"---\nname: demo\ndescription: d\nallowed-tools:\n{at}{extra}---\n# demo\n", encoding="utf-8")
    (d / "scripts" / "run.py").write_text("print('x')\n", encoding="utf-8")
    return d


def test_untrusted_clamped_no_net_no_host_fs(tmp_path):
    d = _mk_skill(tmp_path, third_party=True, allowed=("Read", "WebFetch", "Bash"))
    fm, _ = parse_frontmatter(d / "SKILL.md")
    assert is_trusted_skill(fm) is False
    tok = token_for_skill(fm, skill_dir=str(d), workspace=str(tmp_path / "ws"))
    # 第三方:即便声称 WebFetch,也无网络
    assert has_net(tok) is False
    ro, rw = mounts_from_token(tok)
    # 只读=技能目录;可写=工作区;没有别的宿主路径
    assert str(d) in " ".join(ro)
    assert str(tmp_path / "ws") in " ".join(rw)
    assert len(rw) == 1   # 仅工作区可写,不给宿主任意写


def test_trusted_skill_gets_net_when_declared(tmp_path):
    d = _mk_skill(tmp_path, third_party=False, allowed=("Read", "WebFetch"))
    fm, _ = parse_frontmatter(d / "SKILL.md")
    assert is_trusted_skill(fm) is True
    tok = token_for_skill(fm, skill_dir=str(d), workspace=str(tmp_path / "ws"))
    assert has_net(tok) is True   # 自家技能声明 WebFetch → 放开网络


def test_trusted_without_net_tool_has_no_net(tmp_path):
    d = _mk_skill(tmp_path, third_party=False, allowed=("Read", "Write"))
    fm, _ = parse_frontmatter(d / "SKILL.md")
    tok = token_for_skill(fm, skill_dir=str(d), workspace=str(tmp_path / "ws"))
    assert has_net(tok) is False


def test_run_skill_script_uses_sandbox_with_derived_token(tmp_path):
    d = _mk_skill(tmp_path, third_party=True)
    ws = tmp_path / "ws"; ws.mkdir()
    sb = FakeSandbox()
    r = asyncio.run(run_skill_script(str(d), "scripts/run.py", ["arg1"], sandbox=sb, workspace=str(ws)))
    assert r.exit_code == 0 and sb.calls
    call = sb.calls[0]
    assert call["argv"][0] == "python3" and call["argv"][-1] == "arg1"
    assert call["cwd"] == str(ws)
    assert has_net(call["token"]) is False   # 第三方脚本入沙箱无网络


def test_script_path_traversal_rejected(tmp_path):
    d = _mk_skill(tmp_path, third_party=True)
    sb = FakeSandbox()
    try:
        asyncio.run(run_skill_script(str(d), "../../../etc/passwd", sandbox=sb, workspace=str(tmp_path / "ws")))
        assert False, "应拒绝越界脚本路径"
    except ValueError:
        pass
    assert not sb.calls   # 越界 → 根本没进沙箱
