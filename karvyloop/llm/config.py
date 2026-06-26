"""LLM 配置(llm/config.py)。

从 `~/.karvyloop/config.yaml` 读 LLM 配置(5 问硬规则 L1 强制)。

配置结构:
  llm:
    default: <provider-name>
    providers:
      <name>:
        type: anthropic|minimax|mock
        api_key: <key>  (mock 不需要)
        base_url: <url>
        default_model: <model-id>

设计稿:docs/21 §3.1-3.2 + §4 8 不变量。
"""
from __future__ import annotations

import dataclasses
import pathlib
import re
from typing import Optional

# 真实 key 形状(防止误把真 key 写进仓库)
_REAL_KEY_PATTERNS = (
    re.compile(r"^sk-ant-"),  # Anthropic
    re.compile(r"^sk-[A-Za-z0-9]{20,}"),  # OpenAI / 通用
    re.compile(r"^eyJ"),  # JWT
)

# 测试/示例 fixture key 白名单(5 问硬规则 L4:必带这些字样)
_FAKE_KEY_MARKERS = ("FAKE", "DO-NOT-LEAK", "EXAMPLE", "REPLACE", "TEST-KEY")


def _is_fake_key(value: str) -> bool:
    """5 问硬规则 L4:fixture key 必带 FAKE/DO-NOT-LEAK 字样。"""
    return any(marker in value for marker in _FAKE_KEY_MARKERS)


@dataclasses.dataclass(frozen=True)
class ProviderConfig:
    """单个 provider 配置(M3.0 批 1 拍 1)。

    与 karvyloop.schemas.ProviderConfig 区别:
      - 这个是 llm 层简化版(只 4 字段)
      - 内部自动转 schemas.ProviderConfig 喂给 gateway adapter
    """
    type: str                                       # "anthropic" / "minimax" / "mock"
    api_key: str = ""                               # mock 时空
    base_url: str = ""
    default_model: str = ""


@dataclasses.dataclass(frozen=True)
class LLMConfig:
    """LLM 配置总集(从 ~/.karvyloop/config.yaml 读)。"""
    default: str                                    # provider name
    providers: dict[str, ProviderConfig]            # name -> config


# ---------- 错误 ----------

class ConfigNotFoundError(FileNotFoundError):
    """配置文件不存在。"""


class MissingDefaultError(ValueError):
    """配置缺 default 字段。"""


class MissingProvidersError(ValueError):
    """配置缺 providers 字段。"""


class MissingApiKeyError(ValueError):
    """anthropic/minimax provider 缺 api_key。"""


class RealKeyInRepoError(ValueError):
    """检测到真实 API key 形状(5 问硬规则 L5:真 key 不能进仓库)。"""


# ---------- 加载 ----------

DEFAULT_CONFIG_PATH = pathlib.Path.home() / ".karvyloop" / "config.yaml"


def _looks_like_real_key(value: str) -> bool:
    """5 问硬规则 L5:检测真 key 形状。"""
    if not value:
        return False
    for pat in _REAL_KEY_PATTERNS:
        if pat.match(value):
            return True
    return False


def _resolve_config_path(path: Optional[pathlib.Path]) -> pathlib.Path:
    """解析配置路径。None → ~/.karvyloop/config.yaml。"""
    if path is None:
        return DEFAULT_CONFIG_PATH
    return pathlib.Path(path).expanduser()


def load_config(
    path: Optional[pathlib.Path] = None,
    *,
    allow_real_keys: bool = False,
) -> LLMConfig:
    """从 YAML 文件加载 LLM 配置。

    Args:
        path: 配置文件路径,None 用 ~/.karvyloop/config.yaml。
        allow_real_keys: True 时允许真 key 形状(**仅**生产环境人工用,
                        仓库内单测必须 False,5 问硬规则 L5)。

    Returns:
        LLMConfig(default + providers)。

    Raises:
        ConfigNotFoundError: 文件不存在。
        MissingDefaultError: 缺 default。
        MissingProvidersError: 缺 providers。
        MissingApiKeyError: anthropic/minimax 缺 api_key。
        RealKeyInRepoError: 检测到真 key 形状且 allow_real_keys=False。

    5 问硬规则承诺(L1-L5):
      - 真 key 只在 ~/.karvyloop/config.yaml(仓库外)
      - 仓库内调用必 allow_real_keys=False → 真 key 进 → 抛
    """
    resolved = _resolve_config_path(path)
    if not resolved.exists():
        raise ConfigNotFoundError(f"配置文件不存在: {resolved}")

    # 解析 YAML
    try:
        import yaml  # pyyaml(已在依赖)
    except ImportError as e:
        raise RuntimeError("pyyaml 未安装,pip install pyyaml") from e

    with open(resolved, "r", encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    if not isinstance(raw, dict):
        raise ValueError(f"配置根必须是 dict,实际 {type(raw).__name__}")

    llm_section = raw.get("llm")
    if not isinstance(llm_section, dict):
        raise MissingProvidersError("缺 llm.providers 段")

    # default
    default = llm_section.get("default")
    if not isinstance(default, str) or not default:
        raise MissingDefaultError("缺 llm.default(provider name)")

    # providers
    providers_raw = llm_section.get("providers")
    if not isinstance(providers_raw, dict) or not providers_raw:
        raise MissingProvidersError("缺 llm.providers 或为空")

    # 校验 default 必须存在于 providers
    if default not in providers_raw:
        raise ValueError(f"default '{default}' 不在 providers 列表中: {list(providers_raw)}")

    # 构造 ProviderConfig(L5 真 key 检测)
    providers: dict[str, ProviderConfig] = {}
    for name, p_raw in providers_raw.items():
        if not isinstance(p_raw, dict):
            raise ValueError(f"provider '{name}' 必须是 dict")

        ptype = p_raw.get("type")
        if ptype not in ("anthropic", "minimax", "mock"):
            raise ValueError(f"provider '{name}' type 必须是 anthropic/minimax/mock,实际 {ptype}")

        api_key = p_raw.get("api_key", "") or ""
        base_url = p_raw.get("base_url", "") or ""
        default_model = p_raw.get("default_model", "") or ""

        # L2: mock 不需要 api_key
        if ptype == "mock":
            api_key = ""  # 强制清空

        # L3: anthropic/minimax 必须有 api_key
        if ptype in ("anthropic", "minimax") and not api_key:
            raise MissingApiKeyError(
                f"provider '{name}' type={ptype} 必须有 api_key"
            )

        # L5: 真 key 形状检测(但 fixture key 带 FAKE 字样放行)
        if (
            api_key
            and _looks_like_real_key(api_key)
            and not _is_fake_key(api_key)
            and not allow_real_keys
        ):
            raise RealKeyInRepoError(
                f"provider '{name}' api_key 形状是真实 key(sk-ant-/sk-/eyJ),"
                f"在仓库内调用必须用 FAKE key(allow_real_keys=True 仅生产环境)"
            )

        providers[name] = ProviderConfig(
            type=ptype,
            api_key=api_key,
            base_url=base_url,
            default_model=default_model,
        )

    return LLMConfig(default=default, providers=providers)
