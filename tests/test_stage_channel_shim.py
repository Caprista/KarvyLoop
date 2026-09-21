"""The DingTalk channel is provided by the canonical package source."""

from karvyloop.channels import dingtalk_channel as canonical


def test_canonical_channel_exports_required_symbols():
    assert canonical.DingTalkChannel
    assert canonical.handle_incoming
    assert canonical._requires_markdown_fallback
