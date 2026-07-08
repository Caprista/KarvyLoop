"""test_ssrf — web_fetch / 出站 HTTP 的 SSRF 地板对抗验收(HIGH,雷达点名缺口)。

背景:web_fetch 是每个 agent 的基础能力,URL 来自**不可信输入**(模型输出 / 用户贴的
链接 / 被抓网页里的重定向)。修复前 web_fetch 对任意 http(s) URL 直发且自动跟随重定向 →
可被"帮我读下 http://169.254.169.254/latest/meta-data/"窃取云实例临时凭证,或探测/打内网。
本组锁住 urlguard 地板(karvyloop/coding/tools/urlguard.py)+ web_fetch 端到端拦截。

判据全部**用字面 IP**(不靠 DNS),CI 里可离线确定性运行:
- 云元数据(AWS/GCP/Azure 169.254.169.254、ECS 169.254.170.2)
- 环回(127.0.0.1 / localhost / [::1] / v4-mapped [::ffff:127.0.0.1] / 十进制 2130706433)
- 私网(10/8、172.16/12、192.168/16)、链路本地(169.254/16、fe80::/10)、ULA(fd00::/7)
- 未指定(0.0.0.0 / ::)
- 非 http(s) scheme(file:// ftp:// gopher:// data://)
- credential 混淆(http://evil.com@169.254.169.254/)
- 重定向到内网(逐跳校验)
"""
from __future__ import annotations

import pathlib
import sys

import pytest

ROOT = pathlib.Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from karvyloop.coding.tools.urlguard import SsrfBlocked, check_url  # noqa: E402
from karvyloop.coding.tools.web import WebFetchTool, _http_get  # noqa: E402

pytestmark = pytest.mark.security


# ---- 被挡向量:全部用字面 IP / scheme,不依赖 DNS ----
BLOCKED_URLS = [
    # 云实例元数据端点(SSRF 最高价值目标:临时凭证)
    "http://169.254.169.254/latest/meta-data/iam/security-credentials/",
    "http://169.254.169.254/computeMetadata/v1/",     # GCP
    "http://[fd00:ec2::254]/latest/meta-data/",        # AWS IMDS over IPv6
    "http://169.254.170.2/v2/credentials/",            # ECS task metadata
    # 环回(各种编码绕过形态)
    "http://127.0.0.1/admin",
    "http://127.0.0.1:8766/api/spend",                 # 打本机 console
    "http://[::1]:8080/",
    "http://[::ffff:127.0.0.1]/",                      # IPv4-mapped IPv6 环回
    "http://2130706433/",                              # 十进制编码的 127.0.0.1
    "http://0.0.0.0/",                                 # 未指定 → 本机
    # 内网私有段
    "http://10.0.0.5/",
    "http://172.16.0.1/",
    "http://192.168.1.1/",
    "http://[fd12:3456::1]/",                          # IPv6 ULA
    "http://[fe80::1]/",                               # IPv6 链路本地
    # 非 http(s) scheme
    "file:///etc/passwd",
    "file://C:/Windows/win.ini",
    "ftp://10.0.0.1/",
    "gopher://127.0.0.1:6379/_SET%20x%20y",            # 打内网 redis
    "data:text/plain;base64,SGVsbG8=",
    # credential 混淆(@ 后才是真 host)
    "http://trusted.example.com@169.254.169.254/latest/meta-data/",
    "http://user:pass@127.0.0.1/",
]


@pytest.mark.parametrize("url", BLOCKED_URLS)
def test_urlguard_blocks_ssrf_vectors(url):
    """每个 SSRF 向量都必须被 check_url 挡下(fail-closed)。"""
    with pytest.raises(SsrfBlocked):
        check_url(url)


def test_urlguard_allows_public_literal_ip():
    """公网字面 IP 放行(证明地板不是"全拒"——只拒内网/保留/元数据)。"""
    check_url("http://93.184.216.34/")   # 公网 IP(不解析 DNS,离线可判)
    check_url("https://1.1.1.1/")        # Cloudflare 公共 DNS,公网


def test_urlguard_allows_fake_ip_proxy_range():
    """代理 fake-ip 池(RFC 2544 benchmark 198.18.0.0/15)必须放行。

    Clash/Surge/sing-box/V2Ray 在 fake-ip 模式下把**真实公网域名**映射到这段合成 IP,再由代理
    内核转发到真址。一刀切按 is_private 挡掉 = 让"贴链接给馆员读"对全体 fake-ip 用户彻底失效
    (Hardy 真机实拍:baike.baidu.com / addyosmani.com 全解析到 198.18.x.x → 全被误挡)。
    这段公网不可路由、普通机器无内网服务监听于此,放行不换来任何真实 SSRF 防护损失。"""
    for ip in ("http://198.18.0.0/", "http://198.18.1.110/", "http://198.19.255.255/"):
        check_url(ip)   # 不抛 = 放行(fake-ip 合成地址)
    # 回归护栏:carve-out 绝不能顺手放开真正的内网/元数据靶
    import ipaddress

    from karvyloop.coding.tools.urlguard import _ip_is_blocked
    for bad in ("169.254.169.254", "127.0.0.1", "10.0.0.1", "192.168.1.1", "172.16.0.1", "0.0.0.0"):
        assert _ip_is_blocked(ipaddress.ip_address(bad)) is True
    # 边界外(198.17.x / 198.20.x)是普通公网,本就放行——确认 carve-out 没越界成"挡公网"
    for edge in ("198.17.255.255", "198.20.0.0"):
        assert _ip_is_blocked(ipaddress.ip_address(edge)) is False


def test_urlguard_rejects_missing_host_and_empty():
    for bad in ("http:///path", "https://", "notaurl"):
        with pytest.raises(SsrfBlocked):
            check_url(bad)


async def test_web_fetch_blocks_metadata_end_to_end():
    """端到端:WebFetchTool 抓云元数据 → ok=False + 人话 SSRF 原因,**绝不真发请求命中内网**。"""
    tool = WebFetchTool()
    res = await tool.__call__({"url": "http://169.254.169.254/latest/meta-data/"})
    assert res.ok is False
    assert "SSRF" in (res.error_message or "")


async def test_web_fetch_blocks_localhost_console():
    """经典内网跳板:让 agent 打本机 console。必须被拦。"""
    tool = WebFetchTool()
    res = await tool.__call__({"url": "http://127.0.0.1:8766/api/spend"})
    assert res.ok is False and "SSRF" in (res.error_message or "")


async def test_web_fetch_blocks_credential_confusion():
    tool = WebFetchTool()
    res = await tool.__call__(
        {"url": "http://trusted.example.com@169.254.169.254/latest/meta-data/"})
    assert res.ok is False and "SSRF" in (res.error_message or "")


async def test_http_get_blocks_before_any_network(monkeypatch):
    """确定性:被挡的 URL 在 _http_get 里**根本不发 httpx 请求**(拦在网络前)。"""
    import httpx

    sent = {"n": 0}
    real_get = httpx.AsyncClient.get

    async def _spy(self, *a, **k):
        sent["n"] += 1
        return await real_get(self, *a, **k)

    monkeypatch.setattr(httpx.AsyncClient, "get", _spy)
    ok, msg = await _http_get("http://169.254.169.254/latest/meta-data/")
    assert ok is False and "SSRF" in msg
    assert sent["n"] == 0, "被挡的 SSRF URL 不该真发出任何 HTTP 请求"


async def test_web_fetch_redirect_to_internal_is_blocked(monkeypatch):
    """重定向到内网必须逐跳拦:首个 URL 是公网,301 到 169.254.169.254 → 拦。

    用 httpx MockTransport 造一个 30x→内网 的响应,验 _http_get 手动跟随时对下一跳
    重新过 SSRF 闸(不给"首 URL 干净、跳到内网"留缝)。
    """
    import httpx

    def _handler(request: httpx.Request) -> httpx.Response:
        # 首个公网 URL 返回 302 → 云元数据端点
        return httpx.Response(302, headers={"location": "http://169.254.169.254/latest/meta-data/"})

    transport = httpx.MockTransport(_handler)
    real_init = httpx.AsyncClient.__init__

    def _init(self, *a, **k):
        k["transport"] = transport
        real_init(self, *a, **k)

    monkeypatch.setattr(httpx.AsyncClient, "__init__", _init)
    # 首个 URL 用公网字面 IP(过首闸),它 302 到内网元数据(必须被第二跳拦)
    ok, msg = await _http_get("http://93.184.216.34/redir")
    assert ok is False
    assert "SSRF" in msg and "重定向" in msg
