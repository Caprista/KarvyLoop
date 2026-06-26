"""共享基类：所有 KarvyLoop 数据契约的根。

- `extra="forbid"`：契约层禁止未知字段——任何拼写错误/字段漂移在构造时即报错，
  呼应项目"no silent drift"纪律（#0 §5.1 类型化产物交接 / #3 §4 统一协议面）。
- `protected_namespaces=()`：解除 Pydantic 对 `model_*` 命名空间的保护，
  让我们能用 `model`（模型引用）作为字段名（#7 §1）。
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict


class Schema(BaseModel):
    """KarvyLoop 所有数据契约的基类。"""

    model_config = ConfigDict(extra="forbid", protected_namespaces=())
