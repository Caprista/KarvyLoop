"""urlguard —— web_fetch / 出站 HTTP 的 SSRF 地板(只读联网 = 基础能力,但不能被当跳板)。

威胁模型:web_fetch 的 URL 来自**不可信输入**(模型输出、用户贴的链接、被抓网页里的
重定向)。若不设防,一个"帮我读下这个页面 http://169.254.169.254/latest/meta-data/"
就能把云厂商实例元数据(含临时凭证)喂回模型,或探测/打内网(SSRF)。

地板(fail-closed):
- 只允许 http/https(挡 file:// ftp:// gopher:// data:// 等)。
- 解析 host 到 IP,任一解析结果落在**私有/环回/链路本地/保留/组播/未指定**段 → 拒
  (挡 169.254.169.254 云元数据、127.0.0.1/localhost、10./172.16./192.168. 内网、
   [::1]、fd00::/7、fe80::/10 等;host 直接写 IP 也一样过这关)。
- host 为空 / URL 带 credential(user:pass@host)→ 拒(credential 混淆 SSRF)。
- **每一跳都要过闸**:重定向必须由调用方逐跳重新校验(见 web._http_get),不能只信首个 URL。

为什么按**解析后的 IP** 判:光按主机名黑名单(localhost/metadata.*)会被 DNS 指向内网
(`http://myhost.attacker.com` → A 记录 127.0.0.1)绕过;解析到 IP 再判才真正堵住。
DNS rebinding(TOCTOU:校验时公网 IP、连接时私网 IP)在纯 httpx 下无法 100% 消除,
但逐跳校验 + 连接后无二次解析已挡住绝大多数;彻底消除需 pin IP 连接(未来可加)。

无第三方依赖:纯 stdlib(ipaddress + socket + urllib.parse)。
"""

from __future__ import annotations

import ipaddress
import socket
from urllib.parse import urlsplit


class SsrfBlocked(ValueError):
    """URL 未通过 SSRF 地板(拒绝出站)。message 是人话原因,可回灌模型。"""


_ALLOWED_SCHEMES = ("http", "https")

# 代理 fake-ip 池(RFC 2544 benchmark 段 198.18.0.0/15):Clash/Surge/sing-box/V2Ray 在 fake-ip
# 模式下把**真实公网域名**映射到这段合成 IP,再由代理内核拦截、转发到真实地址——中国用户几乎
# 人手一个(Hardy 真机实拍:baike.baidu.com/addyosmani.com 全解析到 198.18.x.x)。
# 威胁再分析:这段本身在公网**不可路由**,普通机器上无任何内网服务监听于此(连过去直接失败);
# fake-ip 机器上它等价于"经代理访问那个公网域名",不比放行该域名本身更危险。一刀切按 is_private
# 挡掉 = 让"贴链接给馆员读"对全体 fake-ip 用户彻底失效,却换不到任何真实 SSRF 防护。故显式放行。
# (真正的 SSRF 靶——169.254 云元数据 / 127 环回 / 10·172.16·192.168 内网 / CGNAT 100.64——照挡不误。)
_FAKE_IP_PROXY = ipaddress.ip_network("198.18.0.0/15")


def _ip_is_blocked(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """这个 IP 落在不许出站的段里吗?(私有/环回/链路本地/保留/组播/未指定)"""
    # IPv6 映射的 IPv4(::ffff:127.0.0.1 之类)先剥回 v4 再判,防绕过。
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    if isinstance(ip, ipaddress.IPv4Address) and ip in _FAKE_IP_PROXY:
        return False   # 代理 fake-ip 合成地址:放行(见上方威胁再分析),不当内网挡
    return (
        ip.is_private          # 10/8, 172.16/12, 192.168/16, fc00::/7 …
        or ip.is_loopback      # 127/8, ::1
        or ip.is_link_local    # 169.254/16(云元数据!), fe80::/10
        or ip.is_reserved
        or ip.is_multicast
        or ip.is_unspecified   # 0.0.0.0, ::
    )


def _resolve_all_ips(host: str) -> list[str]:
    """host → 所有解析出的 IP(A/AAAA)。裸 IP 字面量直接返回自身。解析失败抛给调用方。"""
    # 已经是 IP 字面量?(含去掉 IPv6 方括号后的形态)
    bare = host.strip("[]")
    try:
        ipaddress.ip_address(bare)
        return [bare]
    except ValueError:
        pass
    infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    return [info[4][0] for info in infos]


def check_url(url: str) -> None:
    """URL 未过 SSRF 地板 → 抛 SsrfBlocked;过了 → 静默返回 None。

    校验:① scheme ∈ {http,https} ② 无 credential(user:pass@)③ host 非空
    ④ host 解析出的**每一个 IP** 都不在被挡段里。
    """
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in _ALLOWED_SCHEMES:
        raise SsrfBlocked(f"scheme {scheme!r} 不允许(只准 http/https)")
    # credential 混淆:http://evil.com@169.254.169.254/ 里浏览器/库以 @ 后为真 host,
    # 但人/正则常被 @ 前迷惑。带 userinfo 一律拒(合法只读抓取不需要 URL 内嵌账密)。
    if parts.username is not None or parts.password is not None or "@" in (parts.netloc or ""):
        raise SsrfBlocked("URL 不允许内嵌 credential(user:pass@host = SSRF 混淆向量)")
    host = parts.hostname
    if not host:
        raise SsrfBlocked("URL 缺少 host")
    try:
        ips = _resolve_all_ips(host)
    except (OSError, UnicodeError) as e:
        # 解析不了就别连(DNS 失败也可能是攻击者的探测)。人话原因回灌。
        raise SsrfBlocked(f"host {host!r} 无法解析:{type(e).__name__}") from e
    if not ips:
        raise SsrfBlocked(f"host {host!r} 未解析出任何 IP")
    for ip_str in ips:
        try:
            ip = ipaddress.ip_address(ip_str.split("%", 1)[0])  # 去掉 IPv6 zone id
        except ValueError:
            raise SsrfBlocked(f"host {host!r} 解析出无法识别的地址 {ip_str!r}")
        if _ip_is_blocked(ip):
            raise SsrfBlocked(
                f"host {host!r} 解析到内网/保留地址 {ip}(拒绝出站,防 SSRF/云元数据窃取)")


__all__ = ["check_url", "SsrfBlocked"]
