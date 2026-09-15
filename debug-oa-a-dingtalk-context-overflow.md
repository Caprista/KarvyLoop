# Debug Session: OA_A DingTalk Context Overflow

Status: [DIAGNOSED]
Session ID: `oa-a-dingtalk-context-overflow`

## Symptom

The DingTalk session identified by `cidH4j+ceKfc+I5Jn0LvGgDIKsztgWeA7SHV6SrtOMb+AM=` under OA_A frequently triggers context-length overflow.

## Root Cause

The context-governance layer and the gateway hard ceiling use different token estimators:

- `karvyloop.context.budget` estimated all text as roughly four characters per token and treated non-text tool blocks as a fixed eight tokens.
- `karvyloop.gateway.client` estimates CJK as roughly one token per character and includes tool block `content` and `input`.

Large Chinese OA workflow details were therefore severely underestimated by `govern()`. Auto-compaction did not run, then the gateway's stricter estimator rejected the assembled request with `ContextCeilingError`.

## Evidence Log

- Remote host: `enjoy@10.10.95.182` (macOS).
- Target conversation file: `/Users/enjoy/.karvyloop/conversations/l0/channel__dingtalk_cidH4j+ceKfc+I5Jn0LvGgDIKsztgWeA7SHV6SrtOMb+AM=/ea3f0372a59e4aad.jsonl`.
- Conversation size: 115,068 bytes, 87 physical JSONL records, about 69 logical turns.
- The persisted conversation is bounded before entering a task: recent turns plus a 2,000 estimated-token history prefix. It is not sent wholesale.
- Context-limit responses occurred for `2，3，5，6，7，8批准`, `1，2，4同意`, and a later `？` on 2026-09-14.
- The failing `1，2，4同意` run called the OA pending-list tool and three OA workflow-detail tools, then terminated as `context_limit` after four successful tool calls.
- The later `？` repeated the same pending-list plus three-detail query pattern and failed identically.
- OA_A's DingTalk channel is configured with role `OA_A`. The observed failing runs used `custom/gpt-5.6-sol`, configured with a 128,000-token context window and 4,096 output tokens.
- Provider token ledger rows immediately before the failure were only 4,315 and 7,209 input tokens. No provider call was recorded for the final rejected turn, consistent with the local gateway hard ceiling rejecting it before dispatch.
- Remote source matches local source: governance uses `len(text)//4` and fixed eight-token non-text blocks, while the gateway uses CJK-aware counting and includes tool block payloads.
- Token history also contains 100k–177k input requests, confirming that individual agent tasks can accumulate very large tool/message contexts independently of the persisted DingTalk history.
- `/new` messages remained in the same conversation ID. DingTalk currently does not appear to reset this channel conversation when `/new` is sent; this is a separate session-control defect, not the direct cause of these failures.

## Hypothesis Results

1. Whole persisted conversation sent without truncation: rejected.
2. Persisted conversation itself abnormally large enough to explain the failure: rejected as primary cause.
3. Model-window mismatch: partially relevant; the failing run used the 128k model, but its configured window was resolved correctly.
4. OA tool results accumulating in one task: confirmed.
5. Token undercount prevents timely compaction: confirmed as the direct code defect.
6. Infinite loop: not proven; traces show repeated planning/query patterns and user retries rather than an executor-level infinite loop.

## Change Log

- Updated local `karvyloop/context/budget.py` to use CJK-aware text estimation.
- Updated message estimation to count tool block `text`, `content`, and `input` payloads.
- Updated clipping to respect the CJK-aware token budget.
- Added regression coverage for Chinese OA-like tool results and 128k-window auto-compaction.
- Relevant local tests: 36 passed.
- Remote application files were not modified, and the remote service was not restarted.
