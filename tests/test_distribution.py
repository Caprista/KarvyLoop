"""分发测试(M3+ 批 7)。

设计:plans/snoopy-singing-sunbeam.md §批 7。

AC 列表:
  AC1: pyproject.toml 含 [project.scripts] karvyloop = "karvyloop.cli.main:main"(已落地)
  AC2: 装好后 `karvyloop --help` 跑得通(当前 venv + 已 `pip install -e .` 验证)
  AC3: 装好后 `karvyloop --version` 跑得通 + `karvyloop run --help` / `karvyloop replay --help` 子命令路由在
  AC4: Nuitka 编译产物能跑 — Windows + Python 3.14 + standalone 模式被 Dependency Walker 互动阻断,
       AC4 在本环境**xfail** 标出(spike 失败诚实记录;P1 解决:换 PyInstaller 或 Linux 构建机)

边界:
- 测试在**已 `pip install -e .` 的 venv** 跑(无需重新装)。
- 测试**不**修改 PATH,直接调 `karvyloop` 命令(PATH 已含 venv Scripts/)。
- AC4 Nuitka xfail:Windows + Python 3.14 + Nuitka 4.1.2 — 待 PyInstaller 备选或 Linux 构建机。
"""
from __future__ import annotations

import pathlib
import re
import shutil
import subprocess
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[1]
PYPROJECT = ROOT / "pyproject.toml"


# ---------- 共享 fixture ----------

@pytest.fixture
def karvyloop_path():
    """找 venv Scripts/karvyloop(或 Unix bin/karvyloop);Shutil.which('karvyloop') 已含 venv。"""
    path = shutil.which("karvyloop")
    if path is None:
        pytest.skip("karvyloop 不在 PATH — 跑 `pip install -e .` 后再跑此测试")
    return path


# ---------- AC1: pyproject.toml console_scripts ----------


# ---------- AC1: pyproject.toml console_scripts ----------

class TestAC1ConsoleScriptsDeclared:
    """AC1: pyproject.toml 含 [project.scripts] karvyloop = karvyloop.cli.main:main。"""

    def test_console_scripts_karvyloop_declared(self):
        content = PYPROJECT.read_text(encoding="utf-8")
        # 容忍 key 顺序 / 多余空格
        m = re.search(r"\[project\.scripts\](.*?)(?=\[|$)", content, re.DOTALL)
        assert m, "pyproject.toml 缺 [project.scripts] section"
        body = m.group(1)
        assert re.search(r'karvyloop\s*=\s*["\']karvyloop\.cli\.main:main["\']', body), \
            f"console_scripts 缺 karvyloop entry\n--- body ---\n{body}\n---"

    def test_build_backend_is_setuptools(self):
        """[build-system] 用 setuptools(M3 拍 3a 已选;不为批 7 改 build backend)。"""
        content = PYPROJECT.read_text(encoding="utf-8")
        assert "build-backend" in content
        assert "setuptools" in content


# ---------- AC2: `karvyloop --help` 在装好后能跑 ----------

class TestAC2KarvyloopHelpWorks:
    """AC2: 装好后 `karvyloop --help` 跑通(子命令 init/run/chat/replay 都在)。"""

    def test_help_lists_all_subcommands(self, karvyloop_path):
        r = subprocess.run(
            [karvyloop_path, "--help"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
        assert r.returncode == 0, f"karvyloop --help 退出码 {r.returncode}: {r.stderr}"
        # 4 个子命令(M3+ 批 6 加 replay)
        for sub in ("init", "run", "chat", "replay"):
            assert sub in r.stdout, f"--help 输出缺 {sub} 子命令:\n{r.stdout}"

    def test_version_returns_version(self, karvyloop_path):
        r = subprocess.run(
            [karvyloop_path, "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
        assert r.returncode == 0
        # 输出形如 "karvyloop 0.1.0-m0" / "karvyloop 0.0.1"
        assert re.search(r"karvyloop\s+\d+\.\d+\.\d+", r.stdout), \
            f"--version 输出异常: {r.stdout!r}"


# ---------- AC3: 子命令 --help 路由在 ----------

class TestAC3SubcommandHelpWorks:
    """AC3: `karvyloop {init,run,chat,replay} --help` 都能跑(子命令路由 + arg parser 通)。"""

    @pytest.mark.parametrize("subcmd,expected_flag", [
        ("init", "--force"),
        ("run", "--no-recall"),
        ("chat", "--headless"),
        ("replay", "--trace-path"),
    ])
    def test_subcommand_help(self, karvyloop_path, subcmd, expected_flag):
        r = subprocess.run(
            [karvyloop_path, subcmd, "--help"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
        assert r.returncode == 0, f"{subcmd} --help 退出码 {r.returncode}: {r.stderr}"
        assert expected_flag in r.stdout, \
            f"{subcmd} --help 缺 {expected_flag} flag:\n{r.stdout}"


# ---------- AC4: Nuitka 编译产物 — Windows + Py3.14 当前不可行,xfail ----------

class TestAC4NuitkaSpike:
    """AC4: Nuitka --onefile 编译产物能跑 — Windows + Python 3.14 当前环境**xfail**。

    失败原因(诚实状态):
      - Nuitka 4.1.2 在 Python 3.14 上是"experimental";推荐 3.13 或更新 Nuitka。
      - `--mode=standalone` / `--mode=onefile` 在 Windows 上需要 Dependency Walker
        分析 .pyd 扩展模块依赖;非交互模式默认拒绝下载(无 NUITKA_DISABLE_DEPENDENCY_WALKER
        env var / 无等价 flag)。
      - 影响:无法在当前(win32 + Python 3.14)环境产出 Nuitka 二进制分发。

    P1 解决路径(留待执行):
      1. 切换 Linux 构建机 + Python 3.13(企业 / CI 容器)
      2. PyInstaller 备选(许可证 BSD,反编译比 Nuitka 容易但比 .pyc 难)
      3. 升级到 Python 3.13 在本机(降 Nuitka experimental 警告)

    本批决策:`pip install .` 是 M3+ 批 7 的**主分发路径**;Nuitka 二进制降级为 P1,
    留 docs/NUITKA-SPIKE.md 给后续参照。
    """

    @pytest.mark.xfail(
        reason="Nuitka 4.1.2 在 Windows + Python 3.14 上需要 Dependency Walker 互动下载,"
               "无法非交互自动产出 standalone/onefile 二进制;留 P1。",
        strict=False,
    )
    def test_nuitka_onefile_runs(self, tmp_path):
        """试编译 karvyloop/cli/main.py 为 --onefile 二进制,跑 `karvyloop-spike --version`。"""
        if sys.platform != "win32":
            pytest.skip("本 AC 仅在 Windows 上验证(spike 在本机做)")
        try:
            import nuitka  # noqa: F401
        except ImportError:
            pytest.skip("nuitka 未装 — 跑 `pip install nuitka` 再来")

        # 跑编译(spike 命令 — 超时短,失败就 fail-fast)
        r = subprocess.run(
            [
                sys.executable, "-m", "nuitka",
                "--onefile", "--output-filename=karvyloop-spike.exe",
                f"--output-dir={tmp_path}",
                str(ROOT / "karvyloop" / "cli" / "main.py"),
            ],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=300,
        )
        # 期望:编译产物存在 + --version 跑通
        binary = tmp_path / "karvyloop-spike.exe"
        assert binary.exists(), f"Nuitka 编译产物不存在:\n--- stdout ---\n{r.stdout[:2000]}\n--- stderr ---\n{r.stderr[:2000]}"
        r2 = subprocess.run(
            [str(binary), "--version"], capture_output=True, text=True,
            encoding="utf-8", errors="replace", timeout=15,
        )
        assert r2.returncode == 0
        assert "karvyloop" in r2.stdout.lower()

    def test_nuitka_module_compile_succeeds_baseline(self, tmp_path):
        """Sanity: --module 模式(无 walker 互动)在本环境能编译。

        这条**不** xfail — 它是已验证的事实(批 7 spike 副产物)。
        验证:karvyloop/cli/run.py 编译产出 .pyd 文件存在。
        """
        try:
            import nuitka  # noqa: F401
        except ImportError:
            pytest.skip("nuitka 未装")

        r = subprocess.run(
            [
                sys.executable, "-m", "nuitka",
                "--module", f"--output-dir={tmp_path}",
                str(ROOT / "karvyloop" / "cli" / "run.py"),
            ],
            capture_output=True, text=True,
            encoding="utf-8", errors="replace",
            timeout=120,
        )
        assert r.returncode == 0, f"nuitka --module 失败:\n{r.stderr[-2000:]}"
        # 产物形如 run.cp314-win_amd64.pyd(Python 3.14 + win_amd64)
        products = list(tmp_path.glob("*.pyd"))
        assert products, f"无 .pyd 产物:\nls {tmp_path} = {list(tmp_path.iterdir())}"