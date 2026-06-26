"""test_crystallize_cluster — token-overlap 累积聚类(修"换说法不结晶",拍 9.4-门2).

门1 真机:同任务 6 种说法 → 6 签名各 usage=1 → 永不结晶(M1 生死线)。
用户拍 token-overlap 累积(不引向量)。本拍:CJK-bigram token 重叠把同任务不同说法
归并到同一 cluster → 攒得上 → 能结晶;不同任务(只共享 python)不误并。

AC:
- AC1: intent_tokens 含 CJK bigram(中文整词不重叠的修复)
- AC2: overlap_score 同任务高、不同任务低
- AC3: match_cluster 阈值行为(达标归并 / 不达标开新 / threshold<=0 关闭)
- AC4: observe(cluster_threshold>0) 把 6 种说法累积到 1 个 cluster(usage_count=6)
- AC5: observe(cluster_threshold=0) 保持精确签名旧行为(6 说法 = 6 sig)
- AC6: 不同任务(共享 python)不被误并
- AC7: sqlite 持久化 intent_repr(跨进程聚类不丢)+ 旧库迁移补列
"""
from __future__ import annotations

from pathlib import Path

from karvyloop.crystallize.cluster import intent_tokens, match_cluster, overlap_score
from karvyloop.crystallize.observe import observe
from karvyloop.crystallize.store import InMemoryUsageStore
from karvyloop.schemas.atom import AtomRun

# 同一任务的 6 种中文说法(token-overlap 能聚:都含"平方")。
# 注:token 聚类**不跨语言** —— "square 函数" 与 "平方" 不 token-重叠(无语义层),
# 那是 token 方案的已知边界(用户已知选 token 不选向量),非 bug。
SQUARE = [
    "写一个 Python 文件计算 n 的平方",
    "帮我写个 python 脚本算平方",
    "创建一个计算平方的 python 函数",
    "用 python 实现一个求平方的功能",
    "python 算一个数的平方",
    "做个 python 平方计算器",
]


def _run(intent: str, ts: float) -> AtomRun:
    return AtomRun(atom_id="a", input={"intent": intent}, output={"text": "x"},
                   success=True, tool_calls=[], trace_ref="t", ts=ts)


# ---- AC1 ----
def test_intent_tokens_has_cjk_bigrams():
    toks = intent_tokens("计算平方")
    assert "平方" in toks  # bigram
    assert "计算" in toks


# ---- AC2 ----
def test_overlap_same_task_high_diff_low():
    a = intent_tokens(SQUARE[0])
    same = overlap_score(intent_tokens(SQUARE[5]), a)      # 平方计算器 vs 写平方
    diff = overlap_score(intent_tokens("写个 python 爬虫抓网页"), a)
    assert same > diff
    assert diff < 0.2  # 只共享 python → 低


# ---- AC3 ----
def test_match_cluster_threshold():
    existing = [("c0", SQUARE[0])]
    assert match_cluster(SQUARE[5], existing, 0.2) == "c0"     # 达标归并
    assert match_cluster("写个 python 爬虫", existing, 0.2) is None  # 不达标开新
    assert match_cluster(SQUARE[5], existing, 0.0) is None     # 关闭聚类


# ---- AC4: 6 说法累积成 1 cluster ----
def test_observe_clusters_phrasings():
    store = InMemoryUsageStore()
    for i, p in enumerate(SQUARE):
        observe([_run(p, 1000.0 + i * 100)], store, debounce_sec=0, cluster_threshold=0.2)
    clusters = list(store.all())
    assert len(clusters) == 1, f"应塌缩成 1 簇,实得 {len(clusters)}"
    assert clusters[0][1].usage_count == 6  # 6 次都累到一起 → 够结晶门槛


# ---- AC5: 关闭聚类 = 旧行为 ----
def test_observe_no_cluster_legacy():
    store = InMemoryUsageStore()
    for i, p in enumerate(SQUARE):
        observe([_run(p, 1000.0 + i * 100)], store, debounce_sec=0, cluster_threshold=0.0)
    assert len(list(store.all())) == 6  # 精确签名 → 6 个


# ---- AC6: 不同任务不误并 ----
def test_observe_diff_task_not_merged():
    store = InMemoryUsageStore()
    for i, p in enumerate(SQUARE):
        observe([_run(p, 1000.0 + i * 100)], store, debounce_sec=0, cluster_threshold=0.2)
    observe([_run("写个 python 爬虫抓取网页内容", 9999.0)], store, debounce_sec=0, cluster_threshold=0.2)
    observe([_run("帮我用 python 发一封邮件", 9999.0)], store, debounce_sec=0, cluster_threshold=0.2)
    assert len(list(store.all())) == 3  # 平方簇 + 爬虫 + 邮件


# ---- AC7: sqlite intent_repr 持久 + 旧库迁移 ----
def test_sqlite_persists_intent_repr_and_migrates(tmp_path: Path):
    import sqlite3
    from karvyloop.crystallize import SqliteUsageStore
    db = tmp_path / "u.sqlite"
    s = SqliteUsageStore(db)
    observe([_run(SQUARE[0], 1000.0)], s, debounce_sec=0, cluster_threshold=0.2)
    # 跨"进程":重开,新说法应归并到已有 cluster(读回了 intent_repr)
    s.close()
    s2 = SqliteUsageStore(db)
    observe([_run(SQUARE[5], 2000.0)], s2, debounce_sec=0, cluster_threshold=0.2)
    rows = list(s2.all())
    assert len(rows) == 1 and rows[0][1].usage_count == 2  # 归并成功
    assert rows[0][1].intent_repr  # 代表意图持久了
    s2.close()
    # 旧库(无 intent_repr 列)迁移:造旧 schema
    old = tmp_path / "old.sqlite"
    c = sqlite3.connect(str(old))
    c.execute("CREATE TABLE usage_stats (sig TEXT PRIMARY KEY, usage_count INTEGER DEFAULT 0, "
              "last_used_at REAL DEFAULT 0, success_count INTEGER DEFAULT 0, failure_count INTEGER DEFAULT 0, "
              "recall_count INTEGER DEFAULT 0, param_variants_json TEXT DEFAULT '[]', "
              "steered_by_user_json TEXT DEFAULT '[]', archived INTEGER DEFAULT 0)")
    c.execute("INSERT INTO usage_stats (sig) VALUES ('x')")
    c.commit(); c.close()
    s3 = SqliteUsageStore(old)  # 应迁移补 intent_repr 列,不崩
    cols = [r[1] for r in s3._conn.execute("PRAGMA table_info(usage_stats)").fetchall()]
    assert "intent_repr" in cols
    s3.close()
