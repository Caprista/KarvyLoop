"""promptfoo custom provider — exposes KarvyLoop's provenance fence as a red-team target.

The injection-defense boundary we want to attack is `cognition.fence.fence_untrusted`: every
piece of untrusted content (fetched web body, MCP tool result, imported-agent text, recalled
memory) is wrapped as *data, not instructions* with bidirectional fake-tag scrubbing before it
reaches the model. This provider runs the red-team payload through that real function so probes
attack the actual boundary — **pure-local, no API key**: the deterministic assertions in
`promptfooconfig.yaml` check that fake fence-closers / system tags are neutralised and the
untrusted text is fenced.

promptfoo calls `call_api(prompt, options, context)` and expects `{"output": ...}`.
Run from the repo root (so `karvyloop` is importable): `npx promptfoo eval -c redteam/promptfooconfig.yaml`.
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def call_api(prompt, options=None, context=None):
    """Fence one untrusted payload. `source` comes from the test's vars (defaults to 'external')."""
    from karvyloop.cognition.fence import fence_untrusted

    source = "external"
    if isinstance(context, dict):
        vars_ = context.get("vars")
        if isinstance(vars_, dict) and vars_.get("source"):
            source = str(vars_["source"])
    return {"output": fence_untrusted(prompt, source=source)}
