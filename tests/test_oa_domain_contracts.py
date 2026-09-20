from dataclasses import FrozenInstanceError, fields, is_dataclass

import pytest

from karvyloop.oa import (
    ApprovalContext,
    ChannelEnvelope,
    OAIntent,
    RuleIntent,
    ToolExecutionEvent,
)


@pytest.mark.parametrize("cls", [ChannelEnvelope, OAIntent, RuleIntent, ApprovalContext, ToolExecutionEvent])
def test_contracts_are_frozen_dataclasses(cls):
    assert is_dataclass(cls)
    assert all(field.name for field in fields(cls))


def test_channel_envelope_is_platform_neutral_and_copies_metadata():
    metadata = {"provider_message_id": "m-1"}
    envelope = ChannelEnvelope("dingtalk", "oa-a", "conversation-1", "user-1", "刷新", metadata=metadata)
    metadata["provider_message_id"] = "changed"
    assert envelope.conversation_key == "conversation-1"
    assert envelope.metadata["provider_message_id"] == "m-1"
    with pytest.raises(FrozenInstanceError):
        envelope.text = "other"


def test_intent_rejects_invalid_indexes():
    with pytest.raises(ValueError):
        OAIntent("approve", indexes=(0,))
    assert OAIntent("approve", indexes=[2, 3]).indexes == (2, 3)


def test_rule_intent_keeps_conditions_as_data():
    rule = RuleIntent(conditions={"all": [{"field": "request.amount", "operator": "lte", "value": 5000}]})
    assert rule.conditions["all"][0]["field"] == "request.amount"
    with pytest.raises(ValueError):
        RuleIntent(priority=-1)


def test_tool_event_and_approval_context():
    event = ToolExecutionEvent("oa.approval.approve", "approved", request_id="r-1")
    context = ApprovalContext(amount=100, currency="CNY", attachment_count=2)
    assert event.request_id == "r-1"
    assert context.amount == 100
    with pytest.raises(ValueError):
        ApprovalContext(attachment_count=-1)
