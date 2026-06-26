"""影子分类器占位（capability/classifier.py）。

M1+ 计划（docs/modules/capability.md §2.6）：
  - 两阶段：fast(≤64 token 求 block yes/no) → 可疑升 thinking
  - transcript 只含 user 文本+tool_use（丢弃 assistant 文本防自操纵）
  - 连续拒超限 → 升级人工

M0：未实现；`broker.classify` 透传 'allow'。本模块保留文件以让 import 稳定。
"""
