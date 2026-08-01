"""docs/99 computer-use setup/doctor —— 计划核(纯函数)确定性锁 + 可导入 + main 无副作用退。

真跑 privileged setup 需 Linux 桌面 + sudo,不在自动测;这里锁 plan_setup 的确定性映射
(环境状态 → 修复步骤 + 特权/自动标),防 setup 逻辑悄悄跑偏。
"""
from __future__ import annotations

from karvyloop.cli.computer_use_setup import (
    Env, _pkg_install_cmd, _ydotoold_unit, main, plan_setup,
)


def _env(**kw) -> Env:
    base = dict(is_linux=True, session_type="wayland", desktop="ubuntu:GNOME",
                server_installed=True, ydotool_installed=True, ydotoold_running=True,
                a11y_on=True, distro_pkg="apt", uid=1000, gid=1000)
    base.update(kw)
    return Env(**base)


class TestPlan:
    def test_all_ready_only_optional_window(self):
        assert [s.code for s in plan_setup(_env())] == ["window_targeting"]

    def test_missing_everything(self):
        plan = plan_setup(_env(server_installed=False, ydotool_installed=False,
                               ydotoold_running=False, a11y_on=False))
        by = {s.code: s for s in plan}
        for c in ("install_server", "install_ydotool", "enable_a11y", "ydotoold_service"):
            assert c in by, c
        # a11y = 非特权 + 自动;装类/服务 = 特权 + 非自动
        assert by["enable_a11y"].auto and not by["enable_a11y"].privileged
        assert by["install_ydotool"].privileged and not by["install_ydotool"].auto
        assert by["install_server"].privileged
        assert by["ydotoold_service"].privileged and not by["ydotoold_service"].auto

    def test_not_linux_short_circuits(self):
        assert [s.code for s in plan_setup(_env(is_linux=False))] == ["not_linux"]

    def test_no_gui_session_flagged(self):
        assert "no_gui_session" in [s.code for s in plan_setup(_env(session_type="tty"))]

    def test_wayland_and_x11_no_gui_warning(self):
        for st in ("wayland", "x11"):
            assert "no_gui_session" not in [s.code for s in plan_setup(_env(session_type=st))]

    def test_a11y_already_on_no_step(self):
        assert "enable_a11y" not in [s.code for s in plan_setup(_env(a11y_on=True))]

    def test_ydotoold_unit_socket_at_client_default_path(self):
        """VM 门到门实测:computer-use-linux 写死 $XDG_RUNTIME_DIR/.ydotool_socket、不认
        YDOTOOL_SOCKET → 服务 socket 必须放这条默认路径(不是 /run/ydotoold.socket)。"""
        plan = plan_setup(_env(ydotoold_running=False))
        svc = next(s for s in plan if s.code == "ydotoold_service")
        blob = "\n".join(svc.commands)
        assert "/run/user/1000/.ydotool_socket" in blob and "--socket-own=1000:1000" in blob
        assert "/run/ydotoold.socket" not in blob   # 不能是那条 client 不认的路径
        assert "systemctl enable --now ydotoold" in blob

    def test_unit_text_owner_and_default_path(self):
        u = _ydotoold_unit(1000, 1000)
        assert "--socket-path=/run/user/1000/.ydotool_socket" in u
        assert "--socket-own=1000:1000" in u and "Restart=always" in u

    def test_pkg_cmd_variants(self):
        assert "apt-get install -y ydotool" in _pkg_install_cmd("apt", "ydotool")
        assert "dnf install -y ydotool" in _pkg_install_cmd("dnf", "ydotool")
        assert "pacman -S" in _pkg_install_cmd("pacman", "ydotool")
        assert "未能识别" in _pkg_install_cmd("", "ydotool")   # 认不出发行版 → 诚实提示


def test_main_not_linux_no_side_effects(monkeypatch, capsys):
    """main 在非 Linux(monkeypatch _detect):打印 not_linux 提示、返回 0,不跑任何 auto/privileged。"""
    from karvyloop.cli import computer_use_setup as m
    monkeypatch.setattr(m, "_detect", lambda: Env(
        is_linux=False, session_type="", desktop="", server_installed=False,
        ydotool_installed=False, ydotoold_running=False, a11y_on=False,
        distro_pkg="", uid=0, gid=0))
    assert m.main([]) == 0
    out = capsys.readouterr().out
    assert "computer use" in out.lower() and "Linux" in out
