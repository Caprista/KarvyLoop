"""The deployment-stage DingTalk module must delegate to canonical source."""

from pathlib import Path

import remote_oa_stage.dingtalk_channel as staged
from karvyloop.channels import dingtalk_channel as canonical


def test_stage_module_delegates_channel_symbols():
    assert staged.DingTalkChannel is canonical.DingTalkChannel
    assert staged.handle_incoming is canonical.handle_incoming
    assert staged._requires_markdown_fallback is canonical._requires_markdown_fallback


def test_stage_module_is_a_small_compatibility_shim():
    stage_path = Path(staged.__file__)
    assert stage_path.stat().st_size < 4_096
    assert "canonical" in stage_path.read_text(encoding="utf-8").lower()
