"""Shared pytest fixtures / safety nets."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def _isolate_token_ledger():
    """No test may leave a token ledger registered globally.

    `cmd_console` registers a **real-path** ledger (`~/.karvyloop/tokens.db`). If a test
    invokes it (or otherwise registers a ledger) and doesn't clean up, every later test's
    `token_ledger.record()` writes into that leaked ledger — polluting the user's real token
    panel with synthetic stub data (Hardy saw 6M fake `anthropic/claude-opus` + `p/a` rows on
    the VM panel, exactly this). Reset the global registration around every test so a leak in
    one can't bleed into the next. (We can't redirect HOME globally — the real-model e2e suite
    needs the real `~/.karvyos/config.yaml`.)
    """
    from karvyloop.llm import token_ledger
    prev = token_ledger.get_ledger()
    # 给每个测试预注册一个**内存账本**:① 测试真记账(不再静默丢)② 网关构造见已有账本 → 不会
    # 注册真 ~/.karvyloop/tokens.db(否则真模型 e2e 会把用量写进用户真账本)。测试结束还原。
    token_ledger.register_ledger(token_ledger.TokenLedger(None))
    try:
        yield
    finally:
        token_ledger.register_ledger(prev)
