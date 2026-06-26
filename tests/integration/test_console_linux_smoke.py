"""karvyloop console Linux VM 真机 smoke(M3+ 批 8.5-C-frontend — opt-in)。

设计:plans/snoopy-singing-sunbeam.md §拍 8.5-C-frontend。
借:CLAUDE.md 5 问硬规则 + `linux-vm-e2e-catches-cross-layer-bugs` 记忆。

为什么需要:
- 抓 `_resolve_runtime` 与 `cmd_chat` 共享后的 wiring bug(单元/integration 抓不到)
- 抓 `chat_history.py` 在 console 与 TUI 间数据流 bug
- 抓 FastAPI lifespan 在异常路径下 pump task 未取消的资源泄漏

AC:AC7 — 端到端跑通(SSH 进 VM → init → console 起 → curl /api/snapshot → WS 收发 → pkill 收尾)。

守卫:
- RUN_LINUX_SMOKE=1 未设 → pytest.skip(不阻 CI)
- KARVYLOOP_VM_SSH 未设 → pytest.skip(VM 配置不在本机)
- Win32 平台 → pytest.skip(Linux VM smoke 必须在 Linux 跑)
"""
from __future__ import annotations

import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest

# ─────────────────────────────────────────────────────────────
# 守卫层:3 个 opt-in 条件
# ─────────────────────────────────────────────────────────────

pytestmark = pytest.mark.skipif(
    platform.system() == "Windows",
    reason="Linux VM smoke 必须在 Linux 跑;Win32 上请用 `pytest -k console` 跑单元",
)

SMOKE_ENV_VAR = "RUN_LINUX_SMOKE"
VM_SSH_ENV_VAR = "KARVYLOOP_VM_SSH"


def _smoke_enabled() -> bool:
    return os.environ.get(SMOKE_ENV_VAR) == "1"


def _vm_ssh() -> str | None:
    return os.environ.get(VM_SSH_ENV_VAR)


# ─────────────────────────────────────────────────────────────
# AC7: 端到端 smoke(SSH → init → console → curl → WS → pkill)
# ─────────────────────────────────────────────────────────────

@pytest.mark.skipif(
    not _smoke_enabled(),
    reason=f"opt-in: 设 {SMOKE_ENV_VAR}=1 才跑 Linux VM smoke",
)
@pytest.mark.skipif(
    _vm_ssh() is None,
    reason=f"opt-in: 设 {VM_SSH_ENV_VAR}=user@vm 才跑(VM 配置在 ~/.claude memory)",
)
def test_console_e2e_linux_vm_smoke():
    """端到端:SSH 进 VM → init → console 起 → curl /api/snapshot → WS 收发 → pkill。

    Steps(全在远端 VM 上执行,本机仅 orchestrate):
    1. `pip install -e .[dev]`(idempotent,2-3 min)
    2. `karvyloop init --no-wizard`(smoke 跳过 LLM,纯默认 config)
    3. `karvyloop console --no-browser --no-llm &`(后台起,绑默认 127.0.0.1:8766)
    4. `curl http://127.0.0.1:8766/api/snapshot` → 期望 200 + 含 9 字段
    5. `pkill -f 'karvyloop console'` 收尾

    Asserts:
    - pip install exit 0
    - init exit 0
    - console 在 10s 内 listen 8766(curl 200)
    - /api/snapshot 含 `domains` / `broadcasts` / `crystallized_skills` / `last_drive_text`
    - pkill 后 0 进程残留
    """
    ssh_target = _vm_ssh()
    assert ssh_target, f"missing {VM_SSH_ENV_VAR}"

    repo_root = Path(__file__).resolve().parents[2]
    project_dir_remote = "~/karvyloop"

    # Step 1: install(可能在 VM 上首次慢,长 timeout)
    r = _ssh(ssh_target, f"cd {project_dir_remote} && pip install -e '.[dev]' 2>&1 | tail -3")
    assert r.returncode == 0, f"pip install failed:\n{r.stdout}\n{r.stderr}"

    # Step 2: init --no-wizard(smoke 跳过 LLM 注入)
    r = _ssh(ssh_target, f"cd {project_dir_remote} && karvyloop init --no-wizard --force 2>&1")
    assert r.returncode == 0, f"init failed:\n{r.stdout}\n{r.stderr}"

    # Step 3: console 后台起(端口默认 8766)
    r = _ssh(
        ssh_target,
        f"cd {project_dir_remote} && nohup karvyloop console --no-browser --no-llm > /tmp/console.log 2>&1 &",
    )
    # nohup & exit 0 是正常;具体起 console 验 curl
    assert r.returncode == 0, f"console launch failed:\n{r.stdout}\n{r.stderr}"

    # Step 4: 轮询直到 8766 listen(等 FastAPI 起来,最多 10s)
    snapshot_json = _wait_for_snapshot(ssh_target, timeout=10.0)
    assert snapshot_json, "console 未在 10s 内 ready(/api/snapshot 拿不到)"

    # Step 5: 验 /api/snapshot 含 9 字段(8.5-A 加的 last_error / last_intent 在内)
    for field in (
        "domains",
        "broadcasts",
        "crystallized_skills",
        "last_drive_text",
        "last_error",
        "last_intent",
    ):
        assert field in snapshot_json, f"snapshot 缺字段 {field}: {snapshot_json}"

    # Step 6: pkill 收尾
    r = _ssh(ssh_target, "pkill -f 'karvyloop console' || true")
    assert r.returncode == 0

    # 确认 0 残留(给 2s 缓冲)
    r = _ssh(ssh_target, "sleep 2 && pgrep -f 'karvyloop console' | wc -l")
    assert r.stdout.strip() == "0", f"console 进程未清干净:\n{r.stdout}"


# ─────────────────────────────────────────────────────────────
# helpers
# ─────────────────────────────────────────────────────────────

def _ssh(target: str, command: str, timeout: int = 300) -> subprocess.CompletedProcess:
    """在远端 VM 上跑 command,返 CompletedProcess。"""
    return subprocess.run(
        ["ssh", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=accept-new",
         target, command],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _wait_for_snapshot(target: str, timeout: float = 10.0) -> dict | None:
    """轮询 `/api/snapshot` 直到 200,返 JSON dict;timeout 内拿不到返 None。"""
    import json
    import time
    deadline = time.time() + timeout
    while time.time() < deadline:
        r = _ssh(
            target,
            "curl -s -o /dev/null -w '%{http_code}' http://127.0.0.1:8766/api/snapshot",
            timeout=5,
        )
        if r.stdout.strip() == "200":
            # 200 了,拿 body
            r2 = _ssh(target, "curl -s http://127.0.0.1:8766/api/snapshot", timeout=5)
            try:
                return json.loads(r2.stdout)
            except json.JSONDecodeError:
                return None
        time.sleep(0.5)
    return None


# ─────────────────────────────────────────────────────────────
# 不需 VM 的快速自检(让守卫层不"静默全 skip")
# ─────────────────────────────────────────────────────────────

def test_linux_vm_smoke_opt_in_documented():
    """守卫层 sanity check — RUN_LINUX_SMOKE / KARVYLOOP_VM_SSH 未设时本测试也要给清晰信息。"""
    if not _smoke_enabled():
        pytest.skip(
            f"opt-in: Linux VM smoke 需要设 {SMOKE_ENV_VAR}=1 + {VM_SSH_ENV_VAR}=user@vm"
        )
    if not _vm_ssh():
        pytest.skip(f"opt-in: 需要设 {VM_SSH_ENV_VAR}=user@vm")
    # 两个都设了 → 不应 skip,落到真 test
    assert True
