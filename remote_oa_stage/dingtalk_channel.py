"""Compatibility shim for the canonical DingTalk channel implementation.

The runtime source of truth is :mod:`karvyloop.channels.dingtalk_channel`.
This staging directory contains deployment assets and must not carry a second
copy of the channel implementation: a copied implementation can silently
drift from the code loaded by the console. Keep this module only for older
deployment scripts or tests that still import the historical stage path.
"""
from __future__ import annotations

from karvyloop.channels import dingtalk_channel as _canonical

# Re-export all symbols, including the private helpers used by legacy tests.
__all__ = [name for name in dir(_canonical) if not name.startswith("__")]


def __getattr__(name: str):
    return getattr(_canonical, name)


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(dir(_canonical)))
