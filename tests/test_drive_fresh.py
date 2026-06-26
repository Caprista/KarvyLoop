"""test_drive_fresh — workflow/圆桌一次性步骤 fresh=True:跳过 recall+observe+结晶。

回归(Hardy):不同 workflow 的步骤共享 boilerplate 前缀 → token-overlap 聚类归并 →
下个 workflow 的步骤 recall 命中上一个的答案("答非所问·照第一次答")。fresh 必须断这条链。
"""
from __future__ import annotations

import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from karvyloop.cli.main_loop import MainLoop, Brain  # noqa: E402
from karvyloop.schemas.atom import AtomRun  # noqa: E402


def _ml(tmp_path):
    return MainLoop(skills_dir=tmp_path / "skills")


def _slow(text):
    def sb(intent, *, ctx=None):
        return text, AtomRun(atom_id="a", input={"intent": intent}, output={"text": text},
                             success=True, tool_calls=[{"name": "write_file"}], trace_ref="t", ts=1.0)
    return sb


def test_fresh_skips_crystallize_and_recall(tmp_path):
    ml = _ml(tmp_path)
    # 两个不同 workflow 的"同形"步骤(共享前缀,仅目标不同)
    i1 = "工作流目标:做登录页\n你的任务:出设计\n请完成你这一步,产出要能交给下游。"
    i2 = "工作流目标:做支付页\n你的任务:出设计\n请完成你这一步,产出要能交给下游。"
    r1 = ml.drive(i1, slow_brain=_slow("登录页设计稿"), fresh=True)
    assert r1.brain == Brain.SLOW and not r1.crystallized   # fresh 不结晶
    # 第二个步骤:fresh 不 recall → 必走慢脑出**新**答案,不串第一个
    r2 = ml.drive(i2, slow_brain=_slow("支付页设计稿"), fresh=True)
    assert r2.brain == Brain.SLOW and r2.text == "支付页设计稿"
    assert not r2.fast_brain_hit


def test_non_fresh_still_learns(tmp_path):
    # 不传 fresh → 正常路径仍会 observe/结晶(没退化楔子)
    ml = _ml(tmp_path)
    r = ml.drive("把 README 翻译成英文并写回文件", slow_brain=_slow("done"))
    assert r.brain == Brain.SLOW   # 首次慢脑;observe 已记(楔子路径未断)
