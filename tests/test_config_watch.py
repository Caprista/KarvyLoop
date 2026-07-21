"""test_config_watch — 内测报障双修的回归锁。

① 错误咽喉人话化(tasks._humanize_bare_terminal):聊天气泡/任务卡绝不再糊「✗ infra_dead」裸码;
② 配置外改检测(routes_models.check_config_external_change):终端/编辑器改 config.yaml →
   下一次聊天/维护 tick 就热加载 + 主动说一声,坏配置不再潜伏到重启才炸。
"""
from __future__ import annotations

import time
from types import SimpleNamespace

from karvyloop.console.tasks import TaskRegistry, _humanize_bare_terminal


# ================= ① 错误咽喉人话化 =================

def test_bare_terminal_codes_become_human_words():
    """裸终态码(含「✗ 」前缀形态,内测实拍)→ 人话;i18n 键真存在(t 返回≠键名)。"""
    for raw in ("infra_dead", "✗ infra_dead", "max_turns", "blocking_limit",
                "circuit_open", "aborted_tools", "aborted_streaming", "hook_stopped"):
        out = _humanize_bare_terminal(raw)
        assert out != raw and "task.err." not in out, f"{raw!r} 未被人话化: {out!r}"
        assert "_" not in out, f"人话里不该再有下划线机器码: {out!r}"


def test_real_error_text_passes_through_untouched():
    """真错误文本(带上下文)原样过——只匹配"整串就是终态码",绝不误伤。"""
    for raw in ("", "模型服务拒绝了密钥(401/403)", "infra_dead: gateway unreachable",
                "TypeError: x", "「分析师」执行出错: boom"):
        assert _humanize_bare_terminal(raw) == raw


def test_finish_applies_the_choke_point():
    """finish(error=裸码) → 落到 record/事件里的已是人话(咽喉一处堵全部上游路径)。"""
    reg = TaskRegistry()
    tid = reg.start(who="小卡", intent="x", kind="drive")
    reg.finish(tid, error="infra_dead")
    rec = reg.get(tid)
    assert rec["status"] == "error"
    assert "infra_dead" not in rec["result"], f"裸码漏上屏: {rec['result']!r}"
    assert rec["last_event"]["kind"] == "error"
    assert "infra_dead" not in rec["last_event"]["text"]


# ================= ② 配置外改检测 =================

def _app_with_config(tmp_path, yaml_text: str):
    p = tmp_path / "config.yaml"
    p.write_text(yaml_text, encoding="utf-8")
    return SimpleNamespace(state=SimpleNamespace(
        config_path=str(p), runtime_kwargs={"gateway": SimpleNamespace(reg=None)})), p


def _touch_later(p):
    """确保 mtime 真变(文件系统 mtime 粒度可能 1s → 显式设一个未来值)。"""
    import os
    st = p.stat()
    os.utime(p, (st.st_atime, st.st_mtime + 2.0))


def test_first_check_only_records_baseline(tmp_path, monkeypatch):
    from karvyloop.console import routes_models as rm
    app, p = _app_with_config(tmp_path, "models: {}\n")
    calls = []
    monkeypatch.setattr(rm, "_reload_gateway_registry",
                        lambda a: calls.append(1) or (True, ""))
    assert rm.check_config_external_change(app) is False   # 首次=登记基线,不算外改
    assert not calls
    # mtime 未变 → 仍 False
    assert rm.check_config_external_change(app) is False


def test_external_change_triggers_reload_once(tmp_path, monkeypatch):
    from karvyloop.console import routes_models as rm
    app, p = _app_with_config(tmp_path, "models: {}\n")
    rm.check_config_external_change(app)                    # 基线
    reloads = []

    def _fake_reload(a):
        reloads.append(1)
        rm._mark_config_seen(a)                             # 与真实现同约:成败都记 seen
        return True, ""
    monkeypatch.setattr(rm, "_reload_gateway_registry", _fake_reload)
    pushed = []
    monkeypatch.setattr("karvyloop.console.task_events.schedule_system_error",
                        lambda a, s, m: pushed.append((s, m)))
    _touch_later(p)
    assert rm.check_config_external_change(app) is True     # 外改 → 热加载 + 推提示
    assert len(reloads) == 1
    assert pushed and pushed[0][0] == "config_watch"
    # seen 已更新 → 不重复轰
    assert rm.check_config_external_change(app) is False
    assert len(reloads) == 1


def test_external_broken_config_fails_loud_not_silent(tmp_path):
    """外改成坏配置 → 走真 _reload_gateway_registry 失败路径:返回 True(检测到)、
    推 fail-loud 提示、seen 已记(下一轮不重复轰)。这正是内测"终端写坏"的那一刻。"""
    from karvyloop.console import routes_models as rm
    app, p = _app_with_config(tmp_path, "models: {}\n")
    rm.check_config_external_change(app)                    # 基线
    p.write_text("models: {providers: {x: {models: [{bad", encoding="utf-8")  # 坏 yaml
    _touch_later(p)
    import karvyloop.console.task_events as te
    pushed = []
    orig = te.schedule_system_error
    te.schedule_system_error = lambda a, s, m: pushed.append((s, m))
    try:
        assert rm.check_config_external_change(app) is True
        assert pushed and "config_watch" == pushed[0][0]
        assert rm.check_config_external_change(app) is False   # 已报过,不重复轰
    finally:
        te.schedule_system_error = orig


def test_own_save_does_not_count_as_external(tmp_path):
    """自己保存(_reload_gateway_registry 成功路径记 seen)→ watcher 不当外改报。"""
    from karvyloop.console import routes_models as rm
    app, p = _app_with_config(tmp_path, "models: {}\n")
    rm.check_config_external_change(app)                    # 基线
    _touch_later(p)                                         # 模拟自己写盘
    rm._mark_config_seen(app)                               # 保存路径的记账动作
    assert rm.check_config_external_change(app) is False    # 不误报
