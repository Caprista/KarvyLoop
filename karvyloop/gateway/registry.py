"""模型注册表（gateway/registry.py）。

全局唯一处加载 providers + models（含密钥）。agent 只持引用串，不内嵌配置——
密钥只活在这里，绝不进可分享的镜像（#0 §2.1）。规格：docs/modules/gateway.md §3。
"""

from __future__ import annotations

import os
import re
from pathlib import Path

from karvyloop.schemas import ModelDefinition, ProviderConfig

_ENV = re.compile(r"\$\{([^}]+)\}")


class UnknownModelError(KeyError):
    """引用了注册表里不存在的模型（fail-closed：不静默兜底，报错）。"""


def _expand_env(v):
    """递归展开 ${VAR} 为环境变量值（密钥从环境读，不写进 yaml）。"""
    if isinstance(v, str):
        return _ENV.sub(lambda m: os.environ.get(m.group(1), ""), v)
    if isinstance(v, dict):
        return {k: _expand_env(x) for k, x in v.items()}
    if isinstance(v, list):
        return [_expand_env(x) for x in v]
    return v


class ModelRegistry:
    def __init__(self, providers: dict[str, ProviderConfig],
                 models: dict[str, ModelDefinition],
                 default_chat: str, default_embedding: str):
        self.providers = providers
        self.models = models
        self.default_chat = default_chat
        self.default_embedding = default_embedding

    @classmethod
    def from_config(cls, cfg: dict) -> "ModelRegistry":
        cfg = _expand_env(cfg)
        providers: dict[str, ProviderConfig] = {}
        models: dict[str, ModelDefinition] = {}
        for name, p in cfg["models"]["providers"].items():
            p = dict(p)
            mdicts = []
            for md in p.get("models", []):
                md = dict(md)
                md.setdefault("name", md["id"])     # name 缺省取 id（schema 要求 name）
                mdicts.append(md)
            p["models"] = mdicts
            pc = ProviderConfig(name=name, **p)
            providers[name] = pc
            for m in pc.models:
                if m.id in models:
                    raise ValueError(f"duplicate model id: {m.id}")  # 全局唯一
                models[m.id] = m
        # embedding 是**可选**槽位(闭环审计断②尾巴:网页引导只写 chat 模型,没有 embedding 段;
        # 此前 cfg["embedding"] 硬取 → KeyError → 重启后 gateway 仍构造失败 → 永远到不了首次对话,
        # 且 readiness 连带永远 must_setup=强制引导死循环)。项目匹配/召回不上向量(既定决策),
        # embed() 无生产调用者 —— 没配就留空,真调 embed 时 fail-closed(UnknownModelError)。
        reg = cls(providers, models,
                  default_chat=cfg["agents"]["defaults"]["model"],
                  default_embedding=((cfg.get("embedding") or {}).get("model", "") or ""))
        reg._validate()
        return reg

    @classmethod
    def load(cls, path: str | Path) -> "ModelRegistry":
        import yaml  # 延迟导入：测试走 from_config(dict) 时无需 yaml
        return cls.from_config(yaml.safe_load(Path(path).read_text(encoding="utf-8")))

    def _validate(self) -> None:
        if self.default_chat not in self.models:
            raise ValueError(f"agents.defaults.model '{self.default_chat}' 不在 models 注册表里")
        if self.default_embedding and self.default_embedding not in self.models:
            raise ValueError(f"embedding.model '{self.default_embedding}' 不在 models 注册表里")

    def get(self, model_ref: str) -> ModelDefinition:
        if model_ref not in self.models:
            raise UnknownModelError(model_ref)   # fail-closed
        return self.models[model_ref]

    def provider_of(self, model_ref: str) -> ProviderConfig:
        return self.providers[model_ref.split("/", 1)[0]]
