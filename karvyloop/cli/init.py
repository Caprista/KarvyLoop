"""karvyloop init:写 ~/.karvyloop/config.yaml（cli/init.py）。

规格：docs/modules/workbench-cli.md §3 init.py + §4 本地优先默认。
- 默认本地(Ollama/127.0.0.1),数据不出门
- API key 用 ${ENV_VAR} 占位(不写明文,密钥只活在 env 或 vault)
- 必填字段:providers / default chat / embedding
"""

from __future__ import annotations

from pathlib import Path
from typing import Optional


# 默认配置(本地优先)
DEFAULT_CONFIG_YAML = """\
# KarvyLoop 配置(~/.karvyloop/config.yaml)
# 本地优先:默认走 ollama(127.0.0.1:11434,数据不出门)
# 密钥走环境变量 ${VAR},不写明文进 yaml(护城河之一)
#
# schema 纪律:
#   - auth 必须是 api-key / oauth / aws-sdk / token 之一
#   - api 字段在每个 model 上(不在 provider 上)
#   - provider 名字跟 model id 前缀对应(ollama/x 里的 ollama)

# UI 语言偏好(GUI 语言切换器会自动写这里;也可手填 en / zh)。设一次,之后启动自动生效。
# lang: en

models:
  # prompt cache(省钱开关,默认 true):给每次调用基本不变的稳定前缀(system 尾 + tools 尾)
  # 打缓存断点,重复调用命中 cache_read 省该前缀约 90% input 成本(Anthropic 系)。
  # OpenAI/DeepSeek 系是自动缓存(无需标记),命中照样记进账本 cache 列。设 false 关掉。
  # prompt_cache: true
  providers:
    ollama:
      base_url: http://127.0.0.1:11434
      auth: api-key
      api_key: dummy
      models:
        - id: ollama/qwen2.5-coder:7b
          name: Qwen 2.5 Coder 7B
          api: openai-completions
          context_window: 32768
          max_tokens: 4096
        - id: ollama/nomic-embed-text
          name: Nomic Embed Text
          api: ollama
          context_window: 8192
          max_tokens: 2048
    anthropic:
      base_url: https://api.anthropic.com
      auth: api-key
      api_key: ${ANTHROPIC_API_KEY}
      models:
        - id: anthropic/claude-sonnet-4-6
          name: Claude Sonnet 4.6
          api: anthropic-messages
          context_window: 200000
          max_tokens: 8192

agents:
  defaults:
    model: ollama/qwen2.5-coder:7b

embedding:
  model: ollama/nomic-embed-text

crystallize:
  # 技能结晶落盘目录(可被 --skills-dir CLI flag 覆盖)
  # M3+ 拍 6 落地;拍 8 写进默认 config 让 wizard 用户能直接看到
  skills_dir: ~/.karvyloop/skills
  # 结晶灵敏度旋钮(9.4 起真 config-driven;"阈值是旋钮非真理")—— 决定技能库长多快/多严:
  min_usage_count: 5        # 同一任务用够几次才结晶(调低=更快长技能库,调高=更稳/更少噪音)
  min_success_rate: 0.8     # 成功率下限(低于此不结晶)
  usage_debounce_sec: 60    # 同任务去抖窗口(秒):窗口内重复不重复计数,防单次爆发灌计数
  cluster_overlap_threshold: 0.2  # ⭐同一任务"换个说法"也算同一个(token 重叠聚类):
                                  # 调低=更宽松更易长技能库(但过低会把不同任务并一起),0=关
  # promote_score: 3.0      # (高级)价值分门槛
  # generalized_distinct: 2 # (高级)判"可泛化"所需的不同参数变体数

# 联网搜索(可选):agent 的 web_search 默认走**无 key 的 DuckDuckGo**,开箱即用。
# 想要更稳/更高质量,填一个搜索 API key(真 key 只写在这个仓外的 config.yaml):
#   provider: brave   → 去 https://brave.com/search/api/ 拿 key
#   provider: tavily  → 去 https://tavily.com 拿 key(面向 LLM 的搜索)
# 配了就自动优先用它,出错再回落 DuckDuckGo。也可用环境变量 KARVYLOOP_SEARCH_API_KEY 覆盖。
# search:
#   provider: brave
#   api_key: "BSA..."

# MCP server(可选):接任意 MCP server,它的工具会注入给每个 agent(键带 mcp_<server>_ 前缀)。
# 复用你已配的 LLM key 的搜索(无需再办新 key):MiniMax Token-Plan 自带 web_search,只用你的
# minimax key(消耗你的 Token Plan 额度)。需 PATH 有 uvx(`pip install uv`)或填 uvx 绝对路径。
# mcp:
#   servers:
#     - name: minimax
#       command: uvx
#       args: ["minimax-coding-plan-mcp", "-y"]
#       env:
#         MINIMAX_API_KEY: "@provider:minimax"        # 复用上面 minimax provider 的 key
#         MINIMAX_API_HOST: "@provider_host:minimax"  # 自动用你 minimax 的区域 host
"""


CONFIG_DIR = Path.home() / ".karvyloop"
CONFIG_PATH = CONFIG_DIR / "config.yaml"


def default_config_path() -> Path:
    """返回默认配置路径(M0 写死 ~/.karvyloop/config.yaml;M1+ 加 --config 覆盖)。"""
    return CONFIG_PATH


def cmd_init(
    *,
    path: Optional[Path] = None,
    interactive: bool = True,
    force: bool = False,
    stdout=None,
    no_wizard: bool = False,
) -> int:
    """生成 config.yaml。

    - path=None → 默认 ~/.karvyloop/config.yaml
    - force=True → 覆盖已存在
    - interactive=True → 询问(非 TTY 自动跳过)
    - no_wizard=True → 跳过 wizard(M3+ 拍 8 加,开发者 / CI 用),走原 CLI flag 路径
    - 走 wizard 流程(interactive=True + TTY + no_wizard=False, M3+ 拍 8 加):
        问 provider / API key / 测试连接 / 写 yaml
    - 返回 0 成功 / 1 失败
    """
    import sys

    target = Path(path) if path else CONFIG_PATH
    if target.exists() and not force:
        if interactive and sys.stdin.isatty():
            out = stdout or sys.stdout
            out.write(f"配置已存在:{target}\n")
            out.write("覆盖?[y/N] ")
            out.flush()
            try:
                ans = sys.stdin.readline().strip().lower()
            except (EOFError, KeyboardInterrupt):
                return 1
            if ans not in ("y", "yes"):
                out.write("取消。\n")
                return 0
        else:
            # 非交互 + 已存在 + 不强制 → 退出
            if stdout is not None:
                stdout.write(f"配置已存在:{target}(加 --force 覆盖)\n")
            return 1

    # M3+ 拍 8:wizard 模式
    if interactive and sys.stdin.isatty() and not no_wizard:
        from .render import Renderer
        from .wizard import WizardError, run_wizard
        renderer = Renderer()
        try:
            return run_wizard(target=target, renderer=renderer)
        except WizardError:
            if stdout is not None:
                stdout.write("wizard 取消。\n")
            return 1

    # 写默认(非交互 / --no-wizard / 非 TTY 走这条)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(DEFAULT_CONFIG_YAML, encoding="utf-8")
    if stdout is not None:
        stdout.write(f"已写入:{target}\n")
        stdout.write("下一步:export ANTHROPIC_API_KEY=... 或启动 ollama,然后 karvyloop run \"...\"\n")
    return 0


__all__ = ["cmd_init", "default_config_path", "CONFIG_PATH", "DEFAULT_CONFIG_YAML"]
