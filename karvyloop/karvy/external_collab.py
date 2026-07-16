"""karvy/external_collab.py — M2:外部公民进圆桌/workflow 当**客人供稿席**(#71 §7)。

薄接缝(少脚手架多信模型):复用现有圆桌/workflow 编排 + external_agent 派活桥 + citizen
registry,**不重造协作引擎**。本模块只提供三件纯/可测的东西,由 roundtable_engine /
workflow_engine 注入调用:

1. `find_external_target(citizen_registry, domain_id, name)`:把一个圆桌参与者/ workflow
   step 目标名解析成 ExternalCitizen(复合键 (域, citizen_id);miss → None)。
2. `drive_external_contribution(...)`:把一件事派给外部公民(走 bridge),回来的产出恒
   **provenance=untrusted**,登记进 registry 供稿账本(可撤销可追溯),**永不写记忆/
   record_turn 主线**。产出只当**供稿数据**流给下游 role 参考。
3. `build_external_adopt_proposal(...)`:把一条 untrusted 供稿包成 `KIND_EXTERNAL_ADOPT`
   决策卡 —— H2A 采纳门。**只有 ACCEPT 才让这条产出穿过来源边界**(升记忆/被当结论);
   无采纳 = 只当参考数据。

三条红线钉死在这里(测试锁死):
- **恒 untrusted**:外部产出 provenance 恒 untrusted、绝不自动采纳(H2A 才采纳)。
- **不占决策席**:外部公民进圆桌是**客人供稿席**,不进 role 的 record_turn 对话主线、
  不进问责链;它的供稿**永远不能直接触发另一个 agent 行动**(A2A Contagion 防御:
  来源判定不是内容判定,要接力必经小卡编排 + H2A)。
- **确定性边界**:外部公民能进的圆桌/workflow 受它所绑域约束(scoped=T1 只在绑定域;
  guest=T0 无域深度);它够不到域私有认知(citizen.can_read_domain_private 恒 False);
  子进程内部靠 bridge 的 untrusted 收口 + egress(M1 已有)。

**clean-room 纪律**:中性词(external_runtime/bridge/公民),不点参照工程名。
"""
from __future__ import annotations

import logging
from typing import Any, Optional

logger = logging.getLogger(__name__)

# 外部产出的来源标签(跟数据走整条链;任何"升记忆/结晶/当结论"前查它 → untrusted 必过 H2A)。
PROVENANCE_UNTRUSTED = "untrusted"
SOURCE_EXTERNAL = "external_runtime"

# 圆桌/workflow 里外部公民的显示前缀(🔌 异色徽标语义:不与原生 role 混脸,#71 §7.1)。
EXTERNAL_BADGE = "🔌"


def find_external_target(citizen_registry: Any, domain_id: str, name: str) -> Optional[Any]:
    """把一个参与者/step 目标名解析成 ExternalCitizen(复合键 (域, citizen_id))。

    - citizen_registry 为 None(未接外部 runtime)→ None(零回归:纯原生 role 协作)。
    - **防御性**:registry 方法名按 C 代理正在写的契约走 getattr,缺则安全返 None(集成点报,
      别编辑 external_runtime/)。
    - 命中顺序:先复合键 (域, name) 精确;miss 再退回该 name 的任一挂载(私聊/无域 step)。
    """
    if citizen_registry is None or not name:
        return None
    # 优先复合键精确解析(resolve_in(域, id));C1 契约方法。
    resolve_in = getattr(citizen_registry, "resolve_in", None)
    if callable(resolve_in):
        try:
            hit = resolve_in(domain_id or "", name)
            if hit is not None:
                return hit
        except Exception:  # noqa: BLE001 — 解析出错不炸,当没这个外部目标
            pass
    # 退回便捷解析(resolve(id, domain_id=)):私聊/无域场景。
    resolve = getattr(citizen_registry, "resolve", None)
    if callable(resolve):
        try:
            return resolve(name, domain_id=domain_id or "")
        except TypeError:
            try:
                return resolve(name)
            except Exception:  # noqa: BLE001
                return None
        except Exception:  # noqa: BLE001
            return None
    return None


def can_join_domain(citizen: Any, domain_id: str) -> bool:
    """外部公民能不能进这个域的圆桌/workflow(确定性域约束,#71 §2.6.5③/④)。

    - **scoped(T1)**:绑定单个域深度协作 → 只能进它绑定的那个域(跨域拒,deny-by-default)。
    - **guest(T0)**:一次性客人、无域绑定深度 → 可进任意域当纯客人供稿(产出恒 untrusted、
      零留存;它本就够不到任何域私有认知)。
    未知/篡改的 tier 已被 citizen 层归一到 guest(deny-by-default),这里按 guest 处理。
    """
    if citizen is None:
        return False
    is_scoped = getattr(citizen, "is_scoped_member", None)
    if callable(is_scoped) and is_scoped():
        # T1:只在它绑定的那个域(复合键锁死)。
        bound = getattr(citizen, "domain_id", "") or ""
        return bool(bound) and bound == (domain_id or "")
    # T0 guest:纯客人,任意域可供稿(够不到私有认知,零留存)。
    return True


async def drive_external_contribution(
    citizen: Any,
    task: str,
    *,
    bridge_factory: Any,
    token_recorder: Any = None,
    citizen_registry: Any = None,
    seed_id: str = "",
    context_note: str = "",
) -> dict:
    """把一件事派给外部公民(走 bridge 子进程),回来的产出恒 untrusted 供稿。

    返回 dict:
      - 成功:{"ok": True, "speaker", "text", "provenance": "untrusted", "source",
               "origin", "citizen_id", "usage", "seed_id", "is_external": True}
      - 失败:{"ok": False, "reason", "citizen_id", "is_external": True,
               "input_required"?: True}

    **铁律**:
    - 产出恒 `provenance=untrusted`、`source=external_runtime`——绝不自动采纳。
    - **不写记忆、不写 record_turn 主线**——只返回供稿数据(调用侧当客人稿插入,不进对话主线)。
    - 登记进 registry 供稿账本(adopted=False)供 detach 可追溯 + 后续 mark_adopted。
    - input_required → 诚实上报(调用侧升 H2A),不静默等。
    - 任何异常都收成 {"ok": False}(客人席失败不拖垮整桌,#71 §3.4 fail-loud)。
    """
    citizen_id = getattr(citizen, "citizen_id", "") or ""
    speaker = f"{EXTERNAL_BADGE} {citizen_id}" if citizen_id else EXTERNAL_BADGE
    if citizen is None or not (task or "").strip():
        return {"ok": False, "reason": "缺外部公民或任务", "citizen_id": citizen_id,
                "is_external": True}
    # 状态门:非 active 不派(fail-loud,不静默挂)。
    try:
        from karvyloop.external_runtime import STATUS_ACTIVE
        status_ok = (getattr(citizen, "status", "") == STATUS_ACTIVE)
    except Exception:  # noqa: BLE001 — 取不到常量就按字面 active 判
        status_ok = (getattr(citizen, "status", "") == "active")
    if not status_ok:
        return {"ok": False, "reason": f"「{citizen_id}」当前不可达({getattr(citizen, 'status', '')})",
                "citizen_id": citizen_id, "is_external": True}
    recipe_fn = getattr(citizen, "recipe", None)
    recipe = recipe_fn() if callable(recipe_fn) else None
    if recipe is None:
        return {"ok": False, "reason": f"「{citizen_id}」没有可用配方", "citizen_id": citizen_id,
                "is_external": True}
    # use-time 指纹复验(rug-pull 防御;与 external_agent 工具同款)。
    try:
        from karvyloop.external_runtime import verify_manifest_hash
        hv = verify_manifest_hash(recipe, getattr(citizen, "manifest_hash", "") or "")
        if not getattr(hv, "ok", False):
            return {"ok": False, "needs_reattach": True, "citizen_id": citizen_id,
                    "is_external": True,
                    "reason": f"「{citizen_id}」指纹复验不过(疑似被换过),没派活"}
    except Exception:  # noqa: BLE001 — 复验能力缺失不阻断(降级为不复验,桥仍收口)
        pass
    # egress:只对 scoped(T1 绑域)且设了 allowlist 的成员传(guest/未设 → 二元网络,零回归)。
    egress_allowlist: tuple = ()
    is_scoped = getattr(citizen, "is_scoped_member", None)
    if callable(is_scoped) and is_scoped():
        egress_allowlist = tuple(getattr(citizen, "egress_allowlist", ()) or ())
    # 派活给 task 自足(外部执行体看不到我方上下文);把可给的非机密上下文当纯文本前缀。
    prompt = task if not context_note else f"{context_note}\n\n{task}"
    try:
        bridge = bridge_factory(recipe)
        if egress_allowlist:
            result = bridge.start(prompt, egress_allowlist=egress_allowlist)
        else:
            result = bridge.start(prompt)
    except Exception as e:  # noqa: BLE001 — 客人席起不来:失败不拖垮整桌
        logger.warning(f"[external_collab] 「{citizen_id}」起不来: {e}")
        return {"ok": False, "reason": f"「{citizen_id}」起不来:{type(e).__name__}",
                "citizen_id": citizen_id, "is_external": True}
    if getattr(result, "input_required", False):
        return {"ok": False, "input_required": True, "citizen_id": citizen_id,
                "is_external": True, "reason": getattr(result, "reason", "") or "要权限/澄清"}
    if not getattr(result, "ok", False):
        return {"ok": False, "citizen_id": citizen_id, "is_external": True,
                "reason": getattr(result, "reason", "") or "外部执行体失败"}
    # 记独立 token_source(§6):有 usage 才记,拿不到只落 provenance。
    usage = getattr(result, "usage", None)
    if token_recorder is not None and usage:
        try:
            src = getattr(citizen, "source_tag", None)
            token_recorder(src() if callable(src) else f"ext:{citizen_id}", usage)
        except Exception:  # noqa: BLE001 — 记账失败绝不打断供稿
            pass
    text = (getattr(result, "text", "") or "").strip()
    # 登记进供稿账本(adopted=False:未采纳临时数据,detach 会清;采纳后 mark_adopted 保留)。
    if citizen_registry is not None and seed_id:
        rec = getattr(citizen_registry, "record_contribution", None)
        if callable(rec):
            try:
                rec(getattr(citizen, "domain_id", "") or "", citizen_id,
                    seed_id=seed_id, note=(task or "")[:200], adopted=False)
            except Exception:  # noqa: BLE001 — 账本登记失败不阻断供稿
                pass
    return {
        "ok": True,
        "speaker": speaker,
        "text": text,
        # 三条红线的钉子:来源标签跟着数据走整条链。
        "provenance": PROVENANCE_UNTRUSTED,
        "source": SOURCE_EXTERNAL,
        "origin": f"external:{citizen_id}",
        "citizen_id": citizen_id,
        "seed_id": seed_id,
        "is_external": True,
        "usage": usage,
    }


def build_external_adopt_proposal(
    *,
    citizen_id: str,
    domain_id: str,
    seed_id: str,
    output: str,
    context: str = "",
    ts: float,
    conversation_id: str = "",
    strength: float = 0.5,
):
    """把一条 untrusted 外部供稿包成 `KIND_EXTERNAL_ADOPT` 决策卡(H2A 采纳门,#71 §7.3)。

    **唯一升级门**:ACCEPT 才让这条外部产出穿过来源边界(升记忆/被当结论/并入共享状态);
    REJECT/不处理 = 只当参考数据(永不自动进记忆、永不占决策席、不担责)。

    卡渲染**原始产出**(防 Lies-in-the-Loop:不透传外部 agent 自述,由我方模型摘要在别处生成)。
    幂等:proposal_id 按 (citizen_id, seed_id) 稳定派生 → 同一供稿一张卡,不刷屏。
    """
    import hashlib

    from karvyloop import i18n
    from karvyloop.karvy.atoms import Proposal

    cid = (citizen_id or "").strip() or i18n.t("proposal.external_adopt.default_citizen")
    body = (output or "").strip()
    preview = body[:500] + ("…" if len(body) > 500 else "")
    ctx = (context or "").strip()
    basis_parts = [
        i18n.t("proposal.external_adopt.basis_head", badge=EXTERNAL_BADGE, cid=cid),
    ]
    if ctx:
        basis_parts.append(i18n.t("proposal.external_adopt.basis_ctx", ctx=ctx))
    basis_parts.append(
        i18n.t("proposal.external_adopt.basis_tail",
               preview=(preview or i18n.t("proposal.external_adopt.empty"))))
    basis = "  ".join(basis_parts)
    stable = f"{cid}:{seed_id}"
    pid = "external_adopt-" + hashlib.sha1(stable.encode("utf-8")).hexdigest()[:8]
    return Proposal(
        summary=i18n.t("proposal.external_adopt.summary", badge=EXTERNAL_BADGE, cid=cid),
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=strength,
        evidence_refs=(),
        habit_id=0,
        model_ref="",
        ts=ts,
        kind="external_adopt",
        payload={
            "citizen_id": cid,
            "domain_id": domain_id or "",
            "seed_id": seed_id or "",
            "output": body[:4000],
            "context": ctx[:400],
            "conversation_id": conversation_id or "",
            # 恒 untrusted:任何消费侧查这个标签,采纳前一律当不可信。
            "provenance": PROVENANCE_UNTRUSTED,
            "source": SOURCE_EXTERNAL,
        },
        proposal_id=pid,
        basis=basis,
    )


__all__ = [
    "PROVENANCE_UNTRUSTED",
    "SOURCE_EXTERNAL",
    "EXTERNAL_BADGE",
    "find_external_target",
    "can_join_domain",
    "drive_external_contribution",
    "build_external_adopt_proposal",
]
