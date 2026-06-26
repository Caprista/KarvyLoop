"""cli update — 检测有没有新版本(只检测+提示,**绝不自动升级**)。"""
from __future__ import annotations


def cmd_update() -> int:
    from karvyloop.i18n import t
    from karvyloop.update import check_update

    r = check_update(force=True)   # CLI 显式查 → 跳缓存,要最新结果
    cur = r["current"]
    if r.get("source") == "disabled":
        print(t("update.disabled", current=cur))
        return 0
    if not r.get("checked") or r.get("latest") is None:
        print(t("update.unreachable", current=cur))
        return 0
    if r["newer"]:
        print(t("update.available", current=cur, latest=r["latest"]))
        print(t("update.command", command=r["command"]))
        if r.get("url"):
            print(t("update.notes", url=r["url"]))
    else:
        print(t("update.uptodate", current=cur))
    return 0
