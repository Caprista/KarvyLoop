"""karvyloop.i18n._strings — 字符串表(en 默认 + zh)。

每个 key 一行,两个 locale 同 key 同占位符。新增用户可见字符串时:
  ① 在此两张表都加同名 key(en 必填,zh 跟上);
  ② 调用处 `i18n.t("namespace.key", **占位)` 取串。

key 命名:`<surface>.<what>`,如 console.* / cli.* / wizard.* / tokens.*。
占位符用 `{name}`(str.format);两个 locale 必须用**相同**占位名。

> 这是 A2 的种子表(覆盖 console 启动横幅 + 几条 CLI)。A3 逐面铺开时
> 把 console/CLI/wizard/错误/建议卡的硬编码中文搬进来,调用处改走 t()。
"""
from __future__ import annotations

# ---- English(默认)----

# 双语表本体已按 en/zh 拆成 _strings_en.py / _strings_zh.py(god-module 行数红线;
# 本文件只保聚合出口 TABLES —— 既有 import 面(i18n/__init__ 与 parity 测试)零变)。
from ._strings_en import EN as _EN
from ._strings_zh import ZH as _ZH

TABLES = {"en": _EN, "zh": _ZH}

__all__ = ["TABLES"]
