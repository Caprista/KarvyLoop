"""external_runtime 桥测试:解析(三 parse_mode)/ 密钥过滤 / fail-loud 六态 / usage 边车。

CI 侧([[Q2]]):fixture 拦截 subprocess,验 stdout 形态 / 退出码 / 空成功判 failed /
stderr 密钥泄露断言(fixture key 带 FAKE/DO-NOT-LEAK)。离线、三平台一致。
"""
from __future__ import annotations

import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from karvyloop.external_runtime import (  # noqa: E402
    DriveRecipe, ExitSpec, ParseSpec,
    PARSE_NDJSON, PARSE_RAW_TEXT, PARSE_SINGLE_JSON,
    bridge_factory, builtin_recipe, contains_secret, redact,
)
from karvyloop.external_runtime.bridge import STATUS_DONE, STATUS_FAILED  # noqa: E402

# fixture key 带 DO-NOT-LEAK / FAKE 字样(防泄露断言用)
FAKE_SK = "sk-FAKEDONOTLEAK1234567890"
# 某些 provider 的 key 是 JWT(以 eyJ 开头,不带 sk-)——只配 sk- 会整条漏
FAKE_JWT = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9."
            "eyJGQUtFIjoiRE8tTk9ULUxFQUsiLCJpYXQiOjE1MTYyMzkwMjJ9."
            "DONOTLEAKsignatureFAKE1234567890abcdef")


def _fake_runner(*, returncode=0, stdout="", stderr=""):
    def run(argv, *, env, timeout, cwd):
        class P:
            pass
        P.returncode = returncode
        P.stdout = stdout
        P.stderr = stderr
        return P()
    return run


# ---- 解析:raw_text ----

def test_raw_text_returns_whole_stdout():
    r = DriveRecipe(runtime_kind="rt", bin_path="x", argv_template=("-z", "{prompt}"),
                    parse=ParseSpec(mode=PARSE_RAW_TEXT))
    b = bridge_factory(r, runner=_fake_runner(stdout="7\n"))
    res = b.start("1+2*3")
    assert res.status == STATUS_DONE
    assert res.text == "7"


# ---- 解析:single_json ----

def test_single_json_digs_text_path():
    r = DriveRecipe(runtime_kind="rt", bin_path="x", argv_template=("-m", "{prompt}"),
                    parse=ParseSpec(mode=PARSE_SINGLE_JSON, text_path="payloads[0].text",
                                    meta_path="meta"))
    out = json.dumps({"payloads": [{"text": "hello"}],
                      "meta": {"model": "M", "usage": {"input_tokens": 10, "output_tokens": 3,
                                                       "total_tokens": 13}}})
    b = bridge_factory(r, runner=_fake_runner(stdout=out))
    res = b.start("hi")
    assert res.status == STATUS_DONE and res.text == "hello"
    assert res.usage and res.usage["total"] == 13 and res.usage["model"] == "M"


# ---- 解析:ndjson ----

def test_ndjson_takes_last_assistant_text():
    r = DriveRecipe(runtime_kind="rt", bin_path="x", argv_template=("-p", "{prompt}"),
                    parse=ParseSpec(mode=PARSE_NDJSON))
    lines = "\n".join([json.dumps({"type": "progress", "stage": "start"}),
                       json.dumps({"type": "assistant", "text": "final answer"})])
    b = bridge_factory(r, runner=_fake_runner(stdout=lines))
    res = b.start("hi")
    assert res.status == STATUS_DONE and res.text == "final answer"


# ---- fail-loud:退非 0 ----

def test_nonzero_exit_is_failed_not_silent():
    r = builtin_recipe("raw_text_sidecar")
    b = bridge_factory(r, runner=_fake_runner(returncode=1, stdout="", stderr="boom"))
    res = b.start("hi")
    assert res.status == STATUS_FAILED
    assert res.exit_code == 1 and "boom" in res.reason


# ---- fail-loud:空成功坑(退 0 但产出空)----

def test_empty_success_judged_failed():
    r = DriveRecipe(runtime_kind="rt", bin_path="x", argv_template=("-z", "{prompt}"),
                    parse=ParseSpec(mode=PARSE_RAW_TEXT),
                    exit=ExitSpec(ok_codes=(0,), empty_is_failure=True))
    b = bridge_factory(r, runner=_fake_runner(returncode=0, stdout="   \n"))
    res = b.start("hi")
    assert res.status == STATUS_FAILED and "空" in res.reason


# ---- fail-loud:起不来(FileNotFoundError)----

def test_binary_not_found_is_failed():
    def raiser(argv, *, env, timeout, cwd):
        raise FileNotFoundError("no such file: /nope/ext-cli")
    r = builtin_recipe("raw_text_sidecar")
    b = bridge_factory(r, runner=raiser)
    res = b.start("hi")
    assert res.status == STATUS_FAILED and "找不到" in res.reason


# ---- fail-loud:超时(TimeoutExpired)----

def test_timeout_is_failed():
    import subprocess

    def raiser(argv, *, env, timeout, cwd):
        raise subprocess.TimeoutExpired(cmd=argv, timeout=timeout)
    r = builtin_recipe("raw_text_sidecar")
    b = bridge_factory(r, runner=raiser)
    res = b.start("hi")
    assert res.status == STATUS_FAILED and "超时" in res.reason


# ---- fail-loud:input_required 上报(升 H2A,不静默等)----

def test_input_required_reported():
    r = DriveRecipe(runtime_kind="rt", bin_path="x", argv_template=("-z", "{prompt}"),
                    parse=ParseSpec(mode=PARSE_RAW_TEXT))
    b = bridge_factory(r, runner=_fake_runner(stdout="Approval required to run rm -rf build/"))
    res = b.start("hi")
    assert res.status == STATUS_FAILED and res.input_required is True


# ---- 密钥过滤:stdout 里的 sk- key 被 redact,不入产出 ----

def test_stdout_sk_key_redacted():
    r = DriveRecipe(runtime_kind="rt", bin_path="x", argv_template=("-z", "{prompt}"),
                    parse=ParseSpec(mode=PARSE_RAW_TEXT))
    b = bridge_factory(r, runner=_fake_runner(stdout=f"here is your answer, key={FAKE_SK}"))
    res = b.start("hi")
    assert not contains_secret(res.text), "sk- key leaked into bridge output"
    assert FAKE_SK not in res.text


# ---- 密钥过滤:非 sk- 形态(JWT)也守得住(防"只测 sk- 就通过"的假绿)----

def test_stdout_jwt_key_redacted():
    r = DriveRecipe(runtime_kind="rt", bin_path="x", argv_template=("-z", "{prompt}"),
                    parse=ParseSpec(mode=PARSE_RAW_TEXT))
    b = bridge_factory(r, runner=_fake_runner(stdout=f"answer token: {FAKE_JWT}"))
    res = b.start("hi")
    assert not contains_secret(res.text), "JWT key leaked into bridge output"
    assert "DONOTLEAK" not in res.text or "[REDACTED]" in res.text


# ---- 密钥过滤:stderr 泄 key 也过滤 ----

def test_stderr_key_redacted():
    r = builtin_recipe("raw_text_sidecar")
    b = bridge_factory(r, runner=_fake_runner(returncode=1,
                                              stderr=f"error: bearer {FAKE_SK}"))
    res = b.start("hi")
    assert not contains_secret(res.stderr)
    assert not contains_secret(res.reason)


# ---- 凭证隔离(一道防线):真 key 不进子进程 env ----

def test_credentials_not_in_subprocess_env():
    captured = {}

    def capture_env(argv, *, env, timeout, cwd):
        captured.update(env)

        class P:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return P()

    r = DriveRecipe(runtime_kind="rt", bin_path="x", argv_template=("-z", "{prompt}"),
                    parse=ParseSpec(mode=PARSE_RAW_TEXT))
    dirty_env = {"PATH": "/usr/bin", "MINIMAX_API_KEY": FAKE_JWT,
                 "ANTHROPIC_API_KEY": FAKE_SK, "SOME_TOKEN": "secret", "HOME": "/home/x"}
    b = bridge_factory(r, env_base=dirty_env, runner=capture_env)
    b.start("hi")
    # 白名单外 + *_API_KEY/*_TOKEN 一律不进子进程 env
    assert "MINIMAX_API_KEY" not in captured
    assert "ANTHROPIC_API_KEY" not in captured
    assert "SOME_TOKEN" not in captured
    assert captured.get("PATH") == "/usr/bin"  # 白名单进


# ---- blocked_entrypoints:已知泄 key 入口桥拒调(不靠事后过滤兜)----

def test_blocked_entrypoint_refused():
    # 造一个首 argv token 命中黑名单的配方
    r = DriveRecipe(runtime_kind="rt", bin_path="x",
                    argv_template=("agent_entrypoint", "{prompt}"),
                    parse=ParseSpec(mode=PARSE_RAW_TEXT),
                    blocked_entrypoints=("agent_entrypoint",))
    called = {"n": 0}

    def runner(argv, *, env, timeout, cwd):
        called["n"] += 1

        class P:
            returncode = 0
            stdout = "should not run"
            stderr = ""
        return P()

    b = bridge_factory(r, runner=runner)
    res = b.start("hi")
    assert res.status == STATUS_FAILED and "黑名单" in res.reason
    assert called["n"] == 0, "blocked entrypoint should not spawn at all"


# ---- usage 边车:raw_text_sidecar 从 --usage-file 解析 usage ----

def test_sidecar_usage_parsed(monkeypatch, tmp_path):
    # 桥写临时 sidecar 路径,fake runner 往那写 usage JSON 再返回
    r = builtin_recipe("raw_text_sidecar")

    def runner_writes_sidecar(argv, *, env, timeout, cwd):
        # argv 末尾是 sidecar 路径(--usage-file {sidecar_path})
        sidecar = argv[-1]
        with open(sidecar, "w", encoding="utf-8") as f:
            json.dump({"input_tokens": 100, "output_tokens": 5, "total_tokens": 105,
                       "model": "MiniMax-M3", "provider": "minimax-cn"}, f)

        class P:
            returncode = 0
            stdout = "7"
            stderr = ""
        return P()

    b = bridge_factory(r, runner=runner_writes_sidecar)
    res = b.start("1+2*3")
    assert res.status == STATUS_DONE
    assert res.usage and res.usage["total"] == 105 and res.usage["model"] == "MiniMax-M3"


# ---- argv 数组:绝不拼 shell(占位符只填 argv 元素)----

def test_argv_never_shell_joined():
    captured = {}

    def capture_argv(argv, *, env, timeout, cwd):
        captured["argv"] = argv

        class P:
            returncode = 0
            stdout = "ok"
            stderr = ""
        return P()

    r = DriveRecipe(runtime_kind="rt", bin_path="/bin/ext-cli",
                    argv_template=("-z", "{prompt}", "--safe-mode"),
                    parse=ParseSpec(mode=PARSE_RAW_TEXT))
    b = bridge_factory(r, runner=capture_argv)
    # 恶意 prompt 带 shell 元字符:必须整体作为一个 argv 元素,不被拆
    b.start("hi; rm -rf / && echo pwned")
    argv = captured["argv"]
    assert argv[0] == "/bin/ext-cli"
    assert "hi; rm -rf / && echo pwned" in argv, "prompt must be one argv element (no shell split)"
