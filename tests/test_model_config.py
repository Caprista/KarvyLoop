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


def test_reject_bad_api(tmp_path):
    p = _w(tmp_path)
    ok, reason = cm.upsert_model({"provider": "x", "model_id": "x/y", "api": "bogus"}, p)
    assert not ok and "api" in reason
