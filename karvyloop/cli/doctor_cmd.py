"""cli doctor / status — 确定性自检的人话渲染(无门槛"修",零模型)+ 活性检查 + 日志落盘。"""
from __future__ import annotations

import sys
from typing import Optional

_ICON = {"ok": "✓", "warn": "⚠", "fail": "✗"}
# 这些 code 有"怎么修"的一行(其余只报状态)
_FIX_CODES = {
    "config_missing", "config_unreadable", "no_default_model", "no_key",
    "model_not_ready", "dep_missing", "data_corrupt", "version_newer", "port_busy",
    # 活性检查的修法一行
    "endpoint_unreachable", "local_endpoint_down", "disk_not_writable", "sandbox_stub",
}


def _render(findings) -> None:
    from karvyloop.i18n import t
    for f in findings:
        icon = _ICON.get(f.level, "·")
        print(f"  {icon} " + t("doctor.msg." + f.code, **f.params))
        if f.code in _FIX_CODES:
            print("      → " + t("doctor.fix." + f.code, **f.params))


def _confirm(prompt: str) -> bool:
    """交互确认(y/N)。非 TTY → 默认 False(危险修绝不在非交互里悄悄跑)。"""
    if not sys.stdin.isatty():
        return False
    try:
        sys.stdout.write(prompt + " [y/N] ")
        sys.stdout.flush()
        ans = sys.stdin.readline().strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("y", "yes")


def cmd_doctor(*, fix: bool = False, online: bool = False,
               confirm_fn=None) -> int:
    """确定性自检(+ --online 活性 + --fix 自愈)。结果落盘 ~/.karvyloop/logs/doctor.log。

    confirm_fn: 危险修的确认回调(测试可注入);默认走 CLI y/N 交互。
    """
    from karvyloop import doctor as D
    from karvyloop.doctor import FAIL, apply_fixes, overall, run_doctor
    from karvyloop.i18n import t
    confirm = confirm_fn or _confirm

    findings = run_doctor()
    if online:
        from karvyloop.doctor_liveness import run_liveness
        findings = findings + run_liveness()

    print(t("doctor.header"))
    _render(findings)

    # 落盘:每次 doctor 结果都进固定日志(可读性有了家)。不写 key(只落 code+params)。
    try:
        from karvyloop.doctor_log import log_findings
        log_findings(findings, phase="doctor")
    except Exception:
        pass

    if fix:
        # 安全那批:直接自动修(可逆/幂等/不覆盖用户内容)
        repaired = apply_fixes(findings, include_confirmed=False)
        # 危险那批(重写 config 等):逐个问 y/N,同意才修
        confirmed: list = []
        for f in findings:
            if f.code in D.CONFIRM_FIXABLE:
                if confirm(t("doctor.confirm." + f.code, **f.params)):
                    r = D.repair_finding(f)
                    if r is not None:
                        confirmed.append(r)
                else:
                    print("  " + t("doctor.confirm.skipped"))
        repaired = repaired + confirmed

        if repaired:
            print("\n" + t("doctor.fixing"))
            _render(repaired)
            try:
                from karvyloop.doctor_log import log_findings as _lf
                _lf(repaired, phase="fix")
            except Exception:
                pass
            findings = run_doctor()           # 修完重诊,显示新状态
            if online:
                from karvyloop.doctor_liveness import run_liveness as _rl
                findings = findings + _rl()
            print("\n" + t("doctor.after_fix"))
            _render(findings)
        else:
            print("\n" + t("doctor.nothing_to_fix"))   # 没有可自动修的(剩下的都得你拍)
    ov = overall(findings)
    print("\n" + t("doctor.overall." + ov))
    try:
        from karvyloop.doctor_log import log_path
        p = log_path()
        if p is not None:
            print(t("doctor.log_at", path=str(p)))
    except Exception:
        pass
    return 1 if ov == FAIL else 0   # fail → 非零退出码(脚本/CI 可判)


def cmd_status() -> int:
    """精简状态:版本 + 模型就绪 + 有无新版(doctor 的轻量版)。"""
    from karvyloop.doctor import check_config, check_version
    from karvyloop.i18n import t
    print(t("status.header"))
    _render(check_version())
    _render(check_config())
    return 0


def health_summary(*, online: bool = False,
                    config_path: Optional[object] = None) -> dict:
    """给 console `/api/health` 用:结构化健康摘要(零渲染,前端自己走 i18n)。

    返回 {overall, findings:[{level,code,params,fixable}], log_path}。
    fixable ∈ auto|confirm|no —— 前端据此决定"一键修"按钮怎么显示。永不抛、不含 key。
    """
    from karvyloop import doctor as D
    from karvyloop.doctor import overall, run_doctor
    findings = run_doctor(config_path=config_path)
    if online:
        try:
            from karvyloop.doctor_liveness import run_liveness
            findings = findings + run_liveness(config_path=config_path)
        except Exception:
            pass

    def _fixable(code: str) -> str:
        if code in D.AUTO_FIXABLE:
            return "auto"
        if code in D.CONFIRM_FIXABLE:
            return "confirm"
        return "no"

    try:
        from karvyloop.doctor_log import log_findings, log_path
        log_findings(findings, phase="health")
        lp = str(log_path() or "")
    except Exception:
        lp = ""
    return {
        "overall": overall(findings),
        "findings": [{**f.to_dict(), "fixable": _fixable(f.code)} for f in findings],
        "log_path": lp,
    }
