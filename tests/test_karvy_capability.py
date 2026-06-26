"""test_karvy_capability — 小卡 scope 边界 + 按 peer 分派(D6 重做,拍 9.4-门2-fix)。

设计:docs/29(纠正后)+ docs/20 K1。**边界是 scope,不是读/写**:
小卡能执行(系统/全局/运维/检索/个人活),只是不参与业务域(K1:只 observer,不接业务角色)。

AC:
- AC1 (K1): karvy_can_take_role 只认 observer,任何业务角色 → False
- AC2: is_karvy_peer / is_business_domain(l0=个人场;非 l0=业务域)
- AC3: 意图分类 —— 默认 execute(小卡自己干);显式转达=courier;显式委派=route
- AC4: dispatch 私聊小卡默认 should_drive=True(小卡个人/系统 scope 直接执行)← 修正核心
- AC5: dispatch 私聊小卡 + 委派语 → should_route=True、should_drive=False(不自己进业务域)
- AC6: dispatch 私聊小卡 + 转达语 → 都 False(courier)
- AC7: dispatch 业务域 peer → should_drive=True(业务 role 自己干)
"""
from __future__ import annotations

import pytest

from karvyloop.karvy.capability import (
    INTENT_COURIER,
    INTENT_EXECUTE,
    INTENT_ROUTE,
    KARVY_ROLE_OBSERVER,
    classify_karvy_intent,
    dispatch_for_peer,
    is_business_domain,
    is_karvy_peer,
    karvy_can_take_role,
)


# ---- AC1: K1 —— 小卡只 observer,不接业务角色 ----
def test_k1_only_observer_role():
    assert karvy_can_take_role(KARVY_ROLE_OBSERVER) is True
    assert karvy_can_take_role("观察者") is False
    assert karvy_can_take_role("设计师") is False
    assert karvy_can_take_role("会计") is False
    assert karvy_can_take_role("") is False
    assert karvy_can_take_role(None) is False


# ---- AC2: 场判定 ----
def test_peer_and_business_domain():
    assert is_karvy_peer("l0") is True
    assert is_karvy_peer("dom-1") is False
    assert is_business_domain("dom-1") is True
    assert is_business_domain("l0") is False
    assert is_business_domain(None) is False


# ---- AC3: 意图分类(默认 execute)----
@pytest.mark.parametrize("intent,expected", [
    ("搜一下最新论文", INTENT_EXECUTE),          # 检索 = 小卡自己干
    ("把系统时区改成上海", INTENT_EXECUTE),       # 系统设置 = 小卡自己干
    ("清一下磁盘缓存跑个运维脚本", INTENT_EXECUTE), # 运维 = 小卡自己干
    ("帮我把这批文件批量重命名", INTENT_EXECUTE),  # 个人活 = 小卡自己干
    ("帮我告诉张三明天开会", INTENT_COURIER),      # 转达
    ("让设计师做一版海报", INTENT_ROUTE),          # 委派业务 role
    ("把这个需求交给产品经理", INTENT_ROUTE),      # 委派
])
def test_classify(intent, expected):
    assert classify_karvy_intent(intent) == expected


# ---- AC4: 修正核心 —— 私聊小卡默认就执行 ----
@pytest.mark.parametrize("intent", [
    "搜一下天气", "改个系统设置", "跑个运维脚本", "重构我个人的脚本目录",
])
def test_karvy_peer_executes_by_default(intent):
    d = dispatch_for_peer("l0", intent)
    assert d.is_karvy is True
    assert d.intent_class == INTENT_EXECUTE
    assert d.should_drive is True    # 小卡个人/系统 scope 直接干(聊天即执行对小卡是对的)
    assert d.should_route is False


# ---- AC5: 委派业务活 → route,不自己进业务域 ----
def test_karvy_peer_delegates_business():
    d = dispatch_for_peer("l0", "让会计把这个月账做了")
    assert d.is_karvy is True
    assert d.intent_class == INTENT_ROUTE
    assert d.should_drive is False
    assert d.should_route is True


# ---- AC6: 转达 → courier(不 drive 不 route)----
def test_karvy_peer_courier():
    d = dispatch_for_peer("l0", "告诉张三我同意了")
    assert d.is_karvy is True
    assert d.intent_class == INTENT_COURIER
    assert d.should_drive is False
    assert d.should_route is False


# ---- AC7: 业务域 peer → 业务 role 自己执行 ----
def test_business_peer_executes():
    d = dispatch_for_peer("dom-1", "出一版设计稿")
    assert d.is_karvy is False
    assert d.should_drive is True
    assert d.should_route is False
