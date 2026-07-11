"""external_runtime/citizen — 频道公民身份(ExternalCitizen)+ 注册表(持久化)。

**本体论对齐(#71 §2.1)**:外部 runtime 既不是 role、也不是 atom、也不是小卡,是**第四类实体**——
一个 opaque、归属外部主人的执行体。它借用 role 的**寻址壳**(复合键 (域, citizen_id)、频道成员显示)
和小卡的**中间人语义**,但**不获得 role 的本体论地位**(无记忆、无结晶、无决策席)。

- 身份 = 一张能力卡(探测生成,非灵魂 7 文件),不合成假 soul。
- 记忆 = 无(不联邦记忆)。结晶过不了边界(A2A opacity 天然护城河)。
- 寻址 = (域, citizen_id) 复合键,但解析到的是**桥**不是 role。
- 决策席 = 无,只能供稿。

manifest_hash = hash-pin(bin.path + version + argv_template + blocked_entrypoints),
版本/命令模板变更即重审(rug-pull 防御)。
"""
from __future__ import annotations

import dataclasses
import hashlib
import hmac
import json
import secrets
import time
from typing import Optional

from .recipe import DriveRecipe, builtin_recipe

# 公民地址里固定的 role 段(#71 §3.3:to=Address(域, role="external", agent_id=citizen_id))
EXTERNAL_ROLE = "external"

STATUS_ACTIVE = "active"
STATUS_UNREACHABLE = "unreachable"
STATUS_BLOCKED = "blocked"
STATUS_RETIRED = "retired"
STATUS_NEEDS_REATTACH = "needs_reattach"
# pending:建壳但外部 runtime 还没认领回连(等待接入)。认领成功后翻成 active。
STATUS_PENDING = "pending"

# ---- 成员等级(受限成员生命周期,承 T0 客人)----
# T0 = guest(现状:opaque 供稿人,无域绑定深度,私聊/无域派活)。
# T1 = scoped(受限成员:绑定单个业务域深度协作,但 deny-by-default —— 只读该域**公共**记忆、
#      可**逆向供稿**(写的都可撤),域私有认知((域,角色)隔离)**完全不可读不可写**)。
# T2(写域私有认知)**绝不实现、不留代码路径** —— 它锁到未来 H2A 采纳门;现在建 = 给护城河
#      开无守卫写口。任何 tier != {guest, scoped} 一律当最不信任(guest)处理(deny-by-default)。
TIER_GUEST = "guest"      # T0:客人(现状)
TIER_SCOPED = "scoped"    # T1:受限成员(绑定单域,deny-by-default,可逆供稿)
_KNOWN_TIERS = frozenset({TIER_GUEST, TIER_SCOPED})


def normalize_tier(tier: str) -> str:
    """把任意 tier 值归一到已知等级;未知/空 → guest(deny-by-default,不确定就当最不信任)。

    **护城河纪律**:除 guest/scoped 外一律拒(尤其绝不认 "t2"/"private"/"write" 之类字样),
    防有人塞个高权 tier 字符串就拿到域私有认知写口。
    """
    t = (tier or "").strip().lower()
    return t if t in _KNOWN_TIERS else TIER_GUEST


@dataclasses.dataclass(frozen=True)
class ExternalCitizen:
    """一个已接入的外部 runtime 频道公民(#71 §2.2 字段最小集)。"""
    citizen_id: str                        # 频道内寻址花名,如 "cc" / "helper"
    runtime_kind: str                      # 配方类型(generic_cli / single_json_cli / raw_text_sidecar)
    bin_path: str                          # 探测出的二进制真路径
    domain_id: str = ""                    # 挂载域(空=私聊/无域;复合键 (域, citizen_id))
    capability_card: dict = dataclasses.field(default_factory=dict)  # 探测事实,非假 soul
    token_source: str = ""                 # 独立记账口,如 "ext:cc"(空则从 citizen_id 派生)
    manifest_hash: str = ""                # 能力/命令 hash-pin(rug-pull 防御)
    created_by: str = "user"
    status: str = STATUS_ACTIVE
    tier: str = TIER_GUEST                  # 成员等级(guest=T0 客人 / scoped=T1 受限成员;deny-by-default)
    #: 按域名的 egress(出网)白名单(默认空=保持二元网络行为;非空=只放行这些 host,其余沙箱层
    #: 确定性拒/fail-closed)。**只对 scoped(T1 绑域)成员生效**——派活时经 bridge.start(egress_allowlist=)
    #: 构造 net_allowlist 非空的 CapabilityToken,由沙箱后端 runner 对 opaque 外部子进程做域名级 egress
    #: 强制。guest 成员不设(默认空=零回归)。attach scoped 时可设。
    egress_allowlist: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        # egress_allowlist 归一到 tuple[str,...](from_dict 可能给 list;去空白项)。
        raw = self.egress_allowlist
        norm_eg = tuple(str(x).strip() for x in raw if str(x).strip()) if raw else ()
        if norm_eg != self.egress_allowlist:
            object.__setattr__(self, "egress_allowlist", norm_eg)
        # tier 归一(deny-by-default):未知/篡改的 tier 值一律降到 guest。
        # frozen dataclass → object.__setattr__ 绕过不可变(仅归一,不放宽)。
        norm = normalize_tier(self.tier)
        if norm != self.tier:
            object.__setattr__(self, "tier", norm)

    def is_scoped_member(self) -> bool:
        """T1 受限成员(绑定单域深度协作,deny-by-default)。"""
        return self.tier == TIER_SCOPED

    def can_read_domain_public(self, domain_id: str) -> bool:
        """T1 只读**它绑定的那个域**的公共记忆;跨域一律拒(deny-by-default)。guest 无域读权。"""
        return (self.tier == TIER_SCOPED
                and bool(self.domain_id)
                and self.domain_id == (domain_id or ""))

    def can_read_domain_private(self, domain_id: str) -> bool:
        """域私有认知((域,角色)隔离):**任何 tier 都不可读**。T2 绝不实现。"""
        return False

    def can_write_domain_private(self, domain_id: str) -> bool:
        """写域私有认知:**任何 tier 都不可写**。T2 锁到未来 H2A 采纳门,现在恒 False。"""
        return False

    def source_tag(self) -> str:
        """token 账本的独立 source(默认 ext:<citizen_id>)。"""
        return self.token_source or f"ext:{self.citizen_id}"

    def recipe(self) -> Optional[DriveRecipe]:
        """取该公民的驱动配方(内置库按 runtime_kind 取,bin_path 用探测值覆盖)。"""
        base = builtin_recipe(self.runtime_kind)
        if base is None:
            return None
        return dataclasses.replace(base, bin_path=self.bin_path or base.bin_path)

    def to_dict(self) -> dict:
        return {
            "citizen_id": self.citizen_id, "runtime_kind": self.runtime_kind,
            "bin_path": self.bin_path, "domain_id": self.domain_id,
            "capability_card": self.capability_card, "token_source": self.token_source,
            "manifest_hash": self.manifest_hash, "created_by": self.created_by,
            "status": self.status, "tier": self.tier,
            # egress 白名单落 list(JSON 无 tuple);from_dict 读回归一成 tuple。空=不占位也可,显式落审计更清。
            "egress_allowlist": list(self.egress_allowlist),
        }

    @staticmethod
    def from_dict(d: dict) -> "ExternalCitizen":
        d = d or {}
        return ExternalCitizen(
            citizen_id=str(d.get("citizen_id") or ""),
            runtime_kind=str(d.get("runtime_kind") or ""),
            bin_path=str(d.get("bin_path") or ""),
            domain_id=str(d.get("domain_id") or ""),
            capability_card=dict(d.get("capability_card") or {}),
            token_source=str(d.get("token_source") or ""),
            manifest_hash=str(d.get("manifest_hash") or ""),
            created_by=str(d.get("created_by") or "user"),
            status=str(d.get("status") or STATUS_ACTIVE),
            # 旧记录无 tier → 默认 guest(向后兼容,deny-by-default);normalize 在 __post_init__ 兜。
            tier=str(d.get("tier") or TIER_GUEST),
            # 旧记录无 egress_allowlist → 空(向后兼容,二元网络);__post_init__ 归一成 tuple。
            egress_allowlist=tuple(d.get("egress_allowlist") or ()),
        )


def compute_manifest_hash(*, bin_path: str, version: str,
                          argv_template, blocked_entrypoints) -> str:
    """rug-pull 防御:bin/version/命令模板/黑名单变更即 hash 变 → re-probe 时标 needs_reattach。"""
    h = hashlib.sha256()
    h.update((bin_path or "").encode("utf-8"))
    h.update((version or "").encode("utf-8"))
    h.update(json.dumps(list(argv_template or ()), ensure_ascii=False).encode("utf-8"))
    h.update(json.dumps(sorted(blocked_entrypoints or ()), ensure_ascii=False).encode("utf-8"))
    return h.hexdigest()[:16]


# ---- 认领码握手(反向接入:GitHub-runner-注册那种)----
# 模型:建壳(pending 公民)+ 发一把一次性、带过期的认领秘钥 → 外部 runtime 拿秘钥连回
# claim 端点 → 校验(一次性/未过期/匹配某 pending 壳)→ 激活该壳成正式公民 → 秘钥立即作废。
#
# **秘钥纪律(和 API key 同):明文秘钥绝不落盘、绝不进日志。** 只落**盐 + HMAC 摘要**;校验时
# 用 hmac.compare_digest 常量时间比对(防时序侧信道)。明文秘钥只在建壳那一刻返回一次(前端展示),
# 之后系统再也拿不到明文——就算注册表文件泄了,也反推不出秘钥。

# 认领秘钥默认有效期(秒)=10 分钟(短窗:接入是即时动作,过期即作废逼重发)。
CLAIM_TICKET_TTL_S = 600
# 秘钥明文长度(token_urlsafe 的字节数;32 字节 ≈ 43 字符 base64url,足够抗暴力)。
_CLAIM_SECRET_BYTES = 32


def _hash_claim_secret(secret: str, salt: str) -> str:
    """盐 + HMAC-SHA256 摘要(明文秘钥永不落盘;校验只比对摘要)。"""
    return hmac.new(salt.encode("utf-8"), (secret or "").encode("utf-8"),
                    hashlib.sha256).hexdigest()


@dataclasses.dataclass(frozen=True)
class ClaimTicket:
    """一把绑定到某个 pending 壳的一次性认领秘钥(只存摘要,不存明文)。

    - `ticket_id`:秘钥的公开标识(明文秘钥 = "<ticket_id>.<secret>",外部 runtime 回连时带整串;
      校验时按 ticket_id 找到本票、再比对 secret 摘要)。用公开 id 定位、密文比对 secret,避免遍历。
    - `secret_hash` / `salt`:秘钥的 HMAC 摘要 + 盐(明文绝不落)。
    - `citizen_id`/`domain_id`:这把秘钥绑定的那个 pending 壳(复合键)。别的壳不能用这把认领。
    - `expires_at`:过期墙(epoch 秒)。过期即废。
    - `used_at`:一次性 —— 认领成功即写 used_at,再来(重放)一律拒。
    """
    ticket_id: str
    secret_hash: str
    salt: str
    citizen_id: str
    domain_id: str = ""
    issued_at: float = 0.0
    expires_at: float = 0.0
    used_at: float = 0.0

    def is_expired(self, now: Optional[float] = None) -> bool:
        return (now if now is not None else time.time()) >= self.expires_at

    def is_used(self) -> bool:
        return self.used_at > 0.0

    def verify(self, secret: str, *, now: Optional[float] = None) -> bool:
        """常量时间比对 secret 摘要(未过期、未用过才算通过)。"""
        if self.is_used() or self.is_expired(now):
            return False
        cand = _hash_claim_secret(secret or "", self.salt)
        return hmac.compare_digest(cand, self.secret_hash)

    def to_dict(self) -> dict:
        return {
            "ticket_id": self.ticket_id, "secret_hash": self.secret_hash, "salt": self.salt,
            "citizen_id": self.citizen_id, "domain_id": self.domain_id,
            "issued_at": self.issued_at, "expires_at": self.expires_at, "used_at": self.used_at,
        }

    @staticmethod
    def from_dict(d: dict) -> "ClaimTicket":
        d = d or {}
        return ClaimTicket(
            ticket_id=str(d.get("ticket_id") or ""),
            secret_hash=str(d.get("secret_hash") or ""),
            salt=str(d.get("salt") or ""),
            citizen_id=str(d.get("citizen_id") or ""),
            domain_id=str(d.get("domain_id") or ""),
            issued_at=float(d.get("issued_at") or 0.0),
            expires_at=float(d.get("expires_at") or 0.0),
            used_at=float(d.get("used_at") or 0.0),
        )


def mint_claim_ticket(citizen_id: str, domain_id: str = "", *,
                      ttl_s: int = CLAIM_TICKET_TTL_S,
                      now: Optional[float] = None) -> tuple[ClaimTicket, str]:
    """发一把新认领秘钥,返回 (ticket, 明文完整秘钥)。**明文只此一次返回,之后系统不再持有。**

    完整秘钥形态 = "<ticket_id>.<secret>"(前段公开定位、后段密文比对)。
    """
    now = now if now is not None else time.time()
    ticket_id = secrets.token_urlsafe(9)          # 短公开 id
    secret = secrets.token_urlsafe(_CLAIM_SECRET_BYTES)  # 高熵密文段
    salt = secrets.token_urlsafe(9)
    ticket = ClaimTicket(
        ticket_id=ticket_id, secret_hash=_hash_claim_secret(secret, salt), salt=salt,
        citizen_id=citizen_id or "", domain_id=domain_id or "",
        issued_at=now, expires_at=now + max(1, int(ttl_s)), used_at=0.0)
    full_secret = f"{ticket_id}.{secret}"
    return ticket, full_secret


def split_claim_secret(full_secret: str) -> tuple[str, str]:
    """把外部 runtime 回连带的完整秘钥拆成 (ticket_id, secret);形态不对 → ("", "")。"""
    s = (full_secret or "").strip()
    if "." not in s:
        return "", ""
    tid, _, sec = s.partition(".")
    return tid.strip(), sec.strip()


class ExternalCitizenRegistry:
    """已接入公民注册表(持久化;用户数据默认存盘)。

    复合键寻址:`resolve_in(domain_id, citizen_id)` —— 同一花名跨域是不同挂载点。
    `resolve(citizen_id)` 是调用侧带当前域的便捷入口(域可空=私聊/无域)。
    """

    def __init__(self, *, store=None, probe_fn=None, ticket_store=None) -> None:
        # 键 = (domain_id, citizen_id);同花名跨域独立
        self._by_key: dict[tuple[str, str], ExternalCitizen] = {}
        # 供稿账本(可撤销供稿的可追溯性):key=(domain_id, citizen_id) → list[dict]
        # 每条 = {seed_id, note, adopted}。adopted=True 的产出已是用户数据(采纳 = H2A 拍板),
        # detach 不级联删;adopted=False 的未采纳供稿由 detach 清理。**纯内存**(不落盘):
        # 采纳后的产出去向由消费侧(记忆/结晶)持久化,这里只做撤销可追溯的登记面。
        self._contributions: dict[tuple[str, str], list[dict]] = {}
        self._store = store
        # 认领秘钥台账:ticket_id → ClaimTicket(只存摘要,明文绝不落)。持久化(重启后 pending 壳仍可认领)。
        self._tickets: dict[str, ClaimTicket] = {}
        self._ticket_store = ticket_store
        # 探活函数注入(liveness 复用探活;默认懒加载真 probe)。测试可注入假 probe。
        self._probe_fn = probe_fn
        self.persist_error: str = ""
        self.ticket_persist_error: str = ""
        self.last_detach_trace: dict = {}   # 最近一次 detach 的撤销可追溯记录
        if ticket_store is not None:
            try:
                for td in (ticket_store.load_all() or []):
                    tk = ClaimTicket.from_dict(td)
                    if tk.ticket_id:
                        self._tickets[tk.ticket_id] = tk
                # 启动时反收已过期票(台账跨重启不无限增长;过期票留着也会被 verify 的 is_expired 拦)。
                # 持久票携带的是真实时钟 expires_at(生产 mint 用真 time),startup 用真时钟反收安全。
                if self._reap_tickets_at_load():
                    self._persist_tickets()
            except Exception as e:  # noqa: BLE001 — 票据加载失败不炸(空台账起步)
                self.ticket_persist_error = f"load: {type(e).__name__}: {e}"
        if store is not None:
            try:
                for d in (store.load_all() or []):
                    c = ExternalCitizen.from_dict(d)
                    if c.citizen_id:
                        self._by_key[(c.domain_id, c.citizen_id)] = c
            except Exception as e:  # noqa: BLE001 — 加载失败不炸,空表起步
                self.persist_error = f"load: {type(e).__name__}: {e}"

    def add(self, citizen: ExternalCitizen) -> bool:
        """注册/覆盖一个公民,落盘。返回是否持久化成功(内存态总是写上)。"""
        self._by_key[(citizen.domain_id, citizen.citizen_id)] = citizen
        return self._persist()

    def resolve_in(self, domain_id: str, citizen_id: str) -> Optional[ExternalCitizen]:
        """复合键精确解析((域, citizen_id))。"""
        return self._by_key.get((domain_id or "", citizen_id or ""))

    def resolve(self, citizen_id: str, *, domain_id: str = "") -> Optional[ExternalCitizen]:
        """调用侧便捷解析:先按 (给定域, id) 精确查;miss 再退回该 id 的任一挂载(私聊无域场景)。"""
        hit = self._by_key.get((domain_id or "", citizen_id or ""))
        if hit is not None:
            return hit
        # 私聊/无域:同 citizen_id 的任一挂载(优先无域挂载)
        cands = [c for (d, cid), c in self._by_key.items() if cid == citizen_id]
        if not cands:
            return None
        cands.sort(key=lambda c: (c.domain_id != "", c.domain_id))
        return cands[0]

    def remove(self, domain_id: str, citizen_id: str) -> bool:
        """软删一个公民(retired)。返回是否有此公民。"""
        key = (domain_id or "", citizen_id or "")
        if key not in self._by_key:
            return False
        self._by_key.pop(key, None)
        self._persist()
        return True

    def list_all(self) -> list[ExternalCitizen]:
        return list(self._by_key.values())

    def list_active(self) -> list[ExternalCitizen]:
        return [c for c in self._by_key.values() if c.status == STATUS_ACTIVE]

    def list(self, domain=None) -> list[ExternalCitizen]:
        """契约入口(C2 消费):domain=None → 全部;给了域 → 只列该域挂载的公民。

        `list(domain="d1")` = 该业务域里所有成员(供 UI 域成员面/detach 选择)。
        私聊/无域挂载(domain_id=="")用 domain="" 取。
        """
        if domain is None:
            return list(self._by_key.values())
        did = domain or ""
        return [c for (d, _cid), c in self._by_key.items() if d == did]

    # ---- 认领码握手:建壳 + 发码 → 外部 runtime 回连认领 → 激活 ----

    def create_pending(self, citizen_id: str, *, domain_id: str = "",
                       runtime_kind: str = "", tier: str = TIER_GUEST,
                       ttl_s: int = CLAIM_TICKET_TTL_S,
                       now: Optional[float] = None) -> tuple[Optional[ExternalCitizen], str, str]:
        """建一个 pending 壳 + 发一把一次性认领秘钥。

        返回 (pending_citizen, 明文完整秘钥, error)。error 非空 = 失败(壳/秘钥都不发)。
        **明文秘钥只此一次返回**(前端展示给用户复制去 runtime 里跑),系统之后只留摘要。

        - citizen_id 复合键 (域, id) 已存在 → 拒(别覆盖在线公民;认领是"加新的")。
        - tier 归一(deny-by-default):未知一律 guest。pending 壳 status=pending,认领成功才 active。
        """
        cid = (citizen_id or "").strip()
        did = (domain_id or "").strip()
        if not cid:
            return None, "", "需要 citizen_id(给外部 runtime 起个花名)"
        if (did, cid) in self._by_key:
            return None, "", f"「{cid}」已存在(复合键 (域={did or '—'}, {cid}));换个花名"
        pending = ExternalCitizen(
            citizen_id=cid, runtime_kind=(runtime_kind or "").strip(), bin_path="",
            domain_id=did, capability_card={}, token_source=f"ext:{cid}",
            created_by="user", status=STATUS_PENDING, tier=normalize_tier(tier))
        ticket, full_secret = mint_claim_ticket(cid, did, ttl_s=ttl_s, now=now)
        self._by_key[(did, cid)] = pending
        self._tickets[ticket.ticket_id] = ticket
        self._persist()
        self._persist_tickets()
        return pending, full_secret, ""

    def claim(self, full_secret: str, *, reported: Optional[dict] = None,
              now: Optional[float] = None) -> dict:
        """外部 runtime 拿秘钥回连认领:校验(一次性/未过期/匹配某 pending 壳)→ 激活壳 → 秘钥作废。

        `reported` = 外部 runtime 自报的身份/能力(**untrusted 数据**:登记但不当指令、不据此提权)。
          认可的字段:runtime_kind / bin_path / version / capabilities(其余忽略)。tier/domain 由**建壳侧**
          定,外部自报一律不改(防自提权)。
        返回 {ok, reason?, citizen_id?, domain_id?, status?}。秘钥错/过期/重放/壳不在 → ok=False(fail-loud)。
        """
        now = now if now is not None else time.time()
        reported = reported or {}
        tid, sec = split_claim_secret(full_secret)
        # 统一的拒绝话术:不区分"没这个 ticket"和"secret 错"(别给暴力/枚举侧信道)。
        deny = {"ok": False, "reason": "认领秘钥无效、已过期或已使用"}
        if not tid or not sec:
            return deny
        ticket = self._tickets.get(tid)
        if ticket is None:
            return deny
        if not ticket.verify(sec, now=now):
            # 过期/已用/摘要不匹配 —— 一律统一拒(fail-loud,不透露是哪种)。
            return deny
        key = (ticket.domain_id or "", ticket.citizen_id or "")
        shell = self._by_key.get(key)
        if shell is None or shell.status != STATUS_PENDING:
            # 壳被删了 or 已不是 pending(已认领过/被改)→ 秘钥同时作废(防悬空票)。
            self._invalidate_ticket(tid, now)
            return {"ok": False, "reason": "对应的待接入壳不存在或已激活"}
        # 激活:外部自报的 runtime_kind/bin/能力登记(untrusted);tier/domain 保持建壳侧所定不变。
        rk = str(reported.get("runtime_kind") or shell.runtime_kind or "").strip()
        bin_path = str(reported.get("bin_path") or "").strip()
        card = dict(shell.capability_card or {})
        rep_caps = reported.get("capabilities")
        # capability_card 里明确标 source=external_self_report(消费侧一眼知这是 untrusted 自报,不是我们探的)。
        card.update({
            "version": str(reported.get("version") or ""),
            "self_reported": True,
            "reported_capabilities": list(rep_caps) if isinstance(rep_caps, (list, tuple)) else [],
        })
        activated = dataclasses.replace(
            shell, runtime_kind=rk, bin_path=bin_path,
            capability_card=card, status=STATUS_ACTIVE)
        self._by_key[key] = activated
        # **顺序纪律(防半写锁死)**:先把激活落盘,成功了才作废秘钥。否则一旦
        # 秘钥先作废、公民却没落盘,重启后壳退回 pending 而秘钥已死 → 用户永久锁死。
        if not self._persist():
            # 公民落盘失败:回滚内存激活、**不作废秘钥**(让用户能重试同一把码),fail-loud 报错。
            self._by_key[key] = shell
            return {"ok": False,
                    "reason": f"认领落盘失败(未激活,可重试):{self.persist_error}"}
        # 激活已持久 → 秘钥立即作废(一次性:标 used_at + 落盘;重放这把再来一律拒)。
        self._invalidate_ticket(tid, now)
        return {"ok": True, "citizen_id": activated.citizen_id,
                "domain_id": activated.domain_id, "status": STATUS_ACTIVE,
                "tier": activated.tier}

    def _invalidate_ticket(self, ticket_id: str, now: float) -> None:
        """把一把秘钥标为已用(一次性回收);落盘。"""
        tk = self._tickets.get(ticket_id)
        if tk is not None and not tk.is_used():
            self._tickets[ticket_id] = dataclasses.replace(tk, used_at=now)
            self._persist_tickets()

    def cancel_pending(self, domain_id: str, citizen_id: str) -> bool:
        """撤掉一个还没认领的 pending 壳(用户取消等待):删壳 + 作废它的所有未用秘钥。返回是否有此壳。"""
        key = (domain_id or "", citizen_id or "")
        c = self._by_key.get(key)
        if c is None or c.status != STATUS_PENDING:
            return False
        self._by_key.pop(key, None)
        now = time.time()
        for tid, tk in list(self._tickets.items()):
            if (tk.domain_id or "") == (domain_id or "") and tk.citizen_id == citizen_id and not tk.is_used():
                self._tickets[tid] = dataclasses.replace(tk, used_at=now)
        self._persist()
        self._persist_tickets()
        return True

    def pending_ticket_for(self, domain_id: str, citizen_id: str,
                           now: Optional[float] = None) -> Optional[ClaimTicket]:
        """取某 pending 壳当前**仍有效**(未用未过期)的认领票(供前端显示过期时间);无 → None。"""
        now = now if now is not None else time.time()
        for tk in self._tickets.values():
            if ((tk.domain_id or "") == (domain_id or "") and tk.citizen_id == citizen_id
                    and not tk.is_used() and not tk.is_expired(now)):
                return tk
        return None

    def _reap_tickets_at_load(self, now: Optional[float] = None) -> bool:
        """启动时回收**已过期**的票(台账跨重启不无限增长)。返回是否反收了任何票。

        安全:只反收已过期的 —— 过期票就算被重放也会被 verify 的 is_expired 拦(不靠留着它做拒);
        未过期的票(含刚 used_at 的一次性票)必须留到过期,否则一次性/重放检测会因票消失而失效。
        **只在 load 时用真实时钟反收**(持久票携带真实 expires_at);不在每次写时跑,免得干扰注入合成时钟的测试。
        """
        now = now if now is not None else time.time()
        expired = [tid for tid, tk in self._tickets.items() if tk.is_expired(now)]
        for tid in expired:
            self._tickets.pop(tid, None)
        return bool(expired)

    def _persist_tickets(self) -> bool:
        if self._ticket_store is None:
            return True
        try:
            self._ticket_store.save_all([t.to_dict() for t in self._tickets.values()])
            self.ticket_persist_error = ""
            return True
        except Exception as e:  # noqa: BLE001
            self.ticket_persist_error = f"{type(e).__name__}: {e}"
            return False

    # ---- 供稿账本(可撤销供稿的可追溯性)----

    def record_contribution(self, domain_id: str, citizen_id: str, *,
                            seed_id: str, note: str = "", adopted: bool = False) -> None:
        """登记一条外部公民的供稿(供 detach 追溯)。

        - adopted=False:未采纳供稿 —— detach 时清理。
        - adopted=True :已被 H2A 采纳,已是用户数据 —— detach **不**级联删。
        纯内存登记(不落盘):采纳后的真产出由消费侧持久化,这里只记撤销可追溯的映射。
        """
        key = (domain_id or "", citizen_id or "")
        self._contributions.setdefault(key, []).append(
            {"seed_id": str(seed_id or ""), "note": str(note or "")[:200], "adopted": bool(adopted)})

    def mark_adopted(self, domain_id: str, citizen_id: str, seed_id: str) -> bool:
        """把某条供稿标为已采纳(H2A 拍板后调):之后 detach 不再清它。返回是否命中。"""
        key = (domain_id or "", citizen_id or "")
        hit = False
        for c in self._contributions.get(key, []):
            if c.get("seed_id") == str(seed_id or ""):
                c["adopted"] = True
                hit = True
        return hit

    def detach(self, domain, citizen_id) -> bool:
        """scoped 优雅撤销:撤一个成员**不 kill 整个域**(§T1 安全件)。

        - **已采纳产出不级联删**:adopted=True 的供稿已是用户数据,保留(retired 成员的贡献不因人走而消失)。
        - **未采纳供稿清理**:adopted=False 的供稿丢弃(它没进用户数据,撤人即撤稿)。
        - **可追溯**:返回前把该成员 seed 过哪些认知记进 last_detach_trace(哪些保留/哪些清)。
        不做粗暴 kill:成员从注册表移除、落盘,但采纳过的内容原地留存。

        返回是否有此成员(没有 → False)。
        """
        key = (domain or "", citizen_id or "")
        citizen = self._by_key.get(key)
        if citizen is None:
            self.last_detach_trace = {"citizen_id": citizen_id or "", "domain_id": domain or "",
                                      "found": False, "kept_adopted": [], "cleared_unadopted": []}
            return False
        contribs = self._contributions.get(key, [])
        kept = [c["seed_id"] for c in contribs if c.get("adopted")]        # 已采纳:留
        cleared = [c["seed_id"] for c in contribs if not c.get("adopted")]  # 未采纳:清
        # 只清未采纳供稿的登记;已采纳的保留(供稿去向已是用户数据,可追溯它 seed 过什么)
        remaining = [c for c in contribs if c.get("adopted")]
        if remaining:
            self._contributions[key] = remaining
        else:
            self._contributions.pop(key, None)
        # 撤成员(优雅:移出注册表 + 落盘;不触碰已采纳产出)
        self._by_key.pop(key, None)
        self._persist()
        self.last_detach_trace = {
            "citizen_id": citizen_id or "", "domain_id": domain or "", "found": True,
            "tier": citizen.tier, "kept_adopted": kept, "cleared_unadopted": cleared}
        return True

    def liveness(self, citizen_id) -> dict:
        """探活原料(确定性探一次,不起心跳线程;UI 侧按需调)。

        复用 probe 的确定性探活(二进制在不在 / 能不能跑冒烟)。返回至少
        {status: online|offline|unreachable}:
          - 注册表无此成员 / 无可用配方 → offline
          - probe active → online
          - probe 不过(bin 缺 / 冒烟失败)→ unreachable(+ reason)
        探活出错也 fail-loud 成 unreachable,绝不假装 online。
        """
        c = self.resolve(citizen_id)
        if c is None:
            return {"status": "offline", "reason": "无此成员", "citizen_id": citizen_id or ""}
        recipe = c.recipe()
        if recipe is None:
            return {"status": "offline", "reason": f"无可用配方(runtime_kind={c.runtime_kind})",
                    "citizen_id": c.citizen_id}
        probe_fn = self._probe_fn
        if probe_fn is None:
            from .probe import probe as probe_fn  # 懒加载,避免循环
        try:
            # smoke=False:确定性静态探(bin 在不在 / key 源在不在),不真起子进程冒烟 —— 探一次够 UI 用。
            pr = probe_fn(recipe, smoke=False)
        except Exception as e:  # noqa: BLE001 — 探活出错 fail-loud 成 unreachable,不假装 online
            return {"status": "unreachable", "reason": f"探活出错:{type(e).__name__}",
                    "citizen_id": c.citizen_id}
        if getattr(pr, "ok", False):
            return {"status": "online", "citizen_id": c.citizen_id, "tier": c.tier,
                    "domain_id": c.domain_id}
        return {"status": "unreachable", "reason": getattr(pr, "reason", "") or "探活不过",
                "citizen_id": c.citizen_id}

    def _persist(self) -> bool:
        if self._store is None:
            return True
        try:
            self._store.save_all([c.to_dict() for c in self._by_key.values()])
            self.persist_error = ""
            return True
        except Exception as e:  # noqa: BLE001
            self.persist_error = f"{type(e).__name__}: {e}"
            return False


__all__ = [
    "ExternalCitizen", "ExternalCitizenRegistry", "compute_manifest_hash",
    "EXTERNAL_ROLE",
    "STATUS_ACTIVE", "STATUS_UNREACHABLE", "STATUS_BLOCKED",
    "STATUS_RETIRED", "STATUS_NEEDS_REATTACH", "STATUS_PENDING",
    "TIER_GUEST", "TIER_SCOPED", "normalize_tier",
    # 认领码握手
    "ClaimTicket", "mint_claim_ticket", "split_claim_secret", "CLAIM_TICKET_TTL_S",
]
