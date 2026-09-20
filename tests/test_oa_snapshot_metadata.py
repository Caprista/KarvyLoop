import importlib.util
from pathlib import Path

import pytest


pytest.importorskip("mcp")
path = Path(__file__).parents[1] / "remote_oa_stage" / "oa_rest_adapter.py"
spec = importlib.util.spec_from_file_location("oa_snapshot_metadata", path)
assert spec and spec.loader
oa = importlib.util.module_from_spec(spec)
spec.loader.exec_module(oa)


def test_todo_list_adds_snapshot_and_index_map(monkeypatch):
    monkeypatch.setattr(oa, "invoke", lambda *_args, **_kwargs: {
        "data": {"result": {"code": "SUCCESS", "data": [
            {"requestId": "r1"}, {"requestId": "r2"},
        ]}}
    })
    result = oa.get_doing_workflow_request_list()
    assert len(result["snapshot_id"]) == 32
    assert result["index_map"] == {"1": "r1", "2": "r2"}
