"""test_e2e_csv_analysis — CSV 上传 → 让角色分析 的**全链缝合测**(审计 ③ MED)。

背景:recall(意图→命中 data-analyst)和 read_file(相对/绝对路径读 CSV)此前各有
**零件测**(test_data_analyst_recall.py),但「角色真分析并回答」从未**端到端**驱动过 ——
缝在零件之间的 bug 正藏在这里(方法制导没把文件路径带到 slow_brain?compose_rerun_context
吞了 [当前任务]?read_file 拿到的路径不是意图里那个?)。

本文件把整链走一遍,**只 stub LLM 决策**(slow_brain 内部"决定读哪个文件"这一步),
其余全真:
    真 MainLoop.drive → 真 recall(命中 data-analyst)→ 真 compose_rerun_context(方法制导)
    → slow_brain 从制导后的 brain_intent 里抠出 CSV 路径 → 真 ReadTool 读真 CSV
    → 产出带数字结论。

断言四条缝:
  S1 recall 真命中 data-analyst(不是别的技能、不是没命中)。
  S2 送进 slow_brain 的 brain_intent **同时**含 (a) data-analyst 的方法段(制导生效)
     和 (b) 原意图里的 CSV 路径(路径没在组装时蒸发)—— 这是最容易断的缝。
  S3 read_file 真被调用、真读到 CSV 内容(revenue 列、数值都在)。
  S4 drive 产出里带从 CSV 真算出的数字结论(不是编的)。

LLM 层(slow_brain 的"决定")用确定性 stub 代替,但**读文件走真 ReadTool + 真沙箱**,
所以这不是纯 mock:文件真被读、路径真被验、结论真从数据算。
"""
from __future__ import annotations

import asyncio
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.crystallize.skill_index import SkillIndex        # noqa: E402
from karvyloop.runtime.main_loop import MainLoop                # noqa: E402
from karvyloop.schemas.atom import AtomRun                      # noqa: E402


# ---- 真工作区 + 真 CSV(与生产同形状:workspace/data/sales.csv)----

def _mk_workspace(tmp_path):
    ws = tmp_path / "ws"
    (ws / "data").mkdir(parents=True)
    csv = ws / "data" / "sales.csv"
    # 3 个月营收,便于"最大值 / 总和 / 趋势"这类确定性结论
    csv.write_text("month,revenue\n2026-01,100\n2026-02,150\n2026-03,210\n",
                   encoding="utf-8")
    return ws, csv


def _real_read_tool(ws):
    """真 ReadTool + 真沙箱(BubblewrapSandbox.read_file 是纯 Python,全平台可测真语义)。"""
    from karvyloop.capability.token import mint
    from karvyloop.coding.filestate import FileState
    from karvyloop.coding.tools.read import ReadTool
    from karvyloop.platform.linux.bubblewrap import BubblewrapSandbox
    from karvyloop.schemas import Capability
    tok = mint("t-e2e-csv", [Capability(resource=f"fs:{ws}", ops=["read", "write"])])
    return ReadTool(BubblewrapSandbox(), FileState(), str(ws), token=tok)


def _index(user_dir) -> SkillIndex:
    """真索引:扫 bundled system_skills(data-analyst 在此)+ 用户 skills 目录。"""
    idx = SkillIndex()
    idx.rebuild_from_disk(pathlib.Path(user_dir))
    return idx


class _CsvAnalystSlowBrain:
    """代替 LLM 的确定性 slow_brain:模拟 agent 收到方法制导后的行为 ——

    1) 从 brain_intent 里抠出 CSV 路径(regex,同前端 files_panel 注入的形状);
    2) 用**真 ReadTool** 读那个文件(真沙箱、真边界校验);
    3) 从 CSV 内容算一个**确定性数字结论**(最大营收月 + 总和),塞进产出文本。

    它把每次收到的 brain_intent 存下来(测试断言"方法+路径都在里面")、把每次
    read_file 调用记下来(断言"真读了、读的就是意图里那个路径")。
    """

    def __init__(self, ws, read_tool):
        self.ws = ws
        self.read_tool = read_tool
        self.seen_brain_intents: list[str] = []
        self.read_calls: list[str] = []
        self.read_payloads: list[str] = []
        self._n = 0

    def __call__(self, brain_intent: str):
        self.seen_brain_intents.append(brain_intent)
        n = self._n
        self._n += 1

        # agent 从(制导后的)意图里抠文件路径 —— 缝在这:路径若被组装吞了,这里抠不到
        m = re.search(r"\S+\.csv", brain_intent)
        payload_text = ""
        conclusion = "无法定位数据文件"
        if m:
            rel = m.group(0)
            self.read_calls.append(rel)
            res = asyncio.run(self.read_tool({"file_path": rel}))
            if res.ok:
                payload_text = str(res.payload)
                self.read_payloads.append(payload_text)
                conclusion = self._analyze(payload_text)
            else:
                conclusion = f"read_file 失败: {res.error_message}"

        text = (f"根据 {m.group(0) if m else '?'} 的数据分析:{conclusion}")
        run = AtomRun(
            atom_id=f"atom-csv-{n}",
            input={"intent": brain_intent},
            output={"text": text},
            success=True,
            tool_calls=[{"name": "read_file", "input": {"file_path": m.group(0) if m else ""}}],
            trace_ref=f"trace://atom-csv/{n}",
            ts=1000.0 + n,
        )
        return (text, run)

    @staticmethod
    def _analyze(csv_text: str) -> str:
        """从 read_file 读到的 CSV 文本算确定性结论(证明结论真从数据来,不是编的)。

        read_file 产出带行号前缀(`     2\t2026-01,100`),先剥行号再解析。
        """
        rows: list[tuple[str, int]] = []
        for line in csv_text.splitlines():
            # 剥 ReadTool 的 "<lineno>\t" 前缀
            cell = line.split("\t", 1)[-1].strip()
            parts = cell.split(",")
            if len(parts) == 2 and parts[1].strip().isdigit():
                rows.append((parts[0].strip(), int(parts[1].strip())))
        if not rows:
            return "数据为空"
        total = sum(v for _, v in rows)
        top_month, top_val = max(rows, key=lambda r: r[1])
        return f"营收峰值在 {top_month}(={top_val}),三个月合计 {total}"


# ---- 缝合测主体 ----

def test_csv_upload_to_analysis_full_chain(tmp_path, monkeypatch):
    """用户上传 CSV → 让角色分析:整链走真 drive/recall,断四条缝。

    相对路径按**生产默认**解析:console 从工作区启动(workspace_root=cwd),故 chdir 到
    工作区(同 test_data_analyst_recall.test_intent_relative_path_readable 的生产前提)。
    """
    ws, csv = _mk_workspace(tmp_path)
    idx = _index(tmp_path)
    read_tool = _real_read_tool(ws)
    brain = _CsvAnalystSlowBrain(ws, read_tool)

    # 真 MainLoop:skills_dir=用户目录(空);索引扫到 bundled data-analyst。
    ml = MainLoop(skills_dir=tmp_path, skill_index=idx, scope="user")

    # files 面板"让TA分析"按钮注入的原文形状(相对工作区路径)
    intent = "帮我分析一下 data/sales.csv 这份销售数据"
    monkeypatch.chdir(ws)   # 生产:console 从工作区启动,相对路径以 workspace 为根

    result = ml.drive(intent, slow_brain=brain)

    # S1:recall 真命中 data-analyst(而非 miss / 命中别的)
    assert result.skill_name == "data-analyst", \
        f"recall 没命中 data-analyst(缝1):skill_name={result.skill_name!r}"

    # S2:送进 slow_brain 的 brain_intent 同时含【方法段】和【CSV 路径】
    assert brain.seen_brain_intents, "slow_brain 从未被调用(drive 没走到慢脑)"
    bi = brain.seen_brain_intents[-1]
    #   (a) 方法制导生效:data-analyst 的方法特征串在里面(compose_rerun_context 带了 body)
    assert ("semantic layer" in bi or "Procedure" in bi or "已有方法" in bi), \
        "brain_intent 没带 data-analyst 的方法段(缝2a:方法制导没生效)"
    #   (b) 原意图里的 CSV 路径没在组装时蒸发([当前任务] 段保住了它)
    assert "data/sales.csv" in bi, \
        "brain_intent 丢了 CSV 路径(缝2b:compose_rerun_context 吞了 [当前任务] / 路径)"

    # S3:read_file 真被调用、读到的就是意图里那个路径、真读到 CSV 内容
    assert brain.read_calls == ["data/sales.csv"], \
        f"read_file 调用的路径不是意图里那个(缝3):{brain.read_calls}"
    assert brain.read_payloads and "revenue" in brain.read_payloads[-1] \
        and "210" in brain.read_payloads[-1], \
        "read_file 没真读到 CSV 内容(缝3:文件桥断)"

    # S4:产出带从 CSV 真算出的数字结论(峰值月 + 合计),不是空话
    assert "2026-03" in result.text and "210" in result.text and "460" in result.text, \
        f"产出没带从数据算出的数字结论(缝4):{result.text!r}"


def test_csv_chain_forward_slash_absolute_path_variant(tmp_path):
    """前端若注入**正斜杠绝对路径**(不依赖 cwd),整链同样通(相对路径变体的对照)。

    用 forward-slash 绝对路径(KarvyLoop Linux 首发,files 面板注入正斜杠路径的生产形状):
    绝对路径不依赖 cwd,直接读到。
    """
    ws, csv = _mk_workspace(tmp_path)
    idx = _index(tmp_path)
    read_tool = _real_read_tool(ws)
    brain = _CsvAnalystSlowBrain(ws, read_tool)
    ml = MainLoop(skills_dir=tmp_path, skill_index=idx, scope="user")

    csv_fwd = str(csv).replace("\\", "/")
    intent = f"帮我分析一下这份销售数据的趋势,文件是 {csv_fwd}"
    result = ml.drive(intent, slow_brain=brain)

    assert result.skill_name == "data-analyst", \
        f"正斜杠绝对路径意图没命中 data-analyst:{result.skill_name!r}"
    assert brain.read_calls == [csv_fwd], f"绝对路径没原样传到 read_file:{brain.read_calls}"
    assert "460" in result.text


def test_csv_chain_windows_backslash_path_recall_dilution(tmp_path):
    """记录一个**真实边缘**(审计缝合测产物,LOW):意图里若嵌**长 Windows 反斜杠绝对路径**,
    路径噪声 token(c/users/appdata/local/temp/…)会挤占 recall 的意图聚类窗口
    (signature 聚类有界),把语义 token(数据/csv/趋势)挤出窗口 → recall **漏命中** data-analyst。

    - 影响面窄:仅"长反斜杠绝对路径嵌在语义词之前"这一形状触发;
    - 生产路径不受影响:files 面板注入**工作区相对路径 / 正斜杠路径**(上两测已证命中);
    - 修复需动 recall/signature 聚类(本任务显式禁改的核心)→ 只记录不修。

    本测**锁住现状**:若哪天聚类改了(命中了),这条会红 → 提示回来把它升成正例。
    """
    ws, csv = _mk_workspace(tmp_path)
    idx = _index(tmp_path)
    ml = MainLoop(skills_dir=tmp_path, skill_index=idx, scope="user")
    brain = _CsvAnalystSlowBrain(ws, _real_read_tool(ws))

    # 长反斜杠绝对路径嵌在"分析…数据"之间(噪声在前,语义在后)
    win_path = r"C:\Users\ch\AppData\Local\Temp\ws\data\sales.csv"
    intent = f"帮我分析一下 {win_path} 这份销售数据的趋势"
    result = ml.drive(intent, slow_brain=brain)
    # 现状 = 漏命中(记录,不是期望的最终行为)
    assert result.skill_name != "data-analyst", \
        ("Windows 反斜杠路径稀释现象已消失(聚类被改?)—— 把本测升成正例并核对生产路径。"
         f" 实际 skill_name={result.skill_name!r}")


def test_csv_chain_irrelevant_intent_does_not_recall_analyst(tmp_path):
    """对照:无关意图(写诗)不命中 data-analyst —— 证明 S1 的命中不是"总命中"假阳。"""
    ws, _ = _mk_workspace(tmp_path)
    idx = _index(tmp_path)
    brain = _CsvAnalystSlowBrain(ws, _real_read_tool(ws))
    ml = MainLoop(skills_dir=tmp_path, skill_index=idx, scope="user")

    result = ml.drive("帮我写一首关于春天的诗", slow_brain=brain)
    assert result.skill_name != "data-analyst", \
        f"无关意图误命中 data-analyst(S1 假阳):{result.skill_name!r}"
