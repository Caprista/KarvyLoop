"""Platform-neutral OA domain helpers."""

from .approval_rules import (
    ALLOWED_FIELDS,
    ALLOWED_OPERATORS,
    RuleValidationError,
    explain_rule_match,
    matches_rule,
    normalize_rule,
    validate_rule,
)
from .domain import ApprovalContext, ChannelEnvelope, OAIntent, RuleIntent, ToolExecutionEvent
from .approval_service import ApprovalSnapshotStore, ApprovalStateService, OAApprovalService
from .keyword_matcher import KeywordMatchResult, match_keywords, normalize_text

__all__ = [
    "ALLOWED_FIELDS",
    "ALLOWED_OPERATORS",
    "RuleValidationError",
    "explain_rule_match",
    "matches_rule",
    "normalize_rule",
    "validate_rule",
    "ApprovalContext",
    "ChannelEnvelope",
    "OAIntent",
    "RuleIntent",
    "ToolExecutionEvent",
    "ApprovalSnapshotStore",
    "OAApprovalService",
    "ApprovalStateService",
    "KeywordMatchResult",
    "match_keywords",
    "normalize_text",
]
