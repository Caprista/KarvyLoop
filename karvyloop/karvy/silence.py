"""karvy/silence.py — 「挣来的静音」:口味命中率从**仪表**变**控制器**(docs/49 ⑫机制2、docs/50 决定1)。

**v2:统计判决版(docs/52 §2 世界雷达六条修正,全落)。** 旧门(n≥20 且裸命中率≥90%)被
统计证据证伪:①n=20@90% 的 Wilson 95% 下界只有 ~0.70,证明不了 90% 水平;②用户对决策卡
的批准基线很高(业界实测 ~93%),"无脑押会批"的常数策略稳过裸命中率门;③"跑到连中为止"
是无 alpha-spending 的序贯检验作弊;④授权-吊销振荡里,吊销永远发生在损害之后。修正:

1. **不可逆动作永不进自动池**(irreversible_semantics):外发(邮件/消息)/删除/支付/生产写
   的语义,kind 级 + 单卡 payload 级双层硬排除 —— 业界最大邮件服务商做了十几年"学你的收件箱"
   也从不代发;头部模型厂商的自动模式弃"信任分/历史战绩"、改逐动作判险。战绩再好也不解锁。
2. **门槛 = Wilson 95% 置信下界 ≥ 0.90**(wilson_lower_bound,z=1.96)+ **评估水位**:
   每桶每攒满 SILENCE_EVAL_BATCH_N 个新对账样本才允许判一次门(固定批次窗口,治 peeking)。
3. **批/拒两类分别达标**(docs/52 原文"批准准确率和拒绝准确率各自过线",杀常数策略):
   ACCEPT 向(静音真正会自动执行的方向)该向 Wilson 下界也须 ≥0.90,不许靠拒向样本撑整体;
   REJECT 向须押中 ≥SILENCE_MIN_REJECT_CORRECT 条 **且**该向 Wilson 下界 ≥SILENCE_MIN_REJECT_LB
   (95% 置信优于抛硬币)—— 全押 ACCEPT 的零智能策略 0 样本必挂,"大量瞎押 REJECT 碰运气
   凑 2 条"的带噪变体在拒向准确率上必挂。
4. **不告知随机抽查**:已授权桶的新卡按 SILENCE_AUDIT_RATE 概率照常出卡(**卡上不标注**,
   防应付)—— automation complacency 文献里唯一被证实有效的解;抽查样本照常进对账流。
5. **月度强制续期 + 风险加权复核**:授权带 expires_at(30 天);到期回正常出卡 + 出续期卡
   (带上月对账:静音 N 次/抽查对账 M 次中 H 次/最老留痕指针,**并按风险加权抽 5 条本期留痕
   摆上卡供逐条复核**:翻案>失败>最贵>新近),人看过、ACCEPT 才续 30 天。
6. **爆炸半径硬顶**(基础设施层,不信战绩)两层:①**单日次数顶**——任何桶(不分 kind)
   滚动 24h 静音 ≥SILENCE_DAILY_MAX 次即回人工,授权≠无限流量;②执行类 kind
   (SILENCE_EXEC_KINDS)再查该桶近期平均 token 成本(token_ledger 按 token_task 归因),
   超 SILENCE_COST_CAP_TOKENS 不静音回人工。

**红线(不可破坏,任务钉死)**:静音永远不吞**高价值卡**与**违背你已定标准的卡**——
决策卡的 Cut2 守线(decision_card_wire.check_violations → high_value=True 挡拍板)长在建卡
路径上,而被静音的卡永远走不到建卡那步 → 本模块在静音路径内**自带同口径检查**:
① 卡命中你已结晶的高价值决策偏好(is_high_value,确定性)→ 永不静音;
② 押 ACCEPT 且够置信后、真兑现前,跑同一 check_violations(strict:检查失败=拦,不是放
   —— 建卡路径失败可以放因为有人兜底,静音路径没有人)→ 有违背/查不了 → 回人工出卡。
K5 不变量:静音兑现走 dispatch_accept(handler 直调),不伪造用户决策、不进 decision_log、
不进命中率账本(没有 ground truth 的"命中"绝不记账);授权/续期/吊销本身永远走 H2A。

原有骨架保留:分桶命中率复用 TastePredictionStore 对账流水 ⨝ decision_log(唯一账本);
授权本身永远走 H2A;只静音 ACCEPT 向 + 完整留痕;押错/翻案立即吊销,重挣只认新鲜证据;
高危 kind 硬排除 + 授权卡自指防护。

保守铁律(Hardy):所有边界向保守倒 —— 判定链上任何一环失败,都回"正常出卡问人"。
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import math
import random
import re
import time
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger(__name__)

# ---- kind 常量(决策卡 wire 格式;handler 在 console/proposal_handlers.py 注册)----
KIND_SILENCE_GRANT = "silence_grant"      # 授权卡/续期卡:要不要(继续)替你静音处理?
KIND_SILENCE_REVOKED = "silence_revoked"  # 告知卡:押错/翻案 → 已自动收回该桶授权

WS_TYPE_SILENCE_NOTICE = "silence_notice"   # WS 轻通知(前端 i18n key 见交付报告)

# ---- 授权门(统计判决版;逐条依据见 docs/52 §2)----
# WILSON_Z = 1.96:双侧 95% 置信的标准正态分位数。docs/52 用同一口径打脸旧门:
#   n=20 命中 18(90%)的 Wilson 下界 = 0.699 —— "20 连中 18"根本证明不了 90% 水平。
WILSON_Z = 1.96
# 门槛:Wilson 95% 下界 ≥ 0.90(不是裸命中率 ≥ 0.90)。诚实勘误:docs/52 写"约 48/50",
#   精确计算 48/50 的下界只有 0.865(仍拒);全中也要 n≥35 才可能过(35/35 → 0.9011)。
#   真实最小达门形态举例:35/35、50/50(0.9287)、59/60(0.9114)。比文档更严 = 更保守,方向安全。
SILENCE_MIN_WILSON_LB = 0.90
# SILENCE_MIN_N = 35:z=1.96 下即使零失误也要 n≥35 才可能下界≥0.90 —— 这是数学地板,
#   不是拍的;比它小的桶零成本先拒(旧值 20 已无意义,Wilson 门天然吸收)。
SILENCE_MIN_N = 35
# 评估水位(治 peeking / 序贯凑连击,docs/52 §2 修正②):对账按固定批次窗口滚动 ——
#   上次评估后该桶新增对账样本 < 25 就不再判门;"每来一个样本试一次门"= 无 alpha-spending
#   的序贯作弊(允许系统挑最好看的一段样本报账)。
SILENCE_EVAL_BATCH_N = 25
# 分向达标门(docs/52 §2 修正③「批/拒两类分别达标…各自过线」,杀常数策略):
#   用户批准基线 ~93% → "无脑押 ACCEPT"稳过任何整体裸命中率门;更阴的带噪变体是
#   "大量押 ACCEPT + 顺手瞎押几条 REJECT 碰运气凑证据"——只数"押 REJECT 且中 ≥2 条"
#   会被它骗过(拒向错 8 条中 2 条也算过)。所以两向**各自过线**:
#   - ACCEPT 向:该向 Wilson 95% 下界 ≥ SILENCE_MIN_WILSON_LB(0.90)——这是静音真正会
#     自动执行的方向,必须独立扛满线,不许靠拒向样本把整体撑过去。
#   - REJECT 向:押中 ≥ SILENCE_MIN_REJECT_CORRECT 条 且 该向 Wilson 95% 下界 ≥
#     SILENCE_MIN_REJECT_LB(0.50 = 95% 置信优于抛硬币;最小达门形态:4/4 全对 → 0.5101,
#     3/3 → 0.4385 不够,7/8 → 0.529 够)。
#   诚实勘误(设计侧,统计上诚实为准绳):docs/52 只说"各自过线"没给拒向的线。拒向若也套
#   0.90,按 ~93% 批准基线要攒 ≥35 条押 REJECT 全对 ≈ 500+ 次拍板/桶,超过账本留存上限
#   (_OUTCOME_RETAIN=500)——门在数学上不可达 = 假门。拒向从不被自动执行(押 REJECT 一律
#   问人),它的统计职责是证明判别力(不是常数策略),"95% 置信优于随机"是其最小诚实形态。
SILENCE_MIN_REJECT_CORRECT = 2
SILENCE_MIN_REJECT_LB = 0.50
# 单卡执行门:桶级授权之外,每张卡的预测还要 ACCEPT 向且置信 ≥ 此值才真静音执行;
# 0.80 与"押注要压低 confidence 表达不确定"(taste_eval prompt)对齐 —— 模型自己没把握就问人。
SILENCE_MIN_CONFIDENCE = 0.80
# 不告知随机抽查(docs/52 §2 修正④):恒定高可靠性最大化 automation complacency(自动驾驶
#   /自动合并的事故复盘一致),间歇不告知抽查是文献里唯一被证实有效的解。15% 取文献建议区间
#   (10-20%)中值;**抽查卡不带任何标注**(标注=用户能区别对待=抽查失效)。
SILENCE_AUDIT_RATE = 0.15
# 月度强制续期(docs/52 §2 修正⑤):授权 30 天后过期,回正常出卡 + 续期卡;"没人看的月度
#   对账"改成"不 ACCEPT 就停"的硬门(自动合并类事故的复盘:60% 无人过目)。
SILENCE_GRANT_TTL_S = 30 * 86400
# 爆炸半径硬顶(docs/52 §2 修正⑥「每类自动动作配基础设施层上限(单日次数/金额/影响面)」,
#   预算上限在基础设施层,不信任"战绩"能约束半径)。两层:
#   ① 单日次数顶(设计原文的"次数"维,**所有 kind 都吃**):一个桶滚动 24h 静音处理
#     ≥ SILENCE_DAILY_MAX 次 → 后续回人工。授权 ≠ 无限流量;上游疯了(提案风暴)时,
#     静音不放大爆炸半径,人在第 N+1 张卡就看见异常。从静音台账现数,确定性零 LLM。
#   ② 成本顶(执行类 kind 会真跑任务烧 token,半径不只是"办错一张卡"):现状没有
#     per-handler 预算注入口(dispatch_accept 的 handler 只收 proposal),所以在静音执行前
#     查该桶近期平均成本(token_ledger 按 token_task="silenced:<pid>" 归因,近
#     SILENCE_COST_WINDOW 次均值),超过 30k token(约一次重任务的量级)不静音回人工。
SILENCE_DAILY_MAX = 10
SILENCE_DAILY_WINDOW_S = 86400
SILENCE_EXEC_KINDS = frozenset({"route_to_role", "run_task", "roundtable"})
SILENCE_COST_CAP_TOKENS = 30_000
SILENCE_COST_WINDOW = 10
# 同桶授权/续期卡被 REJECT/无人理后,过这么久才允许再提(别把"要授权"变成新打扰)。
OFFER_COOLDOWN_S = 7 * 86400
_LEDGER_RETAIN = 1000   # 静音台账留存上限(月度对账/折叠区够用,有界)

# ---- 高危 kind 硬排除(授权门 + SilenceGrantStore.grant 双层都拒)----
# 字符串字面量(而非 import 各处常量):kind 是 wire 格式稳定字符串,且避免 karvy↔console↔
# cognition 的 import 环。逐条依据:
HIGH_RISK_KINDS = frozenset({
    "fs_access",             # 文件系统扩权授权卡(任务点名;安全边界绝不自动放行)
    KIND_SILENCE_GRANT,      # 授权卡自身 —— 自指防护:静音授权永不被静音(任务点名)
    KIND_SILENCE_REVOKED,    # 吊销告知 —— 静音它=用户永远不知道授权没了
    "ops_fix",               # 会执行系统修复(哪怕确定性可逆,也属系统状态变更)
    "resolve_conflict",      # 治理冲突处置(value.md 层面的事必须人拍)
    "confirm_decision_pref", # 元循环(taste_eval SKIP_KINDS 同口径:确认偏好本身不押注)
    "cocreate_finalize",     # 真建域+角色(结构性变更)
    "merge_atoms",           # 删原子(护城河资产;rewire-before-delete 也不自动删)
    "confirm_result",        # 裁定自造原子沉淀(护城河资产变更;不处理本就有孤儿巡检兜底)
    "infeasible_report",     # H2A 升级报告 —— 静音=吞掉升级,决策 loop 塌(「怎么样了?」反模式)
    "weekly_digest",         # 信息送达类:静音=用户看不到成绩单/对账,信任阶梯自断
    # ---- docs/52 §2 修正① 语义审查补入(删除/外发/付款/生产写 全覆盖) ----
    "merge_knowledge",       # 合并后删原知识点(先写后删)——删除语义,护城河资产,与 merge_atoms 同口径
    "inbox_decision",        # 收件箱"需要拍板":报价/合同/付款类 —— 付款语义,永不自动
    "inbox_reply",           # 收件箱"需要回复":外发(邮件)语义的入口 + 信息送达(静音=漏报杀信任)
    "revise_skill",          # 改写既有 SKILL.md —— 对护城河资产的生产写,与 merge_atoms/confirm_result 同口径
    "mesh_takeover",         # 跨设备接活重跑(docs/74 §6.3):设备"判死"是 lease 推断不是事实,自动接=双跑风险;产品裁决=H2A 确认才动,永不自动
    # crystallize_skill 留池:纯新增、provisional、可删,非破坏性(与上面的"改/删"不同类)
})

# ---- 不可逆动作语义硬排除(docs/52 §2 修正①,kind 表之外的第二层)----
# 依据:业界最大邮件服务商十几年只做分诊/草稿、从不代发;头部模型厂商的自动模式弃信任分、
# 改逐动作判险 —— 外发/删除/支付/生产写这四类,无论桶战绩多好都永不自动。
# kind 级(桶永不授权)扫 kind 名;单卡级(池内 kind 的具体卡)再扫 payload+summary ——
# route_to_role/run_task 这类"执行但沙箱内"的留在池里,但一张卡蕴含不可逆语义就单卡回人工。
# 英文走词边界(防 "pay" 误中 "payload");误伤方向 = 多问一次人,保守可接受。
_IRREVERSIBLE_PATTERNS: tuple[tuple[str, "re.Pattern[str]"], ...] = (
    ("outbound", re.compile(
        r"发邮件|发送|发给|外发|群发|发消息|私信|回复邮件|回信"
        r"|\b(send|email|e-mail|mail|sms|dm|publish|broadcast|reply)\b", re.I)),
    ("delete", re.compile(
        r"删除|删掉|删库|清空|移除|抹掉|卸载|销毁"
        r"|\brm\s+-|\b(delete|remove|drop|purge|erase|wipe|truncate|uninstall|destroy)\b", re.I)),
    ("payment", re.compile(
        r"付款|支付|转账|汇款|扣费|购买|下单|退款|充值|缴费"
        r"|\b(pay|payment|purchase|transfer|refund|charge|billing|checkout)\b", re.I)),
    ("prod_write", re.compile(
        r"上线|部署|发布到|推送到主干|生产环境|删生产"
        r"|\b(deploy|production|prod|release|rollout)\b|push\s+to\s+(main|master|prod)", re.I)),
)


def irreversible_semantics(kind: str, payload: Any = None, summary: str = "") -> str:
    """卡/桶是否蕴含不可逆动作语义。命中返回类别("outbound"/"delete"/"payment"/"prod_write"),
    未命中返回 ""。payload 序列化后连同 kind、summary 一起扫(扫不出=允许,扫出=永不静音)。"""
    parts = [str(kind or ""), str(summary or "")]
    if payload:
        # **全量**序列化,绝不截断:对抗验收实锤过截断绕过(良性长前缀把危险文本顶出扫描窗
        # → "转账+发邮件"的卡被静音执行)。正则线性扫,payload 再大也便宜;序列化炸了当命中。
        try:
            parts.append(json.dumps(payload, ensure_ascii=False, default=str))
        except Exception:
            return "scan_error"   # 序列化不了 = 扫不完整 = 不可静音(fail-closed)
    # 归一,防边界漏扫(漏扫方向 = 该拦不拦,不能接受):
    # ① snake/kebab → 空格:下划线是 \w,"send_email" 里 \bsend\b 匹不上;
    # ② camelCase 拆词:"DeployToProd" → "Deploy To Prod";
    # ③ CJK 与拉丁之间补空格:CJK 也是 \w,"把email发给客户"没有 \b 边界。
    text = " ".join(parts).replace("_", " ").replace("-", " ")
    text = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", " ", text)
    text = re.sub(r"([　-鿿豈-﫿])(?=[A-Za-z0-9])", r"\1 ", text)
    text = re.sub(r"([A-Za-z0-9])(?=[　-鿿豈-﫿])", r"\1 ", text)
    for label, pat in _IRREVERSIBLE_PATTERNS:
        try:
            if pat.search(text):
                return label
        except Exception:
            return "scan_error"   # 扫描炸了也当命中(保守:宁可回人工)
    return ""


# ---------------------------------------------------------------- Wilson 下界(纯数学)
def wilson_lower_bound(hits: int, n: int, *, z: float = WILSON_Z) -> float:
    """Wilson score interval 的下界(命中率的 95% 置信下界,z=1.96 双侧)。

    这是授权门的判决量:它回答"最坏情况下这个桶的真实命中率至少多少",而裸命中率只回答
    "这段样本里碰巧中了多少"。例:18/20 裸命中率 0.90,Wilson 下界仅 0.699。
    纯函数、无 IO;越界输入向保守夹断(n≤0 → 0.0,hits 夹到 [0, n])。
    """
    n_i = int(n)
    if n_i <= 0:
        return 0.0
    h = max(0, min(int(hits), n_i))
    p = h / n_i
    z2 = z * z
    denom = 1.0 + z2 / n_i
    center = p + z2 / (2.0 * n_i)
    spread = z * math.sqrt(p * (1.0 - p) / n_i + z2 / (4.0 * n_i * n_i))
    return max(0.0, (center - spread) / denom)


# ---------------------------------------------------------------- 不告知随机抽查
# 模块级独立 RNG(不用全局 random:外部 seed 不干扰抽查,测试可注入/播种)。
_audit_rng = random.Random()


def _should_audit() -> bool:
    """已授权桶的新卡是否本次抽查(照常出卡、**不标注**;样本照常进对账流)。"""
    return _audit_rng.random() < SILENCE_AUDIT_RATE


# ---------------------------------------------------------------- 桶
def _norm_domain(domain: str) -> str:
    """域归一:l0(私聊/全局)与空同桶 —— 两侧(decision_log 的 domain / 卡 payload 的
    domain_id)口径不一时只会导致**不静音**(保守),归一只做无风险的 l0≡空。"""
    d = (domain or "").strip()
    return "" if d in ("", "l0") else d


def bucket_key(kind: str, domain: str = "") -> str:
    d = _norm_domain(domain)
    return f"{kind}|{d}" if d else str(kind)


def _proposal_domain(proposal: Any) -> str:
    p = getattr(proposal, "payload", {}) or {}
    return _norm_domain(str(p.get("domain_id") or p.get("group_domain_id") or ""))


def bucket_stats(app: Any, *, min_ts: float = 0.0) -> dict[str, dict]:
    """分桶命中率:{bucket: {kind, domain, n, hits, hit_rate, wilson_lb,
                            accept_n, accept_hits, accept_lb,
                            reject_n, reject_hits, reject_lb, reject_correct}}。

    数据源 = TastePredictionStore.outcomes()(唯一账本,只读)⨝ decision_log(proposal_id →
    kind/domain)。min_ts>0 时只算该时刻**之后**的对账 —— 吊销后重挣授权只认新鲜证据。
    分向计数(docs/52 §2 修正③「批/拒分别达标」):按**预测方向**切 —— accept_* 是"押你会批"
    的样本(静音真正会自动执行的方向),reject_* 是"押你会拒"的样本(判别力证据);
    reject_correct ≡ reject_hits(留旧名,授权卡 payload/旧调用方兼容)。
    任何一环读不到 → 返回空(没有统计就没有授权,保守)。
    """
    tstore = getattr(app.state, "taste_predictions", None)
    dlog = getattr(app.state, "decision_log", None)
    if tstore is None or dlog is None:
        return {}
    try:
        outs = tstore.outcomes()
    except Exception:
        return {}
    meta: dict[str, tuple] = {}
    try:
        for e in dlog.query(limit=5000):   # newest-first;同 pid 取最新一条(kind/domain 相同)
            pid = e.get("proposal_id") or ""
            if pid and pid not in meta and e.get("kind"):
                meta[pid] = (e.get("kind", ""), _norm_domain(e.get("domain", "")))
    except Exception:
        return {}
    buckets: dict[str, dict] = {}
    for o in outs:
        try:
            ts = float(o.get("ts", 0.0))
        except (TypeError, ValueError):
            ts = 0.0
        if min_ts > 0 and ts <= min_ts:
            continue
        km = meta.get(o.get("pid", ""))
        if not km:
            continue   # 关联不上 → 不计入(不猜桶)
        kind, domain = km
        b = bucket_key(kind, domain)
        d = buckets.setdefault(b, {"kind": kind, "domain": domain, "n": 0, "hits": 0,
                                   "accept_n": 0, "accept_hits": 0,
                                   "reject_n": 0, "reject_hits": 0, "reject_correct": 0})
        d["n"] += 1
        hit = bool(o.get("hit"))
        if hit:
            d["hits"] += 1
        # record_prediction 只收 ACCEPT/REJECT;万一账本混入别的值,归到 accept 向 ——
        # accept 向的线(0.90)比 reject 向(0.50)严,归错方向只会更难过门(保守)。
        if str(o.get("predicted", "")).upper() == "REJECT":
            d["reject_n"] += 1
            if hit:
                d["reject_hits"] += 1
                d["reject_correct"] += 1
        else:
            d["accept_n"] += 1
            if hit:
                d["accept_hits"] += 1
    for d in buckets.values():
        d["hit_rate"] = (d["hits"] / d["n"]) if d["n"] else 0.0
        d["wilson_lb"] = wilson_lower_bound(d["hits"], d["n"])
        d["accept_lb"] = wilson_lower_bound(d["accept_hits"], d["accept_n"])
        d["reject_lb"] = wilson_lower_bound(d["reject_hits"], d["reject_n"])
    return buckets


# ---------------------------------------------------------------- 授权台账
class SilenceGrantStore:
    """静音授权台账(~/.karvyloop/silence_grants.json;fail-safe:坏文件当空)。

    结构:{"grants": {bucket: {kind, domain, granted_at, expires_at, n, hits,
                              revoked_at, revoke_reason}},
          "offers": {bucket: last_offer_ts},
          "eval_marks": {bucket: 上次评估门时该桶的对账样本数}}。
    吊销**不删记录**(revoked_at 留审计 + 重挣授权的新鲜证据水位);重新授权覆盖写
    (granted_at/expires_at 更新)。eval_marks 是评估水位(固定批次窗口,治 peeking)。
    """

    def __init__(self, path: Optional[Path] = None) -> None:
        self._path = Path(path) if path else None
        self._grants: dict[str, dict] = {}
        self._offers: dict[str, float] = {}
        self._eval_marks: dict[str, int] = {}
        if self._path is not None and self._path.exists():
            try:
                d = json.loads(self._path.read_text(encoding="utf-8"))
                self._grants = {str(k): dict(v) for k, v in (d.get("grants") or {}).items()
                                if isinstance(v, dict)}
                self._offers = {str(k): float(v) for k, v in (d.get("offers") or {}).items()
                                if isinstance(v, (int, float))}
                self._eval_marks = {str(k): int(v) for k, v in (d.get("eval_marks") or {}).items()
                                    if isinstance(v, (int, float))}
            except Exception:
                pass   # 坏文件当空(与 taste store 同调;丢授权=回到逐张问人,安全方向)

    def _save(self) -> None:
        if self._path is None:
            return
        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._path.write_text(json.dumps(
                {"grants": self._grants, "offers": self._offers,
                 "eval_marks": self._eval_marks},
                ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception as e:
            logger.warning("[silence] 授权台账落盘失败(不阻断): %s", e)

    def grant(self, kind: str, domain: str = "", *, n: int = 0, hits: int = 0,
              now: Optional[float] = None) -> Optional[dict]:
        """授权一个桶(30 天有效,docs/52 §2 修正⑤)。高危 kind / 不可逆语义 kind
        **硬地板拒绝**(返回 None)—— 即使卡被伪造也授不出权。"""
        k = (kind or "").strip()
        if not k or k in HIGH_RISK_KINDS or irreversible_semantics(k):
            return None
        d = _norm_domain(domain)
        granted_at = now if now is not None else time.time()
        g = {"kind": k, "domain": d,
             "granted_at": granted_at,
             "expires_at": granted_at + SILENCE_GRANT_TTL_S,
             "n": int(n), "hits": int(hits), "revoked_at": None, "revoke_reason": ""}
        self._grants[bucket_key(k, d)] = g
        self._save()
        return dict(g)

    @staticmethod
    def _expires_at(g: dict) -> float:
        """兼容旧记录(无 expires_at):按 granted_at + TTL 推 —— 旧授权也吃月度续期,不豁免。"""
        try:
            e = g.get("expires_at")
            if e is not None:
                return float(e)
            return float(g.get("granted_at") or 0.0) + SILENCE_GRANT_TTL_S
        except (TypeError, ValueError):
            return 0.0   # 解析不了 → 当已过期(保守)

    def is_granted(self, kind: str, domain: str = "", *, now: Optional[float] = None) -> bool:
        """活跃授权:未吊销 **且未过期**(到期 = 回正常出卡,续期要人 ACCEPT)。"""
        g = self._grants.get(bucket_key((kind or "").strip(), domain))
        if not g or g.get("revoked_at"):
            return False
        n = now if now is not None else time.time()
        return n < self._expires_at(g)

    def expired_unrevoked(self, kind: str, domain: str = "", *,
                          now: Optional[float] = None) -> Optional[dict]:
        """已到期但未吊销的授权(续期卡的触发态)。没有 → None。"""
        g = self._grants.get(bucket_key((kind or "").strip(), domain))
        if not g or g.get("revoked_at"):
            return None
        n = now if now is not None else time.time()
        return dict(g) if n >= self._expires_at(g) else None

    def revoke(self, bucket: str, *, reason: str = "", now: Optional[float] = None) -> bool:
        """吊销(可撤销的"撤销":记录保留,授权失效)。没有可吊销的授权 → False。
        同时清该桶评估水位:吊销后统计基线重置(只认新鲜证据),水位跟着从 0 攒。"""
        g = self._grants.get(bucket)
        if not g or g.get("revoked_at"):
            return False
        g["revoked_at"] = now if now is not None else time.time()
        g["revoke_reason"] = reason or "revoked"
        self._eval_marks.pop(bucket, None)
        self._save()
        return True

    def last_revoked_at(self, bucket: str) -> float:
        g = self._grants.get(bucket)
        if not g:
            return 0.0
        try:
            return float(g.get("revoked_at") or 0.0)
        except (TypeError, ValueError):
            return 0.0

    def active_grants(self, *, now: Optional[float] = None) -> dict[str, dict]:
        n = now if now is not None else time.time()
        return {b: dict(g) for b, g in self._grants.items()
                if not g.get("revoked_at") and n < self._expires_at(g)}

    def note_offer(self, bucket: str, now: Optional[float] = None) -> None:
        self._offers[bucket] = now if now is not None else time.time()
        self._save()

    def offer_recently(self, bucket: str, *, now: Optional[float] = None) -> bool:
        ts = self._offers.get(bucket)
        if ts is None:
            return False
        n = now if now is not None else time.time()
        return (n - ts) < OFFER_COOLDOWN_S

    # ---- 评估水位(固定批次窗口,docs/52 §2 修正②)----
    def eval_mark(self, bucket: str) -> int:
        return int(self._eval_marks.get(bucket, 0))

    def note_eval(self, bucket: str, n: int) -> None:
        """记一次门评估(过/不过都记 —— 这就是"固定批次窗口":每满一批只看一眼)。"""
        self._eval_marks[bucket] = int(n)
        self._save()


def get_store(app: Any) -> SilenceGrantStore:
    """app.state 上懒加载授权台账(entry 不用改接线;测试注入 silence_grants_path 或直接放实例)。"""
    st = getattr(app.state, "silence_grants", None)
    if isinstance(st, SilenceGrantStore):
        return st
    path = getattr(app.state, "silence_grants_path", None) \
        or (Path.home() / ".karvyloop" / "silence_grants.json")
    st = SilenceGrantStore(Path(path))
    app.state.silence_grants = st
    return st


# ---------------------------------------------------------------- 静音留痕台账
def _ledger_path(app: Any) -> Path:
    p = getattr(app.state, "silenced_ledger_path", None)
    return Path(p) if p else (Path.home() / ".karvyloop" / "silenced_decisions.json")


def read_ledger(app: Any) -> list[dict]:
    try:
        p = _ledger_path(app)
        if not p.exists():
            return []
        data = json.loads(p.read_text(encoding="utf-8"))
        return [d for d in data if isinstance(d, dict)] if isinstance(data, list) else []
    except Exception:
        return []


def _write_ledger(app: Any, items: list[dict]) -> None:
    p = _ledger_path(app)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(items[-_LEDGER_RETAIN:], ensure_ascii=False), encoding="utf-8")


def record_silenced(app: Any, entry: dict) -> None:
    """静音处理留痕(跨重启;"已按你的口味处理"折叠区 + 月度对账/续期卡的数据源)。"""
    try:
        items = read_ledger(app)
        items.append(dict(entry))
        _write_ledger(app, items)
    except Exception as e:
        logger.warning("[silence] 静音台账落盘失败(Trace 仍会记): %s", e)


def _ledger_bucket(it: dict) -> str:
    return it.get("bucket") or bucket_key(it.get("kind", ""), it.get("domain", ""))


def _ledger_ts(it: dict) -> float:
    try:
        return float(it.get("ts", 0.0) or 0.0)
    except (TypeError, ValueError):
        return 0.0


def _daily_marks(app: Any) -> dict[str, list[float]]:
    """进程内的静音时间戳标记(单日顶的第二本账)。台账文件坏掉时 read_ledger 返空 ——
    硬顶不许因此失明放行,所以 _silent_handle 每次兑现同时在内存记一笔;
    计数取两本账的 max(重启后靠台账,进程内靠这里,谁在谁兜底)。"""
    d = getattr(app.state, "_silence_daily_marks", None)
    if d is None:
        d = app.state._silence_daily_marks = {}
    return d


def note_daily_mark(app: Any, bucket: str, *, now: Optional[float] = None) -> None:
    n_ts = now if now is not None else time.time()
    marks = _daily_marks(app).setdefault(bucket, [])
    marks.append(n_ts)
    cutoff = n_ts - SILENCE_DAILY_WINDOW_S
    if len(marks) > 4 * SILENCE_DAILY_MAX:   # 有界:窗外的顺手剪(计数时还会再滤)
        marks[:] = [t for t in marks if t >= cutoff]


def bucket_daily_count(app: Any, bucket: str, *, now: Optional[float] = None) -> int:
    """该桶滚动 24h 内的静音处理次数(修正⑥-① 单日次数顶的判决量)。确定性零 LLM;
    取 max(台账现数, 进程内标记) —— 台账坏文件当空会让硬顶失明,进程内账兜住这半。"""
    n_ts = now if now is not None else time.time()
    since = n_ts - SILENCE_DAILY_WINDOW_S
    from_ledger = sum(1 for it in read_ledger(app)
                      if _ledger_bucket(it) == bucket and _ledger_ts(it) >= since)
    in_mem = sum(1 for t in _daily_marks(app).get(bucket, []) if t >= since)
    return max(from_ledger, in_mem)


def risk_weighted_review_items(app: Any, bucket: str, *, since: float = 0.0,
                               k: int = 5) -> list[dict]:
    """续期复核抽样(docs/52 §2 修正⑤「每月按风险加权抽 5 条,逐条复核完毕授权才延续」):
    从该桶本期(since=granted_at 之后)的静音台账抽 k 条风险最高的,摆进续期卡供人逐条看 ——
    续期不是盲点头,是"看过这些还愿意续"。风险序(高→低):被翻案 > 兑现失败 > 成本高 > 新近。
    纯查表零 LLM;条目字段做过防御归一(坏值当 0/空,不崩续期卡)。"""
    items = []
    for it in read_ledger(app):
        if _ledger_bucket(it) != bucket or _ledger_ts(it) < since:
            continue
        try:
            cost = int(float(it.get("cost_tokens", 0) or 0))
        except (TypeError, ValueError):
            cost = 0
        items.append({"proposal_id": str(it.get("proposal_id") or ""),
                      "ts": _ledger_ts(it),
                      "summary": str(it.get("summary") or "")[:120],
                      "ok": bool(it.get("ok", True)),
                      "overturned": bool(it.get("overturned", False)),
                      "cost_tokens": cost})
    items.sort(key=lambda x: (x["overturned"], not x["ok"], x["cost_tokens"], x["ts"]),
               reverse=True)
    return items[:max(0, int(k))]


# ---------------------------------------------------------------- 爆炸半径(执行类成本顶)
def bucket_recent_avg_cost(app: Any, bucket: str, *, limit: int = SILENCE_COST_WINDOW) -> float:
    """该桶最近 limit 次静音执行的平均 token 成本(input+output)。

    成本按 token_task="silenced:<pid>" 从 token_ledger 现查(执行若有异步尾巴,记账晚于
    台账快照,现查拿到的是最新值);查不到退台账里的 cost_tokens 快照。没有任何成本数据
    → 0.0(还没静音执行过,无从超顶)。任何一环失败 → 0.0 + warning(测量层失败不阻断,
    但硬顶靠它,所以要可见)。
    """
    try:
        from karvyloop.llm.token_ledger import get_ledger
        led = get_ledger()
    except Exception:
        led = None
    try:
        items = [it for it in read_ledger(app)
                 if (it.get("bucket") or bucket_key(it.get("kind", ""), it.get("domain", "")))
                 == bucket][-max(1, int(limit)):]
    except Exception as e:
        logger.warning("[silence] 读静音台账算成本失败(按 0 处理): %s", e)
        return 0.0
    costs: list[int] = []
    for it in items:
        c = 0
        tid = str(it.get("token_task_id") or "")
        if led is not None and tid:
            try:
                c = int(led.task_total(tid))
            except Exception:
                c = 0
        if c <= 0:
            try:
                c = int(it.get("cost_tokens", 0) or 0)
            except (TypeError, ValueError):
                c = 0
        if c > 0:
            costs.append(c)
    return (sum(costs) / len(costs)) if costs else 0.0


# ---------------------------------------------------------------- 卡片工厂
def proposal_for_silence_grant(*, kind: str, domain: str = "", n: int, hits: int, ts: float,
                               wilson_lb: float = 0.0, reject_correct: int = 0,
                               accept_n: int = 0, accept_hits: int = 0, reject_n: int = 0):
    """授权卡:达门的桶 → 问人"要不要以后这类替你静音处理?"。稳定 id 按桶派生(同桶收敛一张)。
    分向数据(accept_*/reject_*)进 payload —— 成绩单两向都摆,不只报整体(修正③可核)。"""
    from karvyloop.karvy.atoms import Proposal
    d = _norm_domain(domain)
    b = bucket_key(kind, d)
    digest = hashlib.sha1(b.encode("utf-8")).hexdigest()[:8]
    dom_disp = f"(域「{d}」)" if d else ""
    lb = wilson_lb or wilson_lower_bound(hits, n)
    return Proposal(
        summary=(f"「{kind}」{dom_disp}这类板,我最近 {n} 次押中 {hits} 次"
                 f"(95% 置信下界 {int(lb * 100)}%)—— 要不要以后这类替你静音处理?"),
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=0.6, evidence_refs=(), habit_id=0, model_ref="", ts=ts,
        kind=KIND_SILENCE_GRANT,
        payload={"kind": kind, "domain": d, "bucket": b, "n": int(n), "hits": int(hits),
                 "wilson_lb": round(float(lb), 4), "reject_correct": int(reject_correct),
                 "accept_n": int(accept_n), "accept_hits": int(accept_hits),
                 "reject_n": int(reject_n)},
        proposal_id=f"{KIND_SILENCE_GRANT}-0-{digest}",
        basis=(f"这不是要更多权限 —— 是同类卡上我 {n} 次押中 {hits} 次的成绩单,按 95% 置信"
               f"下界算也 ≥{int(SILENCE_MIN_WILSON_LB * 100)}%(不是碰巧连中,批/拒两向"
               f"各自过线),其中我押"
               f"你会拒 {reject_n or reject_correct} 次、押对 {reject_correct} 次"
               f"(证明我能替你挡坏的,不只会点头)。"
               f"ACCEPT 后 30 天内:这类卡我**只**替你办「我押你会 ACCEPT 且把握 ≥"
               f"{int(SILENCE_MIN_CONFIDENCE * 100)}%」的;押 REJECT 或没把握的照旧问你;"
               f"我还会不定期抽一部分照常出卡对答案(哪张是抽查不告诉你);删除/外发/付款/"
               f"上线这类不可逆的永远问你。每次静音处理完整留痕(运行记录+台账)、满 30 天"
               f"要你亲手续期;我**押错一次立即自动收回**授权,你也随时可撤。"
               f"REJECT=保持现状,每张都问你。"),
    )


def proposal_for_silence_renewal(*, kind: str, domain: str = "", granted_at: float,
                                 silenced_n: int, audit_n: int, audit_hits: int,
                                 oldest_pid: str = "", ts: float,
                                 review_items: Optional[list] = None):
    """续期卡(docs/52 §2 修正⑤):授权满 30 天到期 → 带上月对账数据 + **风险加权复核抽样**
    (本期留痕里翻案/失败/最贵优先的 ≤5 条,payload.review_items,逐条看完再续)问人续不续。
    id 按 桶+granted_at 派生:同一期授权只收敛一张,下一期是新卡(不撞已拍过的)。"""
    from karvyloop.karvy.atoms import Proposal
    d = _norm_domain(domain)
    b = bucket_key(kind, d)
    digest = hashlib.sha1(f"renew|{b}|{int(granted_at)}".encode("utf-8")).hexdigest()[:8]
    dom_disp = f"(域「{d}」)" if d else ""
    audit_disp = (f"抽查对账 {audit_n} 次中 {audit_hits} 次" if audit_n
                  else "本期没攒到抽查对账样本")
    review = list(review_items or [])
    review_disp = ""
    if review:
        gists = ";".join(
            f"{'⚠翻案 ' if it.get('overturned') else ('✗失败 ' if not it.get('ok', True) else '')}"
            f"「{(it.get('summary') or it.get('proposal_id') or '')[:40]}」"
            for it in review[:5])
        review_disp = f"。本期风险最高的 {len(review)} 条(翻案/失败/最贵优先):{gists}"
    return Proposal(
        summary=(f"「{kind}」{dom_disp}的静音授权满 30 天到期 —— 上月替你静音 {silenced_n} 次,"
                 f"{audit_disp};要续 30 天吗?"),
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=0.6, evidence_refs=(), habit_id=0, model_ref="", ts=ts,
        kind=KIND_SILENCE_GRANT,
        payload={"kind": kind, "domain": d, "bucket": b,
                 "n": int(audit_n), "hits": int(audit_hits), "renew": True,
                 "silenced_n": int(silenced_n), "audit_n": int(audit_n),
                 "audit_hits": int(audit_hits), "oldest_pid": oldest_pid,
                 "prev_granted_at": float(granted_at),
                 "review_items": review},
        proposal_id=f"{KIND_SILENCE_GRANT}-1-{digest}",
        basis=(f"静音授权只有 30 天,到期必须你亲手续 —— 没人看的对账不算数,不点就停"
               f"(这类卡已恢复逐张问你)。本期账:静音 {silenced_n} 次、{audit_disp}"
               + (f"、最老一条留痕 {oldest_pid}" if oldest_pid else "")
               + review_disp
               + ";每条都在台账/运行记录里可查,逐条看完再决定。ACCEPT=续 30 天(规则不变:"
                 f"只办押你会 ACCEPT 且把握 ≥{int(SILENCE_MIN_CONFIDENCE * 100)}% 的,"
                 f"继续不定期抽查,押错一次立即收回);REJECT=不续,每张都问你。"),
    )


def proposal_for_silence_revoked(*, kind: str, domain: str = "", ts: float, reason: str = ""):
    """吊销告知卡:押错/翻案 → 授权已自动收回。带时间戳派生 id(多次吊销不撞已拍过的卡)。"""
    from karvyloop.karvy.atoms import Proposal
    d = _norm_domain(domain)
    b = bucket_key(kind, d)
    digest = hashlib.sha1(f"{b}|{int(ts)}".encode("utf-8")).hexdigest()[:8]
    dom_disp = f"(域「{d}」)" if d else ""
    return Proposal(
        summary=f"已自动收回「{kind}」{dom_disp}的静音授权 —— 这类卡恢复逐张问你",
        options=("ACCEPT", "DEFER", "REJECT"),
        strength=0.7, evidence_refs=(), habit_id=0, model_ref="", ts=ts,
        kind=KIND_SILENCE_REVOKED,
        payload={"kind": kind, "domain": d, "bucket": b, "reason": (reason or "")[:200]},
        proposal_id=f"{KIND_SILENCE_REVOKED}-0-{digest}",
        basis=(f"{reason or '我押错了一次你的拍板'}。挣来的静音只在命中率兑现时有效 —— "
               f"押错一次立即收回(保守边界);要重新拿授权,得吊销之后重新攒新鲜对账"
               f"(95% 置信下界 ≥{int(SILENCE_MIN_WILSON_LB * 100)}%,至少 "
               f"{SILENCE_MIN_N} 次)我才会再问你。ACCEPT=知悉。"),
    )


# ---------------------------------------------------------------- 出卡
def _deliver(app: Any, card: Any) -> None:
    """出卡:有事件循环 → broadcast(register+押注+WS 推);无循环(REST 线程池/测试)→
    直接登记待决表(boot-fetch 会取到)。allow_silence=False:本模块出的卡不再过静音判定。"""
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None
    if loop is not None:
        async def _push() -> None:
            from karvyloop.console.proposals import broadcast_proposal
            await broadcast_proposal(app, card, allow_silence=False)
        t = loop.create_task(_push())
        _track_task(app, t)
        return
    reg = getattr(app.state, "proposal_registry", None)
    if reg is not None:
        try:
            reg.register(card)
        except Exception as e:
            logger.warning("[silence] 出卡登记失败: %s", e)


def _track_task(app: Any, task: Any) -> None:
    tasks = getattr(app.state, "_silence_tasks", None)
    if tasks is None:
        tasks = app.state._silence_tasks = set()
    tasks.add(task)

    def _done(t: Any) -> None:
        tasks.discard(t)
        try:
            exc = t.exception()
        except BaseException:   # CancelledError 是 BaseException(py3.8+),关停时别穿透吵日志
            return
        if exc is not None:
            logger.error("[silence] 后台任务异常: %s", exc)
            try:
                from karvyloop.console.task_events import schedule_system_error
                schedule_system_error(app, "silence", str(exc))
            except Exception:
                pass

    task.add_done_callback(_done)


def _same_bucket_grant_pending(app: Any, bucket: str) -> Optional[bool]:
    """同桶授权/续期卡是否已挂在待决表。读不到 registry → None(调用方保守处理)。"""
    reg = getattr(app.state, "proposal_registry", None)
    if reg is None:
        return False
    try:
        for pr in reg.pending():
            if getattr(pr, "kind", "") == KIND_SILENCE_GRANT and \
                    (getattr(pr, "payload", {}) or {}).get("bucket") == bucket:
                return True
        return False
    except Exception:
        return None


# ---------------------------------------------------------------- 授权门
def maybe_offer_grant(app: Any, *, kind: str, domain: str = "",
                      now: Optional[float] = None) -> Optional[Any]:
    """某桶新添了一次命中后调:达授权门 → 出授权卡(同桶挂着/已授权/冷却中不重复)。

    判定链(docs/52 §2):硬排除 → 评估水位(每满一批才看一眼,治 peeking)→
    n ≥ SILENCE_MIN_N → Wilson 95% 下界 ≥ 0.90 → 判别力(押 REJECT 且中 ≥2)。
    返回出的卡(或 None)。全链任何一环失败 → None(没有授权就没有静音,保守)。
    """
    k = (kind or "").strip()
    if not k or k in HIGH_RISK_KINDS or irreversible_semantics(k):
        return None
    try:
        from karvyloop.crystallize.taste_eval import SKIP_KINDS
        if k in SKIP_KINDS:
            return None
    except Exception:
        return None
    n_ts = now if now is not None else time.time()
    d = _norm_domain(domain)
    b = bucket_key(k, d)
    store = get_store(app)
    if store.expired_unrevoked(k, d, now=n_ts) is not None:
        # 到期未续的授权:走续期卡(带对账数据),不当新桶重新推销
        return maybe_offer_renewal(app, kind=k, domain=d, now=n_ts)
    if store.is_granted(k, d, now=n_ts) or store.offer_recently(b, now=n_ts):
        return None
    pending = _same_bucket_grant_pending(app, b)
    if pending is None or pending:
        return None   # 同桶卡已挂着 / registry 读不到 → 不重复/保守不出
    stats = bucket_stats(app, min_ts=store.last_revoked_at(b))   # 吊销过 → 只认新鲜证据
    st = stats.get(b)
    if not st:
        return None
    # 评估水位(固定批次窗口):上次评估后新增 < SILENCE_EVAL_BATCH_N 不看门 —— 每来一个
    # 样本试一次 = 序贯凑连击。过/不过都消耗这一批(note_eval),下一批再看。
    mark = store.eval_mark(b)
    if st["n"] < mark:
        store.note_eval(b, st["n"])   # 账本留存截断导致 n 回缩 → 重锚水位,本次不评
        return None
    if st["n"] - mark < SILENCE_EVAL_BATCH_N:
        return None
    store.note_eval(b, st["n"])
    if st["n"] < SILENCE_MIN_N:
        return None
    lb = wilson_lower_bound(st["hits"], st["n"])
    if lb < SILENCE_MIN_WILSON_LB:
        return None
    # 修正③ 分向达标(批/拒各自过线):整体达标可能是混合幻觉 —— 两向单独验。
    accept_lb = wilson_lower_bound(st.get("accept_hits", 0), st.get("accept_n", 0))
    reject_lb = wilson_lower_bound(st.get("reject_hits", 0), st.get("reject_n", 0))
    if accept_lb < SILENCE_MIN_WILSON_LB:
        # ACCEPT 向是会被自动执行的方向,必须自己扛满 0.90 的线(不许靠拒向样本撑整体)
        logger.info("[silence] 桶 %s 整体下界 %.3f 达标但 ACCEPT 向仅 %.3f(%s/%s)"
                    "→ 不出授权卡", b, lb, accept_lb,
                    st.get("accept_hits", 0), st.get("accept_n", 0))
        return None
    if st.get("reject_correct", 0) < SILENCE_MIN_REJECT_CORRECT or \
            reject_lb < SILENCE_MIN_REJECT_LB:
        # 杀常数策略及其带噪变体:押 REJECT 的证据要够数、且拒向准确率 95% 置信优于抛硬币
        # (只数"押中 ≥2"会被"瞎押 10 条 REJECT 碰中 2 条"骗过)。诚实不出卡。
        logger.info("[silence] 桶 %s 整体下界 %.3f 达标但判别力不足"
                    "(押 REJECT %s 次中 %s 次,拒向下界 %.3f < %.2f)→ 不出授权卡",
                    b, lb, st.get("reject_n", 0), st.get("reject_correct", 0),
                    reject_lb, SILENCE_MIN_REJECT_LB)
        return None
    card = proposal_for_silence_grant(kind=k, domain=d, n=st["n"], hits=st["hits"],
                                      ts=n_ts, wilson_lb=lb,
                                      reject_correct=st.get("reject_correct", 0),
                                      accept_n=st.get("accept_n", 0),
                                      accept_hits=st.get("accept_hits", 0),
                                      reject_n=st.get("reject_n", 0))
    store.note_offer(b, now=n_ts)
    _deliver(app, card)
    logger.info("[silence] 桶 %s 达授权门(n=%s, wilson_lb=%.3f, reject_correct=%s)"
                "→ 出静音授权卡", b, st["n"], lb, st.get("reject_correct", 0))
    return card


def maybe_offer_renewal(app: Any, *, kind: str, domain: str = "",
                        now: Optional[float] = None) -> Optional[Any]:
    """授权到期未吊销 → 出续期卡(带上月对账数据,复用 monthly_reconciliation)。
    同桶挂着/冷却中不重复。返回出的卡(或 None)。"""
    k = (kind or "").strip()
    n_ts = now if now is not None else time.time()
    d = _norm_domain(domain)
    b = bucket_key(k, d)
    store = get_store(app)
    g = store.expired_unrevoked(k, d, now=n_ts)
    if g is None or store.offer_recently(b, now=n_ts):
        return None
    pending = _same_bucket_grant_pending(app, b)
    if pending is None or pending:
        return None
    granted_at = float(g.get("granted_at") or 0.0)
    recon = monthly_reconciliation(app, days=30, now=n_ts)
    bstat = recon["by_bucket"].get(b, {"silenced": 0, "overturned": 0})
    # 抽查对账 = 授权期内该桶照常出卡并被拍板的样本(抽查/回退的卡都会开奖进对账流)
    audit = bucket_stats(app, min_ts=granted_at).get(b) or {"n": 0, "hits": 0}
    oldest_pid = ""
    for it in read_ledger(app):
        if _ledger_bucket(it) == b and _ledger_ts(it) >= granted_at:
            oldest_pid = str(it.get("proposal_id") or "")
            break
    # 修正⑤:风险加权抽 ≤5 条本期留痕摆上卡(翻案>失败>最贵>新近),逐条复核完毕才续
    try:
        review = risk_weighted_review_items(app, b, since=granted_at, k=5)
    except Exception as e:
        logger.warning("[silence] 续期复核抽样失败(续期卡带空样本,对账计数仍在): %s", e)
        review = []
    card = proposal_for_silence_renewal(
        kind=k, domain=d, granted_at=granted_at, silenced_n=int(bstat.get("silenced", 0)),
        audit_n=int(audit.get("n", 0)), audit_hits=int(audit.get("hits", 0)),
        oldest_pid=oldest_pid, ts=n_ts, review_items=review)
    store.note_offer(b, now=n_ts)
    _deliver(app, card)
    logger.info("[silence] 桶 %s 授权到期 → 出续期卡(静音 %s 次/对账 %s 次)",
                b, bstat.get("silenced", 0), audit.get("n", 0))
    return card


def on_outcome(app: Any, *, proposal_id: str, kind: str, domain: str = "",
               hit: Optional[bool], now: Optional[float] = None) -> None:
    """拍板对账后的控制器钩子(decision_wire 段3b 调,单一接缝)。

    - 押错(hit=False)且该桶有授权(活跃或到期未吊销)→ **立即吊销** + 出告知卡
      (押错一次都不容忍;到期未吊销的也吊 —— 押错的桶不配走续期)。
    - 押中(hit=True)→ 看这次是否把桶推过授权门(受评估水位约束,不是每次都看)。
    hit=None(没押过注)→ 不动。
    """
    if hit is None or not (kind or "").strip():
        return
    n_ts = now if now is not None else time.time()
    d = _norm_domain(domain)
    b = bucket_key(kind, d)
    if hit is False:
        store = get_store(app)
        if store.is_granted(kind, d, now=n_ts) or \
                store.expired_unrevoked(kind, d, now=n_ts) is not None:
            store.revoke(b, reason=f"押错(proposal {proposal_id})", now=n_ts)
            _deliver(app, proposal_for_silence_revoked(
                kind=kind, domain=d, ts=n_ts, reason="这次我押错了你的拍板"))
            logger.info("[silence] 桶 %s 押错 → 自动吊销静音授权", b)
        return
    maybe_offer_grant(app, kind=kind, domain=d, now=n_ts)


# ---------------------------------------------------------------- 红线:高价值 / Cut2 违背
def _applicable_prefs(app: Any, proposal: Any) -> list:
    """本卡适用的已结晶决策偏好(与 decision_card_wire._recall_aligned_prefs 同口径:
    applicable_decision_prefs 按 payload 域/角色过滤 + 相关性排序)。异常上抛,调用方 fail-closed。"""
    mem = getattr(app.state, "memory", None)
    if mem is None:
        return []   # 无记忆 = 无已结晶标准(不是失明,是真没有)
    from karvyloop.crystallize.decision_pref import applicable_decision_prefs
    beliefs: list = []
    for sc in ("personal", "domain"):
        beliefs.extend(mem.index.all(sc))
    payload = getattr(proposal, "payload", {}) or {}
    query = " ".join([getattr(proposal, "summary", "") or ""]
                     + [str(payload.get(k, "")) for k in ("requirement", "topic", "intent")]).strip()
    return applicable_decision_prefs(
        beliefs, query=query,
        domain=str(payload.get("domain_id") or payload.get("group_domain_id") or ""),
        role=str(payload.get("role") or ""))


def _high_value_pref_blocks(app: Any, proposal: Any) -> bool:
    """红线①(确定性半):这张卡如果走建卡路径会不会被标 high_value —— 命中你已结晶的
    **高价值**决策偏好(is_high_value,strength ≥0.7)= 「这类该你拍的」,永不静音。

    比建卡逻辑更保守:建卡只看相关性前 5 条,这里看**全部适用条**(超集;静音是没有人
    兜底的路径,红线只许更宽不许更窄)。读不出(异常)→ True(fail-closed:红线失明时不放行)。
    """
    try:
        from karvyloop.crystallize.decision_pref import is_high_value
        return any(is_high_value(b) for b in _applicable_prefs(app, proposal))
    except Exception as e:
        logger.warning("[silence] 高价值偏好检查失败 → 不静音(fail-closed): %s", e)
        return True


async def _cut2_blocks(app: Any, proposal: Any) -> bool:
    """红线②(LLM 半,Cut2 违背即拦):对这张待静音的卡跑与建卡守线**同一个** check_violations
    (标准 = 适用偏好按相关性前 5 条,与 decision_card_wire 建卡口径一致)。

    方向翻转:建卡路径检查失败返 [](放 —— 人还会看到卡,人兜底);静音路径没有人兜底,
    所以走 strict(失败=抛)且本函数把**任何异常当拦**。有违背 / 查不了 → True(回人工)。
    """
    try:
        prefs = _applicable_prefs(app, proposal)[:5]   # 建卡同口径:相关性前 5 条上守线
        if not prefs:
            return False   # 没有适用标准 → 无线可违背
        from karvyloop.console.decision_card_wire import check_violations
        from karvyloop.llm.token_ledger import token_source
        payload = getattr(proposal, "payload", {}) or {}
        text = " ".join([getattr(proposal, "summary", "") or "",
                         getattr(proposal, "basis", "") or ""]
                        + [str(payload.get(k, "")) for k in ("requirement", "topic", "intent")]).strip()
        with token_source("silence_cut2"):
            violations = await check_violations(app, text, [b.content for b in prefs], strict=True)
        if violations:
            logger.info("[silence] 卡 %s 违背已定标准(%s 条)→ 不静音,回人工出卡",
                        getattr(proposal, "proposal_id", ""), len(violations))
            return True
        return False
    except Exception as e:
        logger.warning("[silence] Cut2 守线检查失败 → 不静音(fail-closed): %s", e)
        return True


# ---------------------------------------------------------------- 静音拦截(register 咽喉)
def try_silence(app: Any, proposal: Any) -> bool:
    """broadcast_proposal 顶部调:已授权桶的卡 → 走静音路径(不进待决表、不推卡)。

    返回 True = 已接管(caller 直接返回);False = 走正常路径。**判定链任何一环不满足都
    返回 False**(高危 kind / 不可逆语义 / 未授权 / 已到期 / 无兑现 handler / 无 LLM /
    命中高价值标准[红线①] / 单日次数超顶 / 爆炸半径超顶 / 抽中随机抽查 / 无事件循环
    —— 宁可打扰)。接管后 _silent_handle 里还有红线②(Cut2 违背即拦,strict)与置信门。
    """
    kind = (getattr(proposal, "kind", "") or "").strip()
    pid = getattr(proposal, "proposal_id", "") or ""
    if not kind or not pid or kind in HIGH_RISK_KINDS:
        return False   # 自指防护:silence_grant/silence_revoked 在 HIGH_RISK_KINDS,永不被静音
    try:
        from karvyloop.crystallize.taste_eval import SKIP_KINDS
        if kind in SKIP_KINDS:
            return False
    except Exception:
        return False
    # 修正①:单卡不可逆语义(外发/删除/支付/生产写)—— 桶战绩再好,这张卡也回人工
    sem = irreversible_semantics(kind, getattr(proposal, "payload", None),
                                 getattr(proposal, "summary", "") or "")
    if sem:
        logger.info("[silence] 卡 %s 蕴含不可逆语义(%s)→ 永不静音,照常出卡", pid, sem)
        return False
    dom = _proposal_domain(proposal)
    try:
        store = get_store(app)
        # 修正⑤:授权到期 → 回正常出卡 + 出续期卡(人 ACCEPT 才续)
        if store.expired_unrevoked(kind, dom) is not None:
            try:
                maybe_offer_renewal(app, kind=kind, domain=dom)
            except Exception as e:
                logger.warning("[silence] 出续期卡失败(卡照常回人工): %s", e)
            return False
        if not store.is_granted(kind, dom):
            return False
    except Exception:
        return False
    handlers = getattr(app.state, "proposal_handlers", None) or {}
    if handlers.get(kind) is None:
        return False   # 没真兑现能力 → 静音等于吞卡,绝不
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    if rk.get("gateway") is None:
        return False   # 无 LLM 无预测 → 不静音
    b = bucket_key(kind, dom)
    # 红线①:命中你已结晶的高价值标准的卡 = 建卡路径会标 high_value 的卡 —— 永不静音
    # (确定性检查,查不出也不放行;LLM 半[Cut2 违背]在 _silent_handle 里,同样 fail-closed)
    if _high_value_pref_blocks(app, proposal):
        logger.info("[silence] 卡 %s 命中高价值决策标准 → 永不静音,照常出卡", pid)
        return False
    # 修正⑥-①:单日次数硬顶(所有 kind)—— 授权 ≠ 无限流量,滚动 24h 超顶回人工
    try:
        today_n = bucket_daily_count(app, b)
    except Exception:
        today_n = SILENCE_DAILY_MAX   # 数不出来当到顶(硬顶失明时不放行,保守)
    if today_n >= SILENCE_DAILY_MAX:
        logger.info("[silence] 桶 %s 滚动 24h 已静音 %s 次(顶 %s)→ 回人工",
                    b, today_n, SILENCE_DAILY_MAX)
        return False
    # 修正⑥-②:爆炸半径成本顶 —— 执行类 kind 查该桶近期平均成本,超顶回人工
    if kind in SILENCE_EXEC_KINDS:
        try:
            avg = bucket_recent_avg_cost(app, b)
        except Exception:
            avg = float("inf")   # 算不出成本 → 当超顶(硬顶失明时不放行,保守)
        if avg > SILENCE_COST_CAP_TOKENS:
            logger.info("[silence] 桶 %s 近期平均成本 %.0f token 超硬顶 %s → 回人工",
                        b, avg, SILENCE_COST_CAP_TOKENS)
            return False
    # 修正④:不告知随机抽查 —— 抽中的卡照常出卡(**不标注**),开奖照常进对账流
    if _should_audit():
        logger.debug("[silence] 桶 %s 卡 %s 抽中随机抽查 → 照常出卡", b, pid)
        return False
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return False   # 同步上下文起不了静音任务 → 正常出卡
    _track_task(app, loop.create_task(_silent_handle(app, proposal)))
    logger.info("[silence] 已授权桶 %s → 卡 %s 走静音路径", b, pid)
    return True


async def _predict_for_silence(app: Any, proposal: Any) -> Optional[tuple]:
    """静音路径的口味预测(与押注同一模型/同一 prealign 口径;token 记 silence_predict)。
    模块级函数 → 测试可注入。失败返 None(→ 回正常路径)。"""
    rk = getattr(app.state, "runtime_kwargs", None) or {}
    gw = rk.get("gateway")
    if gw is None:
        return None
    from karvyloop.crystallize.decision_pref import is_decision_pref, prealign_block
    from karvyloop.crystallize.taste_eval import predict_decision
    from karvyloop.llm.token_ledger import token_source
    prefs_block = ""
    mem = getattr(app.state, "memory", None)
    if mem is not None:
        try:
            beliefs = [b for sc in ("personal", "domain") for b in mem.index.all(sc)
                       if is_decision_pref(b)]
            prefs_block = prealign_block(beliefs, query=getattr(proposal, "summary", "") or "")
        except Exception:
            prefs_block = ""
    with token_source("silence_predict"):
        return await predict_decision(
            gw, rk.get("model_ref", "") or "",
            summary=getattr(proposal, "summary", "") or "",
            basis=getattr(proposal, "basis", "") or "",
            kind=getattr(proposal, "kind", "") or "",
            prefs_block=prefs_block)


async def _fallback(app: Any, proposal: Any) -> None:
    """静音条件不满足 → 回正常路径出卡(allow_silence=False 防递归)。最后防线:回退也失败
    → 直接登记待决表,**绝不让卡蒸发**(早返回不留痕=决策 loop 塌的老病根)。"""
    try:
        from karvyloop.console.proposals import broadcast_proposal
        await broadcast_proposal(app, proposal, allow_silence=False)
    except Exception as e:
        logger.warning("[silence] 回退广播失败,直接登记待决: %s", e)
        reg = getattr(app.state, "proposal_registry", None)
        if reg is not None:
            try:
                reg.register(proposal)
            except Exception:
                pass


async def _silent_handle(app: Any, proposal: Any) -> None:
    """静音路径主体:预测 → (只 ACCEPT 向且够置信)自动兑现 → 完整留痕 + WS 轻通知。
    兑现裹 token_task("silenced:<pid>") —— 爆炸半径硬顶(修正⑥)按它归因每次静音的成本。"""
    pid = getattr(proposal, "proposal_id", "") or ""
    kind = (getattr(proposal, "kind", "") or "").strip()
    dom = _proposal_domain(proposal)
    got = None
    try:
        got = await _predict_for_silence(app, proposal)
    except Exception as e:
        logger.debug("[silence] 预测失败(回正常路径): %s", e)
    dec, conf = (got if got else ("", 0.0))
    if dec != "ACCEPT" or float(conf) < SILENCE_MIN_CONFIDENCE:
        # **只静音 ACCEPT 向**:预测 REJECT / 置信不足 / 预测失败 → 出卡问人(宁可打扰绝不错办)
        await _fallback(app, proposal)
        return
    # 红线②(Cut2 违背即拦):真兑现前跑建卡同款守线 —— 违背你已定的标准 / 守线查不了
    # → 回人工出卡(人路径的卡也会带 violations 红条,链路不受静音影响)。
    if await _cut2_blocks(app, proposal):
        await _fallback(app, proposal)
        return
    handlers = getattr(app.state, "proposal_handlers", None) or {}
    if handlers.get(kind) is None:   # try_silence 查过;授权/接线在 await 间隙变了也兜住
        await _fallback(app, proposal)
        return
    # 单日顶的进程内账(与台账 1:1;台账坏文件时硬顶靠这本不失明)
    note_daily_mark(app, bucket_key(kind, dom))
    from karvyloop.karvy.proposal_registry import dispatch_accept
    tid = f"silenced:{pid}"
    cost = 0
    try:
        from karvyloop.llm.token_ledger import get_ledger, token_task
        with token_task(tid):   # contextvar 跨 to_thread 传播 → 兑现烧的 token 归到这次静音
            res = await asyncio.to_thread(dispatch_accept, proposal, handlers)
        ok, detail = bool(res.ok), str(res.detail)
        led = get_ledger()
        if led is not None:
            try:
                cost = int(led.task_total(tid))
            except Exception:
                cost = 0
    except Exception as e:
        ok, detail = False, f"dispatch error: {e}"
    entry = {
        "ts": time.time(), "proposal_id": pid, "kind": kind, "domain": dom,
        "bucket": bucket_key(kind, dom),
        "summary": (getattr(proposal, "summary", "") or "")[:200],
        "predicted": "ACCEPT", "confidence": float(conf),
        "ok": ok, "detail": detail[:400], "overturned": False,
        "token_task_id": tid, "cost_tokens": cost,
    }
    record_silenced(app, entry)     # ① 台账(折叠区/月度对账/续期卡/成本顶,跨重启)
    _trace_silenced(app, entry)     # ② Trace kind=silenced_decision(所有评价的唯一数据源)
    try:
        await _ws_notify(app, {"type": WS_TYPE_SILENCE_NOTICE, "payload": dict(entry)})  # ③ 轻通知
    except Exception as e:
        logger.debug("[silence] WS 轻通知失败(留痕已落): %s", e)
    if not ok:
        # 兑现失败:**不重发原卡**(handler 可能已部分执行,重发→用户 ACCEPT=二次执行);
        # fail-loud 推系统错误,让人看见(§0.7 灭静默死角)。
        try:
            from karvyloop.console.task_events import schedule_system_error
            schedule_system_error(app, "silence",
                                  f"静音兑现失败({kind}/{pid}):{detail[:160]}")
        except Exception:
            pass


def _trace_silenced(app: Any, entry: dict) -> None:
    try:
        trace = getattr(getattr(app.state, "main_loop", None), "trace", None)
        if trace is None:
            return
        from karvyloop.cognition.trace import TraceEntry
        trace.append(TraceEntry(task_id=entry["proposal_id"], kind="silenced_decision",
                                payload=dict(entry), agent="karvy", source="silence"))
    except Exception as e:
        logger.warning("[silence] 落 Trace 失败(台账已落,不阻断): %s", e)


async def _ws_notify(app: Any, message: dict) -> int:
    clients = getattr(app.state, "ws_clients", None)
    if not clients:
        return 0
    sent = 0
    dead: list = []
    for ws in list(clients):
        try:
            await ws.send_json(message)
            sent += 1
        except Exception:
            dead.append(ws)
    for ws in dead:
        clients.discard(ws)
    return sent


# ---------------------------------------------------------------- 撤销 / 翻案 / 月度对账
def revoke_grant(app: Any, bucket: str, *, reason: str = "user",
                 now: Optional[float] = None) -> bool:
    """撤销某桶的静音授权(用户主动撤 / 上层接口用)。没有可撤的授权 → False。"""
    return get_store(app).revoke(bucket, reason=reason, now=now)


def overturn_silenced(app: Any, proposal_id: str, *, now: Optional[float] = None) -> Optional[dict]:
    """翻案:推翻一条已静音处理的决定(最强负信号)→ 台账标记 + **吊销该桶授权** + 出告知卡。
    返回被翻案的台账条目(找不到/已翻过 → None)。"""
    n_ts = now if now is not None else time.time()
    items = read_ledger(app)
    target = None
    for it in reversed(items):
        if it.get("proposal_id") == proposal_id and not it.get("overturned"):
            it["overturned"] = True
            it["overturned_ts"] = n_ts
            target = it
            break
    if target is None:
        return None
    try:
        _write_ledger(app, items)
    except Exception as e:
        logger.warning("[silence] 翻案标记落盘失败: %s", e)
    b = target.get("bucket") or bucket_key(target.get("kind", ""), target.get("domain", ""))
    if get_store(app).revoke(b, reason=f"翻案(proposal {proposal_id})", now=n_ts):
        _deliver(app, proposal_for_silence_revoked(
            kind=target.get("kind", ""), domain=target.get("domain", ""),
            ts=n_ts, reason="你推翻了一次静音处理"))
    return dict(target)


def monthly_reconciliation(app: Any, *, days: int = 30, now: Optional[float] = None) -> dict:
    """月度对账数据(digest / 续期卡用):"这个月替你挡掉了 N 次打扰,你翻案 M 次"。纯查表,零 LLM。"""
    n_ts = now if now is not None else time.time()
    since = n_ts - days * 86400
    items = [it for it in read_ledger(app) if float(it.get("ts", 0.0) or 0.0) >= since]
    by_bucket: dict[str, dict] = {}
    for it in items:
        b = it.get("bucket", "") or bucket_key(it.get("kind", ""), it.get("domain", ""))
        d = by_bucket.setdefault(b, {"silenced": 0, "overturned": 0})
        d["silenced"] += 1
        if it.get("overturned"):
            d["overturned"] += 1
    return {
        "days": days, "since_ts": since,
        "silenced_n": len(items),
        "overturned_n": sum(1 for it in items if it.get("overturned")),
        "failed_n": sum(1 for it in items if not it.get("ok", True)),
        "by_bucket": by_bucket,
        "active_grants": get_store(app).active_grants(now=n_ts),
    }


__all__ = [
    "KIND_SILENCE_GRANT", "KIND_SILENCE_REVOKED",
    "WILSON_Z", "SILENCE_MIN_WILSON_LB", "SILENCE_MIN_N", "SILENCE_EVAL_BATCH_N",
    "SILENCE_MIN_REJECT_CORRECT", "SILENCE_MIN_REJECT_LB", "SILENCE_MIN_CONFIDENCE",
    "SILENCE_AUDIT_RATE", "SILENCE_GRANT_TTL_S",
    "SILENCE_DAILY_MAX", "SILENCE_DAILY_WINDOW_S",
    "SILENCE_EXEC_KINDS", "SILENCE_COST_CAP_TOKENS", "SILENCE_COST_WINDOW",
    "OFFER_COOLDOWN_S", "HIGH_RISK_KINDS", "WS_TYPE_SILENCE_NOTICE",
    "wilson_lower_bound", "irreversible_semantics",
    "bucket_key", "bucket_stats", "bucket_recent_avg_cost",
    "bucket_daily_count", "note_daily_mark", "risk_weighted_review_items",
    "SilenceGrantStore", "get_store",
    "read_ledger", "record_silenced",
    "proposal_for_silence_grant", "proposal_for_silence_renewal",
    "proposal_for_silence_revoked",
    "maybe_offer_grant", "maybe_offer_renewal", "on_outcome", "try_silence",
    "revoke_grant", "overturn_silenced", "monthly_reconciliation",
]
