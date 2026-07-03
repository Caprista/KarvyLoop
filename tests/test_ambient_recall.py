"""test_ambient_recall — ⑤c 工作台环境感知召回(karvy/ambient.py + console/ws.py 集成)。

AC:
- AC1 技能命中:真 SKILL.md 进候选,相关 intent 召回、无关不召回
- AC2 知识命中:MemoryManager 里的 Belief 同理(含域隔离口径)
- AC3 冷却:同 intent 指纹 2 次只推 1 次(换说法=同指纹也冷却;过窗恢复)
- AC4 阈值:低分/弱共享静默(宁静默勿噪音);上限 ≤3
- AC5 零 LLM:打桩 gateway,ambient 路径 0 调用
- AC6 WS 广播:intent 到达 → 广播 `ambient_recall`,payload 形状 {hits, for_intent}
"""
from __future__ import annotations

import pathlib
import sys
import time

import pytest
from fastapi.testclient import TestClient

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.karvy.ambient import (  # noqa: E402
    AmbientCooldown, ambient_recall, intent_fingerprint,
)
from karvyloop.cognition.memory import MemoryManager  # noqa: E402
from karvyloop.schemas import Belief  # noqa: E402


# ---- fixtures ----

def _write_skill(skills_dir: pathlib.Path, name: str, *, description: str,
                 when_to_use: str, scope: str = "user", tags: str = "") -> None:
    d = skills_dir / name
    d.mkdir(parents=True)
    tags_line = f"tags: [{tags}]\n" if tags else ""
    (d / "SKILL.md").write_text(
        "---\n"
        f"name: {name}\n"
        f"description: {description}\n"
        f"when_to_use: {when_to_use}\n"
        f"scope: {scope}\n"
        "signature: cafe0000cafe0000\n"
        f"{tags_line}"
        "---\n"
        "## Steps\n1. do it\n",
        encoding="utf-8",
    )


@pytest.fixture
def skills_dir(tmp_path: pathlib.Path) -> pathlib.Path:
    d = tmp_path / "skills"
    _write_skill(d, "weekly-report",
                 description="汇总本周工作生成周报文档",
                 when_to_use="用户要汇总本周工作或写周报时",
                 tags="周报, 汇总")
    _write_skill(d, "flight-booking",
                 description="查询并预订航班机票",
                 when_to_use="用户要订机票/查航班时")
    return d


def _belief(content: str, *, domain: str = "", bid: str = "") -> Belief:
    prov: dict = {"source": "test", "ts": time.time()}
    if bid:
        prov["id"] = bid
    if domain:
        prov["applies"] = {"domain": domain}
    return Belief(content=content, provenance=prov,
                  freshness_ts=time.time(), scope="personal")


@pytest.fixture
def memory() -> MemoryManager:
    mem = MemoryManager()
    mem.write(_belief("季度复盘报告的模板放在共享盘 templates 目录", bid="b-tpl"))
    mem.write(_belief("咖啡豆偏好:浅烘埃塞俄比亚", bid="b-coffee"))
    return mem


# ---- AC1 技能命中 ----

class TestSkillHits:
    def test_related_intent_recalls_skill(self, skills_dir):
        hits = ambient_recall("帮我汇总本周工作写个周报", skills_dir=skills_dir)
        names = [h.name for h in hits]
        assert "weekly-report" in names
        h = next(h for h in hits if h.name == "weekly-report")
        assert h.kind == "skill" and h.id == "weekly-report"
        assert 0.0 < h.score <= 1.0
        assert h.summary  # 一句话非空
        # 无关技能不混进来
        assert "flight-booking" not in names

    def test_unrelated_intent_is_silent(self, skills_dir):
        assert ambient_recall("推荐一部科幻电影", skills_dir=skills_dir) == []

    def test_scope_filter(self, skills_dir):
        # 技能是 user scope;domain 场里不该浮出(场作用域隔离,同 crystallize recall 口径)
        assert ambient_recall("帮我汇总本周工作写个周报", skills_dir=skills_dir,
                              skill_scope="domain") == []

    def test_skill_index_path(self, skills_dir):
        # SkillIndex 在场时走索引(不扫盘);手工 register 一条验证同样命中
        from karvyloop.crystallize.skill_index import SkillIndex
        idx = SkillIndex()
        idx.register(name="weekly-report", sig="cafe0000cafe0000", scope="user",
                     when_to_use="用户要汇总本周工作或写周报时",
                     description="汇总本周工作生成周报文档",
                     path=str(skills_dir / "weekly-report" / "SKILL.md"))
        hits = ambient_recall("帮我汇总本周工作写个周报", skill_index=idx)
        assert [h.name for h in hits] == ["weekly-report"]


# ---- AC2 知识命中 ----

class TestBeliefHits:
    def test_related_intent_recalls_belief(self, memory):
        hits = ambient_recall("季度复盘报告该用哪个模板", memory=memory)
        assert len(hits) == 1
        h = hits[0]
        assert h.kind == "belief" and h.id == "b-tpl"
        assert "复盘" in h.summary

    def test_unrelated_intent_is_silent(self, memory):
        assert ambient_recall("明天天气怎么样呢", memory=memory) == []

    def test_domain_private_belief_stays_in_domain(self, memory):
        # §2.6:带 applies.domain 的私有认知只在本域浮出(与 recall_block 同口径)
        memory.write(_belief("季度复盘报告要抄送法务部门审阅", domain="dom-legal", bid="b-legal"))
        ids_global = {h.id for h in ambient_recall("季度复盘报告该用哪个模板", memory=memory)}
        assert "b-legal" not in ids_global          # 私聊(domain="")看不到域私有
        ids_in = {h.id for h in ambient_recall("季度复盘报告该用哪个模板", memory=memory,
                                               domain="dom-legal")}
        assert {"b-tpl", "b-legal"} <= ids_in       # 本域=共享层+本域私有

    def test_mixed_skill_and_belief_sorted_by_score(self, skills_dir, memory):
        memory.write(_belief("写周报要先汇总本周工作再发团队邮件组", bid="b-mail"))
        hits = ambient_recall("帮我汇总本周工作写个周报", skills_dir=skills_dir, memory=memory)
        assert hits and {h.kind for h in hits} == {"skill", "belief"}
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)


# ---- AC3 冷却 ----

class TestCooldown:
    def test_same_fingerprint_pushes_once(self, skills_dir):
        cd = AmbientCooldown(ttl_s=600)
        first = ambient_recall("帮我汇总本周工作写个周报", skills_dir=skills_dir,
                               cooldown=cd, now=1000.0)
        assert first
        second = ambient_recall("帮我汇总本周工作写个周报", skills_dir=skills_dir,
                                cooldown=cd, now=1010.0)
        assert second == []                          # 10 分钟窗口内同指纹 → 静默
        third = ambient_recall("帮我汇总本周工作写个周报", skills_dir=skills_dir,
                               cooldown=cd, now=1000.0 + 601.0)
        assert third                                 # 过窗恢复

    def test_silence_does_not_burn_cooldown(self, skills_dir):
        # 空结果不占冷却窗:先问无关的(静默),马上问相关的仍然推
        cd = AmbientCooldown(ttl_s=600)
        assert ambient_recall("推荐一部科幻电影", skills_dir=skills_dir,
                              cooldown=cd, now=1000.0) == []
        assert ambient_recall("帮我汇总本周工作写个周报", skills_dir=skills_dir,
                              cooldown=cd, now=1001.0)

    def test_fingerprint_normalizes_rephrasing(self):
        # _intent_cluster 归一(停用词/同义词):"请 帮我 总结…"与"汇报…"落同一指纹
        assert intent_fingerprint("请 帮我 总结 本周 工作") == \
               intent_fingerprint("汇报 本周 工作")
        assert intent_fingerprint("订机票") != intent_fingerprint("写周报")


# ---- AC4 阈值 + 上限 ----

class TestThresholdAndCap:
    def test_weak_overlap_is_silent(self, memory):
        # 只共享 1 个 bigram("报告")→ 低于 min_shared=2 → 静默
        memory.write(_belief("报告", bid="b-one"))
        hits = ambient_recall("把那份东西整理成报告发我", memory=memory)
        assert all(h.id != "b-one" for h in hits)

    def test_low_ratio_is_silent(self, memory):
        # 长 query 只碰到候选一点点(归一化分 < 0.25)→ 静默
        long_q = ("请你把这份很长很长的会议纪要按照议题逐条整理归档"
                  "并把其中提到的季度复盘两个字标出来")
        hits = ambient_recall(long_q, memory=memory)
        assert hits == []

    def test_cap_at_three(self):
        mem = MemoryManager()
        for i in range(5):
            mem.write(_belief(f"季度复盘报告模板第{i}版", bid=f"b{i}"))
        hits = ambient_recall("季度复盘报告模板", memory=mem)
        assert 0 < len(hits) <= 3

    def test_empty_query_is_silent(self, memory):
        assert ambient_recall("", memory=memory) == []
        assert ambient_recall("   ", memory=memory) == []


# ---- AC5 零 LLM ----

class TestZeroLlm:
    def test_ambient_path_never_calls_gateway(self, skills_dir, memory, monkeypatch):
        """硬契约:环境召回本身零 LLM 调用 —— 打桩 gateway.complete,全路径跑一遍必须 0 命中。"""
        calls: list = []

        async def _spy(self, *a, **kw):
            calls.append((a, kw))
            raise AssertionError("ambient 路径不许调 LLM")

        from karvyloop.gateway.client import GatewayClient
        monkeypatch.setattr(GatewayClient, "complete", _spy)

        cd = AmbientCooldown()
        hits = ambient_recall("帮我汇总本周工作写个周报", skills_dir=skills_dir,
                              memory=memory, cooldown=cd)
        assert hits                     # 真跑了召回路径(不是空转)
        assert calls == []              # 0 次 LLM 调用


# ---- AC6 WS 集成:广播形状 ----

class TestWsBroadcast:
    def _app(self, memory):
        from karvyloop.console import build_console_app
        from karvyloop.karvy.observer import WorkbenchObserver
        app = build_console_app(workbench=WorkbenchObserver(), main_loop=None)
        app.state.memory = memory
        return app

    def test_intent_triggers_ambient_recall_broadcast(self, memory):
        client = TestClient(self._app(memory))
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()   # snapshot
            ws.send_json({"type": "intent",
                          "payload": {"intent": "季度复盘报告该用哪个模板"}})
            got = {}
            for _ in range(2):  # drive_done(stub)与 ambient_recall 顺序不保证
                msg = ws.receive_json()
                got[msg["type"]] = msg
            assert "drive_done" in got          # 不挡 drive:回答照常
            assert "ambient_recall" in got
            payload = got["ambient_recall"]["payload"]
            # payload 契约:{hits: [{kind,id,name,summary,score}], for_intent}
            assert payload["for_intent"] == "季度复盘报告该用哪个模板"
            assert isinstance(payload["hits"], list) and payload["hits"]
            hit = payload["hits"][0]
            assert set(hit) == {"kind", "id", "name", "summary", "score"}
            assert hit["kind"] in ("skill", "belief")
            assert isinstance(hit["score"], float)

    def test_unrelated_intent_no_broadcast(self, memory):
        """无命中 → 不广播(静默;只有 drive_done)。"""
        client = TestClient(self._app(memory))
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "intent", "payload": {"intent": "推荐一部科幻电影"}})
            msg = ws.receive_json()
            assert msg["type"] == "drive_done"
            # 再发一个 ping 逼一轮收发:期间若有 ambient 广播会先于 pong 到达
            ws.send_json({"type": "ping"})
            msg2 = ws.receive_json()
            assert msg2["type"] == "pong"

    def test_cooldown_across_ws_messages(self, memory):
        """同 intent 连发 2 次 → 只广播 1 次 ambient_recall(冷却表挂 app.state,进程级)。"""
        client = TestClient(self._app(memory))
        with client.websocket_connect("/ws") as ws:
            ws.receive_json()
            ws.send_json({"type": "intent",
                          "payload": {"intent": "季度复盘报告该用哪个模板"}})
            types = []
            for _ in range(2):
                types.append(ws.receive_json()["type"])
            assert sorted(types) == ["ambient_recall", "drive_done"]
            # 第二次:只该有 drive_done + pong,不再有 ambient_recall
            ws.send_json({"type": "intent",
                          "payload": {"intent": "季度复盘报告该用哪个模板"}})
            msg = ws.receive_json()
            assert msg["type"] == "drive_done"
            ws.send_json({"type": "ping"})
            assert ws.receive_json()["type"] == "pong"
