"""test_devices_share_ui — 「我的设备」面板:分享 UI + mesh 任务板可见面的**源级静态契约**。

锁的是真源 frontend/src(build 产物 static/ 由统一构建刷新;built 侧另有 test_console_i18n
的 AC5/AC8 双表 parity + bundle 键存在锁,构建后自动覆盖到新键)。不起浏览器 —— 这里锁接线
契约与安全性质,防 build 前后漂移:

① 分享发起:POST /api/pair/issue 必带 {scope:"read"}(绝不裸 {} —— 旧语义裸 body=full 全权码);
   角色下拉来自 /api/roles,value 用 display_name(兵法 applies.role 存的名字,别的标识符对不上)。
② 全权码防御:后端回的不是 read scope → 不展示(部署偏斜时绝不把 full 码递给外人)。
③ QR 复用:分享码走既有 karvy-pair 深链 + qrcode-generator(仓内唯一 QR 实现,不引新库)。
④ 吊销:走既有 POST /api/pair/revoke + window.confirm 人话确认;管理面经隧道被拒 → 隐藏并给一句为什么。
⑤ 任务板:GET /api/mesh/board 接进设备卡;三态人话键(排队中/在跑/⚠中断)+ 空板零高度(rows 空不挂)。
⑥ i18n:新增 devices.board.* / devices.share.* 键 en/zh 双表齐(源级 parity;tsc 编译期断言同锁)。
"""
from __future__ import annotations

import pathlib
import re

ROOT = pathlib.Path(__file__).resolve().parents[1]
SRC = ROOT / "karvyloop" / "console" / "frontend" / "src"


def _read(name: str) -> str:
    return (SRC / name).read_text(encoding="utf-8")


def _block(ts: str, label: str) -> set:
    """从 i18n.ts 抽 `en: {…}` / `zh: {…}` 键集合(括号配平,同 test_console_i18n 手法)。"""
    m = re.search(rf"\b{label}\s*:\s*\{{", ts)
    assert m, f"i18n.ts 缺 {label} 表"
    start = m.end() - 1
    depth = 0
    for i in range(start, len(ts)):
        if ts[i] == "{":
            depth += 1
        elif ts[i] == "}":
            depth -= 1
            if depth == 0:
                return set(re.findall(r'"([^"]+)"\s*:', ts[start + 1:i]))
    raise AssertionError(f"{label} 块括号不配平")


# ---- ① 分享发起链路 ----

def test_share_issue_posts_explicit_read_scope():
    ts = _read("devices_panel.ts")
    assert '_postJSON("/api/pair/issue", { scope: "read", role:' in ts, \
        "分享发起必须显式 scope:'read'(裸 body 在旧后端=full 全权码)"
    assert '"/api/roles"' in ts, "角色下拉应来自 /api/roles"
    assert "r.display_name || r.nickname || r.id" in ts, \
        "下拉 value 必须优先 display_name(兵法 applies.role 存的名字)"


# ---- ② 全权码防御(部署偏斜安全性质)----

def test_share_refuses_to_show_non_read_code():
    ts = _read("devices_panel.ts")
    share = ts[ts.index("async function _shareScene"):ts.index("function _advancedScene")]
    assert 'd.scope !== "read"' in share, \
        "后端没回 read 码必须拒展示(绝不把 full 码当分享码递出去)"
    assert share.index('d.scope !== "read"') < share.index("createSvgTag"), \
        "scope 防御必须发生在深链/QR 渲染之前"


# ---- ③ QR 复用(不引新库,同一 karvy-pair 深链格式)----

def test_share_reuses_existing_qr_implementation():
    ts = _read("devices_panel.ts")
    assert ts.count('import qrcode from "qrcode-generator"') == 1, \
        "QR 只有一个实现来源(qrcode-generator),分享码不引新库"
    assert ts.count('"karvy-pair:"') >= 2, \
        "分享码应复用 away 配对同一 karvy-pair 深链格式(接入页认这个)"
    # 分享块内真用了 QR(createSvgTag 本地自产 SVG)
    share = ts[ts.index("async function _shareScene"):ts.index("function _advancedScene")]
    assert "createSvgTag" in share and "_b64urlEncode" in share


# ---- ④ 吊销 + 管理权=本地 ----

def test_share_revoke_and_local_only_discipline():
    ts = _read("devices_panel.ts")
    share = ts[ts.index("async function _shareScene"):ts.index("function _advancedScene")]
    assert '"/api/pair/revoke"' in share, "吊销必须走既有 revoke 端点"
    assert "window.confirm" in share, "吊销前必须人话确认(吊销即断,不可逆到要重配)"
    assert 'devices.share.revoke_confirm' in share
    # 经隧道被拒(data.ok === false)→ 隐藏管理面,给一句为什么(后端 reason 翻译或本地键)
    assert "data.ok === false" in share
    assert "devices.share.local_only" in share
    # 手机场景的已授权列表不再混入 read 分享(它们归分享区管)
    assert 'p.scope !== "read"' in ts


# ---- ⑤ 任务板可见面 ----

def test_board_wired_into_device_cards():
    ts = _read("devices_panel.ts")
    assert '"/api/mesh/board"' in ts, "设备面板必须拉任务板快照"
    assert "tasks_by_device" in ts
    # 空板零高度:rows 空直接 return,不渲染空壳
    assert "if (!rows || !rows.length) return;" in ts
    # 三态人话键 + 中断 ⚠ 提示走 i18n(不硬编码中文)
    for key in ("devices.board.queued", "devices.board.running", "devices.board.stalled"):
        assert f'"{key}"' in ts, f"任务板三态人话键缺 {key}"


# ---- ⑥ i18n 源级 parity(新键 en/zh 双表齐;构建后由 AC5/AC8 在 static 侧接力)----

def test_new_i18n_keys_exist_in_both_tables():
    i18n = _read("i18n.ts")
    en, zh = _block(i18n, "en"), _block(i18n, "zh")
    ts = _read("devices_panel.ts")
    used = set(re.findall(r'\bt\(\s*"((?:devices\.board|devices\.share)\.[^"]+)"', ts))
    assert used, "devices_panel.ts 应真用到 board/share 键(别删空了)"
    missing_en = used - en
    missing_zh = used - zh
    assert not missing_en, f"en 表缺键(运行时裸显键名): {missing_en}"
    assert not missing_zh, f"zh 表缺键(切中文裸显键名): {missing_zh}"
