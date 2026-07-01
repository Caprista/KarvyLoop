"""cli verify-web — 无头浏览器真加载网页产物,抓控制台报错(网页类的运行时验收门)。"""
from __future__ import annotations


def cmd_verify_web(path: str, *, entry: str = "index.html") -> int:
    from karvyloop.coding.web_verify import verify_web_app
    from karvyloop.i18n import t
    r = verify_web_app(path, entry=entry)
    if not r.available:
        print(t("verifyweb.unavailable"))
        return 0   # 没装不算失败(降级);只是验不了运行时
    if r.ok is None:
        # 装了 Playwright 但验证器没跑成(浏览器没下成 / 启动失败)→ **没验到运行时**,
        # 绝不能当成「✗ 0 条报错」误报成失败。老实说没验成(降级),退 0。
        print(t("verifyweb.inconclusive"))
        if r.reason:
            print("  " + r.reason)
        return 0
    if r.ok:
        print(t("verifyweb.ok", url=r.url))
        return 0
    print(t("verifyweb.failed", n=len(r.errors)))
    if r.reason:
        print("  " + r.reason)
    for e in r.errors[:20]:
        print("  - " + e)
    return 1   # 有运行时报错 → 非零(agent 的 run_command / CI 能判失败)
