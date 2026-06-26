"""cli doctor / status — 确定性自检的人话渲染(无门槛"修",零模型)。"""
from __future__ import annotations

_ICON = {"ok": "✓", "warn": "⚠", "fail": "✗"}
# 这些 code 有"怎么修"的一行(其余只报状态)
_FIX_CODES = {
    "config_missing", "config_unreadable", "no_default_model", "no_key",
    "model_not_ready", "dep_missing", "data_corrupt", "version_newer", "port_busy",
}


def _render(findings) -> None:
    from karvyloop.i18n import t
    for f in findings:
        icon = _ICON.get(f.level, "·")
        print(f"  {icon} " + t("doctor.msg." + f.code, **f.params))
        if f.code in _FIX_CODES:
            print("      → " + t("doctor.fix." + f.code, **f.params))


def cmd_doctor(*, fix: bool = False) -> int:
    from karvyloop.doctor import FAIL, apply_fixes, overall, run_doctor
    from karvyloop.i18n import t
    findings = run_doctor()
    print(t("doctor.header"))
    _render(findings)
    if fix:
        # L1 自愈:只自动修可逆/低风险的;其余仍在上面列着、留给你拍(--fix 不碰)。
        repaired = apply_fixes(findings)
        if repaired:
            print("\n" + t("doctor.fixing"))
            _render(repaired)
            findings = run_doctor()           # 修完重诊,显示新状态
            print("\n" + t("doctor.after_fix"))
            _render(findings)
        else:
            print("\n" + t("doctor.nothing_to_fix"))   # 没有可自动修的(剩下的都得你拍)
    ov = overall(findings)
    print("\n" + t("doctor.overall." + ov))
    return 1 if ov == FAIL else 0   # fail → 非零退出码(脚本/CI 可判)


def cmd_status() -> int:
    """精简状态:版本 + 模型就绪 + 有无新版(doctor 的轻量版)。"""
    from karvyloop.doctor import check_config, check_version
    from karvyloop.i18n import t
    print(t("status.header"))
    _render(check_version())
    _render(check_config())
    return 0
