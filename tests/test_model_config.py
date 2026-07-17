"""test_model_config — 全局模型增删改查 + 密钥遮罩(Hardy:模型是全局配置要有管理入口)。"""
from __future__ import annotations
import pathlib, sys, textwrap
ROOT = pathlib.Path(__file__).resolve().parents[1]; sys.path.insert(0, str(ROOT))
from karvyloop.gateway import config_models as cm  # noqa: E402

CFG = textwrap.dedent("""
lang: en
models:
  providers:
    anthropic:
      base_url: https://api.anthropic.com
      api_key: sk-ant-SECRET12345
      models:
        - id: anthropic/claude
          name: Claude
          api: anthropic-messages
          context_window: 200000
          max_tokens: 8192
agents:
  defaults:
    model: anthropic/claude
embedding:
  model: anthropic/claude
""")


def _w(tmp):
    p = tmp / "config.yaml"; p.write_text(CFG, encoding="utf-8"); return p


def test_list_masks_literal_key(tmp_path):
    p = _w(tmp_path)
    d = cm.list_models(p)
    m = d["models"][0]
    assert m["id"] == "anthropic/claude" and m["is_default_chat"]
    assert m["api_key_masked"] == "****2345" and "SECRET" not in m["api_key_masked"]  # 不露明文
    assert m["has_key"] is True


def test_env_ref_key_not_masked(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text(CFG.replace("sk-ant-SECRET12345", "${ANTHROPIC_KEY}"), encoding="utf-8")
    assert cm.list_models(p)["models"][0]["api_key_masked"] == "${ANTHROPIC_KEY}"


def test_upsert_add_and_blank_key_keeps(tmp_path):
    p = _w(tmp_path)
    ok, _ = cm.upsert_model({"provider": "anthropic", "model_id": "anthropic/claude",
                             "model_name": "Claude v2", "api": "anthropic-messages",
                             "api_key": ""}, p)   # 留空 = 保留原 key
    assert ok
    import yaml
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    prov = cfg["models"]["providers"]["anthropic"]
    assert prov["api_key"] == "sk-ant-SECRET12345"   # 原 key 没被清掉
    assert prov["models"][0]["name"] == "Claude v2"  # 改名生效


def test_upsert_masked_key_keeps(tmp_path):
    p = _w(tmp_path)
    cm.upsert_model({"provider": "anthropic", "model_id": "anthropic/claude",
                     "api": "anthropic-messages", "api_key": "****2345"}, p)  # 回传遮罩串
    import yaml
    assert yaml.safe_load(p.read_text(encoding="utf-8"))["models"]["providers"]["anthropic"]["api_key"] == "sk-ant-SECRET12345"


# ---- 审计 #87 §3-①③:编辑保存不静默丢 reasoning / reasoning_styles(与 extra_headers 同模具) ----

def _w_reasoning(tmp):
    """写一个 reasoning:true + 带 reasoning_styles 的模型配置。"""
    import textwrap
    p = tmp / "config.yaml"
    p.write_text(textwrap.dedent("""
    models:
      providers:
        kimi-coding:
          base_url: https://api.kimi.com/coding/v1
          api_key: sk-kimi-FAKE-DO-NOT-LEAK-1
          models:
            - id: kimi-coding/kimi-for-coding
              name: Kimi For Coding
              api: openai-completions
              context_window: 256000
              max_tokens: 8192
              reasoning: true
              reasoning_styles:
                deep:
                  reasoning_effort: high
    agents:
      defaults:
        model: kimi-coding/kimi-for-coding
    """), encoding="utf-8")
    return p


def _md(p):
    import yaml
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    return cfg["models"]["providers"]["kimi-coding"]["models"][0]


def test_reasoning_flag_preserved_when_not_carried(tmp_path):
    """§3-①:编辑别的字段(reasoning 键缺席=None)→ reasoning:true 不被重置成 False。"""
    p = _w_reasoning(tmp_path)
    ok, _ = cm.upsert_model({"provider": "kimi-coding", "model_id": "kimi-coding/kimi-for-coding",
                             "model_name": "renamed", "api": "openai-completions",
                             "base_url": "https://api.kimi.com/coding/v1", "api_key": ""}, p)
    assert ok
    md = _md(p)
    assert md["reasoning"] is True          # 核心:没被静默重置
    assert md["name"] == "renamed"          # 改名生效


def test_reasoning_flag_explicit_false_overwrites(tmp_path):
    """显式传 reasoning=False → 覆写(用户真想关时能关)。"""
    p = _w_reasoning(tmp_path)
    cm.upsert_model({"provider": "kimi-coding", "model_id": "kimi-coding/kimi-for-coding",
                     "api": "openai-completions", "base_url": "https://api.kimi.com/coding/v1",
                     "api_key": "", "reasoning": False}, p)
    assert _md(p)["reasoning"] is False


def test_reasoning_flag_new_model_defaults_false(tmp_path):
    """新模型(无旧值)+ 未承载 reasoning → 落 False(零回归)。"""
    p = _w(tmp_path)
    cm.upsert_model({"provider": "openai", "model_id": "openai/gpt", "api": "openai-completions",
                     "base_url": "https://api.openai.com", "api_key": "sk-x"}, p)
    import yaml
    m = yaml.safe_load(p.read_text(encoding="utf-8"))["models"]["providers"]["openai"]["models"][0]
    assert m["reasoning"] is False


def test_reasoning_styles_preserved_when_not_carried(tmp_path):
    """§3-③:编辑别的字段(reasoning_styles 键缺席=None)→ 整段落参表不丢。"""
    p = _w_reasoning(tmp_path)
    cm.upsert_model({"provider": "kimi-coding", "model_id": "kimi-coding/kimi-for-coding",
                     "model_name": "renamed2", "api": "openai-completions",
                     "base_url": "https://api.kimi.com/coding/v1", "api_key": ""}, p)
    assert _md(p)["reasoning_styles"] == {"deep": {"reasoning_effort": "high"}}


def test_reasoning_styles_explicit_overwrites(tmp_path):
    """显式传新表 → 覆写(只留合法档位)。"""
    p = _w_reasoning(tmp_path)
    cm.upsert_model({"provider": "kimi-coding", "model_id": "kimi-coding/kimi-for-coding",
                     "api": "openai-completions", "base_url": "https://api.kimi.com/coding/v1",
                     "api_key": "", "reasoning_styles": {"fast": {"reasoning_effort": "low"},
                                                         "bogus": {"x": 1}}}, p)
    assert _md(p)["reasoning_styles"] == {"fast": {"reasoning_effort": "low"}}   # bogus 档被清洗掉


# ---- 审计 #87 §3-SUSPECTED②:`${VAR}` 未设 → 面板诚实标 env_unset,别冒充"已配置" ----

def test_env_unset_flag_true_when_env_missing(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text(CFG.replace("sk-ant-SECRET12345", "${ANTHROPIC_KEY_MISSING}"), encoding="utf-8")
    monkeypatch.delenv("ANTHROPIC_KEY_MISSING", raising=False)
    m = cm.list_models(p)["models"][0]
    assert m["env_unset"] is True                 # 引用了没设的 env → 标未设
    assert m["api_key_masked"] == "${ANTHROPIC_KEY_MISSING}"   # 引用原样(非秘密)


def test_env_unset_flag_false_when_env_set(tmp_path, monkeypatch):
    p = tmp_path / "config.yaml"
    p.write_text(CFG.replace("sk-ant-SECRET12345", "${ANTHROPIC_KEY_SET}"), encoding="utf-8")
    monkeypatch.setenv("ANTHROPIC_KEY_SET", "sk-real")
    assert cm.list_models(p)["models"][0]["env_unset"] is False


def test_env_unset_flag_false_for_literal_key(tmp_path):
    """字面量 key(非 ${VAR})→ env_unset 恒 False。"""
    assert cm.list_models(_w(tmp_path))["models"][0]["env_unset"] is False


def test_delete_default_blocked(tmp_path):
    p = _w(tmp_path)
    ok, reason = cm.delete_model("anthropic/claude", p)
    assert not ok and "默认" in reason


def test_set_default_and_delete_nondefault(tmp_path):
    p = _w(tmp_path)
    cm.upsert_model({"provider": "openai", "model_id": "openai/gpt", "api": "openai-completions",
                     "base_url": "https://api.openai.com", "api_key": "sk-x"}, p)
    cm.set_default("chat", "openai/gpt", p)
    import yaml
    assert yaml.safe_load(p.read_text(encoding="utf-8"))["agents"]["defaults"]["model"] == "openai/gpt"
    # 仍是 embedding 默认 → 删被守护拦
    blocked, _ = cm.delete_model("anthropic/claude", p)
    assert not blocked
    cm.set_default("embedding", "openai/gpt", p)     # 两个默认都换走
    ok, _ = cm.delete_model("anthropic/claude", p)   # 现在不是任何默认 → 可删
    assert ok


# ---- setup gate 三刀(Hardy 实损:空 key"保存成功"写出空壳还盖配置,重启后全站锁死)----

def test_upsert_new_cloud_provider_blank_key_rejected(tmp_path):
    """云端 provider 首配空 key = 拒绝写盘("保留原值"只对已有密钥的编辑成立)。"""
    p = _w(tmp_path)
    before = p.read_text(encoding="utf-8")
    ok, reason = cm.upsert_model({"provider": "openai", "model_id": "openai/gpt",
                                  "api": "openai-completions",
                                  "base_url": "https://api.openai.com", "api_key": ""}, p)
    assert not ok and "API Key" in reason
    assert p.read_text(encoding="utf-8") == before          # 一个字节都没动
    ok2, _ = cm.upsert_model({"provider": "openai", "model_id": "openai/gpt",
                              "api": "openai-completions",
                              "base_url": "https://api.openai.com", "api_key": "****9999"}, p)
    assert not ok2                                           # 遮罩串也不算首配密钥


def test_upsert_local_provider_blank_key_ok(tmp_path):
    """本地 provider(ollama/localhost)无需真 key,空 key 照常保存(零 key 本地路径不误伤)。"""
    p = _w(tmp_path)
    ok, reason = cm.upsert_model({"provider": "ollama", "model_id": "ollama/llama3",
                                  "api": "openai-completions",
                                  "base_url": "http://127.0.0.1:11434/v1", "api_key": ""}, p)
    assert ok, reason


def test_save_writes_backup_of_previous_config(tmp_path):
    """任何写盘前留一代 .bak:一次误保存不再能毁掉唯一的能用配置。"""
    p = _w(tmp_path)
    original = p.read_text(encoding="utf-8")
    ok, _ = cm.upsert_model({"provider": "anthropic", "model_id": "anthropic/claude",
                             "model_name": "Claude v2", "api": "anthropic-messages",
                             "api_key": ""}, p)
    assert ok
    bak = p.with_suffix(".yaml.bak")
    assert bak.exists()
    assert bak.read_text(encoding="utf-8") == original       # .bak = 改动前的上一版
    assert "Claude v2" in p.read_text(encoding="utf-8")      # 新配置真落了


def test_reject_bad_api(tmp_path):
    p = _w(tmp_path)
    ok, reason = cm.upsert_model({"provider": "x", "model_id": "x/y", "api": "bogus"}, p)
    assert not ok and "api" in reason


# ---- CFG-06(内测建议):UI 删模型 → config.yaml 里"相关配置数据"同步清干净 ----

def test_delete_last_model_removes_provider_block(tmp_path):
    """删掉 provider 的最后一个模型:整个 provider 块(含 api_key/base_url)从 config.yaml 清掉,
    其它手写配置项(lang / 别的 provider / 默认指针)一个不伤,写前照旧留 .bak。"""
    import yaml
    p = _w(tmp_path)
    ok, _ = cm.upsert_model({"provider": "openai", "model_id": "openai/gpt",
                             "api": "openai-completions",
                             "base_url": "https://api.openai.com",
                             "api_key": "FAKE-DO-NOT-LEAK-openai-key"}, p)
    assert ok
    before = p.read_text(encoding="utf-8")
    ok, reason = cm.delete_model("openai/gpt", p)        # openai 唯一模型 → 删空
    assert ok, reason
    raw = p.read_text(encoding="utf-8")
    cfg = yaml.safe_load(raw)
    assert "openai" not in cfg["models"]["providers"]              # provider 块整个没了
    assert "FAKE-DO-NOT-LEAK-openai-key" not in raw                # key 不残留
    assert "api.openai.com" not in raw                             # base_url 不残留
    # 其它键一个不伤
    assert cfg["lang"] == "en"
    assert cfg["models"]["providers"]["anthropic"]["api_key"] == "sk-ant-SECRET12345"
    assert cfg["agents"]["defaults"]["model"] == "anthropic/claude"
    # 写前 .bak(既有 _save 机制):上一版(还含 openai)拿得回
    bak = p.with_suffix(".yaml.bak")
    assert bak.exists() and bak.read_text(encoding="utf-8") == before


def test_delete_one_of_two_keeps_provider_and_key(tmp_path):
    """provider 还有别的模型 → 只删那条模型,provider 块和 key 原样保留。"""
    import yaml
    p = _w(tmp_path)
    ok, _ = cm.upsert_model({"provider": "anthropic", "model_id": "anthropic/haiku",
                             "api": "anthropic-messages", "api_key": ""}, p)   # 同 provider 第二模型
    assert ok
    ok, reason = cm.delete_model("anthropic/haiku", p)
    assert ok, reason
    cfg = yaml.safe_load(p.read_text(encoding="utf-8"))
    prov = cfg["models"]["providers"]["anthropic"]
    assert prov["api_key"] == "sk-ant-SECRET12345"                 # key 还在
    assert [m["id"] for m in prov["models"]] == ["anthropic/claude"]
