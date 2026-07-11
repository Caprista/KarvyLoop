"""external_runtime/connector — 认领码握手的**连接器脚本**(用户复制去自己的 runtime 里跑的那段)。

反向接入模型(不是"本机 attach 填 bin",是 GitHub-runner-注册那种):
  1. 用户在 console 点「＋添加外部 runtime」→ 后端建 pending 壳 + 发一把**一次性、带过期**的认领秘钥
     + 生成一段复制指令(含秘钥 + 认领回调 URL)。
  2. 用户把这段复制到**自己的 runtime 环境**里跑 —— 就是这个脚本:它拿秘钥 POST 回
     `<console>/api/external/claim`,顺带**自报身份/能力**(runtime_kind/bin/version/capabilities)。
  3. 后端校验秘钥(一次性/未过期/匹配那个 pending 壳)→ 激活壳成正式公民 → 秘钥立即作废。

**薄**:只用标准库(urllib),没有第三方依赖 —— 用户复制去任何有 Python 的 runtime 环境都能跑。
**scope(M2)**:本次做**握手 + 本机驱动**。认领时可登记 bin(本机场景),之后仍走现有子进程桥驱动(复用)。
真·远程持久通道(runtime 在另一台机器、长连回连接活)= **M3 TODO**,本脚本不实现(见文末 __M3_TODO__)。

**秘钥纪律**:秘钥是命令行参数 / 环境变量传入,本脚本**不打印秘钥**(和 API key 同);出错也只回状态。
自报能力是**用户这侧**报的,后端当 untrusted 数据登记(不据此提权)。
"""
from __future__ import annotations

import argparse
import json
import os
import platform
import sys
import urllib.error
import urllib.request

# 真·远程持久通道(另一台机器长连回连接活 + 反向驱动)= M3,本连接器只做一次性认领握手。
__M3_TODO__ = (
    "M3: 真远程持久通道 —— runtime 在另一台机器时,认领后维持一条长连(回连/心跳/反向派活),"
    "而非本机子进程桥。本连接器现在只做**一次性认领 POST**,认领后本机场景走现有子进程桥驱动。"
)


def _self_report(runtime_kind: str, bin_path: str, version: str,
                 capabilities: list[str]) -> dict:
    """组装自报载荷(untrusted:后端登记不提权)。带一点确定性环境事实(OS/python)供人读诊断。"""
    caps = list(capabilities or [])
    # 环境事实(非机密):帮机主在 console 上认出"这是哪台 runtime";不含任何秘钥/凭证。
    env_note = f"{platform.system()}/{platform.machine()} py{platform.python_version()}"
    return {
        "runtime_kind": (runtime_kind or "").strip(),
        "bin_path": (bin_path or "").strip(),
        "version": (version or env_note).strip(),
        "capabilities": [str(c).strip() for c in caps if str(c).strip()],
    }


def claim(claim_url: str, secret: str, *, runtime_kind: str = "", bin_path: str = "",
          version: str = "", capabilities: list[str] | None = None,
          timeout_s: float = 15.0) -> dict:
    """POST 认领秘钥 + 自报身份到 claim 端点。返回后端 JSON(dict);网络/解析出错抛异常。

    **不打印/不记 secret**;secret 只进请求体(HTTPS/HTTP 传输,本地优先常是 http://127.0.0.1)。
    """
    payload = {"secret": secret}
    payload.update(_self_report(runtime_kind, bin_path, version, capabilities or []))
    data = json.dumps(payload).encode("utf-8")
    reqst = urllib.request.Request(
        claim_url, data=data, method="POST",
        headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(reqst, timeout=timeout_s) as resp:  # noqa: S310 — 用户自填 URL(本地优先)
        body = resp.read().decode("utf-8", errors="replace")
    try:
        return json.loads(body)
    except json.JSONDecodeError:
        return {"ok": False, "reason": f"认领端点返回非 JSON(HTTP {resp.status})"}


def _redact_secret_from_argv(argv: list[str]) -> None:
    """别让秘钥留在进程 argv 里被 ps 看到:能改就把 --secret 后一位抹成 ***(尽力,平台不一定生效)。"""
    try:
        for i, a in enumerate(argv):
            if a == "--secret" and i + 1 < len(argv):
                argv[i + 1] = "***"
    except Exception:
        pass


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="karvyloop.external_runtime.connector",
        description="认领码握手连接器:拿一次性秘钥连回 console,把这个 runtime 接入成外部公民。")
    p.add_argument("--claim-url", required=True,
                   help="认领回调 URL(console 生成的那段里带,形如 http://<host>:8766/api/external/claim)")
    p.add_argument("--secret", default="",
                   help="一次性认领秘钥(也可用环境变量 KARVYLOOP_CLAIM_SECRET 传,避免留在 shell 历史)")
    p.add_argument("--citizen-id", default="", help="(可选)壳花名,仅用于人读日志,不参与校验")
    p.add_argument("--runtime-kind", default="",
                   help="(可选,自报)这个 runtime 是哪类:generic_cli / single_json_cli / raw_text_sidecar")
    p.add_argument("--bin", dest="bin_path", default="",
                   help="(可选,自报)本机场景登记可执行路径 —— 之后本机驱动走现有子进程桥")
    p.add_argument("--version", default="", help="(可选,自报)runtime 版本/模型提示")
    p.add_argument("--capability", action="append", default=[], dest="capabilities",
                   help="(可选,自报,可多次)声明一项能力标签,如 --capability code --capability web")
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    # 秘钥来源:优先 --secret,退回环境变量(不进 shell 历史)。两个都空 → fail-loud。
    secret = args.secret or os.environ.get("KARVYLOOP_CLAIM_SECRET", "")
    if not secret:
        print("[connector] 缺认领秘钥:用 --secret <秘钥> 或环境变量 KARVYLOOP_CLAIM_SECRET 传入。",
              file=sys.stderr)
        return 2
    _redact_secret_from_argv(sys.argv)

    who = args.citizen_id or "(未命名壳)"
    print(f"[connector] 正在认领「{who}」→ {args.claim_url} …")  # 不打印秘钥
    try:
        result = claim(
            args.claim_url, secret, runtime_kind=args.runtime_kind,
            bin_path=args.bin_path, version=args.version, capabilities=args.capabilities)
    except urllib.error.HTTPError as e:
        # 400/403 等:后端拒(秘钥错/过期/来源门)。不回显秘钥,只回 HTTP 状态 + 尽力读原因。
        detail = ""
        try:
            detail = json.loads(e.read().decode("utf-8", errors="replace")).get("reason", "")
        except Exception:
            pass
        print(f"[connector] 认领被拒(HTTP {e.code}):{detail or e.reason}", file=sys.stderr)
        return 1
    except urllib.error.URLError as e:
        print(f"[connector] 连不上认领端点:{e.reason}(检查 --claim-url 可达、console 在跑)",
              file=sys.stderr)
        return 1
    except Exception as e:  # noqa: BLE001 — 连接器永不穿透栈,只回可读失败
        print(f"[connector] 认领出错:{type(e).__name__}: {e}", file=sys.stderr)
        return 1

    if result.get("ok"):
        print(f"[connector] ✓ 接入成功:公民「{result.get('citizen_id')}」已激活"
              f"(域={result.get('domain_id') or '—'},tier={result.get('tier')})。"
              f"回 console 面板,它已从'等待接入'翻成'在线'。")
        return 0
    print(f"[connector] ✗ 认领未通过:{result.get('reason') or '未知原因'}", file=sys.stderr)
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
