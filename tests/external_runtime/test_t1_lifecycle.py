"""T1 受限成员生命周期(承 T0 客人)+ 两个安全件:

- T1 admission:attach tier=scoped 绑定单域;scoped 无域被拒;未知 tier 降 guest(deny-by-default)。
- T1 权限边界:只读绑定域公共记忆;跨域拒;域私有认知任何 tier 不可读/不可写(T2 绝不实现)。
- use-time hash 复验(rug-pull):派活前指纹漂移 → needs_reattach 拒派,绝不静默跑被换的 runtime。
- scoped 优雅撤销 detach:已采纳产出不级联删,未采纳供稿清理,可追溯。
- liveness 三态:online | offline | unreachable。
- 契约四项(C2 消费):ExternalCitizen.tier / detach(domain, citizen_id) / liveness(citizen_id) / list(domain=None)。
"""
from __future__ import annotations

import asyncio
import dataclasses
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from karvyloop.external_runtime import (  # noqa: E402
    BridgeResult, ExternalCitizen, ExternalCitizenRegistry, ExternalCitizenStore,
    STATUS_ACTIVE, STATUS_UNREACHABLE, TIER_GUEST, TIER_SCOPED,
    ProbeResult, normalize_tier, verify_manifest_hash, builtin_recipe,
)
from karvyloop.external_runtime.bridge import STATUS_DONE  # noqa: E402
from karvyloop.karvy.tools import (  # noqa: E402
    make_attach_external_agent_tool, make_external_agent_tool,
    make_revoke_external_agent_tool,
)


def _run(tool, inp):
    return asyncio.run(tool.call(inp, token=None, sandbox=None))


def _ok_probe(recipe, smoke=True):
    # 假探活:恒 active,manifest_hash 用真 compute(与 use-time 复验口径一致)
    from karvyloop.external_runtime import compute_manifest_hash
    h = compute_manifest_hash(bin_path=recipe.bin_path, version="",
                              argv_template=recipe.argv_template,
                              blocked_entrypoints=recipe.blocked_entrypoints)
    return ProbeResult(status=STATUS_ACTIVE, version="v1",
                       capability_card={"version": "v1"}, manifest_hash=h)


# ============ 1. tier 归一(deny-by-default)============

def test_normalize_tier_denies_unknown():
    assert normalize_tier("guest") == TIER_GUEST
    assert normalize_tier("scoped") == TIER_SCOPED
    assert normalize_tier("SCOPED") == TIER_SCOPED       # 大小写归一
    # 未知/篡改的高权字样一律降 guest(护城河纪律)
    for bad in ("t2", "private", "write", "admin", "", None, "root"):
        assert normalize_tier(bad) == TIER_GUEST


def test_citizen_post_init_normalizes_tier():
    # 塞个非法 tier → dataclass __post_init__ 降到 guest
    c = ExternalCitizen(citizen_id="x", runtime_kind="raw_text_sidecar",
                        bin_path="ext-cli", tier="t2_write_private")
    assert c.tier == TIER_GUEST


def test_from_dict_defaults_tier_guest():
    # 旧记录无 tier → 默认 guest(向后兼容)
    c = ExternalCitizen.from_dict({"citizen_id": "x", "runtime_kind": "raw_text_sidecar"})
    assert c.tier == TIER_GUEST
    # roundtrip 保 tier
    c2 = ExternalCitizen.from_dict(
        ExternalCitizen(citizen_id="y", runtime_kind="raw_text_sidecar",
                        bin_path="e", domain_id="d1", tier=TIER_SCOPED).to_dict())
    assert c2.tier == TIER_SCOPED


# ============ 2. T1 权限边界 ============

def test_scoped_reads_only_bound_domain_public():
    c = ExternalCitizen(citizen_id="m", runtime_kind="raw_text_sidecar",
                        bin_path="ext-cli", domain_id="d1", tier=TIER_SCOPED)
    assert c.is_scoped_member() is True
    assert c.can_read_domain_public("d1") is True     # 绑定域公共:读
    assert c.can_read_domain_public("d2") is False    # 跨域:拒(deny-by-default)
    # 域私有认知((域,角色)隔离):任何 tier 不可读/不可写(T2 绝不实现)
    assert c.can_read_domain_private("d1") is False
    assert c.can_write_domain_private("d1") is False


def test_guest_has_no_domain_public_read():
    c = ExternalCitizen(citizen_id="g", runtime_kind="raw_text_sidecar",
                        bin_path="ext-cli", domain_id="d1", tier=TIER_GUEST)
    assert c.is_scoped_member() is False
    assert c.can_read_domain_public("d1") is False     # 客人无域读权
    assert c.can_read_domain_private("d1") is False
    assert c.can_write_domain_private("d1") is False


def test_t2_write_private_never_true_any_tier():
    # 兜底:任何 tier(含篡改)对域私有认知的写权恒 False —— T2 无代码路径
    for t in (TIER_GUEST, TIER_SCOPED, "t2", "admin"):
        c = ExternalCitizen(citizen_id="z", runtime_kind="raw_text_sidecar",
                            bin_path="ext-cli", domain_id="d1", tier=t)
        assert c.can_write_domain_private("d1") is False
        assert c.can_read_domain_private("d1") is False


# ============ 3. T1 admission(attach tier=scoped)============

def test_attach_scoped_binds_domain():
    reg = ExternalCitizenRegistry()
    tool = make_attach_external_agent_tool(citizen_registry=reg, probe_fn=_ok_probe)
    out = _run(tool, {"citizen_id": "cc", "runtime_kind": "raw_text_sidecar",
                      "bin_path": "ext-cli", "domain_id": "d1", "tier": "scoped"})
    assert out["ok"] is True and out["tier"] == TIER_SCOPED and out["domain_id"] == "d1"
    c = reg.resolve_in("d1", "cc")
    assert c is not None and c.tier == TIER_SCOPED


def test_attach_scoped_without_domain_rejected():
    reg = ExternalCitizenRegistry()
    tool = make_attach_external_agent_tool(citizen_registry=reg, probe_fn=_ok_probe)
    out = _run(tool, {"citizen_id": "cc", "runtime_kind": "raw_text_sidecar",
                      "bin_path": "ext-cli", "tier": "scoped"})
    assert out["ok"] is False and "业务域" in out["reason"]  # scoped 必须绑域


def test_attach_default_tier_is_guest():
    reg = ExternalCitizenRegistry()
    tool = make_attach_external_agent_tool(citizen_registry=reg, probe_fn=_ok_probe)
    out = _run(tool, {"citizen_id": "cc", "runtime_kind": "raw_text_sidecar", "bin_path": "ext-cli"})
    assert out["ok"] is True and out["tier"] == TIER_GUEST


def test_attach_unknown_tier_downgraded_to_guest():
    reg = ExternalCitizenRegistry()
    tool = make_attach_external_agent_tool(citizen_registry=reg, probe_fn=_ok_probe)
    out = _run(tool, {"citizen_id": "cc", "runtime_kind": "raw_text_sidecar",
                      "bin_path": "ext-cli", "domain_id": "d1", "tier": "t2"})
    assert out["ok"] is True and out["tier"] == TIER_GUEST  # deny-by-default


# ============ 4. use-time hash 复验(rug-pull 防御)============

def test_verify_manifest_hash_ok_when_pinned_matches():
    recipe = dataclasses.replace(builtin_recipe("raw_text_sidecar"), bin_path="ext-cli")
    from karvyloop.external_runtime import compute_manifest_hash
    pin = compute_manifest_hash(bin_path=recipe.bin_path, version="",
                                argv_template=recipe.argv_template,
                                blocked_entrypoints=recipe.blocked_entrypoints)
    # bin 存在性由 _which 判;这里 bin_path 是纯名(shutil.which 可能 miss)→ 用真存在的 bin 测 drift 逻辑
    hv = verify_manifest_hash(recipe, pin)
    # 复验要么 ok(bin 找得到),要么因 bin 找不到拒 —— 但绝不因 hash 不符拒(pin 就是 recipe 算的)
    assert hv.pinned_hash == pin
    if not hv.ok:
        assert "二进制找不到" in hv.reason  # 只可能因 bin 缺,不可能因 drift


def test_verify_manifest_hash_drift_rejected():
    # attach 时 pin 一个值,之后配方漂移(argv 变)→ 复验必拒(rug-pull)
    recipe = dataclasses.replace(builtin_recipe("raw_text_sidecar"), bin_path=sys.executable)
    stale_pin = "deadbeefdeadbeef"  # 与当前配方不符的旧 pin
    hv = verify_manifest_hash(recipe, stale_pin)
    assert hv.ok is False and "漂移" in hv.reason


def test_verify_manifest_hash_empty_pin_rejected():
    recipe = dataclasses.replace(builtin_recipe("raw_text_sidecar"), bin_path=sys.executable)
    hv = verify_manifest_hash(recipe, "")
    assert hv.ok is False  # 无 pin 可复验 → deny-by-default


def test_external_agent_rejects_dispatch_on_hash_drift():
    # 派活热路径:hash 漂移 → needs_reattach,不派活(bridge 绝不被调)
    reg = ExternalCitizenRegistry()
    reg.add(ExternalCitizen(citizen_id="cc", runtime_kind="raw_text_sidecar",
                            bin_path=sys.executable, domain_id="",
                            manifest_hash="staleoldpin000000", status=STATUS_ACTIVE))
    called = {"started": False}

    class _B:
        def __init__(self, recipe):
            pass

        def start(self, task, cwd=""):
            called["started"] = True
            return BridgeResult(status=STATUS_DONE, text="7")

    tool = make_external_agent_tool(citizen_registry=reg, bridge_factory=lambda r: _B(r))
    out = _run(tool, {"citizen_id": "cc", "task": "1+2"})
    assert out["ok"] is False and out.get("needs_reattach") is True
    assert called["started"] is False  # 被换过的 runtime 绝不跑


def test_external_agent_dispatches_when_hash_matches():
    # 指纹对得上 → 正常派活
    reg = ExternalCitizenRegistry()
    recipe = dataclasses.replace(builtin_recipe("raw_text_sidecar"), bin_path=sys.executable)
    from karvyloop.external_runtime import compute_manifest_hash
    good_pin = compute_manifest_hash(bin_path=recipe.bin_path, version="",
                                     argv_template=recipe.argv_template,
                                     blocked_entrypoints=recipe.blocked_entrypoints)
    reg.add(ExternalCitizen(citizen_id="cc", runtime_kind="raw_text_sidecar",
                            bin_path=sys.executable, domain_id="",
                            manifest_hash=good_pin, status=STATUS_ACTIVE))

    class _B:
        def __init__(self, recipe):
            pass

        def start(self, task, cwd=""):
            return BridgeResult(status=STATUS_DONE, text="7")

    tool = make_external_agent_tool(citizen_registry=reg, bridge_factory=lambda r: _B(r))
    out = _run(tool, {"citizen_id": "cc", "task": "1+2"})
    assert out["ok"] is True and out["output"] == "7"


# ============ 5. scoped 优雅撤销 detach ============

def test_detach_keeps_adopted_clears_unadopted():
    reg = ExternalCitizenRegistry()
    reg.add(ExternalCitizen(citizen_id="m", runtime_kind="raw_text_sidecar",
                            bin_path="ext-cli", domain_id="d1", tier=TIER_SCOPED))
    # 一条已采纳(H2A 拍板)+ 一条未采纳供稿
    reg.record_contribution("d1", "m", seed_id="s-adopted", note="预算建议", adopted=True)
    reg.record_contribution("d1", "m", seed_id="s-draft", note="草稿", adopted=False)

    ok = reg.detach("d1", "m")
    assert ok is True
    # 成员没了
    assert reg.resolve_in("d1", "m") is None
    # 撤销可追溯:已采纳保留,未采纳清理
    trace = reg.last_detach_trace
    assert trace["found"] is True
    assert "s-adopted" in trace["kept_adopted"]
    assert "s-draft" in trace["cleared_unadopted"]


def test_detach_missing_member_returns_false():
    reg = ExternalCitizenRegistry()
    assert reg.detach("d1", "ghost") is False
    assert reg.last_detach_trace["found"] is False


def test_detach_does_not_kill_whole_domain():
    # 撤一个成员不动同域其它成员(不粗暴 kill 整个域)
    reg = ExternalCitizenRegistry()
    reg.add(ExternalCitizen(citizen_id="m1", runtime_kind="raw_text_sidecar",
                            bin_path="ext-cli", domain_id="d1", tier=TIER_SCOPED))
    reg.add(ExternalCitizen(citizen_id="m2", runtime_kind="raw_text_sidecar",
                            bin_path="ext-cli", domain_id="d1", tier=TIER_SCOPED))
    reg.detach("d1", "m1")
    assert reg.resolve_in("d1", "m1") is None
    assert reg.resolve_in("d1", "m2") is not None  # 同域另一成员还在
    assert len(reg.list(domain="d1")) == 1


def test_detach_persists(tmp_path):
    path = tmp_path / "external_citizens.json"
    reg = ExternalCitizenRegistry(store=ExternalCitizenStore(path))
    reg.add(ExternalCitizen(citizen_id="m", runtime_kind="raw_text_sidecar",
                            bin_path="ext-cli", domain_id="d1", tier=TIER_SCOPED))
    reg.detach("d1", "m")
    # 重启:被撤成员不回来
    reg2 = ExternalCitizenRegistry(store=ExternalCitizenStore(path))
    assert reg2.resolve_in("d1", "m") is None


def test_revoke_tool_end_to_end():
    reg = ExternalCitizenRegistry()
    reg.add(ExternalCitizen(citizen_id="m", runtime_kind="raw_text_sidecar",
                            bin_path="ext-cli", domain_id="d1", tier=TIER_SCOPED))
    reg.record_contribution("d1", "m", seed_id="s1", note="x", adopted=True)
    tool = make_revoke_external_agent_tool(citizen_registry=reg)
    out = _run(tool, {"citizen_id": "m", "domain_id": "d1"})
    assert out["ok"] is True and "s1" in out["kept_adopted"]
    assert reg.resolve_in("d1", "m") is None
    # 撤不存在的成员 → 诚实拒
    out2 = _run(tool, {"citizen_id": "ghost", "domain_id": "d1"})
    assert out2["ok"] is False


# ============ 6. liveness 三态 ============

def test_liveness_offline_when_missing():
    reg = ExternalCitizenRegistry()
    lv = reg.liveness("ghost")
    assert lv["status"] == "offline"


def test_liveness_online_when_probe_ok():
    def probe_ok(recipe, smoke=False):
        return ProbeResult(status=STATUS_ACTIVE)
    reg = ExternalCitizenRegistry(probe_fn=probe_ok)
    reg.add(ExternalCitizen(citizen_id="m", runtime_kind="raw_text_sidecar",
                            bin_path="ext-cli", domain_id="d1", tier=TIER_SCOPED))
    lv = reg.liveness("m")
    assert lv["status"] == "online" and lv["tier"] == TIER_SCOPED


def test_liveness_unreachable_when_probe_fails():
    def probe_bad(recipe, smoke=False):
        return ProbeResult(status=STATUS_UNREACHABLE, reason="二进制找不到")
    reg = ExternalCitizenRegistry(probe_fn=probe_bad)
    reg.add(ExternalCitizen(citizen_id="m", runtime_kind="raw_text_sidecar",
                            bin_path="ext-cli", domain_id="d1"))
    lv = reg.liveness("m")
    assert lv["status"] == "unreachable" and "找不到" in lv["reason"]


def test_liveness_probe_exception_fails_loud():
    def probe_boom(recipe, smoke=False):
        raise RuntimeError("boom")
    reg = ExternalCitizenRegistry(probe_fn=probe_boom)
    reg.add(ExternalCitizen(citizen_id="m", runtime_kind="raw_text_sidecar",
                            bin_path="ext-cli", domain_id="d1"))
    lv = reg.liveness("m")
    assert lv["status"] == "unreachable"  # 探活出错不假装 online


# ============ 7. 契约四项(C2 消费,严格命名)============

def test_contract_list_domain_filter():
    reg = ExternalCitizenRegistry()
    reg.add(ExternalCitizen(citizen_id="a", runtime_kind="rt", bin_path="x", domain_id="d1"))
    reg.add(ExternalCitizen(citizen_id="b", runtime_kind="rt", bin_path="x", domain_id="d2"))
    reg.add(ExternalCitizen(citizen_id="c", runtime_kind="rt", bin_path="x", domain_id=""))
    assert len(reg.list()) == 3               # list() 无参 = 全部
    assert len(reg.list(domain=None)) == 3     # 显式 None = 全部
    assert len(reg.list(domain="d1")) == 1     # 域过滤
    assert len(reg.list(domain="")) == 1       # 无域挂载


def test_contract_signatures_present():
    # 契约四项严格存在(C2 会消费):tier / detach(domain, citizen_id) / liveness(citizen_id) / list(domain=None)
    import inspect
    assert "tier" in {f.name for f in dataclasses.fields(ExternalCitizen)}
    reg = ExternalCitizenRegistry()
    for name in ("detach", "liveness", "list"):
        assert hasattr(reg, name) and callable(getattr(reg, name))
    # 参数名逐字
    assert list(inspect.signature(reg.detach).parameters) == ["domain", "citizen_id"]
    assert list(inspect.signature(reg.liveness).parameters) == ["citizen_id"]
    assert list(inspect.signature(reg.list).parameters) == ["domain"]
